from __future__ import annotations

import re
import unittest
from pathlib import Path


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
    "/api/llm-traces",
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


if __name__ == "__main__":
    unittest.main()
