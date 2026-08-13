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
            COLL_LLM_TRACES,
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
                COLL_LLM_TRACES,
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
            "confirm": True,
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
        collections[COLL_LLM_TRACES].delete_many.assert_called_once_with(exact_scope)
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

    def test_local_run_reconciliation_is_dry_run_until_confirmed(self) -> None:
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

        with (
            patch("dashboard.backend.routes.runs.get_sync_db", return_value=db),
            patch("dashboard.backend.routes.runs.time.time", return_value=1_000.0),
        ):
            result = reconcile_local_runs(
                self.request(),
                {
                    "agent_id": "agent-1",
                    "device_id": "dev_" + "a" * 32,
                    "owner_type": "user",
                    "owner_id": "user-1",
                    "local_run_ids": ["run-local"],
                },
            )

        collections[COLL_RUNS].delete_many.assert_not_called()
        collections[COLL_PROMPTS].delete_many.assert_not_called()
        self.assertEqual(result["removed"], 0)
        self.assertEqual(result["pending"], ["run-stale"])

    def test_purge_all_is_user_scoped_and_requires_typed_confirmation(self) -> None:
        from fastapi import HTTPException

        from dashboard.backend.db.collections import (
            COLL_AGENT_JOBS,
            COLL_IMAGES,
            COLL_LLM_TRACES,
            COLL_PROMPT_DELIVERIES,
            COLL_PROMPTS,
            COLL_RENDER_COPY_JOBS,
            COLL_RUNS,
        )
        from dashboard.backend.routes.runs import purge_all_user_runs

        db = MagicMock()
        collections = {
            name: MagicMock()
            for name in (
                COLL_AGENT_JOBS,
                COLL_IMAGES,
                COLL_LLM_TRACES,
                COLL_PROMPT_DELIVERIES,
                COLL_PROMPTS,
                COLL_RENDER_COPY_JOBS,
                COLL_RUNS,
            )
        }
        for collection in collections.values():
            collection.delete_many.return_value = SimpleNamespace(deleted_count=2)
        db.__getitem__.side_effect = collections.__getitem__

        with self.assertRaises(HTTPException) as raised:
            with patch("dashboard.backend.routes.runs.get_sync_db", return_value=db):
                purge_all_user_runs(self.request(), {})
        self.assertEqual(raised.exception.status_code, 400)

        with patch("dashboard.backend.routes.runs.get_sync_db", return_value=db):
            result = purge_all_user_runs(self.request(), {"confirm": "PURGE"})

        for name in collections:
            collections[name].delete_many.assert_called_once_with({"user_id": "user-1"})
        self.assertEqual(result["status"], "purged")
        self.assertEqual(result["runs"], 2)

    @staticmethod
    def _delete_collections() -> tuple[MagicMock, dict[str, MagicMock]]:
        from dashboard.backend.db.collections import (
            COLL_AGENT_JOBS,
            COLL_IMAGES,
            COLL_LLM_TRACES,
            COLL_PROMPT_DELIVERIES,
            COLL_PROMPTS,
            COLL_RENDER_COPY_JOBS,
            COLL_RUNS,
        )

        db = MagicMock()
        collections = {
            name: MagicMock()
            for name in (
                COLL_AGENT_JOBS,
                COLL_IMAGES,
                COLL_LLM_TRACES,
                COLL_PROMPT_DELIVERIES,
                COLL_PROMPTS,
                COLL_RENDER_COPY_JOBS,
                COLL_RUNS,
            )
        }
        db.__getitem__.side_effect = collections.__getitem__
        return db, collections

    def test_run_without_local_device_is_hard_deleted_instead_of_409(self) -> None:
        from dashboard.backend.db.collections import (
            COLL_IMAGES,
            COLL_LLM_TRACES,
            COLL_PROMPTS,
            COLL_RUNS,
        )
        from dashboard.backend.routes.runs import delete_run

        db, collections = self._delete_collections()
        collections[COLL_RUNS].find_one.return_value = {
            "run_id": "run-orphan",
            "status": "copy_ready",
        }

        with patch("dashboard.backend.routes.runs.get_sync_db", return_value=db):
            result = delete_run("run-orphan", self.request())

        scope = {"run_id": "run-orphan", "user_id": "user-1"}
        self.assertEqual(result, {"status": "deleted", "run_id": "run-orphan"})
        collections[COLL_RUNS].delete_one.assert_called_once_with(scope)
        for name in (COLL_PROMPTS, COLL_IMAGES, COLL_LLM_TRACES):
            collections[name].delete_many.assert_called_once_with(scope)

    def test_device_bound_run_still_queues_a_local_purge_job(self) -> None:
        from dashboard.backend.db.collections import COLL_RUNS
        from dashboard.backend.routes.runs import delete_run

        db, collections = self._delete_collections()
        collections[COLL_RUNS].find_one.return_value = {
            "run_id": "run-bound",
            "agent_id": "agent-1",
            "device_id": "dev_" + "a" * 32,
            "owner_type": "user",
            "owner_id": "user-1",
            "status": "copy_completed",
        }

        with (
            patch("dashboard.backend.routes.runs.get_sync_db", return_value=db),
            patch(
                "dashboard.backend.routes.runs.create_job",
                return_value={"job_id": "job-1"},
            ) as create_job,
        ):
            result = delete_run("run-bound", self.request())

        self.assertEqual(result["status"], "deleting")
        self.assertEqual(result["purge_job_id"], "job-1")
        self.assertEqual(create_job.call_args.kwargs["job_type"], "purge_run")
        collections[COLL_RUNS].delete_one.assert_not_called()

    def test_bulk_delete_is_user_scoped_and_deduplicates_run_ids(self) -> None:
        from fastapi import HTTPException

        from dashboard.backend.routes.runs import bulk_delete_runs

        with patch(
            "dashboard.backend.routes.runs.delete_run_for_user",
            side_effect=lambda db, *, user_id, run_id: {
                "status": "deleted",
                "run_id": run_id,
            },
        ) as delete_one:
            with patch(
                "dashboard.backend.routes.runs.get_sync_db", return_value=MagicMock()
            ):
                result = bulk_delete_runs(
                    self.request(), {"run_ids": ["run-a", "run-b", "run-a"]}
                )

        self.assertEqual(result["deleted"], 2)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(
            [call.kwargs["run_id"] for call in delete_one.call_args_list],
            ["run-a", "run-b"],
        )
        for call in delete_one.call_args_list:
            self.assertEqual(call.kwargs["user_id"], "user-1")

        for payload in ({}, {"run_ids": []}, {"run_ids": ["ok", 7]}):
            with self.assertRaises(HTTPException) as raised:
                bulk_delete_runs(self.request(), payload)
            self.assertEqual(raised.exception.status_code, 400)

    def test_prompt_metadata_patch_is_user_and_run_scoped(self) -> None:
        from dashboard.backend.db.collections import COLL_PROMPTS, COLL_RUNS
        from dashboard.backend.routes.runs import update_run_prompt_metadata

        db = MagicMock()
        runs = MagicMock()
        prompts = MagicMock()
        db.__getitem__.side_effect = lambda name: {
            COLL_RUNS: runs,
            COLL_PROMPTS: prompts,
        }[name]
        runs.find_one.return_value = {"run_id": "run-1"}
        prompts.update_one.return_value = SimpleNamespace(matched_count=1)

        with patch("dashboard.backend.routes.runs.get_sync_db", return_value=db):
            result = update_run_prompt_metadata(
                "run-1",
                "prompt-1",
                self.request(),
                {"sha256": "a" * 64, "resource_version": 3},
            )

        prompts.update_one.assert_called_once()
        query, update = prompts.update_one.call_args.args
        self.assertEqual(
            query,
            {"user_id": "user-1", "run_id": "run-1", "prompt_id": "prompt-1"},
        )
        self.assertEqual(update["$set"]["sha256"], "a" * 64)
        self.assertEqual(update["$set"]["resource_version"], 3)
        self.assertEqual(result["status"], "updated")

    def test_failed_purge_marks_run_visible_for_retry(self) -> None:
        from dashboard.backend.agent.service import fail_job
        from dashboard.backend.db.collections import COLL_AGENT_JOBS, COLL_RUNS

        db = MagicMock()
        jobs = MagicMock()
        runs = MagicMock()
        db.__getitem__.side_effect = lambda name: {
            COLL_AGENT_JOBS: jobs,
            COLL_RUNS: runs,
        }[name]
        jobs.find_one.return_value = {
            "job_type": "purge_run",
            "run_id": "run-1",
            "client_operation_id": "purge_abc",
        }

        with (
            patch(
                "dashboard.backend.agent.service._terminal_job_update",
                return_value=True,
            ),
            patch("dashboard.backend.agent.service.get_sync_db", return_value=db),
        ):
            self.assertTrue(
                fail_job(
                    "job-1",
                    "agent-1",
                    "dev_" + "a" * 32,
                    1,
                    "evt-fail",
                    "local_execution_failed",
                )
            )

        runs.update_one.assert_called_once()
        query, update = runs.update_one.call_args.args
        self.assertEqual(query["run_id"], "run-1")
        self.assertEqual(update["$set"]["status"], "purge_failed")


if __name__ == "__main__":
    unittest.main()
