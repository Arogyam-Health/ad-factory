from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


class _Result:
    def __init__(
        self,
        modified_count: int = 0,
        deleted_count: int = 0,
    ) -> None:
        self.modified_count = modified_count
        self.deleted_count = deleted_count


class _Cursor(list):
    def sort(self, key, direction=1):
        return _Cursor(sorted(self, key=lambda item: item.get(key, 0), reverse=direction < 0))

    def limit(self, count: int):
        return _Cursor(self[:count])


class _Collection:
    def __init__(self) -> None:
        self.docs: list[dict] = []

    @classmethod
    def _matches(cls, doc: dict, query: dict) -> bool:
        for key, expected in query.items():
            if key == "$or":
                if not any(cls._matches(doc, branch) for branch in expected):
                    return False
                continue
            actual = doc.get(key)
            if isinstance(expected, dict):
                if "$in" in expected and actual not in expected["$in"]:
                    return False
                if "$lte" in expected and not (actual is not None and actual <= expected["$lte"]):
                    return False
                if "$gt" in expected and not (actual is not None and actual > expected["$gt"]):
                    return False
            elif actual != expected:
                return False
        return True

    def insert_one(self, doc: dict) -> None:
        if any(existing.get("job_id") == doc.get("job_id") for existing in self.docs):
            from pymongo.errors import DuplicateKeyError

            raise DuplicateKeyError("duplicate")
        self.docs.append(copy.deepcopy(doc))

    def find_one(self, query: dict, projection=None, **_kwargs):
        for doc in self.docs:
            if self._matches(doc, query):
                result = copy.deepcopy(doc)
                if projection:
                    if any(value == 1 for value in projection.values()):
                        result = {
                            key: value
                            for key, value in result.items()
                            if projection.get(key) == 1 or key == "_id"
                        }
                    for key, include in projection.items():
                        if include == 0:
                            result.pop(key, None)
                return result
        return None

    def find(self, query: dict, projection=None):
        return _Cursor(
            [
                self.find_one({"job_id": doc["job_id"]}, projection)
                for doc in self.docs
                if self._matches(doc, query)
            ]
        )

    def update_one(self, query: dict, update: dict, **_kwargs) -> _Result:
        for doc in self.docs:
            if self._matches(doc, query):
                doc.update(copy.deepcopy(update.get("$set", {})))
                for key in update.get("$unset", {}):
                    doc.pop(key, None)
                for key, amount in update.get("$inc", {}).items():
                    doc[key] = int(doc.get(key) or 0) + amount
                return _Result(1)
        return _Result()

    def delete_one(self, query: dict) -> _Result:
        for index, doc in enumerate(self.docs):
            if self._matches(doc, query):
                self.docs.pop(index)
                return _Result(deleted_count=1)
        return _Result()

    def find_one_and_update(self, query: dict, update: dict, **_kwargs):
        for doc in self.docs:
            if self._matches(doc, query):
                doc.update(copy.deepcopy(update.get("$set", {})))
                for key, amount in update.get("$inc", {}).items():
                    doc[key] = int(doc.get(key) or 0) + amount
                return copy.deepcopy(doc)
        return None


class _DB(dict):
    def __missing__(self, key):
        value = _Collection()
        self[key] = value
        return value


class AgentMetadataJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = _DB()
        self.device_id = "dev_" + "a" * 32
        self.agent_id = "agent_test"
        self.db["agents"].insert_one(
            {
                "agent_id": self.agent_id,
                "user_id": "user-1",
                "device_id": self.device_id,
                "is_active": True,
                "protocol_version": "v1",
            }
        )
        self.db["runs"].insert_one(
            {
                "job_id": "not-a-job",
                "run_id": "run-1",
                "user_id": "user-1",
                "owner_type": "user",
                "owner_id": "user-1",
                "agent_id": self.agent_id,
                "device_id": self.device_id,
            }
        )
        self.db_patch = patch(
            "dashboard.backend.agent.service.get_sync_db", return_value=self.db
        )
        self.db_patch.start()

    def tearDown(self) -> None:
        self.db_patch.stop()

    @staticmethod
    def valid_envelope() -> dict:
        return {
            "job_id": "job_test",
            "agent_id": "agent_test",
            "device_id": "dev_" + "a" * 32,
            "user_id": "user-1",
            "owner_type": "user",
            "owner_id": "user-1",
            "run_id": "run-1",
            "job_type": "execute_run",
            "command": "generate_images",
            "parameters": {
                "engine": "chatgpt",
                "mode": "both",
                "count": 2,
                "manifest_version": 3,
                "config_version_id": "cfgv_123",
            },
            "client_operation_id": "op-1",
            "status": "pending",
            "progress_code": "queued",
            "created_at": 1.0,
            "updated_at": 1.0,
            "started_at": None,
            "completed_at": None,
            "lease_expires_at": None,
            "fence": 0,
            "purge_at": None,
        }

    def test_validator_accepts_only_bounded_metadata_envelope(self) -> None:
        from dashboard.backend.agent.service import validate_job_envelope

        envelope = validate_job_envelope(self.valid_envelope())
        self.assertEqual(envelope["parameters"]["engine"], "chatgpt")
        self.assertLessEqual(len(json.dumps(envelope)), 8192)
        self.assertNotIn("payload", envelope)
        self.assertNotIn("result", envelope)

    def test_validator_rejects_every_prohibited_content_class(self) -> None:
        from dashboard.backend.agent.service import validate_job_envelope

        prohibited = {
            "prompt": "embedded prompt body",
            "document": "embedded document body",
            "config": {"body": "embedded config"},
            "image_base64": "aGVsbG8=",
            "blob": b"bytes",
            "comment": "revision instructions",
            "url": "http://127.0.0.1:8765/files/x",
            "capabilities": ["content:read"],
            "token": "local-capability",
            "secret": "provider-secret",
            "path": "/home/user/private/image.png",
            "logs": "raw browser output",
            "error": "raw stack trace",
        }
        for key, value in prohibited.items():
            envelope = self.valid_envelope()
            envelope["parameters"] = {"engine": "chatgpt", key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_job_envelope(envelope)

    def test_validator_rejects_prohibited_values_even_under_allowed_keys(self) -> None:
        from dashboard.backend.agent.service import validate_job_envelope

        for value in (
            "http://localhost:8765/private",
            "https://example.test/content",
            "/tmp/private",
            "C:\\Users\\name\\secret.txt",
            "data:image/png;base64,AAAA",
        ):
            envelope = self.valid_envelope()
            envelope["parameters"] = {"config_version_id": value}
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_job_envelope(envelope)

    def test_validator_accepts_bounded_product_asset_ids(self) -> None:
        from dashboard.backend.agent.service import validate_job_envelope

        envelope = self.valid_envelope()
        envelope["parameters"]["product_asset_ids"] = [
            "res_" + "a" * 32,
            "res_" + "b" * 32,
        ]
        clean = validate_job_envelope(envelope)
        self.assertEqual(
            clean["parameters"]["product_asset_ids"],
            ["res_" + "a" * 32, "res_" + "b" * 32],
        )

    def test_validator_rejects_product_asset_paths_and_empty_lists(self) -> None:
        from dashboard.backend.agent.service import validate_job_envelope

        for value in ([], ["/tmp/secret.png"], ["http://localhost/x"], "res_only"):
            envelope = self.valid_envelope()
            envelope["parameters"]["product_asset_ids"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_job_envelope(envelope)

    def test_validator_enforces_parameter_and_message_bounds(self) -> None:
        from dashboard.backend.agent.service import validate_job_envelope

        for mutation in (
            lambda doc: doc["parameters"].update({"count": 10001}),
            lambda doc: doc["parameters"].update({"engine": "x" * 65}),
            lambda doc: doc.update({"progress_code": "x" * 65}),
            lambda doc: doc.update({"error_code": "x" * 65}),
            lambda doc: doc.update({"error_message": "x" * 513}),
        ):
            envelope = self.valid_envelope()
            mutation(envelope)
            with self.assertRaises(ValueError):
                validate_job_envelope(envelope)

    def _create(self, operation_id: str = "op-1"):
        from dashboard.backend.agent.service import create_job

        return create_job(
            agent_id=self.agent_id,
            device_id=self.device_id,
            user_id="user-1",
            owner_type="user",
            owner_id="user-1",
            run_id="run-1",
            job_type="execute_run",
            command="generate_images",
            parameters={"engine": "chatgpt", "mode": "both", "count": 2},
            client_operation_id=operation_id,
        )

    def test_creation_is_pinned_and_client_operation_is_idempotent(self) -> None:
        first = self._create()
        second = self._create()

        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(len(self.db["agent_jobs"].docs), 1)
        self.assertEqual(first["device_id"], self.device_id)
        self.assertNotIn("payload", first)

    def test_offline_device_can_receive_idempotent_purge_job(self) -> None:
        from dashboard.backend.agent.service import create_job

        self.db["agents"].docs[0]["is_active"] = False
        values = {
            "agent_id": self.agent_id,
            "device_id": self.device_id,
            "user_id": "user-1",
            "owner_type": "user",
            "owner_id": "user-1",
            "run_id": "run-1",
            "job_type": "purge_run",
            "command": "purge_run",
            "parameters": {},
            "client_operation_id": "purge-run-1",
        }
        with self.assertRaises(ValueError):
            create_job(**values)

        first = create_job(**values, allow_inactive_agent=True)
        second = create_job(**values, allow_inactive_agent=True)
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(first["command"], "purge_run")

    def test_creation_rejects_cross_user_device_and_run_authority(self) -> None:
        from dashboard.backend.agent.service import create_job

        for changes in (
            {"device_id": "dev_" + "b" * 32},
            {"user_id": "user-2", "owner_id": "user-2"},
            {"run_id": "run-other"},
        ):
            values = {
                "agent_id": self.agent_id,
                "device_id": self.device_id,
                "user_id": "user-1",
                "owner_type": "user",
                "owner_id": "user-1",
                "run_id": "run-1",
                "job_type": "execute_run",
                "command": "generate_images",
                "parameters": {"engine": "chatgpt"},
                "client_operation_id": "op-denied",
            }
            values.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                create_job(**values)

    def test_claim_requires_exact_device_and_increments_fence(self) -> None:
        from dashboard.backend.agent.service import claim_job

        job = self._create()
        denied = claim_job(
            job["job_id"], self.agent_id, "dev_" + "b" * 32, "claim-wrong"
        )
        claimed = claim_job(job["job_id"], self.agent_id, self.device_id, "claim-1")

        self.assertIsNone(denied)
        self.assertEqual(claimed["fence"], 1)
        self.assertEqual(claimed["device_id"], self.device_id)

    def test_stale_fence_cannot_report_progress_or_terminal_state(self) -> None:
        from dashboard.backend.agent.service import (
            claim_job,
            complete_job,
            update_job_progress,
        )

        job = self._create()
        first = claim_job(job["job_id"], self.agent_id, self.device_id, "claim-1")
        stored = self.db["agent_jobs"].docs[0]
        stored["lease_expires_at"] = 0
        second = claim_job(job["job_id"], self.agent_id, self.device_id, "claim-2")

        self.assertEqual(second["fence"], first["fence"] + 1)
        self.assertFalse(
            update_job_progress(
                job["job_id"],
                self.agent_id,
                self.device_id,
                first["fence"],
                "generating",
            )
        )
        self.assertFalse(
            complete_job(
                job["job_id"],
                self.agent_id,
                self.device_id,
                first["fence"],
                "evt-stale",
            )
        )
        self.assertEqual(stored["status"], "running")

    def test_terminal_event_is_idempotent_and_sets_ttl(self) -> None:
        from dashboard.backend.agent.service import claim_job, complete_job

        job = self._create()
        claimed = claim_job(job["job_id"], self.agent_id, self.device_id, "claim-1")
        first = complete_job(
            job["job_id"],
            self.agent_id,
            self.device_id,
            claimed["fence"],
            "evt-terminal-1",
        )
        replay = complete_job(
            job["job_id"],
            self.agent_id,
            self.device_id,
            claimed["fence"],
            "evt-terminal-1",
        )

        self.assertTrue(first)
        self.assertTrue(replay)
        stored = self.db["agent_jobs"].docs[0]
        self.assertEqual(stored["status"], "completed")
        self.assertIsInstance(stored["purge_at"], datetime)

    def test_polling_fallback_returns_only_exact_device_jobs(self) -> None:
        from dashboard.backend.agent.service import poll_jobs

        self._create()
        self.assertEqual(len(poll_jobs(self.agent_id, self.device_id)), 1)
        self.assertEqual(
            poll_jobs(self.agent_id, "dev_" + "b" * 32),
            [],
        )

    def test_terminal_job_has_ttl_and_operation_uniqueness_indexes(self) -> None:
        from dashboard.backend.db.collections import COLL_AGENT_JOBS
        from dashboard.backend.db.indexes import INDEX_SPECS

        documents = [index.document for index in INDEX_SPECS[COLL_AGENT_JOBS]]
        self.assertTrue(
            any(
                document.get("key") == {"purge_at": 1}
                and document.get("expireAfterSeconds") == 0
                for document in documents
            )
        )
        self.assertTrue(
            any(
                document.get("unique")
                and document.get("key")
                == {
                    "owner_type": 1,
                    "owner_id": 1,
                    "client_operation_id": 1,
                }
                and document.get("partialFilterExpression")
                == {
                    "owner_type": {"$type": "string"},
                    "owner_id": {"$type": "string"},
                    "client_operation_id": {"$type": "string"},
                }
                for document in documents
            )
        )

    def test_new_local_job_writes_are_metadata_only_and_resolve_manifest(self) -> None:
        from local_agent_runtime.storage import AgentPaths, AgentState

        with tempfile.TemporaryDirectory() as temp:
            state = AgentState(AgentPaths(Path(temp)))
            state.create_run(
                run_id="run-1",
                owner_key="user:user-1",
                device_id=self.device_id,
                workspace_id="wrk-1",
                run_number=1,
                flow_type="structured",
                operation_id="create-run",
            )
            state.record_job(
                "job-1",
                "user:user-1",
                "pending",
                {
                    "run_id": "run-1",
                    "command": "generate_images",
                    "parameters": {"engine": "chatgpt", "mode": "both"},
                },
            )
            context = state.resolve_job_context("job-1")
            with state._connect() as conn:
                stored = conn.execute(
                    "SELECT payload_json FROM jobs WHERE job_id = 'job-1'"
                ).fetchone()[0]

        self.assertEqual(context["run"]["run_id"], "run-1")
        self.assertNotIn("prompt", stored.lower())
        self.assertNotIn("base64", stored.lower())

    def test_local_payload_accepts_product_asset_ids(self) -> None:
        from local_agent_runtime.storage import AgentPaths, AgentState, metadata_job_payload

        ids = ["res_" + "a" * 32, "res_" + "b" * 32]
        clean = metadata_job_payload(
            {
                "run_id": "run-1",
                "command": "generate_images",
                "parameters": {
                    "engine": "chatgpt",
                    "mode": "45",
                    "product_asset_ids": ids,
                },
            }
        )
        self.assertEqual(clean["parameters"]["product_asset_ids"], ids)

        with tempfile.TemporaryDirectory() as temp:
            state = AgentState(AgentPaths(Path(temp)))
            state.record_job(
                "job-images",
                "user:user-1",
                "pending",
                {
                    "run_id": "run-1",
                    "command": "generate_images",
                    "parameters": {
                        "engine": "chatgpt",
                        "mode": "45",
                        "product_asset_ids": ids,
                    },
                },
            )
            with state._connect() as conn:
                stored = json.loads(
                    conn.execute(
                        "SELECT payload_json FROM jobs WHERE job_id = 'job-images'"
                    ).fetchone()[0]
                )
        self.assertEqual(stored["parameters"]["product_asset_ids"], ids)

    def test_local_payload_rejects_paths_and_unknown_parameters(self) -> None:
        from local_agent_runtime.storage import metadata_job_payload

        with self.assertRaises(ValueError):
            metadata_job_payload(
                {
                    "run_id": "run-1",
                    "command": "generate_images",
                    "parameters": {"product_asset_ids": ["/tmp/secret.png"]},
                }
            )
        with self.assertRaises(ValueError):
            metadata_job_payload(
                {
                    "run_id": "run-1",
                    "command": "generate_images",
                    "parameters": {"not_allowed": "x"},
                }
            )

    def test_unsupported_local_parameter_fails_job_once(self) -> None:
        from local_agent_runtime import local_agent
        from local_agent_runtime.storage import AgentPaths, AgentState

        reports: list[tuple[str, str]] = []

        def fake_report(job_id: str, action: str, **_kwargs) -> bool:
            reports.append((job_id, action))
            return True

        with tempfile.TemporaryDirectory() as temp:
            paths = AgentPaths(Path(temp))
            state = AgentState(paths)
            with patch.object(local_agent, "AGENT_STATE", state), patch.object(
                local_agent, "AGENT_PATHS", paths
            ), patch.object(local_agent, "AGENT_TOKEN", "tok"), patch.object(
                local_agent, "ACTIVE_JOB_FENCES", {}
            ), patch.object(
                local_agent, "api_request", return_value={"fence": 1}
            ), patch.object(
                local_agent, "report_job_terminal", side_effect=fake_report
            ):
                local_agent.execute_job(
                    {
                        "job_id": "job_bad",
                        "job_type": "execute_run",
                        "command": "generate_images",
                        "run_id": "run_1",
                        "owner_type": "user",
                        "owner_id": "u1",
                        "parameters": {"not_allowed": "x"},
                    }
                )

        self.assertEqual(reports, [("job_bad", "fail")])

    def test_local_cleanup_is_dry_run_first_idempotent_and_redacted(self) -> None:
        from dashboard.backend.agent.migration import cleanup_local_job_payloads
        from local_agent_runtime.storage import AgentPaths, AgentState

        with tempfile.TemporaryDirectory() as temp:
            paths = AgentPaths(Path(temp))
            state = AgentState(paths)
            with state._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO jobs(job_id, owner_key, status, payload_json, created_at, updated_at)
                    VALUES ('legacy', 'user:user-1', 'completed', ?, 1, 1)
                    """,
                    (json.dumps({"prompt": "private", "base64": "AAAA", "run_id": "run-1"}),),
                )
            dry = cleanup_local_job_payloads(paths, apply=False)
            with state._connect() as conn:
                before = conn.execute(
                    "SELECT payload_json FROM jobs WHERE job_id = 'legacy'"
                ).fetchone()[0]
            applied = cleanup_local_job_payloads(paths, apply=True)
            repeated = cleanup_local_job_payloads(paths, apply=True)
            with state._connect() as conn:
                after = conn.execute(
                    "SELECT payload_json FROM jobs WHERE job_id = 'legacy'"
                ).fetchone()[0]

        self.assertEqual(dry["changed"], 1)
        self.assertIn("prompt", before)
        self.assertNotIn("private", json.dumps(dry))
        self.assertEqual(applied["changed"], 1)
        self.assertEqual(repeated["changed"], 0)
        self.assertEqual(json.loads(after), {"run_id": "run-1"})

    def test_mongo_cleanup_dry_run_strips_legacy_content_and_is_idempotent(self) -> None:
        from dashboard.backend.agent.migration import cleanup_mongo_job_documents

        collection = _Collection()
        collection.docs.append(
            {
                "job_id": "legacy",
                "agent_id": self.agent_id,
                "device_id": self.device_id,
                "user_id": "user-1",
                "run_id": "run-1",
                "job_type": "execute_run",
                "payload": {
                    "prompt": "private",
                    "input_images": [{"base64": "AAAA"}],
                    "engine": "chatgpt",
                },
            }
        )
        dry = cleanup_mongo_job_documents(collection, apply=False)
        self.assertIn("payload", collection.docs[0])
        applied = cleanup_mongo_job_documents(collection, apply=True)
        repeated = cleanup_mongo_job_documents(collection, apply=True)

        self.assertEqual(dry["changed"], 1)
        self.assertEqual(applied["changed"], 1)
        self.assertEqual(repeated["changed"], 0)
        self.assertNotIn("payload", collection.docs[0])
        self.assertNotIn("private", json.dumps(dry))

    def test_mongo_cleanup_deletes_legacy_jobs_that_cannot_be_safely_migrated(
        self,
    ) -> None:
        from dashboard.backend.agent.migration import cleanup_mongo_job_documents

        collection = _Collection()
        collection.docs.append(
            {
                "job_id": "unsafe-legacy",
                "payload": {
                    "prompt": "private",
                    "input_images": [{"base64": "A" * 1000}],
                },
            }
        )

        report = cleanup_mongo_job_documents(collection, apply=True)

        self.assertEqual(report["deleted"], 1)
        self.assertEqual(collection.docs, [])
        self.assertNotIn("private", json.dumps(report))

    def test_control_plane_startup_cleans_legacy_agent_jobs_before_indexes(
        self,
    ) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "dashboard"
            / "backend"
            / "control_app.py"
        ).read_text(encoding="utf-8")
        cleanup = source.index("cleanup_mongo_job_documents")
        indexes = source.index("create_indexes")
        self.assertLess(cleanup, indexes)


if __name__ == "__main__":
    unittest.main()
