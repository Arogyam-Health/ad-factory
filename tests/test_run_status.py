from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from dashboard.backend.routes.runs import public_run_status


class RunStatusTests(TestCase):
    def test_queued_run_with_images_reads_completed(self) -> None:
        self.assertEqual(
            public_run_status({"status": "queued", "image_count": 2}),
            "completed",
        )

    def test_copy_ready_run_without_images_stays_copy_completed(self) -> None:
        self.assertEqual(
            public_run_status({"status": "copy_completed", "image_count": 0}),
            "copy_completed",
        )

    def test_image_job_running_reads_generating(self) -> None:
        self.assertEqual(
            public_run_status({
                "status": "queued",
                "image_count": 0,
                "image_generation": {"status": "running"},
            }),
            "generating",
        )

    def test_list_runs_presents_completed_instead_of_stale_queued(self) -> None:
        from dashboard.backend.routes.runs import list_runs

        db = MagicMock()
        runs = MagicMock()
        db.__getitem__.return_value = runs
        runs.find.return_value.sort.return_value.limit.return_value = [
            {"run_id": "run-1", "status": "queued", "image_count": 2},
            {"run_id": "run-2", "status": "copy_completed", "image_count": 0},
        ]
        request = SimpleNamespace(
            state=SimpleNamespace(user={"user_id": "user-1"}),
            query_params={},
        )
        with patch("dashboard.backend.routes.runs.get_sync_db", return_value=db):
            payload = list_runs(request)
        self.assertEqual(payload["runs"][0]["status"], "completed")
        self.assertEqual(payload["runs"][1]["status"], "copy_completed")

    def test_cancel_run_stops_image_job_and_copy_job(self) -> None:
        from dashboard.backend.db.collections import COLL_AGENT_JOBS
        from dashboard.backend.routes.runs import cancel_run

        db = MagicMock()
        jobs = MagicMock()
        runs = MagicMock()
        db.__getitem__.side_effect = lambda name: jobs if name == COLL_AGENT_JOBS else runs
        jobs.find_one.return_value = {"job_id": "job-1"}
        request = SimpleNamespace(state=SimpleNamespace(user={"user_id": "user-1"}))
        with (
            patch("dashboard.backend.routes.runs.get_sync_db", return_value=db),
            patch(
                "dashboard.backend.routes.runs.cancel_user_job",
                return_value={"status": "cancel_requested"},
            ) as cancel_job,
            patch(
                "dashboard.backend.services.render_copy_jobs.cancel_render_copy_run",
                return_value={"status": "canceled", "run_id": "run-1"},
            ) as cancel_copy,
        ):
            payload = cancel_run("run-1", request)
        cancel_job.assert_called_once_with("user-1", "job-1")
        cancel_copy.assert_called_once_with("run-1", "user-1")
        runs.update_one.assert_called_once()
        self.assertEqual(payload["run_id"], "run-1")
        self.assertTrue(payload["job"])
        self.assertTrue(payload["copy"])
        self.assertEqual(payload["status"], "cancel_requested")

    def test_cancel_run_404s_when_nothing_is_active(self) -> None:
        from fastapi import HTTPException

        from dashboard.backend.routes.runs import cancel_run

        db = MagicMock()
        db.__getitem__.return_value.find_one.return_value = None
        request = SimpleNamespace(state=SimpleNamespace(user={"user_id": "user-1"}))
        with (
            patch("dashboard.backend.routes.runs.get_sync_db", return_value=db),
            patch(
                "dashboard.backend.services.render_copy_jobs.cancel_render_copy_run",
                return_value=None,
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                cancel_run("run-missing", request)
        self.assertEqual(raised.exception.status_code, 404)
