from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "dashboard" / "frontend"

SHIPPED_SOURCES = tuple(
    sorted(
        [
            *FRONTEND.glob("*.html"),
            *(FRONTEND / "js").glob("*.js"),
        ]
    )
)

FORBIDDEN_RENDER_CONTENT_ROUTES = (
    "/api/batch/generate-images-",
    "/api/files/download/",
    "/api/kill-chrome",
    "/api/progress/",
    "/api/prompt-file-content",
    "/api/reference-workspace",
    "/api/runs/cancel-current",
    "/api/runs/download-batches",
    "/api/runs/execute-reference",
    "/api/stop-generation",
    "/api/extension/navigate",
    "/api/extension/command",
    "/api/extension/targets",
    "/api/extension/screenshot",
    "/delete-image",
    "/download-image",
    "/mark-images-to-regenerate",
    "/regenerate-queued-images",
    "/replace-image",
    "/restore-images-from-queue",
    "/revise-image",
)


class FrontendControlPlaneContractTests(unittest.TestCase):
    def test_shipped_frontend_never_calls_retired_render_content_routes(self) -> None:
        failures: list[str] = []
        for path in SHIPPED_SOURCES:
            source = path.read_text(encoding="utf-8")
            for route in FORBIDDEN_RENDER_CONTENT_ROUTES:
                if route in source:
                    failures.append(f"{path.relative_to(ROOT)} still contains {route}")
        self.assertEqual(failures, [], "\n".join(failures))

    def test_run_ui_uses_metadata_projection_not_legacy_manifest_fields(self) -> None:
        source = (FRONTEND / "js" / "runs.js").read_text(encoding="utf-8")
        forbidden = (
            r"\.prompt_files\.length",
            r"\.image_files\.length",
            r"\brun\.batch\b",
            r"\br\.batch\b",
        )
        for pattern in forbidden:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, source))
        self.assertIn("run.prompt_count", source)
        self.assertIn("run.image_count", source)
        self.assertIn("display_batch", source)

    def test_agent_projection_is_a_bearer_authenticated_runtime_path(self) -> None:
        from dashboard.backend.agent.auth import is_agent_runtime_path

        self.assertTrue(
            is_agent_runtime_path("/api/agents/jobs/job_123/projection")
        )

    def test_job_status_reports_control_plane_failure(self) -> None:
        from fastapi import HTTPException
        from pymongo.errors import ServerSelectionTimeoutError

        from dashboard.backend.routes.batch import _batch_job_status

        with patch(
            "dashboard.backend.routes.batch.get_sync_db",
            side_effect=ServerSelectionTimeoutError("unavailable"),
        ):
            with self.assertRaises(HTTPException) as raised:
                _batch_job_status("", {"user_id": "usr_1"})
        self.assertEqual(raised.exception.status_code, 503)

    def test_pairing_recovers_from_local_reset_and_logout_clears_sessions(self) -> None:
        local_client = (
            FRONTEND / "js" / "local-data-plane.js"
        ).read_text(encoding="utf-8")
        auth = (FRONTEND / "js" / "auth.js").read_text(encoding="utf-8")
        main = (FRONTEND / "js" / "main.js").read_text(encoding="utf-8")

        self.assertIn('"invalid_session"', local_client)
        self.assertIn("await this.ensurePaired(", local_client)
        allocate = local_client.index("async allocateLocalRun(")
        self.assertLess(
            local_client.index("await this.ensurePaired(", allocate),
            local_client.index("await this.allocateRun(", allocate),
        )
        self.assertIn("clearLocalPairingSessions();", auth)
        self.assertIn("loadStructuredAssets({ silent: false })", main)

    def test_structured_copy_runs_on_render_and_reference_hydration_stays_local(self) -> None:
        main = (FRONTEND / "js" / "main.js").read_text(encoding="utf-8")
        runs = (FRONTEND / "js" / "runs.js").read_text(encoding="utf-8")
        reference = (
            FRONTEND / "js" / "reference-flow.js"
        ).read_text(encoding="utf-8")

        pipeline_start = main.index("async function runPipeline()")
        pipeline_end = main.index(
            '\n}\n\n\ndocument.getElementById("cancelRunBtn")',
            pipeline_start,
        )
        pipeline = main[pipeline_start:pipeline_end]
        self.assertIn('fetchJSON("/api/runs/allocate-copy"', pipeline)
        self.assertIn("/structured-copy", pipeline)
        self.assertNotIn("ensureStructuredLocal()", pipeline)
        self.assertNotIn("/materialize", pipeline)
        self.assertNotIn("putProviderConfig(", pipeline)
        self.assertNotIn('putText("configs"', pipeline)
        self.assertNotIn('putText("documents"', pipeline)
        self.assertNotIn("Add at least one product image", main)
        self.assertIn('fetchJSON("/api/config/effective")', reference)
        api = (FRONTEND / "js" / "api.js").read_text(encoding="utf-8")
        self.assertIn('cache: "no-store"', api)
        self.assertIn('credentials: "same-origin"', api)
        self.assertIn("if (referenceRunInFlight) return;", reference)
        self.assertLess(
            reference.index("referenceRunInFlight = true;"),
            reference.index("await refreshReferencePersonas();"),
        )
        self.assertIn("references: referenceDeclarations", reference)
        self.assertIn("reconciledProductIds", reference)
        self.assertIn("await localDataPlane.listRuns(", runs)
        self.assertIn("/api/runs?flow=${encodeURIComponent(flow)}", runs)
        self.assertIn("runMatchesFlow", runs)
        self.assertIn('fetchJSON("/api/runs/reconcile-local"', runs)
        self.assertIn("confirm: true", runs)
        self.assertIn("removeMissingRuns", runs)
        self.assertNotIn("Hidden ${inventory.hidden}", runs)


if __name__ == "__main__":
    unittest.main()
