from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch


class _Collection:
    def __init__(self) -> None:
        self.docs: list[dict] = []

    @staticmethod
    def _matches(doc: dict, query: dict) -> bool:
        return all(doc.get(key) == value for key, value in query.items())

    def find_one(self, query: dict, *_args, **_kwargs):
        return next(
            (copy.deepcopy(doc) for doc in self.docs if self._matches(doc, query)),
            None,
        )

    def insert_one(self, doc: dict) -> None:
        self.docs.append(copy.deepcopy(doc))


class _DB(dict):
    def __missing__(self, key):
        collection = _Collection()
        self[key] = collection
        return collection


class RunEnvelopeAllocationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = _DB()
        self.device_id = "dev_" + "a" * 32
        self.db["agents"].insert_one(
            {
                "agent_id": "agent-1",
                "user_id": "user-1",
                "device_id": self.device_id,
                "protocol_version": "v1",
                "supports_pairing": True,
                "is_active": True,
            }
        )
        self.db["org_members"].insert_one(
            {"org_id": "org-1", "user_id": "user-1", "status": "active"}
        )
        self.db_patch = patch(
            "dashboard.backend.agent.service.get_sync_db", return_value=self.db
        )
        self.reserve_patch = patch(
            "dashboard.backend.agent.service.reserve_run_number", return_value=12
        )
        self.db_patch.start()
        self.reserve_patch.start()

    def tearDown(self) -> None:
        self.reserve_patch.stop()
        self.db_patch.stop()

    def allocate(self, **overrides):
        from dashboard.backend.agent.service import allocate_run_envelope

        values = {
            "user_id": "user-1",
            "owner_type": "user",
            "owner_id": "user-1",
            "agent_id": "agent-1",
            "device_id": self.device_id,
            "flow_type": "structured",
            "settings": {"provider": "google", "batch_size": 10},
        }
        values.update(overrides)
        return allocate_run_envelope(**values)

    def test_allocation_is_metadata_only_and_pinned_to_exact_device(self) -> None:
        result = self.allocate()
        stored = self.db["runs"].docs[0]
        serialized = json.dumps(stored).lower()

        self.assertEqual(result["agent_id"], "agent-1")
        self.assertEqual(result["device_id"], self.device_id)
        self.assertEqual(result["run_number"], 12)
        self.assertEqual(stored["status"], "allocated")
        self.assertEqual(stored["flow_type"], "structured")
        self.assertNotIn("workspace_id", stored)
        for forbidden in (
            "content",
            "base64",
            "file",
            "blob",
            "localhost",
            "127.0.0.1",
            "token",
            "path",
            "comment",
            "prompt",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_cross_user_org_device_and_content_settings_are_rejected(self) -> None:
        invalid = (
            {"user_id": "user-2", "owner_id": "user-2"},
            {"owner_type": "org", "owner_id": "org-missing"},
            {"device_id": "dev_" + "b" * 32},
            {"settings": {"prompt": "full prompt body"}},
            {"settings": {"provider": "x" * 201}},
        )
        for overrides in invalid:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.allocate(**overrides)

    def test_org_allocation_uses_owner_scoped_run_number(self) -> None:
        result = self.allocate(owner_type="org", owner_id="org-1")
        stored = self.db["runs"].docs[0]
        self.assertEqual(result["owner_type"], "org")
        self.assertEqual(stored["owner_id"], "org-1")
        self.assertEqual(stored["created_by_user_id"], "user-1")


if __name__ == "__main__":
    unittest.main()
