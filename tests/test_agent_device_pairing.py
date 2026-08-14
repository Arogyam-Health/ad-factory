from __future__ import annotations

import copy
import unittest
from unittest.mock import patch


class _Result:
    def __init__(self, modified_count: int = 0, deleted_count: int = 0) -> None:
        self.modified_count = modified_count
        self.deleted_count = deleted_count


class _Collection:
    def __init__(self) -> None:
        self.docs: list[dict] = []

    @staticmethod
    def _matches(doc: dict, query: dict) -> bool:
        for key, expected in query.items():
            actual = doc.get(key)
            if isinstance(expected, dict):
                if "$ne" in expected and actual == expected["$ne"]:
                    return False
                if "$in" in expected and actual not in expected["$in"]:
                    return False
                if "$gt" in expected and not actual > expected["$gt"]:
                    return False
            elif actual != expected:
                return False
        return True

    def insert_one(self, doc: dict) -> None:
        self.docs.append(copy.deepcopy(doc))

    def find_one(self, query: dict, projection=None, **_kwargs):
        for doc in self.docs:
            if self._matches(doc, query):
                result = copy.deepcopy(doc)
                if projection:
                    for key, include in projection.items():
                        if include == 0:
                            result.pop(key, None)
                return result
        return None

    def find(self, query: dict, projection=None):
        results = []
        for doc in self.docs:
            if not self._matches(doc, query):
                continue
            result = copy.deepcopy(doc)
            if projection:
                for key, include in projection.items():
                    if include == 0:
                        result.pop(key, None)
                    elif include == 1:
                        pass
                if any(value == 1 for value in projection.values()):
                    result = {
                        key: value
                        for key, value in result.items()
                        if projection.get(key) == 1 or key == "_id"
                    }
            results.append(result)
        return _Cursor(results)

    def update_one(self, query: dict, update: dict) -> _Result:
        for doc in self.docs:
            if self._matches(doc, query):
                doc.update(copy.deepcopy(update.get("$set", {})))
                return _Result(1)
        return _Result()

    def update_many(self, query: dict, update: dict) -> _Result:
        count = 0
        for doc in self.docs:
            if self._matches(doc, query):
                doc.update(copy.deepcopy(update.get("$set", {})))
                count += 1
        return _Result(count)

    def delete_many(self, query: dict) -> _Result:
        kept = []
        deleted = 0
        for doc in self.docs:
            if self._matches(doc, query):
                deleted += 1
            else:
                kept.append(doc)
        self.docs = kept
        return _Result(deleted_count=deleted)


class _Cursor(list):
    def sort(self, *_args):
        return self

    def limit(self, count: int):
        return _Cursor(self[:count])


class _DB(dict):
    def __missing__(self, key):
        value = _Collection()
        self[key] = value
        return value


class AgentDevicePairingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = _DB()
        self.db["org_members"].insert_one(
            {"org_id": "org-1", "user_id": "user-1", "status": "active"}
        )
        self.db["org_members"].insert_one(
            {"org_id": "org-2", "user_id": "user-2", "status": "active"}
        )
        self.db_patch = patch(
            "dashboard.backend.agent.service.get_sync_db", return_value=self.db
        )
        self.db_patch.start()

    def tearDown(self) -> None:
        self.db_patch.stop()

    def register(self, user_id: str = "user-1", device_id: str = "dev_" + "a" * 32):
        from dashboard.backend.agent.service import register_agent

        return register_agent(
            user_id,
            "desktop",
            device_id=device_id,
            protocol_version="v1",
            supports_pairing=True,
        )

    def request(self, registration: dict, **overrides):
        from dashboard.backend.agent.service import request_pairing_approval

        values = {
            "user_id": "user-1",
            "owner_type": "user",
            "owner_id": "user-1",
            "agent_id": registration["agent_id"],
            "device_id": registration["device_id"],
            "challenge_id": "pch_" + "b" * 32,
            "challenge": "c" * 43,
            "scopes": ["manifest:read", "assets:write"],
        }
        values.update(overrides)
        return request_pairing_approval(**values)

    def test_registration_persists_only_device_and_protocol_support(self) -> None:
        registration = self.register()
        stored = self.db["agents"].docs[0]

        self.assertEqual(registration["device_id"], "dev_" + "a" * 32)
        self.assertEqual(registration["protocol_version"], "v1")
        self.assertTrue(registration["supports_pairing"])
        self.assertNotIn("capabilities", stored)
        self.assertNotIn("localhost_url", stored)
        self.assertNotIn("local_path", stored)

    def test_register_reuses_one_active_agent_per_user_device(self) -> None:
        first = self.register()
        self.db["agents"].insert_one(
            {
                "agent_id": "agent_stale_sibling",
                "user_id": "user-1",
                "device_id": "dev_" + "a" * 32,
                "is_active": True,
                "supports_pairing": True,
                "name": "old",
            }
        )
        self.db["agent_pairings"].insert_one(
            {"agent_id": "agent_stale_sibling", "challenge_id": "pch_old"}
        )
        self.db["agent_jobs"].insert_one(
            {"agent_id": "agent_stale_sibling", "job_id": "job_old"}
        )

        second = self.register()
        active = [doc for doc in self.db["agents"].docs if doc.get("is_active")]

        self.assertEqual(second["agent_id"], first["agent_id"])
        self.assertNotEqual(second["token"], first["token"])
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["agent_id"], first["agent_id"])
        self.assertFalse(
            any(doc.get("is_active") for doc in self.db["agents"].docs if doc["agent_id"] == "agent_stale_sibling")
        )
        self.assertEqual(self.db["agent_pairings"].docs, [])
        self.assertEqual(self.db["agent_jobs"].docs, [])

    def test_approval_is_metadata_only_and_pinned_to_agent_device_owner(self) -> None:
        registration = self.register()
        response = self.request(registration)
        stored = self.db["agent_pairings"].docs[0]
        serialized = repr(stored)

        self.assertEqual(response["status"], "pending")
        self.assertEqual(stored["agent_id"], registration["agent_id"])
        self.assertEqual(stored["device_id"], registration["device_id"])
        self.assertEqual(stored["owner_key"], "user:user-1")
        self.assertEqual(len(stored["challenge_hash"]), 64)
        self.assertNotIn("c" * 43, serialized)
        for forbidden in ("localhost", "access_token", "capabilities", "local_path"):
            self.assertNotIn(forbidden, serialized.lower())

    def test_cross_user_cross_org_cross_device_and_replay_are_denied(self) -> None:
        registration = self.register()
        for overrides in (
            {"user_id": "user-2", "owner_id": "user-2"},
            {"owner_type": "org", "owner_id": "org-2"},
            {"device_id": "dev_" + "d" * 32},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.request(registration, **overrides)

        self.request(registration)
        with self.assertRaises(ValueError):
            self.request(registration)

    def test_pending_approval_survives_notification_loss_until_agent_ack(self) -> None:
        from dashboard.backend.agent.service import (
            acknowledge_pairing_approval,
            poll_pairing_approvals,
        )

        registration = self.register()
        self.request(
            registration,
            owner_type="org",
            owner_id="org-1",
        )
        pending = poll_pairing_approvals(
            registration["agent_id"], registration["device_id"]
        )

        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["owner_key"], "org:org-1")
        self.assertEqual(len(pending[0]["challenge_hash"]), 64)
        self.assertTrue(
            acknowledge_pairing_approval(
                pending[0]["challenge_id"],
                registration["agent_id"],
                registration["device_id"],
            )
        )
        self.assertEqual(
            poll_pairing_approvals(registration["agent_id"], registration["device_id"]),
            [],
        )


if __name__ == "__main__":
    unittest.main()
