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
            COLL_FILE_MAP,
            COLL_IMAGES,
            COLL_LLM_TRACES,
            COLL_PROMPT_DELIVERIES,
            COLL_PROMPTS,
            COLL_RENDER_COPY_JOBS,
            COLL_RUN_COUNTERS,
            COLL_RUNS,
        )
        from dashboard.backend.routes.runs import purge_all_user_runs

        db = MagicMock()
        collections = {
            name: MagicMock()
            for name in (
                COLL_AGENT_JOBS,
                COLL_FILE_MAP,
                COLL_IMAGES,
                COLL_LLM_TRACES,
                COLL_PROMPT_DELIVERIES,
                COLL_PROMPTS,
                COLL_RENDER_COPY_JOBS,
                COLL_RUNS,
                COLL_RUN_COUNTERS,
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

        for name, collection in collections.items():
            if name == COLL_RUN_COUNTERS:
                collection.delete_many.assert_called_once_with({"owner_id": "user-1"})
            elif name == COLL_FILE_MAP:
                collection.delete_many.assert_called_once_with({"user_id": "user-1"})
            else:
                collection.delete_many.assert_called_once_with({"user_id": "user-1"})
        self.assertEqual(result["status"], "purged")
        self.assertEqual(result["runs"], 2)
        self.assertEqual(result["run_counters"], 2)

    @staticmethod
    def _delete_collections() -> tuple[MagicMock, dict[str, MagicMock]]:
        from dashboard.backend.db.collections import (
            COLL_AGENT_JOBS,
            COLL_FILE_MAP,
            COLL_IMAGES,
            COLL_LLM_TRACES,
            COLL_PROMPT_DELIVERIES,
            COLL_PROMPTS,
            COLL_RENDER_COPY_JOBS,
            COLL_RUN_COUNTERS,
            COLL_RUNS,
        )

        db = MagicMock()
        collections = {
            name: MagicMock()
            for name in (
                COLL_AGENT_JOBS,
                COLL_FILE_MAP,
                COLL_IMAGES,
                COLL_LLM_TRACES,
                COLL_PROMPT_DELIVERIES,
                COLL_PROMPTS,
                COLL_RENDER_COPY_JOBS,
                COLL_RUNS,
                COLL_RUN_COUNTERS,
            )
        }
        db.__getitem__.side_effect = collections.__getitem__
        collections[COLL_RUNS].find.return_value = []
        return db, collections

    def test_run_without_local_device_is_hard_deleted_instead_of_409(self) -> None:
        from dashboard.backend.db.collections import (
            COLL_FILE_MAP,
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
        collections[COLL_FILE_MAP].delete_many.assert_called_once_with({"run_id": "run-orphan"})
        collections[COLL_LLM_TRACES].delete_many.assert_called_once_with({"run_id": "run-orphan"})
        for name in (COLL_PROMPTS, COLL_IMAGES):
            collections[name].delete_many.assert_called_once_with(scope)

    def test_device_bound_run_still_queues_a_local_purge_job(self) -> None:
        from dashboard.backend.db.collections import COLL_FILE_MAP, COLL_RUNS
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

        self.assertEqual(result["status"], "deleted")
        self.assertEqual(result["purge_job_id"], "job-1")
        self.assertEqual(create_job.call_args.kwargs["job_type"], "purge_run")
        collections[COLL_RUNS].delete_one.assert_called_once()
        collections[COLL_FILE_MAP].delete_many.assert_called_once_with({"run_id": "run-bound"})

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

    def test_failed_empty_run_bulk_delete_clears_run_and_traces(self) -> None:
        from dashboard.backend.db.collections import (
            COLL_LLM_TRACES,
            COLL_RENDER_COPY_JOBS,
            COLL_RUN_COUNTERS,
            COLL_RUNS,
        )
        from dashboard.backend.routes.runs import bulk_delete_runs

        class PyMongoLikeDb:
            def __init__(self, collections: dict) -> None:
                self._collections = collections

            def __bool__(self) -> bool:
                raise NotImplementedError(
                    "Database objects do not implement truth value testing "
                    "or bool(). Please compare with None instead: database is not None"
                )

            def __getitem__(self, name: str):
                return self._collections[name]

        db, collections = self._delete_collections()
        collections[COLL_RUNS].find_one.return_value = {
            "run_id": "run-failed",
            "user_id": "user-1",
            "owner_type": "user",
            "owner_id": "user-1",
            "flow_type": "structured",
            "status": "failed",
            "prompt_count": 0,
            "image_count": 0,
            "copy_generation": {
                "status": "failed",
                "error_code": "provider_http_error",
                "last_error": "Invalid API key",
            },
        }
        pymongo_db = PyMongoLikeDb(collections)

        with patch(
            "dashboard.backend.routes.runs.get_sync_db", return_value=pymongo_db
        ):
            result = bulk_delete_runs(
                self.request(), {"run_ids": ["run-failed"]}
            )

        self.assertEqual(result["deleted"], 1)
        self.assertEqual(result["failed"], 0)
        collections[COLL_RUNS].delete_one.assert_called_once_with(
            {"run_id": "run-failed", "user_id": "user-1"}
        )
        collections[COLL_LLM_TRACES].delete_many.assert_called_once_with(
            {"run_id": "run-failed"}
        )
        collections[COLL_RENDER_COPY_JOBS].delete_many.assert_called_once_with(
            {"run_id": "run-failed", "user_id": "user-1"}
        )
        collections[COLL_RUN_COUNTERS].update_one.assert_called_once()

    def test_bulk_delete_keeps_going_when_one_run_errors(self) -> None:
        from dashboard.backend.routes.runs import bulk_delete_runs

        def delete_one(db, *, user_id, run_id):
            del db, user_id
            if run_id == "run-bad":
                raise RuntimeError("boom")
            return {"status": "deleted", "run_id": run_id}

        with (
            patch(
                "dashboard.backend.routes.runs.delete_run_for_user",
                side_effect=delete_one,
            ),
            patch(
                "dashboard.backend.routes.runs.get_sync_db", return_value=MagicMock()
            ),
        ):
            result = bulk_delete_runs(
                self.request(), {"run_ids": ["run-ok", "run-bad"]}
            )

        self.assertEqual(result["deleted"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["results"][1]["run_id"], "run-bad")
        self.assertIn("boom", result["results"][1]["detail"])

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

    def test_failed_execute_run_marks_run_image_generation(self) -> None:
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
            "job_type": "execute_run",
            "run_id": "run-image-1",
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
                    "job-image-1",
                    "agent-1",
                    "dev_" + "a" * 32,
                    1,
                    "evt-fail",
                    "local_execution_failed",
                    "ChatGPT image script was not found next to the local agent",
                )
            )

        runs.update_one.assert_called_once()
        query, update = runs.update_one.call_args.args
        self.assertEqual(query["run_id"], "run-image-1")
        self.assertEqual(update["$set"]["status"], "failed")
        self.assertEqual(update["$set"]["image_generation"]["status"], "failed")
        self.assertEqual(
            update["$set"]["image_generation"]["last_error"],
            "ChatGPT image script was not found next to the local agent",
        )
        self.assertEqual(update["$set"]["image_generation"]["job_id"], "job-image-1")

    def test_list_runs_honors_flow_query(self) -> None:
        from dashboard.backend.db.collections import COLL_RUNS
        from dashboard.backend.routes.runs import list_runs

        db = MagicMock()
        runs = MagicMock()
        db.__getitem__.return_value = runs
        runs.find.return_value.sort.return_value.limit.return_value = []
        request = SimpleNamespace(
            state=SimpleNamespace(user={"user_id": "user-1"}),
            query_params={"flow": "reference"},
        )
        with patch("dashboard.backend.routes.runs.get_sync_db", return_value=db):
            list_runs(request)
        query = runs.find.call_args.args[0]
        self.assertEqual(query["user_id"], "user-1")
        self.assertEqual(query["flow_type"], {"$in": ["reference", "reference_image"]})

        request.query_params = {"flow": "structured"}
        with patch("dashboard.backend.routes.runs.get_sync_db", return_value=db):
            list_runs(request)
        query = runs.find.call_args.args[0]
        self.assertEqual(query["flow_type"], {"$nin": ["reference", "reference_image"]})

        request.query_params = {}
        with patch("dashboard.backend.routes.runs.get_sync_db", return_value=db):
            list_runs(request)
        query = runs.find.call_args.args[0]
        self.assertEqual(query["user_id"], "user-1")
        self.assertNotIn("flow_type", query)

    def test_user_id_falls_back_to_session_cookie(self) -> None:
        from dashboard.backend.routes.runs import _user_id

        request = SimpleNamespace(state=SimpleNamespace(), cookies={"session": "tok"})
        with patch(
            "dashboard.backend.auth.service.get_current_user_from_cookie",
            return_value={"user_id": "user-9"},
        ):
            self.assertEqual(_user_id(request), "user-9")
            self.assertEqual(request.state.user["user_id"], "user-9")

    def test_list_runs_http_attaches_cookie_user(self) -> None:
        from fastapi.testclient import TestClient

        from dashboard.backend import app as app_module

        runs = MagicMock()
        runs.find.return_value.sort.return_value.limit.return_value = []
        db = MagicMock()
        db.__getitem__.return_value = runs
        with patch.object(
            app_module,
            "get_current_user_from_cookie",
            return_value={"user_id": "user-1", "is_active": True},
        ), patch("dashboard.backend.routes.runs.get_sync_db", return_value=db):
            response = TestClient(app_module.app).get(
                "/api/runs",
                cookies={"session": "fake-session"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["runs"], [])

    def test_user_id_without_state_or_cookie_is_401(self) -> None:
        from fastapi import HTTPException

        from dashboard.backend.routes.runs import _user_id

        request = SimpleNamespace(state=SimpleNamespace(), cookies={})
        with patch(
            "dashboard.backend.auth.service.get_current_user_from_cookie",
            return_value=None,
        ):
            with self.assertRaises(HTTPException) as raised:
                _user_id(request)
        self.assertEqual(raised.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
