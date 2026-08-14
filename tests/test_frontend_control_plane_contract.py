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
        self.assertIn("fetchJSON(effectiveConfigUrl())", reference)
        self.assertIn("/api/config/effective", reference)
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
        self.assertIn('fetchJSON("/api/runs/reconcile-local"', runs)
        self.assertIn("/api/runs?flow=${encodeURIComponent(flow)}", runs)
        self.assertIn("runMatchesFlow", runs)
        self.assertIn("Reference Image Flow", runs)
        self.assertIn("isReferenceRun", runs)
        self.assertNotIn("prompts ${run.prompt_count}", runs)
        self.assertIn("confirm: true", runs)
        self.assertIn("removeMissingRuns", runs)
        self.assertNotIn("Hidden ${inventory.hidden}", runs)

    def test_studio_org_and_copy_pipeline_persist_across_reload(self) -> None:
        main = (FRONTEND / "js" / "main.js").read_text(encoding="utf-8")
        state_js = (FRONTEND / "js" / "state.js").read_text(encoding="utf-8")
        config_js = (FRONTEND / "js" / "config.js").read_text(encoding="utf-8")
        self.assertIn("adFactoryStudioOrg", main)
        self.assertIn("adFactoryCopyPipeline", main)
        self.assertIn("restoreStudioOrg()", main)
        self.assertIn("persistStudioOrg()", main)
        self.assertIn("persistCopyPipeline(", main)
        self.assertIn("resumeCopyPipeline()", main)
        self.assertIn('loadDefaults(studioCurrentOrgId || "")', main)
        self.assertIn("org_id=${encodeURIComponent(orgId)}", state_js)
        self.assertIn("adFactoryStudioOrg:${userId}", config_js)

    def test_copy_provider_errors_are_sticky_and_fallback_is_logged(self) -> None:
        main = (FRONTEND / "js" / "main.js").read_text(encoding="utf-8")
        jobs = (
            ROOT / "dashboard" / "backend" / "services" / "render_copy_jobs.py"
        ).read_text(encoding="utf-8")
        self.assertIn("job.last_error", main)
        self.assertIn("appendLog(job.last_error)", main)
        self.assertIn("Falling back to", jobs)
        self.assertIn("copy_generation.last_error", jobs)
        self.assertIn("next_free_opencode_model", jobs)

    def test_editable_fields_readme_covers_studio_and_config_keys(self) -> None:
        readme = (ROOT / "DASHBOARD_EDITABLE_FIELDS.md").read_text(encoding="utf-8")
        for needle in (
            "visual_archetypes",
            "headline_architectures",
            "reference_starting_prompt",
            "reference_product_master_doc",
            "Copy to Org",
            "ad-factory-agent/config/agent.json",
            "--session-cookie",
            "persona_seeds",
            "copy_prompt_templates",
            "copy_architecture",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, readme)

    def test_local_cas_blobs_are_cached_by_resource_version(self) -> None:
        plane = (FRONTEND / "js" / "local-data-plane.js").read_text(encoding="utf-8")
        self.assertIn('CAS_CACHE_NAME = "ad-factory-local-cas"', plane)
        self.assertIn("cachedObjectUrl(", plane)
        self.assertIn("cachedText(", plane)
        self.assertIn("200 * 1024 * 1024", plane)
        self.assertNotIn(
            '/content",\n      { method: "GET", cache: "no-store" }',
            plane,
        )

    def test_user_facing_pages_hide_mongo_ids(self) -> None:
        profile = (FRONTEND / "js" / "profile.js").read_text(encoding="utf-8")
        config = (FRONTEND / "js" / "config.js").read_text(encoding="utf-8")
        orgs = (FRONTEND / "organizations.html").read_text(encoding="utf-8")
        org_routes = (ROOT / "dashboard" / "backend" / "services" / "org_routes.py").read_text(
            encoding="utf-8"
        )
        versions = (
            ROOT / "dashboard" / "backend" / "services" / "config_version_service.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("ID: <code>${escapeHtml(user.user_id", profile)
        self.assertNotIn("Config ID:", config)
        self.assertNotIn("ID: ${esc(m.user_id)}", orgs)
        self.assertNotIn("${esc(org.org_id)}</code>", orgs)
        self.assertIn('"display_name"', org_routes)
        self.assertIn("changed_by_display_name", versions)
        self.assertIn("changed_by_display_name", config)

    def test_studio_init_does_not_block_on_local_assets_and_pages_show_skeletons(self) -> None:
        main = (FRONTEND / "js" / "main.js").read_text(encoding="utf-8")
        ui = (FRONTEND / "js" / "ui.js").read_text(encoding="utf-8")
        config = (FRONTEND / "js" / "config.js").read_text(encoding="utf-8")
        profile = (FRONTEND / "js" / "profile.js").read_text(encoding="utf-8")
        traces = (FRONTEND / "traces.html").read_text(encoding="utf-8")
        orgs = (FRONTEND / "organizations.html").read_text(encoding="utf-8")
        init_start = main.index("async function initDefaults()")
        init_end = main.index("\nfunction populateGoogleModels", init_start)
        init_defaults = main[init_start:init_end]
        self.assertIn("loadStructuredAssets().then(", init_defaults)
        self.assertNotIn("await loadStructuredAssets(", init_defaults)
        self.assertIn("showElementSkeleton", init_defaults)
        self.assertIn("export function skeletonBlock", ui)
        self.assertIn("export function showElementSkeleton", ui)
        self.assertIn("showElementSkeleton(document.getElementById(\"cfgEditors\")", config)
        self.assertIn("showElementSkeleton(panel", profile)
        self.assertIn("page-skeleton", traces)
        self.assertIn("page-skeleton", orgs)


if __name__ == "__main__":
    unittest.main()
