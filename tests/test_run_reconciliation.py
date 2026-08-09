from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch


class RunReconciliationTests(unittest.TestCase):
    @staticmethod
    def request(user_id: str = "user-1") -> SimpleNamespace:
        return SimpleNamespace(state=SimpleNamespace(user={"user_id": user_id}))

    def test_prompt_metadata_delete_is_exactly_user_and_run_scoped(self) -> None:
        from dashboard.backend.db.collections import COLL_PROMPTS, COLL_RUNS
        from dashboard.backend.routes.runs import delete_run_prompt_metadata

        db = MagicMock()
        runs = MagicMock()
        prompts = MagicMock()
        db.__getitem__.side_effect = lambda name: {
            COLL_RUNS: runs,
            COLL_PROMPTS: prompts,
        }[name]
        runs.find_one.return_value = {
            "run_id": "run-1",
            "prompt_count": 3,
            "copy_generation": {
                "prompt_ids": ["prompt-1", "prompt-2", "prompt-3"]
            },
        }
        prompts.delete_one.return_value = SimpleNamespace(deleted_count=1)

        with patch("dashboard.backend.routes.runs.get_sync_db", return_value=db):
            result = delete_run_prompt_metadata(
                "run-1", "prompt-1", self.request()
            )

        prompts.delete_one.assert_called_once_with(
            {
                "user_id": "user-1",
                "run_id": "run-1",
                "prompt_id": "prompt-1",
            }
        )
        runs.update_one.assert_called_once_with(
            {"user_id": "user-1", "run_id": "run-1"},
            {
                "$set": {"prompt_count": 2, "updated_at": ANY},
                "$addToSet": {"deleted_prompt_ids": "prompt-1"},
                "$pull": {"copy_generation.prompt_ids": "prompt-1"},
            },
        )
        self.assertEqual(result["deleted"], 1)

    def test_local_run_reconciliation_cannot_touch_other_users_or_owners(self) -> None:
        from dashboard.backend.db.collections import (
            COLL_AGENT_JOBS,
            COLL_AGENTS,
            COLL_IMAGES,
            COLL_PROMPTS,
            COLL_RUNS,
        )
        from dashboard.backend.routes.runs import reconcile_local_runs

        db = MagicMock()
        collections = {
            name: MagicMock()
            for name in (
                COLL_AGENT_JOBS,
                COLL_AGENTS,
                COLL_IMAGES,
                COLL_PROMPTS,
                COLL_RUNS,
            )
        }
        db.__getitem__.side_effect = collections.__getitem__
        collections[COLL_AGENTS].find_one.return_value = {
            "agent_id": "agent-1",
            "device_id": "dev_" + "a" * 32,
        }
        collections[COLL_RUNS].find.return_value = [
            {"run_id": "run-local"},
            {"run_id": "run-stale"},
        ]

        payload = {
            "agent_id": "agent-1",
            "device_id": "dev_" + "a" * 32,
            "owner_type": "user",
            "owner_id": "user-1",
            "local_run_ids": ["run-local"],
        }
        with (
            patch("dashboard.backend.routes.runs.get_sync_db", return_value=db),
            patch("dashboard.backend.routes.runs.time.time", return_value=1_000.0),
        ):
            result = reconcile_local_runs(self.request(), payload)

        run_query = collections[COLL_RUNS].find.call_args.args[0]
        self.assertEqual(run_query["user_id"], "user-1")
        self.assertEqual(run_query["owner_type"], "user")
        self.assertEqual(run_query["owner_id"], "user-1")
        self.assertEqual(run_query["device_id"], payload["device_id"])
        exact_scope = {
            "user_id": "user-1",
            "run_id": {"$in": ["run-stale"]},
        }
        collections[COLL_RUNS].delete_many.assert_called_once_with(
            {**exact_scope, "device_id": payload["device_id"]}
        )
        collections[COLL_PROMPTS].delete_many.assert_called_once_with(exact_scope)
        collections[COLL_IMAGES].delete_many.assert_called_once_with(exact_scope)
        collections[COLL_AGENT_JOBS].delete_many.assert_called_once_with(exact_scope)
        self.assertEqual(result, {"removed": 1, "run_ids": ["run-stale"]})

    def test_agent_prompt_reconciliation_is_pinned_to_agent_device(self) -> None:
        from dashboard.backend.agent.routes import reconcile_deleted_prompt
        from dashboard.backend.db.collections import COLL_PROMPTS, COLL_RUNS

        db = MagicMock()
        runs = MagicMock()
        prompts = MagicMock()
        db.__getitem__.side_effect = lambda name: {
            COLL_RUNS: runs,
            COLL_PROMPTS: prompts,
        }[name]
        runs.find_one.return_value = {
            "run_id": "run-1",
            "prompt_count": 1,
            "copy_generation": {
                "prompt_ids": ["prompt-1"],
                "prompt_resource_ids": ["resource-1"],
            },
        }
        prompts.delete_one.return_value = SimpleNamespace(deleted_count=1)
        agent = {
            "user_id": "user-1",
            "agent_id": "agent-1",
            "device_id": "dev_" + "a" * 32,
        }

        with patch(
            "dashboard.backend.db.client.get_sync_db", return_value=db
        ):
            result = reconcile_deleted_prompt(
                {
                    "run_id": "run-1",
                    "prompt_id": "prompt-1",
                    "resource_id": "resource-1",
                },
                agent,
            )

        runs.find_one.assert_called_once_with(
            {
                "run_id": "run-1",
                "user_id": "user-1",
                "agent_id": "agent-1",
                "device_id": "dev_" + "a" * 32,
            },
            {
                "_id": 0,
                "run_id": 1,
                "prompt_count": 1,
                "copy_generation": 1,
                "deleted_prompt_ids": 1,
            },
        )
        prompts.delete_one.assert_called_once_with(
            {
                "user_id": "user-1",
                "run_id": "run-1",
                "prompt_id": "prompt-1",
            }
        )
        self.assertEqual(result["status"], "accepted")


if __name__ == "__main__":
    unittest.main()
