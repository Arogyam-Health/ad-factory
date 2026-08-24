from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
REACT_SRC = ROOT / "dashboard" / "web" / "src"

SHIPPED_SOURCES = tuple(
    sorted(
        [
            *REACT_SRC.rglob("*.ts"),
            *REACT_SRC.rglob("*.tsx"),
            *REACT_SRC.rglob("*.js"),
        ]
    )
)

def _read(*parts: str) -> str:
    return (REACT_SRC.joinpath(*parts)).read_text(encoding="utf-8")

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
        source = _read("pages", "Studio.tsx")
        forbidden = (
            r"\.prompt_files\.length",
            r"\.image_files\.length",
            r"\brun\.batch\b",
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
        local_client = _read("lib", "local-data-plane.js")
        auth = _read("lib", "auth.tsx")
        studio = _read("pages", "Studio.tsx")

        self.assertIn('"invalid_session"', local_client)
        self.assertIn("await this.ensurePaired(", local_client)
        allocate = local_client.index("async allocateLocalRun(")
        self.assertLess(
            local_client.index("await this.ensurePaired(", allocate),
            local_client.index("await this.allocateRun(", allocate),
        )
        self.assertIn("clearLocalPairingSessions();", auth)
        self.assertIn("localStorage.getItem(LAST_ACCOUNT_KEY)", auth)
        self.assertIn("localStorage.setItem(LAST_ACCOUNT_KEY, userId)", auth)
        self.assertNotIn("sessionStorage.getItem(LAST_ACCOUNT_KEY)", auth)
        self.assertIn("this._pairedOwner", local_client)
        self.assertIn("ownerKey || this._pairedOwner", local_client)
        self.assertIn("this._pairedOwner || this.activeOwnerKey(deviceId)", local_client)
        self.assertIn("listAssets({ kind: \"product_image\"", studio)

    def test_structured_copy_runs_on_render_and_reference_hydration_stays_local(self) -> None:
        studio = _read("pages", "Studio.tsx")
        reference = _read("pages", "studio", "ReferencePanel.tsx")
        api = _read("lib", "api.ts")

        start = studio.index("async function startStructured()")
        pipeline = studio[start:studio.index("const orgSources", start)]
        self.assertIn("/api/runs/allocate-copy", pipeline)
        self.assertIn("/structured-copy", pipeline)
        self.assertNotIn("ensureStructuredLocal()", pipeline)
        self.assertNotIn("/materialize", pipeline)
        self.assertNotIn("putProviderConfig(", pipeline)
        self.assertNotIn('putText("configs"', pipeline)
        self.assertNotIn('putText("documents"', pipeline)
        self.assertIn("/api/config/effective", studio)
        self.assertIn('cache: "no-store"', api)
        self.assertIn('credentials: "same-origin"', api)
        self.assertIn("references: referenceDeclarations", reference)
        self.assertIn("/api/runs?flow=${flow}", studio)
        self.assertIn("/api/runs?flow=reference", reference)
        self.assertIn("/reference-generation", reference)
        self.assertIn("language_mode: props.language", reference)
        self.assertIn("selected_concept: props.selectedConcept", reference)
        self.assertIn("creative_concept:", reference)
        self.assertIn("catalogConcepts", reference)
        desk = reference[reference.index("export function ReferenceDesk"):]
        self.assertIn("<ConceptSelect", desk)
        self.assertIn("Run reference flow", desk)
        self.assertIn("personas × selected references × language", reference)
        self.assertIn("id=\"googleApiKey\"", studio)
        self.assertIn("<form", studio)
        profile = _read("pages", "Profile.tsx")
        self.assertIn("<form", profile)
        self.assertIn('type="password"', profile)
        self.assertIn('type="submit"', profile)
        self.assertIn("saveConfigFile", _read("lib", "config-keys.ts"))
        self.assertIn("Save file", _read("components", "FileViewer.tsx"))
        self.assertIn("result.notice", _read("components", "FileViewer.tsx"))

    def test_guide_page_and_flexible_formats_are_wired(self) -> None:
        app = _read("App.tsx")
        shell = _read("components", "Shell.tsx")
        studio = _read("pages", "Studio.tsx")
        config = _read("pages", "Config.tsx")
        guide = _read("pages", "Guide.tsx")
        defaults = (
            ROOT / "dashboard" / "backend" / "routes" / "defaults.py"
        ).read_text(encoding="utf-8")
        user_routes = (
            ROOT / "dashboard" / "backend" / "services" / "user_config_routes.py"
        ).read_text(encoding="utf-8")
        org_routes = (
            ROOT / "dashboard" / "backend" / "services" / "org_routes.py"
        ).read_text(encoding="utf-8")
        doc = (ROOT / "docs" / "OPERATOR_PLATE_GUIDE.md").read_text(encoding="utf-8")
        self.assertIn('path="/guide"', app)
        self.assertIn('to: "/guide"', shell)
        self.assertIn("Guide", shell)
        self.assertIn('to="/guide"', studio)
        self.assertIn("catalogFormats", studio)
        self.assertIn("studio?.formats", studio)
        self.assertIn('to="/guide"', config)
        self.assertIn("result.notice", config)
        self.assertIn("/api/guide", guide)
        self.assertIn('"/api/guide"', defaults)
        self.assertIn("format_catalog", defaults)
        self.assertIn('"notice": notice', user_routes)
        self.assertIn("apply_format_archetype_sync", user_routes)
        self.assertIn("apply_format_archetype_sync", org_routes)
        self.assertIn("output_fields", doc)
        self.assertIn("visual archetypes", doc.lower())

    def test_studio_org_and_copy_pipeline_persist_across_reload(self) -> None:
        studio = _read("pages", "Studio.tsx")
        config = _read("lib", "config-keys.ts")
        auth = _read("lib", "auth.tsx")
        api = _read("lib", "api.ts")
        self.assertIn("readStudioOrg", studio)
        self.assertIn("writeStudioOrg", studio)
        self.assertIn("adFactoryCopyPipeline", studio)
        self.assertIn("adFactoryFlowMode", studio)
        self.assertIn("org_id=${encodeURIComponent(orgId)}", studio)
        self.assertIn("/api/defaults?org_id=", studio)
        self.assertIn('kicker="03 · Copy desk"', studio)
        self.assertIn("<OrgConfigChips", studio.split('kicker="03 · Copy desk"')[1])
        self.assertIn('kicker="03 · Hypothesis"', studio)
        self.assertIn('kicker="03 · Business"', studio)
        self.assertIn("tile-business", studio)
        self.assertIn("CONFIG_SECTIONS", config)
        self.assertIn("Hypothesis styles", config)
        self.assertIn("Business rules", config)
        self.assertIn("BUSINESS_CONFIG_KEYS", config)
        self.assertIn("adFactoryStudioOrg:${userId}", config)
        self.assertIn("LAST_STUDIO_ORG_KEY", config)
        self.assertIn("ad_formats", config)
        self.assertIn("ad_languages", config)
        self.assertIn("catalogLanguageModes", config)
        self.assertIn("catalogLanguageModes", studio)
        self.assertIn("ad_guardrails", config)
        self.assertIn("ad_support_shapes", config)
        self.assertIn("copy_starting_prompt", config)
        self.assertIn("visual_archetype_llm_prompt", config)
        self.assertIn("Auto rotate", studio)
        self.assertIn("Leave it to the image model", studio)
        self.assertIn('value="llm_decide"', studio)
        self.assertIn('{ noCache: true }', auth)
        self.assertIn('url.includes("/api/auth/")', api)
        self.assertIn('url.includes("/api/invites/")', api)
        invite = _read("pages", "Invite.tsx")
        self.assertIn('navigate("/", { replace: true })', invite)
        self.assertIn("already been accepted", invite)
        self.assertIn("noCache: true", invite)
        self.assertIn("clearCache()", auth)
        self.assertIn("Save API key", studio)
        self.assertIn("/api/user/provider-config/opencode/catalog", studio)
        self.assertIn("opencode/big-pickle", studio)
        workspace = _read("pages", "studio", "RunWorkspace.tsx")
        gallery = _read("pages", "studio", "OutputGallery.tsx")
        reference = _read("pages", "studio", "ReferencePanel.tsx")
        self.assertIn("Generate 4:5", workspace)
        self.assertIn("Generate 4:5 + 9:16", workspace)
        self.assertIn("Generate revision", gallery)
        self.assertIn("deleteOutput", gallery)
        self.assertIn("outputRawBlob", gallery)
        self.assertIn("Comment & revise", gallery)
        self.assertIn("Optional instruction for only this reference image", reference)
        self.assertIn("SwipeLibrary", reference)
        self.assertIn("scroll-snap-type: x mandatory", _read("styles", "global.css"))
        self.assertIn("displayRunStatus", studio)
        self.assertIn("copyFailureDetail", workspace)
        self.assertIn("Copy failed:", workspace)
        self.assertIn("/image-generation", workspace)
        self.assertIn("Edit on-image copy", workspace)
        self.assertIn("Save on-image copy", workspace)
        self.assertIn("[runId, deviceId, run.device_id, paired, busy, reloadToken, refreshToken]", workspace)
        self.assertIn("localDevice && paired", workspace)
        self.assertIn("Show local images", workspace)
        self.assertIn("replaceExactOnImageCopy", workspace)
        self.assertIn("Select batches", studio)
        self.assertIn("Download batches", studio)
        self.assertIn("DownloadKindDialog", studio)
        self.assertIn("includeRaw", studio)
        self.assertIn("Cropped + raw", _read("components", "DownloadKindDialog.tsx"))
        self.assertIn("DownloadKindDialog", gallery)
        self.assertIn("adFactoryImageEngine", studio)
        self.assertIn("adFactorySelectedConcept", studio)
        self.assertIn("selected_concept: selectedConcept", studio)
        self.assertIn('<option value="">None</option>', studio)
        self.assertIn("toolbar-field", studio)
        self.assertIn(".toolbar-field", _read("styles", "global.css"))
        self.assertNotIn('queueRunImages(id, mode, "chatgpt"', studio)
        self.assertIn("Cancel run", studio)
        self.assertIn("Cancel run", workspace)
        self.assertIn("/api/runs/${encodeURIComponent(runId)}/cancel", reference)
        self.assertIn("setDeskTick((value) => value + 1)", studio)
        self.assertIn("<BatchSelect", studio)
        self.assertIn("Pair local agent", studio)
        self.assertIn("RUNS_PER_PAGE = 5", studio)
        self.assertIn('role="button"', studio)
        self.assertIn("openRunRow", studio)
        self.assertIn("scrollbar-color", _read("styles", "global.css"))
        self.assertIn("effectiveUrl", studio)

    def test_copy_provider_errors_are_sticky_and_fallback_is_logged(self) -> None:
        jobs = (
            ROOT / "dashboard" / "backend" / "services" / "render_copy_jobs.py"
        ).read_text(encoding="utf-8")
        self.assertIn("Falling back to", jobs)
        self.assertIn("copy_generation.last_error", jobs)
        self.assertIn("next_free_opencode_model", jobs)

    def test_editable_fields_readme_covers_studio_and_config_keys(self) -> None:
        readme = (ROOT / "DASHBOARD_EDITABLE_FIELDS.md").read_text(encoding="utf-8")
        for needle in (
            "visual_archetypes",
            "reference_starting_prompt",
            "reference_product_master_doc",
            "Copy to Org",
            "ad-factory-agent/config/agent.json",
            "--session-cookie",
            "persona_seeds",
            "concept",
            "copy_prompt_templates",
            "ad_formats",
            "ad_languages",
            "ad_guardrails",
            "ad_support_shapes",
            "copy_starting_prompt",
            "visual_archetype_llm_prompt",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, readme)

    def test_local_cas_blobs_are_cached_by_resource_version(self) -> None:
        plane = _read("lib", "local-data-plane.js")
        self.assertIn('CAS_CACHE_NAME = "ad-factory-local-cas"', plane)
        self.assertIn("cachedObjectUrl(", plane)
        self.assertIn("cachedText(", plane)
        self.assertIn("200 * 1024 * 1024", plane)
        self.assertNotIn(
            '/content",\n      { method: "GET", cache: "no-store" }',
            plane,
        )

    def test_user_facing_pages_hide_mongo_ids(self) -> None:
        profile = _read("pages", "Profile.tsx")
        config = _read("pages", "Config.tsx")
        orgs = _read("pages", "Organizations.tsx")
        org_routes = (ROOT / "dashboard" / "backend" / "services" / "org_routes.py").read_text(
            encoding="utf-8"
        )
        versions = (
            ROOT / "dashboard" / "backend" / "services" / "config_version_service.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("ID: <code>${escapeHtml(user.user_id", profile)
        self.assertNotIn("Config ID:", config)
        self.assertNotIn("ID: ${esc(m.user_id)}", orgs)
        self.assertIn('"display_name"', org_routes)
        self.assertIn("changed_by_display_name", versions)
        self.assertIn("changed_by_display_name", config)

    def test_config_history_deletes_old_versions_and_personal_saves_overwrite(self) -> None:
        config = _read("pages", "Config.tsx")
        routes = (
            ROOT / "dashboard" / "backend" / "services" / "config_routes.py"
        ).read_text(encoding="utf-8")
        user_config = (
            ROOT / "dashboard" / "backend" / "services" / "user_config.py"
        ).read_text(encoding="utf-8")
        self.assertIn("Delete older versions", config)
        self.assertIn("prune-old-versions", config)
        self.assertIn('method: "DELETE"', config)
        self.assertIn("Save version", config)
        self.assertIn("create_version and owner_type == \"org\"", user_config)
        self.assertIn("def delete_config_version", routes)
        self.assertIn("def prune_old_config_versions", routes)

    def test_studio_init_does_not_block_on_local_assets_and_pages_show_skeletons(self) -> None:
        studio = _read("pages", "Studio.tsx")
        config = _read("pages", "Config.tsx")
        skeleton = _read("components", "Skeleton.tsx")
        self.assertIn("localDataPlane", studio)
        self.assertIn("restoreStoredSession", studio)
        self.assertIn("localDataPlane.discover()", studio)
        self.assertIn("ensurePaired", studio.split("async function pairLocalAgent")[1])
        self.assertIn("Skeleton", studio)
        self.assertIn("SkeletonLines", config)
        self.assertIn("export function Skeleton", skeleton)


if __name__ == "__main__":
    unittest.main()
