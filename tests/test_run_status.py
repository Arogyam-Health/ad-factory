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
