from __future__ import annotations

import cgi
import hashlib
import hmac
import json
import os
import secrets
import shutil
import tempfile
import threading
import time
import urllib.parse
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from .storage import AgentPaths, AgentState, VersionConflictError


ALL_SCOPES = frozenset(
    {
        "manifest:read",
        "content:read",
        "assets:write",
        "documents:write",
        "prompts:write",
        "runs:execute",
        "outputs:write",
        "revisions:write",
        "delete",
    }
)
ASSET_KINDS = frozenset({"product_image", "reference_image"})
IMAGE_SIGNATURES = {
    ".png": ("image/png", lambda data: data.startswith(b"\x89PNG\r\n\x1a\n")),
    ".jpg": ("image/jpeg", lambda data: data.startswith(b"\xff\xd8\xff")),
    ".jpeg": ("image/jpeg", lambda data: data.startswith(b"\xff\xd8\xff")),
    ".webp": (
        "image/webp",
        lambda data: len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP",
    ),
    ".gif": ("image/gif", lambda data: data.startswith((b"GIF87a", b"GIF89a"))),
}


@dataclass
class PairingChallenge:
    digest: str
    expires_at: float
    owner_key: str | None = None
    scopes: frozenset[str] = frozenset()
    agent_id: str | None = None
    device_id: str | None = None
    approved: bool = False
    consumed: bool = False


@dataclass
class LocalSession:
    owner_key: str
    scopes: frozenset[str]
    expires_at: float
    agent_id: str
    device_id: str


class APIError(RuntimeError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _load_or_create_local_secret(path: Path, prefix: str, byte_count: int) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(prefix + secrets.token_hex(byte_count) + "\n", encoding="ascii")
        os.chmod(temporary, 0o600)
        try:
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
    os.chmod(path, 0o600)
    value = path.read_text(encoding="ascii").strip()
    if not value.startswith(prefix) or len(value) > 256:
        raise RuntimeError(f"Invalid local identity file: {path.name}")
    return value


def load_or_create_device_id(paths: AgentPaths) -> str:
    return _load_or_create_local_secret(paths.config / "device-id", "dev_", 16)


def load_or_create_internal_token(paths: AgentPaths) -> str:
    return _load_or_create_local_secret(paths.config / "runtime-token", "lrt_", 32)


class LocalDataPlane:
    """Owner-scoped localhost API. It never serializes local filesystem paths."""

    def __init__(self, service: Any) -> None:
        self.service = service
        self.state: AgentState = service.state
        self._lock = threading.Lock()
        self._challenges: dict[str, PairingChallenge] = {}
        self._sessions: dict[str, LocalSession] = {}
        self.device_id = load_or_create_device_id(self.service.config.paths)

    def approve_challenge(
        self,
        challenge_id: str,
        challenge: str | None = None,
        *,
        challenge_digest: str | None = None,
        owner_key: str,
        scopes: list[str] | tuple[str, ...],
        agent_id: str = "",
        device_id: str = "",
    ) -> None:
        requested = frozenset(scopes)
        supplied_digest = challenge_digest or self._digest(str(challenge or ""))
        effective_agent_id = agent_id or "legacy-local-agent"
        if (
            not owner_key
            or not requested
            or not requested.issubset(ALL_SCOPES)
            or (device_id and device_id != self.device_id)
            or (challenge_digest is not None and len(challenge_digest) != 64)
        ):
            raise ValueError("Invalid pairing approval")
        with self._lock:
            item = self._challenges.get(challenge_id)
            if (
                item is None
                or item.consumed
                or item.expires_at <= time.time()
                or not hmac.compare_digest(item.digest, supplied_digest)
            ):
                raise ValueError("Pairing challenge is invalid or expired")
            if item.approved and (
                item.owner_key != owner_key
                or item.scopes != requested
                or item.agent_id != effective_agent_id
                or item.device_id != (device_id or self.device_id)
            ):
                raise ValueError("Pairing challenge authority cannot be changed")
            item.owner_key = owner_key
            item.scopes = requested
            item.agent_id = effective_agent_id
            item.device_id = device_id or self.device_id
            item.approved = True

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _cleanup_auth(self) -> None:
        now = time.time()
        self._challenges = {
            key: value
            for key, value in self._challenges.items()
            if value.expires_at > now and not value.consumed
        }
        self._sessions = {
            key: value for key, value in self._sessions.items() if value.expires_at > now
        }

    def _origin(self, handler: Any) -> str:
        return str(handler.headers.get("Origin") or "")

    def _validate_request_boundary(self, handler: Any) -> None:
        host = str(handler.headers.get("Host") or "")
        try:
            parsed_host = urllib.parse.urlsplit("//" + host)
            hostname = parsed_host.hostname
            port = parsed_host.port
        except ValueError as exc:
            raise APIError(421, "host_forbidden", "Local API requires a loopback Host") from exc
        if hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise APIError(421, "host_forbidden", "Local API requires a loopback Host")
        if parsed_host.username or parsed_host.password or parsed_host.path not in {"", "/"}:
            raise APIError(421, "host_forbidden", "Local API requires a loopback Host")
        if port is not None and not (1 <= port <= 65535):
            raise APIError(421, "host_forbidden", "Local API requires a loopback Host")
        origin = self._origin(handler)
        if origin == "null" or (origin and origin not in self.service.config.allowed_origins):
            raise APIError(403, "origin_forbidden", "Origin is not allowed")

    def _cors(self, handler: Any) -> None:
        origin = self._origin(handler)
        if origin in self.service.config.allowed_origins:
            handler.send_header("Access-Control-Allow-Origin", origin)
            handler.send_header("Vary", "Origin")
        handler.send_header(
            "Access-Control-Allow-Methods", "GET, HEAD, PUT, POST, DELETE, OPTIONS"
        )
        handler.send_header(
            "Access-Control-Allow-Headers",
            "Authorization, Content-Type, Idempotency-Key, If-Match, Range, X-Filename",
        )
        handler.send_header(
            "Access-Control-Expose-Headers",
            "Content-Disposition, Content-Length, Content-Range, ETag, Accept-Ranges",
        )
        if (
            str(handler.headers.get("Access-Control-Request-Private-Network") or "").lower()
            == "true"
        ):
            handler.send_header("Access-Control-Allow-Private-Network", "true")
        handler.send_header("Cache-Control", "no-store")

    def _json(
        self,
        handler: Any,
        status: int,
        payload: dict[str, Any] | list[Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            handler.send_header(key, value)
        self._cors(handler)
        handler.end_headers()
        if handler.command != "HEAD":
            handler.wfile.write(body)

    def _error(self, handler: Any, error: APIError) -> None:
        self._json(
            handler,
            error.status,
            {"error": {"code": error.code, "message": error.message}},
        )

    def _body(self, handler: Any, maximum: int = 256 * 1024) -> bytes:
        try:
            length = int(handler.headers.get("Content-Length") or "0")
        except ValueError as exc:
            raise APIError(400, "invalid_content_length", "Invalid Content-Length") from exc
        if length <= 0:
            return b""
        if length > maximum:
            raise APIError(413, "body_too_large", "Request body exceeds the allowed limit")
        body = handler.rfile.read(length)
        if len(body) != length:
            raise APIError(400, "incomplete_body", "Request body is incomplete")
        return body

    def _json_body(self, handler: Any, maximum: int = 256 * 1024) -> dict[str, Any]:
        try:
            value = json.loads(self._body(handler, maximum) or b"{}")
        except json.JSONDecodeError as exc:
            raise APIError(400, "invalid_json", "Request body must be valid JSON") from exc
        if not isinstance(value, dict):
            raise APIError(400, "invalid_json", "Request body must be a JSON object")
        return value

    def _session(self, handler: Any, scope: str | None = None) -> LocalSession:
        authorization = str(handler.headers.get("Authorization") or "")
        if not authorization.startswith("Bearer "):
            raise APIError(401, "authentication_required", "A Bearer session is required")
        token = authorization[7:].strip()
        digest = self._digest(token)
        with self._lock:
            self._cleanup_auth()
            session = self._sessions.get(digest)
        if session is None:
            raise APIError(401, "invalid_session", "Session is invalid, expired, or revoked")
        if scope and scope not in session.scopes:
            raise APIError(403, "insufficient_scope", f"Required scope: {scope}")
        return session

    def dispatch(self, handler: Any) -> bool:
        path = urllib.parse.urlparse(handler.path).path
        if not path.startswith("/v1/"):
            return False
        try:
            self._validate_request_boundary(handler)
            if handler.command == "OPTIONS":
                requested_method = str(
                    handler.headers.get("Access-Control-Request-Method") or ""
                ).upper()
                if requested_method and requested_method not in {
                    "GET",
                    "HEAD",
                    "PUT",
                    "POST",
                    "DELETE",
                }:
                    raise APIError(
                        405, "method_not_allowed", "Requested CORS method is not allowed"
                    )
                requested_headers = {
                    value.strip().lower()
                    for value in str(
                        handler.headers.get("Access-Control-Request-Headers") or ""
                    ).split(",")
                    if value.strip()
                }
                allowed_headers = {
                    "authorization",
                    "content-type",
                    "idempotency-key",
                    "if-match",
                    "range",
                    "x-filename",
                }
                if not requested_headers.issubset(allowed_headers):
                    raise APIError(
                        400, "headers_not_allowed", "Requested CORS headers are not allowed"
                    )
                handler.send_response(204)
                self._cors(handler)
                handler.send_header("Content-Length", "0")
                handler.end_headers()
                return True
            self._route(handler, path)
        except APIError as exc:
            self._error(handler, exc)
        except VersionConflictError as exc:
            self._error(handler, APIError(409, "version_conflict", str(exc)))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self._error(handler, APIError(500, "internal_error", "Local API request failed"))
        return True

    def _route(self, handler: Any, path: str) -> None:
        method = handler.command
        if path == "/v1/info" and method == "GET":
            self._json(
                handler,
                200,
                {
                    "protocol_versions": ["v1"],
                    "device_id": self.device_id,
                    "capabilities": [
                        "assets",
                        "documents",
                        "configs",
                        "provider-configs",
                        "runs",
                        "prompts",
                        "outputs",
                        "range-downloads",
                        "resumable-events",
                    ],
                },
            )
            return
        if path == "/v1/pairing/challenges" and method == "POST":
            self._create_challenge(handler)
            return
        if path == "/v1/pairing/sessions" and method == "POST":
            self._create_session(handler)
            return
        if path == "/v1/pairing/sessions/current" and method == "DELETE":
            self._revoke_session(handler)
            return
        if path == "/v1/assets":
            if method == "POST":
                self._upload_assets(handler)
            elif method == "GET":
                self._list_resources(handler, ASSET_KINDS)
            else:
                raise APIError(405, "method_not_allowed", "Method not allowed")
            return
        if path.startswith("/v1/assets/"):
            self._asset_route(handler, path.removeprefix("/v1/assets/"))
            return
        if path.startswith("/v1/documents") or path.startswith("/v1/configs"):
            self._document_route(handler, path)
            return
        if path == "/v1/provider-configs" or path.startswith("/v1/provider-configs/"):
            self._provider_config_route(handler, path)
            return
        if path == "/v1/runs" or path.startswith("/v1/runs/"):
            self._run_route(handler, path)
            return
        if path.startswith("/v1/prompts/"):
            self._prompt_route(handler, path)
            return
        if path.startswith("/v1/outputs/"):
            self._output_route(handler, path)
            return
        if path == "/v1/changes" and method == "GET":
            self._changes(handler)
            return
        if path == "/v1/events" and method == "GET":
            self._events(handler)
            return
        raise APIError(404, "not_found", "Endpoint not found")

    def _provider_config_route(self, handler: Any, path: str) -> None:
        from .structured_copy import LocalProviderStore

        session = self._session(
            handler, "manifest:read" if handler.command == "GET" else "documents:write"
        )
        store = LocalProviderStore(self.service.config.paths)
        suffix = path.removeprefix("/v1/provider-configs").strip("/")
        if not suffix:
            if handler.command != "GET":
                raise APIError(405, "method_not_allowed", "Method not allowed")
            self._json(handler, 200, {"items": store.list_metadata(session.owner_key)})
            return
        provider = str(suffix)
        if provider not in {"opencode", "google_gemini"}:
            raise APIError(404, "provider_not_found", "Provider is not supported")
        if handler.command == "GET":
            metadata = store.metadata(session.owner_key, provider)
            if metadata is None:
                raise APIError(404, "provider_not_found", "Provider config is not available")
            self._json(handler, 200, metadata)
            return
        if handler.command == "PUT":
            payload = self._json_body(handler, 16 * 1024)
            config = payload.get("config") if isinstance(payload.get("config"), dict) else payload
            self._json(handler, 200, store.set(session.owner_key, provider, config))
            return
        if handler.command == "DELETE":
            self._body(handler, 1024)
            store.delete(session.owner_key, provider)
            self._json(handler, 200, {"provider": provider, "status": "deleted"})
            return
        raise APIError(405, "method_not_allowed", "Method not allowed")

    def _create_challenge(self, handler: Any) -> None:
        self._body(handler, 1024)
        challenge_id = "pch_" + secrets.token_hex(16)
        challenge = secrets.token_urlsafe(32)
        expires_at = time.time() + self.service.config.challenge_ttl_seconds
        with self._lock:
            self._cleanup_auth()
            if len(self._challenges) >= 256:
                raise APIError(429, "pairing_busy", "Too many active pairing challenges")
            self._challenges[challenge_id] = PairingChallenge(
                digest=self._digest(challenge), expires_at=expires_at
            )
        self._json(
            handler,
            201,
            {
                "challenge_id": challenge_id,
                "challenge": challenge,
                "device_id": self.device_id,
                "expires_at": expires_at,
            },
        )

    def _create_session(self, handler: Any) -> None:
        payload = self._json_body(handler, 4096)
        challenge_id = str(payload.get("challenge_id") or "")
        challenge = str(payload.get("challenge") or "")
        with self._lock:
            self._cleanup_auth()
            item = self._challenges.get(challenge_id)
            if (
                item is None
                or not item.approved
                or item.consumed
                or item.expires_at <= time.time()
                or not hmac.compare_digest(item.digest, self._digest(challenge))
                or not item.owner_key
                or not item.agent_id
                or item.device_id != self.device_id
            ):
                raise APIError(
                    401, "pairing_not_approved", "Pairing challenge is invalid or not approved"
                )
            if len(self._sessions) >= 256:
                raise APIError(429, "pairing_busy", "Too many active local sessions")
            item.consumed = True
            token = secrets.token_urlsafe(32)
            expires_at = time.time() + self.service.config.session_ttl_seconds
            self._sessions[self._digest(token)] = LocalSession(
                owner_key=item.owner_key,
                scopes=item.scopes,
                expires_at=expires_at,
                agent_id=item.agent_id,
                device_id=item.device_id,
            )
        self._json(
            handler,
            201,
            {
                "access_token": token,
                "token_type": "Bearer",
                "expires_at": expires_at,
                "scopes": sorted(item.scopes),
                "device_id": self.device_id,
                "agent_id": item.agent_id,
            },
        )

    def _revoke_session(self, handler: Any) -> None:
        self._session(handler)
        token = str(handler.headers.get("Authorization"))[7:].strip()
        with self._lock:
            self._sessions.pop(self._digest(token), None)
        handler.send_response(204)
        self._cors(handler)
        handler.send_header("Content-Length", "0")
        handler.end_headers()

    def _operation_id(self, handler: Any, payload: dict[str, Any] | None = None) -> str:
        value = str(handler.headers.get("Idempotency-Key") or "")
        if not value and payload:
            value = str(payload.get("operation_id") or "")
        if not value or len(value) > 200:
            raise APIError(400, "operation_id_required", "A bounded operation ID is required")
        return value

    @staticmethod
    def _safe_logical_key(value: str) -> str:
        decoded = urllib.parse.unquote(value)
        if (
            not decoded
            or decoded in {".", ".."}
            or "/" in decoded
            or "\\" in decoded
            or "\x00" in decoded
            or len(decoded) > 200
        ):
            raise APIError(400, "invalid_key", "Resource key is invalid")
        return decoded

    def _stream_upload(
        self, source: BinaryIO, length: int, filename: str
    ) -> tuple[Path, str, int, str]:
        if Path(filename).name != filename or filename in {".", ".."}:
            raise APIError(400, "invalid_filename", "Upload filename is invalid")
        extension = Path(filename).suffix.lower()
        signature = IMAGE_SIGNATURES.get(extension)
        if signature is None:
            raise APIError(415, "unsupported_media_type", "Image extension is not allowed")
        if length > self.service.config.max_upload_bytes:
            raise APIError(413, "file_too_large", "Uploaded file exceeds the per-file limit")
        descriptor, name = tempfile.mkstemp(
            prefix=".upload-", suffix=".tmp", dir=self.service.config.paths.staging
        )
        path = Path(name)
        digest = hashlib.sha256()
        size = 0
        prefix = b""
        try:
            with os.fdopen(descriptor, "wb") as destination:
                remaining = length
                while remaining:
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise APIError(400, "incomplete_upload", "Upload body is incomplete")
                    remaining -= len(chunk)
                    size += len(chunk)
                    if size > self.service.config.max_upload_bytes:
                        raise APIError(
                            413, "file_too_large", "Uploaded file exceeds the per-file limit"
                        )
                    if len(prefix) < 32:
                        prefix += chunk[: 32 - len(prefix)]
                    digest.update(chunk)
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            if not signature[1](prefix):
                raise APIError(
                    415, "media_type_mismatch", "File extension does not match its content"
                )
            return path, digest.hexdigest(), size, signature[0]
        except Exception:
            path.unlink(missing_ok=True)
            raise

    def _upload_assets(self, handler: Any) -> None:
        session = self._session(handler, "assets:write")
        try:
            request_length = int(handler.headers.get("Content-Length") or "0")
        except ValueError as exc:
            raise APIError(400, "invalid_content_length", "Invalid Content-Length") from exc
        if request_length <= 0 or request_length > self.service.config.max_request_bytes:
            raise APIError(413, "request_too_large", "Upload request exceeds the aggregate limit")
        query = urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)
        kind = str((query.get("kind") or ["product_image"])[0])
        if kind not in ASSET_KINDS:
            raise APIError(400, "invalid_asset_kind", "Asset kind is invalid")
        content_type = str(handler.headers.get("Content-Type") or "")
        staged: list[tuple[Path, str, int, str, str]] = []
        if content_type.startswith("multipart/form-data"):
            environment = {
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
                "CONTENT_LENGTH": str(request_length),
            }
            form = cgi.FieldStorage(
                fp=handler.rfile,
                headers=handler.headers,
                environ=environment,
                keep_blank_values=True,
            )
            fields = form.list or []
            files = [field for field in fields if field.filename]
            if not files:
                raise APIError(400, "file_required", "At least one file is required")
            total = 0
            for field in files:
                field.file.seek(0, os.SEEK_END)
                length = field.file.tell()
                field.file.seek(0)
                total += length
                if total > self.service.config.max_request_bytes:
                    raise APIError(413, "request_too_large", "Aggregate upload limit exceeded")
                staged.append(
                    (*self._stream_upload(field.file, length, Path(field.filename).name), field.filename)
                )
        else:
            filename = str(handler.headers.get("X-Filename") or "")
            staged.append(
                (*self._stream_upload(handler.rfile, request_length, filename), filename)
            )
        operation_id = self._operation_id(handler)
        created: list[dict[str, Any]] = []
        try:
            for index, (path, digest, size, media_type, filename) in enumerate(staged):
                item_operation = operation_id if len(staged) == 1 else f"{operation_id}:{index}"
                version = self.state.put_resource(
                    source=path,
                    owner_key=session.owner_key,
                    kind=kind,
                    logical_key=f"{kind}/{item_operation}",
                    operation_id=item_operation,
                    metadata={"filename": Path(filename).name},
                    media_type=media_type,
                )
                record = self._resource_record(
                    session.owner_key,
                    resource_id=version.resource_id,
                    version=version.version,
                )
                if record is None:
                    raise APIError(500, "commit_failed", "Committed asset could not be read")
                safe = self._metadata(record)
                created.append(
                    {
                        "resource_id": version.resource_id,
                        "version": version.version,
                        "kind": kind,
                        "filename": safe.get("filename", Path(filename).name),
                        "bytes": safe["bytes"],
                        "sha256": safe["sha256"],
                        "media_type": safe["media_type"],
                        "status": "available",
                    }
                )
        finally:
            for item in staged:
                item[0].unlink(missing_ok=True)
        self._json(
            handler,
            201,
            created[0] if len(created) == 1 else {"items": created},
            headers={"ETag": f'"{created[0]["version"]}"'} if len(created) == 1 else None,
        )

    def _resource_record(
        self,
        owner_key: str,
        *,
        resource_id: str | None = None,
        kind: str | None = None,
        logical_key: str | None = None,
        version: int | None = None,
    ) -> dict[str, Any] | None:
        with self.state._connect() as conn:
            conditions = ["r.owner_key = ?", "r.deleted_at IS NULL"]
            values: list[Any] = [owner_key]
            if resource_id is not None:
                conditions.append("r.resource_id = ?")
                values.append(resource_id)
            if kind is not None:
                conditions.append("r.kind = ?")
                values.append(kind)
            if logical_key is not None:
                conditions.append("r.logical_key = ?")
                values.append(logical_key)
            selected_version = "r.current_version" if version is None else "?"
            parameters = ([version] if version is not None else []) + values
            row = conn.execute(
                f"""
                SELECT r.resource_id, r.kind, r.logical_key, r.current_version, r.status,
                       r.created_at, r.updated_at, rv.version, rv.object_sha256,
                       rv.content_hash, rv.metadata_json, o.relative_path, o.bytes, o.media_type
                FROM resources r
                JOIN resource_versions rv
                  ON rv.resource_id = r.resource_id AND rv.version = {selected_version}
                JOIN objects o ON o.sha256 = rv.object_sha256
                WHERE {' AND '.join(conditions)}
                """,
                parameters,
            ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _metadata(record: dict[str, Any]) -> dict[str, Any]:
        metadata = json.loads(record.get("metadata_json") or "{}")
        result = {
            "resource_id": record["resource_id"],
            "kind": record["kind"],
            "logical_key": record["logical_key"],
            "version": int(record["version"]),
            "bytes": int(record["bytes"]),
            "sha256": record["object_sha256"],
            "media_type": record["media_type"],
            "status": record["status"],
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
        }
        if isinstance(metadata.get("filename"), str):
            result["filename"] = Path(metadata["filename"]).name
        for key in ("run_id", "prompt_id", "item_id", "aspect_ratio", "format", "persona", "language"):
            if key in metadata and isinstance(metadata[key], (str, int, float, bool)):
                result[key] = metadata[key]
        return result

    def _list_resources(self, handler: Any, kinds: frozenset[str]) -> None:
        session = self._session(handler, "manifest:read")
        placeholders = ",".join("?" for _ in kinds)
        with self.state._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT resource_id FROM resources
                WHERE owner_key = ? AND kind IN ({placeholders}) AND deleted_at IS NULL
                ORDER BY updated_at DESC
                """,
                [session.owner_key, *sorted(kinds)],
            ).fetchall()
        items = [
            self._metadata(record)
            for row in rows
            if (
                record := self._resource_record(
                    session.owner_key, resource_id=str(row["resource_id"])
                )
            )
        ]
        self._json(handler, 200, {"items": items})

    def _asset_route(self, handler: Any, suffix: str) -> None:
        resource_id, separator, tail = suffix.partition("/")
        resource_id = self._safe_logical_key(resource_id)
        if not separator and handler.command == "DELETE":
            session = self._session(handler, "delete")
            self._delete_resource(
                session.owner_key, resource_id, self._operation_id(handler)
            )
            self._json(handler, 200, {"resource_id": resource_id, "status": "deleted"})
            return
        session = self._session(
            handler,
            "content:read" if separator and tail == "content" else "manifest:read",
        )
        record = self._resource_record(session.owner_key, resource_id=resource_id)
        if record is None or record["kind"] not in ASSET_KINDS:
            raise APIError(404, "asset_not_found", "Asset not found")
        if separator and tail == "content" and handler.command in {"GET", "HEAD"}:
            self._send_file(handler, record)
            return
        if not separator and handler.command == "GET":
            self._json(
                handler,
                200,
                self._metadata(record),
                headers={"ETag": f'"{record["version"]}"'},
            )
            return
        raise APIError(405, "method_not_allowed", "Method not allowed")

    def _send_file(
        self,
        handler: Any,
        record: dict[str, Any],
        *,
        attachment_name: str | None = None,
    ) -> None:
        path = (self.service.config.paths.root / record["relative_path"]).resolve()
        try:
            path.relative_to(self.service.config.paths.root.resolve())
        except ValueError as exc:
            raise APIError(404, "content_not_found", "Content not found") from exc
        if not path.is_file():
            raise APIError(404, "content_not_found", "Content not found")
        size = path.stat().st_size
        start, end, status = 0, max(0, size - 1), 200
        range_header = str(handler.headers.get("Range") or "")
        if range_header:
            if not range_header.startswith("bytes=") or "," in range_header:
                raise APIError(416, "invalid_range", "Only one byte range is supported")
            raw_start, _, raw_end = range_header[6:].partition("-")
            try:
                if raw_start:
                    start = int(raw_start)
                    end = int(raw_end) if raw_end else size - 1
                else:
                    suffix = int(raw_end)
                    start = max(0, size - suffix)
                    end = size - 1
            except ValueError as exc:
                raise APIError(416, "invalid_range", "Byte range is invalid") from exc
            if start < 0 or end < start or start >= size:
                raise APIError(416, "invalid_range", "Byte range is outside content")
            end = min(end, size - 1)
            status = 206
        length = max(0, end - start + 1)
        handler.send_response(status)
        handler.send_header("Content-Type", record["media_type"])
        handler.send_header("Content-Length", str(length))
        handler.send_header("Accept-Ranges", "bytes")
        handler.send_header("ETag", f'"{record["object_sha256"]}"')
        if status == 206:
            handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if attachment_name:
            handler.send_header(
                "Content-Disposition", f'attachment; filename="{Path(attachment_name).name}"'
            )
        self._cors(handler)
        handler.end_headers()
        if handler.command == "HEAD":
            return
        with path.open("rb") as source:
            source.seek(start)
            remaining = length
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                handler.wfile.write(chunk)
                remaining -= len(chunk)

    def _delete_resource(self, owner_key: str, resource_id: str, operation_id: str) -> None:
        with self.state._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = self.state._operation_result(conn, owner_key, operation_id)
            if prior is not None:
                conn.commit()
                return
            row = conn.execute(
                "SELECT current_version FROM resources WHERE resource_id = ? AND owner_key = ?",
                (resource_id, owner_key),
            ).fetchone()
            if row is None:
                raise APIError(404, "resource_not_found", "Resource not found")
            now = time.time()
            conn.execute(
                "UPDATE resources SET status = 'deleted', deleted_at = ?, updated_at = ? "
                "WHERE resource_id = ?",
                (now, now, resource_id),
            )
            self.state._record_change(
                conn,
                owner_key=owner_key,
                resource_type="resource",
                resource_id=resource_id,
                version=int(row["current_version"]),
                operation="deleted",
            )
            self.state._save_operation(
                conn,
                owner_key,
                operation_id,
                "delete_resource",
                {"resource_id": resource_id, "status": "deleted"},
            )
            conn.commit()

    def _document_route(self, handler: Any, path: str) -> None:
        collection = "documents" if path.startswith("/v1/documents") else "configs"
        kind = "product_document" if collection == "documents" else "config_file"
        suffix = path.removeprefix(f"/v1/{collection}").lstrip("/")
        if not suffix:
            if handler.command != "GET":
                raise APIError(405, "method_not_allowed", "Method not allowed")
            self._list_resources(handler, frozenset({kind}))
            return
        logical_key, separator, tail = suffix.partition("/")
        logical_key = self._safe_logical_key(logical_key)
        if separator and tail == "versions" and handler.command == "GET":
            session = self._session(handler, "manifest:read")
            record = self._resource_record(
                session.owner_key, kind=kind, logical_key=logical_key
            )
            if record is None:
                raise APIError(404, "resource_not_found", "Resource not found")
            versions = self.state.resource_versions(record["resource_id"])
            self._json(
                handler,
                200,
                {
                    "items": [
                        {
                            "resource_id": item["resource_id"],
                            "version": int(item["version"]),
                            "sha256": item["object_sha256"],
                            "bytes": int(item["bytes"]),
                            "media_type": item["media_type"],
                            "created_at": item["created_at"],
                        }
                        for item in versions
                    ]
                },
            )
            return
        if separator:
            raise APIError(404, "not_found", "Endpoint not found")
        if handler.command == "GET":
            session = self._session(handler, "content:read")
            record = self._resource_record(
                session.owner_key, kind=kind, logical_key=logical_key
            )
            if record is None:
                raise APIError(404, "resource_not_found", "Resource not found")
            self._send_file(handler, record)
            return
        if handler.command == "PUT":
            session = self._session(handler, "documents:write")
            payload = self._json_body(handler, self.service.config.max_upload_bytes)
            content = payload.get("content")
            if not isinstance(content, str):
                raise APIError(400, "content_required", "String content is required")
            expected = self._expected_version(handler, payload)
            existing = self._resource_record(
                session.owner_key, kind=kind, logical_key=logical_key
            )
            path = self.service.config.paths.staging / f".text-{uuid.uuid4().hex}.tmp"
            path.write_text(content, encoding="utf-8")
            try:
                version = self.state.put_resource(
                    source=path,
                    owner_key=session.owner_key,
                    kind=kind,
                    logical_key=logical_key,
                    resource_id=existing["resource_id"] if existing else None,
                    expected_version=expected,
                    operation_id=self._operation_id(handler, payload),
                    media_type="text/plain; charset=utf-8",
                )
            finally:
                path.unlink(missing_ok=True)
            run_id_value = payload.get("run_id")
            role_value = payload.get("role")
            if isinstance(run_id_value, str) and isinstance(role_value, str):
                run_id = self._safe_logical_key(run_id_value)
                if self._run(session.owner_key, run_id) is None:
                    raise APIError(404, "run_not_found", "Run not found")
                if role_value not in {
                    "product_document",
                    "structured_settings",
                    "backgrounds",
                    "source_config",
                }:
                    raise APIError(400, "invalid_role", "Run resource role is invalid")
                with self.state._connect() as conn:
                    current = conn.execute(
                        """
                        SELECT entry_id FROM run_entries
                        WHERE run_id = ? AND role = ?
                        ORDER BY position LIMIT 1
                        """,
                        (run_id, role_value),
                    ).fetchone()
                    if current is not None:
                        conn.execute(
                            """
                            UPDATE run_entries SET resource_id = ?, resource_version = ?
                            WHERE run_id = ? AND entry_id = ?
                            """,
                            (version.resource_id, version.version, run_id, current["entry_id"]),
                        )
                    else:
                        position = int(
                            conn.execute(
                                "SELECT COALESCE(MAX(position), 0) + 1 FROM run_entries WHERE run_id = ?",
                                (run_id,),
                            ).fetchone()[0]
                        )
                if current is None:
                    self.state.add_run_entry(
                        run_id=run_id,
                        entry_id="ent_" + uuid.uuid4().hex,
                        resource_id=version.resource_id,
                        resource_version=version.version,
                        role=role_value,
                        position=position,
                        operation_id=self._operation_id(handler, payload) + ":entry",
                    )
            result = self._resource_record(session.owner_key, resource_id=version.resource_id)
            self._json(
                handler,
                201 if version.version == 1 else 200,
                self._metadata(result or {}),
                headers={"ETag": f'"{version.version}"'},
            )
            return
        raise APIError(405, "method_not_allowed", "Method not allowed")

    @staticmethod
    def _expected_version(handler: Any, payload: dict[str, Any]) -> int | None:
        value: Any = payload.get("expected_version")
        if value is None and handler.headers.get("If-Match"):
            value = str(handler.headers["If-Match"]).strip().strip('"')
        if value is None:
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise APIError(400, "invalid_version", "Expected version must be an integer") from exc
        if parsed < 0:
            raise APIError(400, "invalid_version", "Expected version cannot be negative")
        return parsed

    def _run(self, owner_key: str, run_id: str) -> dict[str, Any] | None:
        with self.state._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE run_id = ? AND owner_key = ?",
                (run_id, owner_key),
            ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _safe_run(run: dict[str, Any]) -> dict[str, Any]:
        return {
            key: run[key]
            for key in (
                "run_id",
                "device_id",
                "workspace_id",
                "run_number",
                "display_batch",
                "flow_type",
                "status",
                "manifest_resource_id",
                "manifest_version",
                "created_at",
                "updated_at",
            )
        }

    def _run_route(self, handler: Any, path: str) -> None:
        if path == "/v1/runs":
            if handler.command == "POST":
                session = self._session(handler, "runs:execute")
                payload = self._json_body(handler)
                run_id = self._safe_logical_key(str(payload.get("run_id") or ""))
                flow_type = str(payload.get("flow_type") or "")
                if flow_type not in {"structured", "reference"}:
                    raise APIError(400, "invalid_flow_type", "Run flow type is invalid")
                result = self.state.create_run(
                    run_id=run_id,
                    owner_key=session.owner_key,
                    device_id=self.device_id,
                    workspace_id=self._safe_logical_key(
                        str(payload.get("workspace_id") or "")
                    ),
                    run_number=int(payload.get("run_number") or 0),
                    flow_type=flow_type,
                    operation_id=self._operation_id(handler, payload),
                )
                run = self._run(session.owner_key, result["run_id"])
                self._json(handler, 201, self._safe_run(run or {}))
                return
            if handler.command == "GET":
                session = self._session(handler, "manifest:read")
                with self.state._connect() as conn:
                    rows = conn.execute(
                        "SELECT * FROM runs WHERE owner_key = ? ORDER BY run_number DESC",
                        (session.owner_key,),
                    ).fetchall()
                self._json(
                    handler, 200, {"items": [self._safe_run(dict(row)) for row in rows]}
                )
                return
            raise APIError(405, "method_not_allowed", "Method not allowed")
        suffix = path.removeprefix("/v1/runs/")
        run_id, separator, action = suffix.partition("/")
        run_id = self._safe_logical_key(run_id)
        session = self._session(
            handler,
            "runs:execute"
            if handler.command == "POST"
            else ("delete" if handler.command == "DELETE" else "manifest:read"),
        )
        run = self._run(session.owner_key, run_id)
        if run is None:
            raise APIError(404, "run_not_found", "Run not found")
        if not separator and handler.command == "GET":
            self._json(handler, 200, self._safe_run(run))
            return
        if not separator and handler.command == "DELETE":
            receipt = self.state.delete_run(
                run_id,
                operation_id=self._operation_id(handler),
                purge_resources=True,
            )
            self._json(handler, 200, receipt)
            return
        if action == "manifest" and handler.command == "GET":
            manifest = self.state.run_manifest(run_id) or {}
            manifest.pop("owner_key", None)
            for entry in manifest.get("entries", []):
                entry.pop("metadata_json", None)
            self._json(handler, 200, manifest)
            return
        if action == "prompts" and handler.command == "GET":
            self._run_prompts(handler, session.owner_key, run_id)
            return
        if action in {"execute", "generations"} and handler.command == "POST":
            payload = self._json_body(handler)
            command = (
                str(payload.get("command") or "")
                if action == "execute"
                else "generate_outputs"
            )
            if not command or len(command) > 100:
                raise APIError(400, "invalid_command", "A bounded command is required")
            operation_id = self._operation_id(handler, payload)
            bounded = {
                key: payload[key]
                for key in (
                    "engine",
                    "mode",
                    "count",
                    "manifest_version",
                    "config_version_id",
                )
                if isinstance(payload.get(key), (str, int, float, bool))
            }
            command_id, status, _ = self._queue_command(
                session.owner_key,
                operation_id,
                "cmd_",
                {"run_id": run_id, "command": command, "parameters": bounded},
            )
            self._json(
                handler,
                202,
                {"command_id": command_id, "run_id": run_id, "status": status},
            )
            return
        if action == "outputs" and handler.command == "GET":
            self._list_outputs(handler, session.owner_key, run_id)
            return
        if action == "prompt-imports" and handler.command == "POST":
            self._prompt_import(handler, session.owner_key, run_id)
            return
        if action == "prompt-export" and handler.command == "GET":
            self._prompt_export(handler, session.owner_key, run_id)
            return
        if action == "download" and handler.command == "GET":
            self._run_download(handler, session.owner_key, run_id)
            return
        raise APIError(404, "not_found", "Endpoint not found")

    def _prompt_route(self, handler: Any, path: str) -> None:
        suffix = path.removeprefix("/v1/prompts/")
        prompt_id, separator, action = suffix.partition("/")
        prompt_id = self._safe_logical_key(prompt_id)
        if separator and action == "content" and handler.command == "GET":
            session = self._session(handler, "content:read")
            record = self._resource_record(
                session.owner_key, kind="prompt", logical_key=prompt_id
            )
            if record is None:
                raise APIError(404, "prompt_not_found", "Prompt not found")
            self._send_file(handler, record)
            return
        if not separator and handler.command == "PUT":
            session = self._session(handler, "prompts:write")
            payload = self._json_body(handler, self.service.config.max_upload_bytes)
            run_id = self._safe_logical_key(str(payload.get("run_id") or ""))
            if self._run(session.owner_key, run_id) is None:
                raise APIError(404, "run_not_found", "Run not found")
            content = payload.get("content")
            if not isinstance(content, str):
                raise APIError(400, "content_required", "Prompt content is required")
            existing = self._resource_record(
                session.owner_key, kind="prompt", logical_key=prompt_id
            )
            path_tmp = self.service.config.paths.staging / f".prompt-{uuid.uuid4().hex}.tmp"
            path_tmp.write_text(content, encoding="utf-8")
            try:
                version = self.state.put_resource(
                    source=path_tmp,
                    owner_key=session.owner_key,
                    kind="prompt",
                    logical_key=prompt_id,
                    resource_id=existing["resource_id"] if existing else None,
                    expected_version=self._expected_version(handler, payload),
                    operation_id=self._operation_id(handler, payload),
                    metadata={
                        "run_id": run_id,
                        **{
                            key: payload[key]
                            for key in ("format", "persona", "language")
                            if isinstance(payload.get(key), str)
                        },
                    },
                    media_type="text/plain; charset=utf-8",
                )
            finally:
                path_tmp.unlink(missing_ok=True)
            if existing is None:
                with self.state._connect() as conn:
                    position = int(
                        conn.execute(
                            "SELECT COALESCE(MAX(position), 0) + 1 FROM run_entries WHERE run_id = ?",
                            (run_id,),
                        ).fetchone()[0]
                    )
                self.state.add_run_entry(
                    run_id=run_id,
                    entry_id="ent_" + uuid.uuid4().hex,
                    resource_id=version.resource_id,
                    resource_version=version.version,
                    role="prompt",
                    prompt_id=prompt_id,
                    position=position,
                    operation_id=self._operation_id(handler, payload) + ":entry",
                )
            else:
                with self.state._connect() as conn:
                    conn.execute(
                        """
                        UPDATE run_entries
                        SET resource_version = ?
                        WHERE run_id = ? AND prompt_id = ? AND resource_id = ?
                        """,
                        (version.version, run_id, prompt_id, version.resource_id),
                    )
            self._json(
                handler,
                201 if version.version == 1 else 200,
                {
                    "prompt_id": prompt_id,
                    "resource_id": version.resource_id,
                    "version": version.version,
                    "sha256": version.object_sha256,
                },
                headers={"ETag": f'"{version.version}"'},
            )
            return
        raise APIError(404, "not_found", "Endpoint not found")

    def _run_prompts(self, handler: Any, owner_key: str, run_id: str) -> None:
        with self.state._connect() as conn:
            rows = conn.execute(
                """
                SELECT re.prompt_id, re.resource_id, re.resource_version
                FROM run_entries re JOIN resources r ON r.resource_id = re.resource_id
                WHERE re.run_id = ? AND r.owner_key = ? AND r.kind = 'prompt'
                ORDER BY re.position
                """,
                (run_id, owner_key),
            ).fetchall()
        self._json(
            handler,
            200,
            {
                "items": [
                    {
                        "prompt_id": row["prompt_id"],
                        "resource_id": row["resource_id"],
                        "resource_version": int(row["resource_version"]),
                        "status": "ready",
                    }
                    for row in rows
                ]
            },
        )

    def _prompt_import(self, handler: Any, owner_key: str, run_id: str) -> None:
        self._session(handler, "prompts:write")
        payload = self._json_body(handler, self.service.config.max_request_bytes)
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            raise APIError(400, "items_required", "Prompt import items are required")
        imported = []
        base_operation = self._operation_id(handler, payload)
        for index, item in enumerate(items):
            if not isinstance(item, dict) or not isinstance(item.get("content"), str):
                raise APIError(400, "invalid_prompt", "Every prompt requires string content")
            prompt_id = self._safe_logical_key(
                str(item.get("prompt_id") or f"prm_{uuid.uuid4().hex}")
            )
            temporary = self.service.config.paths.staging / f".import-{uuid.uuid4().hex}.tmp"
            temporary.write_text(item["content"], encoding="utf-8")
            try:
                version = self.state.put_resource(
                    source=temporary,
                    owner_key=owner_key,
                    kind="prompt",
                    logical_key=prompt_id,
                    operation_id=f"{base_operation}:{index}",
                    metadata={"run_id": run_id},
                    media_type="text/plain; charset=utf-8",
                )
            finally:
                temporary.unlink(missing_ok=True)
            with self.state._connect() as conn:
                position = int(
                    conn.execute(
                        "SELECT COALESCE(MAX(position), 0) + 1 FROM run_entries WHERE run_id = ?",
                        (run_id,),
                    ).fetchone()[0]
                )
            self.state.add_run_entry(
                run_id=run_id,
                entry_id="ent_" + uuid.uuid4().hex,
                resource_id=version.resource_id,
                resource_version=version.version,
                role="prompt",
                prompt_id=prompt_id,
                position=position,
                operation_id=f"{base_operation}:{index}:entry",
            )
            imported.append({"prompt_id": prompt_id, "version": version.version})
        self._json(handler, 201, {"items": imported})

    def _prompt_export(self, handler: Any, owner_key: str, run_id: str) -> None:
        self._session(handler, "content:read")
        temporary = self._build_run_zip(owner_key, run_id, prompts_only=True)
        try:
            record = {
                "relative_path": temporary.relative_to(
                    self.service.config.paths.root
                ).as_posix(),
                "media_type": "application/zip",
                "object_sha256": self._hash_file(temporary),
            }
            self._send_file(handler, record, attachment_name=f"{run_id}-prompts.zip")
        finally:
            temporary.unlink(missing_ok=True)

    def _output_route(self, handler: Any, path: str) -> None:
        suffix = path.removeprefix("/v1/outputs/")
        output_id, separator, action = suffix.partition("/")
        output_id = self._safe_logical_key(output_id)
        if handler.command == "DELETE":
            required_scope = "delete"
        elif handler.command == "POST":
            required_scope = (
                "revisions:write" if action == "revisions" else "outputs:write"
            )
        elif action == "content":
            required_scope = "content:read"
        else:
            required_scope = "manifest:read"
        session = self._session(handler, required_scope)
        output = self._output(session.owner_key, output_id)
        if output is None:
            raise APIError(404, "output_not_found", "Output not found")
        if not separator and handler.command == "GET":
            self._json(handler, 200, self._safe_output(output))
            return
        if not separator and handler.command == "DELETE":
            self._set_output_status(
                session.owner_key,
                output_id,
                "deleted",
                self._operation_id(handler),
            )
            self._json(handler, 200, {"output_id": output_id, "status": "deleted"})
            return
        if action == "content" and handler.command in {"GET", "HEAD"}:
            record = self._output_resource(session.owner_key, output_id)
            if record is None:
                raise APIError(404, "content_not_found", "Output content not found")
            self._send_file(handler, record)
            return
        if action == "versions" and handler.command == "GET":
            self._json(
                handler,
                200,
                {
                    "items": [
                        {
                            key: row[key]
                            for key in (
                                "output_id",
                                "version",
                                "resource_id",
                                "resource_version",
                                "source_output_version",
                                "revision_id",
                                "created_at",
                            )
                        }
                        for row in self.state.output_versions(output_id)
                    ]
                },
            )
            return
        if action in {"archive", "restore"} and handler.command == "POST":
            payload = self._json_body(handler)
            status = "archived" if action == "archive" else "available"
            self._set_output_status(
                session.owner_key,
                output_id,
                status,
                self._operation_id(handler, payload),
            )
            self._json(handler, 200, {"output_id": output_id, "status": status})
            return
        if action in {"replacements", "revisions"} and handler.command == "POST":
            payload = self._json_body(handler)
            operation_id = self._operation_id(handler, payload)
            parameters: dict[str, Any] = {
                "output_id": output_id,
                "command": action[:-1],
                "source_output_version": int(output["current_version"]),
            }
            if action == "revisions":
                comment = payload.get("comment")
                if not isinstance(comment, str) or not comment.strip():
                    raise APIError(400, "comment_required", "Revision comment is required")
                if len(comment.encode("utf-8")) > 64 * 1024:
                    raise APIError(413, "comment_too_large", "Revision comment is too large")
                temporary = (
                    self.service.config.paths.staging / f".revision-{uuid.uuid4().hex}.tmp"
                )
                temporary.write_text(comment.strip(), encoding="utf-8")
                try:
                    prompt = self.state.put_resource(
                        source=temporary,
                        owner_key=session.owner_key,
                        kind="revision_prompt",
                        logical_key=f"{output_id}/{operation_id}",
                        operation_id=operation_id + ":prompt",
                        metadata={"output_id": output_id},
                        media_type="text/plain; charset=utf-8",
                    )
                finally:
                    temporary.unlink(missing_ok=True)
                parameters.update(
                    {
                        "prompt_resource_id": prompt.resource_id,
                        "prompt_resource_version": prompt.version,
                    }
                )
            elif payload.get("resource_id") is not None:
                replacement = self._resource_record(
                    session.owner_key,
                    resource_id=str(payload.get("resource_id")),
                    version=int(payload.get("resource_version") or 1),
                )
                if replacement is None or replacement["kind"] != "output_image":
                    raise APIError(
                        404, "replacement_not_found", "Replacement resource not found"
                    )
                parameters.update(
                    {
                        "resource_id": replacement["resource_id"],
                        "resource_version": int(replacement["version"]),
                    }
                )
            command_id, status, stored = self._queue_command(
                session.owner_key,
                operation_id,
                "rep_" if action == "replacements" else "rev_",
                parameters,
            )
            self._json(
                handler,
                202,
                {
                    "command_id": command_id,
                    "output_id": output_id,
                    "source_output_version": int(stored["source_output_version"]),
                    "status": status,
                },
            )
            return
        if action.startswith("versions/") and action.endswith("/activate") and handler.command == "POST":
            raw_version = action.removeprefix("versions/").removesuffix("/activate")
            payload = self._json_body(handler)
            self._activate_output(
                session.owner_key,
                output_id,
                int(raw_version),
                self._operation_id(handler, payload),
            )
            self._json(
                handler,
                200,
                {"output_id": output_id, "version": int(raw_version), "status": "available"},
            )
            return
        raise APIError(404, "not_found", "Endpoint not found")

    def _output(self, owner_key: str, output_id: str) -> dict[str, Any] | None:
        with self.state._connect() as conn:
            row = conn.execute(
                """
                SELECT o.* FROM outputs o JOIN runs r ON r.run_id = o.run_id
                WHERE o.output_id = ? AND r.owner_key = ?
                """,
                (output_id, owner_key),
            ).fetchone()
        return dict(row) if row else None

    def _queue_command(
        self,
        owner_key: str,
        operation_id: str,
        prefix: str,
        payload: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any]]:
        command_id = prefix + hashlib.sha256(
            f"{owner_key}\0{operation_id}".encode()
        ).hexdigest()[:24]
        with self.state._connect() as conn:
            row = conn.execute(
                "SELECT status, payload_json FROM jobs WHERE job_id = ? AND owner_key = ?",
                (command_id, owner_key),
            ).fetchone()
        if row is not None:
            return command_id, str(row["status"]), json.loads(row["payload_json"])
        self.state.record_job(command_id, owner_key, "queued", payload)
        return command_id, "queued", payload

    @staticmethod
    def _safe_output(row: dict[str, Any]) -> dict[str, Any]:
        return {
            key: row[key]
            for key in (
                "output_id",
                "run_id",
                "prompt_id",
                "item_id",
                "aspect_ratio",
                "current_version",
                "status",
                "created_at",
                "updated_at",
            )
        }

    def _list_outputs(self, handler: Any, owner_key: str, run_id: str) -> None:
        with self.state._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM outputs WHERE run_id = ? ORDER BY created_at",
                (run_id,),
            ).fetchall()
        self._json(
            handler, 200, {"items": [self._safe_output(dict(row)) for row in rows]}
        )

    def _output_resource(
        self, owner_key: str, output_id: str, version: int | None = None
    ) -> dict[str, Any] | None:
        output = self._output(owner_key, output_id)
        if output is None:
            return None
        selected = int(version or output["current_version"])
        with self.state._connect() as conn:
            row = conn.execute(
                "SELECT resource_id, resource_version FROM output_versions "
                "WHERE output_id = ? AND version = ?",
                (output_id, selected),
            ).fetchone()
        return (
            self._resource_record(
                owner_key,
                resource_id=str(row["resource_id"]),
                version=int(row["resource_version"]),
            )
            if row
            else None
        )

    def _set_output_status(
        self, owner_key: str, output_id: str, status: str, operation_id: str
    ) -> None:
        with self.state._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = self.state._operation_result(conn, owner_key, operation_id)
            if prior is not None:
                conn.commit()
                return
            cursor = conn.execute(
                """
                UPDATE outputs SET status = ?, updated_at = ?
                WHERE output_id = ? AND EXISTS (
                    SELECT 1 FROM runs WHERE runs.run_id = outputs.run_id AND owner_key = ?
                )
                """,
                (status, time.time(), output_id, owner_key),
            )
            if not cursor.rowcount:
                raise APIError(404, "output_not_found", "Output not found")
            self.state._record_change(
                conn,
                owner_key=owner_key,
                resource_type="output",
                resource_id=output_id,
                version=None,
                operation=status,
            )
            self.state._save_operation(
                conn,
                owner_key,
                operation_id,
                "output_status",
                {"output_id": output_id, "status": status},
            )
            conn.commit()

    def _activate_output(
        self, owner_key: str, output_id: str, version: int, operation_id: str
    ) -> None:
        with self.state._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = self.state._operation_result(conn, owner_key, operation_id)
            if prior is not None:
                conn.commit()
                return
            output = conn.execute(
                """
                SELECT o.output_id FROM outputs o JOIN runs r ON r.run_id = o.run_id
                WHERE o.output_id = ? AND r.owner_key = ?
                """,
                (output_id, owner_key),
            ).fetchone()
            target = conn.execute(
                "SELECT 1 FROM output_versions WHERE output_id = ? AND version = ?",
                (output_id, version),
            ).fetchone()
            if output is None or target is None:
                raise APIError(404, "output_version_not_found", "Output version not found")
            conn.execute(
                "UPDATE outputs SET current_version = ?, status = 'available', updated_at = ? "
                "WHERE output_id = ?",
                (version, time.time(), output_id),
            )
            self.state._record_change(
                conn,
                owner_key=owner_key,
                resource_type="output",
                resource_id=output_id,
                version=version,
                operation="activated",
            )
            self.state._save_operation(
                conn,
                owner_key,
                operation_id,
                "activate_output",
                {"output_id": output_id, "version": version},
            )
            conn.commit()

    def _build_run_zip(
        self, owner_key: str, run_id: str, *, prompts_only: bool = False
    ) -> Path:
        descriptor, name = tempfile.mkstemp(
            prefix=".download-", suffix=".zip", dir=self.service.config.paths.staging
        )
        os.close(descriptor)
        path = Path(name)
        with self.state._connect() as conn:
            rows = conn.execute(
                """
                SELECT re.prompt_id, re.resource_id, re.resource_version, re.entry_id,
                       r.kind, o.relative_path
                FROM run_entries re
                JOIN resources r ON r.resource_id = re.resource_id
                JOIN resource_versions rv
                  ON rv.resource_id = re.resource_id AND rv.version = re.resource_version
                JOIN objects o ON o.sha256 = rv.object_sha256
                WHERE re.run_id = ? AND r.owner_key = ?
                ORDER BY re.position
                """,
                (run_id, owner_key),
            ).fetchall()
            output_rows = [] if prompts_only else conn.execute(
                """
                SELECT out.output_id, out.current_version, obj.relative_path, obj.media_type
                FROM outputs out
                JOIN output_versions ov
                  ON ov.output_id = out.output_id AND ov.version = out.current_version
                JOIN resources r ON r.resource_id = ov.resource_id
                JOIN resource_versions rv
                  ON rv.resource_id = ov.resource_id AND rv.version = ov.resource_version
                JOIN objects obj ON obj.sha256 = rv.object_sha256
                JOIN runs run ON run.run_id = out.run_id
                WHERE out.run_id = ? AND run.owner_key = ?
                ORDER BY out.output_id
                """,
                (run_id, owner_key),
            ).fetchall()
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for row in rows:
                if prompts_only and row["kind"] != "prompt":
                    continue
                source = self.service.config.paths.root / row["relative_path"]
                if source.is_file():
                    if row["kind"] == "prompt":
                        arcname = f"prompts/{row['prompt_id']}.txt"
                    else:
                        arcname = f"resources/{row['resource_id']}"
                    archive.write(source, arcname=arcname)
            for row in output_rows:
                source = self.service.config.paths.root / row["relative_path"]
                if source.is_file():
                    extension = {
                        "image/png": ".png",
                        "image/jpeg": ".jpg",
                        "image/webp": ".webp",
                    }.get(row["media_type"], ".bin")
                    archive.write(
                        source,
                        arcname=f"outputs/{row['output_id']}-v{row['current_version']}{extension}",
                    )
        return path

    def _run_download(self, handler: Any, owner_key: str, run_id: str) -> None:
        self._session(handler, "content:read")
        temporary = self._build_run_zip(owner_key, run_id)
        try:
            record = {
                "relative_path": temporary.relative_to(
                    self.service.config.paths.root
                ).as_posix(),
                "media_type": "application/zip",
                "object_sha256": self._hash_file(temporary),
            }
            self._send_file(handler, record, attachment_name=f"{run_id}.zip")
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def _changes(self, handler: Any) -> None:
        session = self._session(handler, "manifest:read")
        query = urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)
        try:
            after = int((query.get("after") or ["0"])[0])
            limit = min(1000, max(1, int((query.get("limit") or ["100"])[0])))
        except ValueError as exc:
            raise APIError(400, "invalid_cursor", "Change cursor is invalid") from exc
        with self.state._connect() as conn:
            rows = conn.execute(
                """
                SELECT sequence, resource_type, resource_id, version, operation, created_at
                FROM change_log
                WHERE owner_key = ? AND sequence > ?
                ORDER BY sequence
                LIMIT ?
                """,
                (session.owner_key, after, limit),
            ).fetchall()
        items = [dict(row) for row in rows]
        self._json(
            handler,
            200,
            {
                "items": items,
                "next_sequence": items[-1]["sequence"] if items else after,
            },
        )

    def _events(self, handler: Any) -> None:
        session = self._session(handler, "manifest:read")
        query = urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)
        try:
            after = int((query.get("after") or [handler.headers.get("Last-Event-ID") or "0"])[0])
        except ValueError as exc:
            raise APIError(400, "invalid_cursor", "Event cursor is invalid") from exc
        with self.state._connect() as conn:
            rows = conn.execute(
                """
                SELECT sequence, resource_type, resource_id, version, operation, created_at
                FROM change_log
                WHERE owner_key = ? AND sequence > ?
                ORDER BY sequence
                LIMIT 1000
                """,
                (session.owner_key, after),
            ).fetchall()
        items = [dict(row) for row in rows]
        chunks = []
        for item in items:
            safe = {
                key: item[key]
                for key in (
                    "sequence",
                    "resource_type",
                    "resource_id",
                    "version",
                    "operation",
                    "created_at",
                )
            }
            chunks.append(
                f"id: {item['sequence']}\nevent: change\ndata: "
                + json.dumps(safe, separators=(",", ":"))
                + "\n\n"
            )
        if not chunks:
            chunks.append(": heartbeat\n\n")
        body = "".join(chunks).encode()
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Content-Length", str(len(body)))
        self._cors(handler)
        handler.end_headers()
        handler.wfile.write(body)
