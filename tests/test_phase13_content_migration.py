from __future__ import annotations

import copy
import base64
import hashlib
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class _Collection:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = copy.deepcopy(documents)
        self.updates = 0

    def find(self, _query: dict[str, Any]) -> list[dict[str, Any]]:
        return copy.deepcopy(self.documents)

    def update_one(self, selector: dict[str, Any], update: dict[str, Any]) -> None:
        for document in self.documents:
            if all(document.get(key) == value for key, value in selector.items()):
                for key, value in update.get("$set", {}).items():
                    _set_path(document, key, copy.deepcopy(value))
                for key in update.get("$unset", {}):
                    _unset_path(document, key)
                self.updates += 1
                return
        raise AssertionError("document not found")


def _set_path(document: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current = document
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def _unset_path(document: dict[str, Any], path: str) -> None:
    parts = path.split(".")
    current: Any = document
    for part in parts[:-1]:
        if not isinstance(current, dict):
            return
        current = current.get(part)
    if isinstance(current, dict):
        current.pop(parts[-1], None)


class _Importer:
    authenticated = True
    device_id = "dev_0123456789abcdef0123456789abcdef"

    def __init__(self, *, bad_hash: bool = False, interrupt: bool = False) -> None:
        self.bad_hash = bad_hash
        self.interrupt = interrupt
        self.content_calls: list[dict[str, Any]] = []
        self.secret_calls: list[dict[str, Any]] = []

    def import_content(self, **request: Any) -> dict[str, Any]:
        self.content_calls.append(request)
        if self.interrupt and len(self.content_calls) == 2:
            raise RuntimeError("interrupted")
        digest = hashlib.sha256(request["content"]).hexdigest()
        return {
            "resource_id": "res_" + request["operation_id"][-24:],
            "version": 1,
            "sha256": "0" * 64 if self.bad_hash else digest,
            "device_id": self.device_id,
        }

    def import_provider_secret(self, **request: Any) -> dict[str, Any]:
        self.secret_calls.append(request)
        return {
            "provider": request["provider"],
            "verified": True,
            "device_id": self.device_id,
        }


class _Backup:
    def __init__(self) -> None:
        self.operations: list[str] = []

    def write(self, operation_id: str, _document: dict[str, Any]) -> None:
        self.operations.append(operation_id)


class ContentMigrationTests(unittest.TestCase):
    def _run(
        self,
        collections: dict[str, _Collection],
        *,
        apply: bool,
        importer: _Importer | None = None,
        backup: _Backup | None = None,
        checkpoint_path: Path | None = None,
        decrypt_secret=lambda value: value,
    ) -> tuple[dict[str, Any], _Importer, _Backup]:
        from dashboard.backend.agent.content_migration import MongoContentMigrator

        importer = importer or _Importer()
        backup = backup or _Backup()
        migrator = MongoContentMigrator(
            collections=collections,
            importer=importer,
            backup=backup,
            checkpoint_path=checkpoint_path,
            decrypt_secret=decrypt_secret,
        )
        return migrator.run(apply=apply), importer, backup

    def test_dry_run_inspects_without_any_mutation(self) -> None:
        prompts = _Collection([{"_id": "p1", "user_id": "u1", "content": "private prompt"}])
        original = copy.deepcopy(prompts.documents)

        report, importer, backup = self._run({"prompts": prompts}, apply=False)

        self.assertEqual(prompts.documents, original)
        self.assertEqual(prompts.updates, 0)
        self.assertEqual(importer.content_calls, [])
        self.assertEqual(backup.operations, [])
        self.assertEqual(report["content_candidates"], 1)
        self.assertNotIn("private prompt", json.dumps(report))

    def test_apply_backs_up_imports_verifies_then_removes_body(self) -> None:
        prompts = _Collection([{"_id": "p1", "user_id": "u1", "content": "private prompt"}])
        backup = _Backup()

        report, importer, _ = self._run(
            {"prompts": prompts}, apply=True, backup=backup
        )

        document = prompts.documents[0]
        self.assertNotIn("content", document)
        self.assertEqual(document["local_reference"]["sha256"], hashlib.sha256(
            importer.content_calls[0]["content"]
        ).hexdigest())
        self.assertEqual(backup.operations, [report["operations"][0]["operation_id"]])
        self.assertEqual(report["migrated"], 1)

    def test_hash_mismatch_never_mutates_mongo(self) -> None:
        prompts = _Collection([{"_id": "p1", "user_id": "u1", "content": "private prompt"}])
        report, _, backup = self._run(
            {"prompts": prompts}, apply=True, importer=_Importer(bad_hash=True)
        )

        self.assertIn("content", prompts.documents[0])
        self.assertEqual(prompts.updates, 0)
        self.assertEqual(len(backup.operations), 1)
        self.assertEqual(report["errors"], 1)

    def test_apply_is_idempotent(self) -> None:
        prompts = _Collection([{"_id": "p1", "user_id": "u1", "content": "private prompt"}])
        first, importer, backup = self._run({"prompts": prompts}, apply=True)
        second, importer, backup = self._run(
            {"prompts": prompts}, apply=True, importer=importer, backup=backup
        )

        self.assertEqual(first["migrated"], 1)
        self.assertEqual(second["migrated"], 0)
        self.assertEqual(len(importer.content_calls), 1)
        self.assertEqual(prompts.updates, 1)

    def test_report_is_redacted_for_content_secrets_and_identifiers(self) -> None:
        collections = {
            "prompts": _Collection([
                {"_id": "sensitive-id", "user_id": "private-owner", "prompt_text": "unique-body"}
            ]),
            "provider_configs": _Collection([
                {
                    "_id": "provider-id",
                    "user_id": "private-owner",
                    "provider": "opencode",
                    "api_key": "unique-secret",
                }
            ]),
        }

        report, _, _ = self._run(collections, apply=False)
        serialized = json.dumps(report)

        for forbidden in (
            "unique-body",
            "unique-secret",
            "private-owner",
            "sensitive-id",
            "provider-id",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_partial_restart_resumes_with_stable_operation_ids(self) -> None:
        prompts = _Collection([
            {"_id": "p1", "user_id": "u1", "content": "first"},
            {"_id": "p2", "user_id": "u1", "content": "second"},
        ])
        with tempfile.TemporaryDirectory() as temporary:
            checkpoint = Path(temporary) / "checkpoint.json"
            importer = _Importer(interrupt=True)
            with self.assertRaises(RuntimeError):
                self._run(
                    {"prompts": prompts},
                    apply=True,
                    importer=importer,
                    checkpoint_path=checkpoint,
                )
            importer.interrupt = False
            report, importer, _ = self._run(
                {"prompts": prompts},
                apply=True,
                importer=importer,
                checkpoint_path=checkpoint,
            )

        self.assertEqual(report["migrated"], 1)
        self.assertEqual(len({call["operation_id"] for call in importer.content_calls}), 2)
        self.assertNotIn("content", prompts.documents[0])
        self.assertNotIn("content", prompts.documents[1])

    def test_ownerless_render_files_are_unassigned_and_not_imported(self) -> None:
        from local_agent_runtime.migration import migrate_render_files

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            owner_file = root / "owner" / "artifact.png"
            owner_file.parent.mkdir()
            owner_file.write_bytes(b"owned")
            global_file = root / "global" / "workspace.json"
            global_file.parent.mkdir()
            global_file.write_text('{"private":true}', encoding="utf-8")
            importer = _Importer()

            report = migrate_render_files(
                owner_roots={"user:u1": owner_file.parent},
                unassigned_roots=[global_file.parent],
                importer=importer,
                apply=True,
            )

        self.assertEqual(report["imported"], 1)
        self.assertEqual(report["unassigned"], 1)
        self.assertEqual(len(importer.content_calls), 1)
        self.assertNotIn("workspace.json", json.dumps(report))

    def test_provider_secret_is_preserved_in_mongodb(self) -> None:
        providers = _Collection([
            {
                "_id": "provider-id",
                "user_id": "u1",
                "provider": "opencode",
                "encrypted_api_key": "ciphertext",
                "default_model": "model",
            }
        ])
        report, importer, _ = self._run(
            {"provider_configs": providers},
            apply=True,
            decrypt_secret=lambda value: "decrypted-secret" if value == "ciphertext" else value,
        )

        document = providers.documents[0]
        self.assertEqual(document["encrypted_api_key"], "ciphertext")
        self.assertNotIn("local_provider_reference", document)
        self.assertEqual(len(importer.secret_calls), 0)
        self.assertNotIn("decrypted-secret", json.dumps(report))

    def test_malformed_content_is_reported_and_preserved(self) -> None:
        prompts = _Collection([{"_id": "p1", "user_id": "u1", "content": b"\xff\x00"}])

        report, importer, _ = self._run({"prompts": prompts}, apply=True)

        self.assertEqual(report["malformed"], 1)
        self.assertEqual(prompts.updates, 0)
        self.assertEqual(importer.content_calls, [])
        self.assertIn("content", prompts.documents[0])

    def test_local_import_endpoint_requires_auth_and_verifies_hash(self) -> None:
        from local_agent_runtime.artifact_server import ArtifactServer, ArtifactServerConfig
        from local_agent_runtime.storage import AgentPaths

        with tempfile.TemporaryDirectory() as temporary:
            server = ArtifactServer(
                ArtifactServerConfig(
                    paths=AgentPaths(Path(temporary) / "agent"),
                    port=0,
                    max_upload_bytes=1024,
                    max_request_bytes=4096,
                )
            )
            server.start()
            content = b'{"content":"private"}'
            payload = json.dumps(
                {
                    "kind": "prompt",
                    "logical_key": "migration-prompt-test",
                    "content_base64": base64.b64encode(content).decode("ascii"),
                    "expected_sha256": hashlib.sha256(content).hexdigest(),
                    "operation_id": "migration-endpoint-test",
                }
            ).encode()
            try:
                request = urllib.request.Request(
                    server.url + "/v1/migrations/content",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as unauthenticated:
                    urllib.request.urlopen(request, timeout=3)
                self.assertEqual(unauthenticated.exception.code, 401)

                challenge_request = urllib.request.Request(
                    server.url + "/v1/pairing/challenges",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(challenge_request, timeout=3) as response:
                    challenge = json.loads(response.read())
                server.approve_pairing_challenge(
                    challenge["challenge_id"],
                    challenge["challenge"],
                    owner_key="user:u1",
                    scopes=["documents:write"],
                )
                session_request = urllib.request.Request(
                    server.url + "/v1/pairing/sessions",
                    data=json.dumps(challenge).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(session_request, timeout=3) as response:
                    token = json.loads(response.read())["access_token"]
                request.add_header("Authorization", f"Bearer {token}")
                with urllib.request.urlopen(request, timeout=3) as response:
                    result = json.loads(response.read())
            finally:
                server.stop()

        self.assertEqual(result["sha256"], hashlib.sha256(content).hexdigest())


if __name__ == "__main__":
    unittest.main()
