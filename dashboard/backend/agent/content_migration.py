from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol


_CONTENT_KEYS = frozenset(
    {
        "body",
        "content",
        "document",
        "prompt",
        "prompt_body",
        "prompt_text",
        "request",
        "request_body",
        "response",
        "response_body",
        "snapshot",
        "trace",
    }
)
_CONFIG_KEYS = frozenset(
    {
        "background_variant",
        "conversion_916_prompt",
        "copy_architecture",
        "copy_prompt_templates",
        "persona_seeds",
        "product_master_doc",
        "prompt_assembler_templates",
        "starting_prompt",
    }
)
_SECRET_KEYS = frozenset(
    {"api_key", "client_secret", "encrypted_api_key", "encrypted_client_secret"}
)
_COLLECTION_KINDS = {
    "agent_jobs": "legacy_job_content",
    "config_versions": "config_history",
    "json_blobs": "config_file",
    "llm_traces": "trace",
    "prompts": "prompt",
    "user_configs": "config_file",
}


class MigrationImporter(Protocol):
    authenticated: bool
    device_id: str

    def import_content(self, **request: Any) -> dict[str, Any]: ...

    def import_provider_secret(self, **request: Any) -> dict[str, Any]: ...


class BackupWriter(Protocol):
    def write(self, operation_id: str, document: dict[str, Any]) -> None: ...


class LocalAgentMigrationClient:
    """Authenticated loopback client for owner-scoped migration imports."""

    authenticated = True

    def __init__(
        self,
        *,
        base_url: str,
        session_token: str,
        owner_key: str,
        timeout: float = 30,
    ) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username
            or parsed.password
            or not session_token.strip()
            or not owner_key.strip()
        ):
            raise ValueError("A loopback URL, owner, and local agent session are required")
        self.base_url = base_url.rstrip("/")
        self._session_token = session_token.strip()
        self.owner_key = owner_key.strip()
        self.timeout = timeout
        self.device_id = ""

    def _post(self, path: str, payload: dict[str, Any], operation_id: str) -> dict[str, Any]:
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
            headers={
                "Authorization": f"Bearer {self._session_token}",
                "Content-Type": "application/json",
                "Idempotency-Key": operation_id,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read())
        except (OSError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            raise RuntimeError("Authenticated local agent migration request failed") from exc
        if not isinstance(result, dict):
            raise RuntimeError("Authenticated local agent migration response is invalid")
        self.device_id = str(result.get("device_id") or self.device_id)
        return result

    def _check_owner(self, request: dict[str, Any]) -> None:
        if str(request.get("owner_key") or "") != self.owner_key:
            raise ValueError("Migration document does not belong to the authenticated owner")

    def import_content(self, **request: Any) -> dict[str, Any]:
        self._check_owner(request)
        operation_id = str(request["operation_id"])
        return self._post(
            "/v1/migrations/content",
            {
                "kind": request["kind"],
                "logical_key": request["logical_key"],
                "content_base64": base64.b64encode(request["content"]).decode("ascii"),
                "media_type": request["media_type"],
                "expected_sha256": request["expected_sha256"],
                "operation_id": operation_id,
            },
            operation_id,
        )

    def import_provider_secret(self, **request: Any) -> dict[str, Any]:
        self._check_owner(request)
        operation_id = str(request["operation_id"])
        return self._post(
            "/v1/migrations/provider-secret",
            {
                "provider": request["provider"],
                "config": request["config"],
                "expected_secret_sha256": request["expected_secret_sha256"],
                "operation_id": operation_id,
            },
            operation_id,
        )


class EncryptedBackupVault:
    """Operation-addressed encrypted backups written before Mongo cleanup."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.key_path = self.root / "backup.key"

    def _fernet(self) -> Any:
        try:
            from cryptography.fernet import Fernet
        except ImportError as exc:
            raise RuntimeError("Encrypted backup support is unavailable") from exc
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        if not self.key_path.exists():
            temporary = self.root / f".backup-key-{os.getpid()}.tmp"
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(Fernet.generate_key())
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.key_path)
            finally:
                temporary.unlink(missing_ok=True)
        os.chmod(self.key_path, 0o600)
        return Fernet(self.key_path.read_bytes())

    def write(self, operation_id: str, document: dict[str, Any]) -> None:
        if not re.fullmatch(r"mig13_[0-9a-f]{64}", operation_id):
            raise ValueError("Migration operation ID is invalid")
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        destination = self.root / f"{operation_id}.backup"
        if destination.exists():
            return
        encrypted = self._fernet().encrypt(_canonical_bytes(document))
        temporary = self.root / f".{operation_id}.{os.getpid()}.tmp"
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                pass
            os.chmod(destination, 0o600)
        finally:
            temporary.unlink(missing_ok=True)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _safe_identifier(value: Any) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(value or "")).strip("-")
    return normalized[:80] or "unknown"


def _selector(document: dict[str, Any]) -> dict[str, Any]:
    if "_id" in document:
        return {"_id": document["_id"]}
    for key in ("prompt_id", "config_id", "version_id", "job_id", "trace_id"):
        if document.get(key):
            return {key: document[key]}
    raise ValueError("Migration candidate has no stable identity")


def _owner(document: dict[str, Any]) -> str:
    owner_id = str(document.get("owner_id") or document.get("user_id") or "").strip()
    owner_type = str(document.get("owner_type") or "user").strip()
    return f"{owner_type}:{owner_id}" if owner_id else ""


def _operation_id(
    collection: str, selector: dict[str, Any], owner: str, digest: str
) -> str:
    identity = _canonical_bytes(
        {
            "collection": collection,
            "selector": {key: str(value) for key, value in selector.items()},
            "owner": owner,
            "sha256": digest,
        }
    )
    return "mig13_" + hashlib.sha256(identity).hexdigest()


def _extract_content(
    value: Any,
    *,
    collection: str,
    path: tuple[str, ...] = (),
) -> tuple[dict[str, Any], list[str], bool]:
    extracted: dict[str, Any] = {}
    paths: list[str] = []
    malformed = False
    if not isinstance(value, dict):
        return extracted, paths, malformed
    for raw_key, child in value.items():
        key = str(raw_key)
        lowered = key.lower()
        current = (*path, key)
        dotted = ".".join(current)
        is_content = (
            lowered in _CONTENT_KEYS
            or lowered.endswith(("_body", "_content"))
            or (
                collection in {"user_configs", "config_versions", "json_blobs"}
                and lowered in _CONFIG_KEYS
            )
            or (path and path[-1] == "files" and lowered in _CONFIG_KEYS)
            or (collection == "agent_jobs" and lowered == "payload")
        )
        if is_content:
            if isinstance(child, (str, int, float, bool, dict, list)) or child is None:
                extracted[dotted] = child
                paths.append(dotted)
            else:
                malformed = True
            continue
        if isinstance(child, dict):
            nested, nested_paths, nested_malformed = _extract_content(
                child, collection=collection, path=current
            )
            extracted.update(nested)
            paths.extend(nested_paths)
            malformed = malformed or nested_malformed
    return extracted, paths, malformed


class MongoContentMigrator:
    """Dry-run-first migration from Mongo content bodies to local references."""

    def __init__(
        self,
        *,
        collections: Mapping[str, Any],
        importer: MigrationImporter,
        backup: BackupWriter,
        checkpoint_path: Path | None = None,
        decrypt_secret: Callable[[str], str] | None = None,
    ) -> None:
        self.collections = collections
        self.importer = importer
        self.backup = backup
        self.checkpoint_path = checkpoint_path
        self.decrypt_secret = decrypt_secret or (lambda value: value)

    def _checkpoint(self, operation_id: str) -> None:
        if self.checkpoint_path is None:
            return
        completed: list[str] = []
        if self.checkpoint_path.is_file():
            try:
                value = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
                completed = [
                    str(item)
                    for item in value.get("completed_operation_ids", [])
                    if isinstance(item, str)
                ]
            except (OSError, json.JSONDecodeError, AttributeError):
                completed = []
        if operation_id not in completed:
            completed.append(operation_id)
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.checkpoint_path.with_name(
            f".{self.checkpoint_path.name}.{os.getpid()}.tmp"
        )
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                {"version": 1, "completed_operation_ids": sorted(completed)},
                stream,
                sort_keys=True,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.checkpoint_path)
        os.chmod(self.checkpoint_path, 0o600)

    @staticmethod
    def _report(apply: bool) -> dict[str, Any]:
        return {
            "apply": bool(apply),
            "scanned": 0,
            "content_candidates": 0,
            "secret_candidates": 0,
            "migrated": 0,
            "secrets_migrated": 0,
            "unassigned": 0,
            "other_owner": 0,
            "malformed": 0,
            "errors": 0,
            "operations": [],
            "redacted": True,
        }

    def run(self, *, apply: bool = False) -> dict[str, Any]:
        report = self._report(apply)
        if apply and not getattr(self.importer, "authenticated", False):
            raise RuntimeError("Authenticated local agent session is required")
        for collection_name, collection in self.collections.items():
            for document in collection.find({}):
                report["scanned"] += 1
                if collection_name == "provider_configs":
                    self._provider_candidate(collection, document, apply, report)
                else:
                    self._content_candidate(
                        collection_name, collection, document, apply, report
                    )
        return report

    def _content_candidate(
        self,
        collection_name: str,
        collection: Any,
        document: dict[str, Any],
        apply: bool,
        report: dict[str, Any],
    ) -> None:
        content, paths, malformed = _extract_content(
            document, collection=collection_name
        )
        if malformed:
            report["malformed"] += 1
        if not content or malformed:
            return
        report["content_candidates"] += 1
        owner = _owner(document)
        if not owner:
            report["unassigned"] += 1
            return
        authenticated_owner = str(getattr(self.importer, "owner_key", "") or "")
        if authenticated_owner and owner != authenticated_owner:
            report["other_owner"] += 1
            return
        try:
            selector = _selector(document)
        except ValueError:
            report["malformed"] += 1
            return
        payload = _canonical_bytes(content)
        digest = hashlib.sha256(payload).hexdigest()
        operation_id = _operation_id(collection_name, selector, owner, digest)
        operation_report = {
            "operation_id": operation_id,
            "kind": "content",
            "status": "candidate" if not apply else "pending",
        }
        report["operations"].append(operation_report)
        if not apply:
            return
        self.backup.write(operation_id, document)
        logical_identity = hashlib.sha256(
            _canonical_bytes({key: str(value) for key, value in selector.items()})
        ).hexdigest()[:24]
        try:
            reference = self.importer.import_content(
                owner_key=owner,
                kind=_COLLECTION_KINDS.get(collection_name, "legacy_content"),
                logical_key=f"migration-{collection_name}-{logical_identity}",
                content=payload,
                media_type="application/json",
                operation_id=operation_id,
                expected_sha256=digest,
            )
            if (
                str(reference.get("sha256") or "") != digest
                or not reference.get("resource_id")
                or int(reference.get("version") or 0) < 1
            ):
                raise ValueError("Local import hash verification failed")
            metadata_reference = {
                "resource_id": str(reference["resource_id"]),
                "resource_version": int(reference["version"]),
                "sha256": digest,
                "authority_device_id": str(
                    reference.get("device_id")
                    or getattr(self.importer, "device_id", "")
                ),
                "migration_operation_id": operation_id,
            }
            collection.update_one(
                selector,
                {
                    "$set": {
                        "local_reference": metadata_reference,
                        "migration_status": "local_verified",
                        "migration_verified_at": time.time(),
                    },
                    "$unset": {path: "" for path in paths},
                },
            )
            self._checkpoint(operation_id)
            operation_report["status"] = "migrated"
            report["migrated"] += 1
        except RuntimeError:
            raise
        except Exception:
            operation_report["status"] = "verification_failed"
            report["errors"] += 1

    def _provider_candidate(
        self,
        collection: Any,
        document: dict[str, Any],
        apply: bool,
        report: dict[str, Any],
    ) -> None:
        secret_fields = {
            key: document[key]
            for key in _SECRET_KEYS
            if key in document and document[key] not in ("", None)
        }
        if not secret_fields:
            return
        report["secret_candidates"] += 1
        owner = _owner(document)
        provider = str(document.get("provider") or "").strip()
        if not owner:
            report["unassigned"] += 1
            return
        authenticated_owner = str(getattr(self.importer, "owner_key", "") or "")
        if authenticated_owner and owner != authenticated_owner:
            report["other_owner"] += 1
            return
        if provider not in {"opencode", "google_gemini"}:
            report["malformed"] += 1
            return
        try:
            selector = _selector(document)
            if not apply:
                operation_id = _operation_id(
                    "provider_configs", selector, owner, "dry-run"
                )
                report["operations"].append(
                    {
                        "operation_id": operation_id,
                        "kind": "provider_secret",
                        "status": "candidate",
                    }
                )
                return
            config: dict[str, str] = {
                key: str(document[key])
                for key in ("api_url", "default_model")
                if isinstance(document.get(key), str)
            }
            for key, value in secret_fields.items():
                target = key.removeprefix("encrypted_")
                plain = (
                    self.decrypt_secret(str(value))
                    if key.startswith("encrypted_")
                    else str(value)
                )
                if not plain:
                    raise ValueError("Provider secret is empty")
                config[target] = plain
            secret_digest = hashlib.sha256(
                _canonical_bytes(
                    {key: config[key] for key in sorted(config) if key in {"api_key", "client_secret"}}
                )
            ).hexdigest()
        except Exception:
            report["malformed"] += 1
            return
        operation_id = _operation_id(
            "provider_configs", selector, owner, secret_digest
        )
        operation_report = {
            "operation_id": operation_id,
            "kind": "provider_secret",
            "status": "pending",
        }
        report["operations"].append(operation_report)
        self.backup.write(operation_id, document)
        try:
            result = self.importer.import_provider_secret(
                owner_key=owner,
                provider=provider,
                config=config,
                operation_id=operation_id,
                expected_secret_sha256=secret_digest,
            )
            if result.get("verified") is not True:
                raise ValueError("Local provider secret verification failed")
            collection.update_one(
                selector,
                {
                    "$set": {
                        "local_provider_reference": {
                            "provider": provider,
                            "authority_device_id": str(
                                result.get("device_id")
                                or getattr(self.importer, "device_id", "")
                            ),
                            "verified": True,
                            "migration_operation_id": operation_id,
                        },
                        "migration_status": "local_verified",
                        "migration_verified_at": time.time(),
                    },
                    "$unset": {key: "" for key in secret_fields},
                },
            )
            self._checkpoint(operation_id)
            operation_report["status"] = "migrated"
            report["secrets_migrated"] += 1
        except RuntimeError:
            raise
        except Exception:
            operation_report["status"] = "verification_failed"
            report["errors"] += 1
