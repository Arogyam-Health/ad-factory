from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEGACY_CONTENT_REQUESTS = (
    ("GET", "/api/defaults"),
    ("GET", "/api/google/models"),
    ("GET", "/api/input-images"),
    ("GET", "/api/input-prompt"),
    ("GET", "/api/opencode/catalog"),
    ("GET", "/api/product-doc"),
    ("GET", "/api/prompt-file-content"),
    ("POST", "/api/runs/cancel-current"),
    ("GET", "/api/runs/download-batches"),
    ("POST", "/api/runs/execute"),
    ("GET", "/api/storage/info"),
    ("POST", "/api/upload-input-images"),
    ("PUT", "/api/user/config"),
    ("GET", "/api/user/json-blobs/bootstrap"),
    ("GET", "/api/admin/configs"),
    ("GET", "/api/admin/provider-configs"),
    ("GET", "/api/config/example"),
    ("GET", "/api/file-content/example"),
    ("GET", "/api/files/download/image/example"),
    ("GET", "/api/generic-config"),
    ("GET", "/api/llm-traces/example"),
    ("GET", "/api/reference-images"),
    ("GET", "/api/reference-workspace"),
    ("GET", "/api/seeds"),
    ("GET", "/api/user/json-blobs/example"),
    ("POST", "/api/batch/generate-images-both"),
    ("PUT", "/api/user/provider-config/google"),
    ("PUT", "/api/orgs/org_1/config"),
    ("GET", "/api/orgs/org_1/configs/shared"),
    ("POST", "/api/runs/execute-reference"),
    ("GET", "/api/runs/run_1/content"),
    ("DELETE", "/api/runs/run_1/delete-image"),
    ("DELETE", "/api/runs/run_1/delete-prompt"),
    ("GET", "/api/runs/run_1/download-batch"),
    ("GET", "/api/runs/run_1/download-image"),
    ("PUT", "/api/runs/run_1/edit-prompt"),
    ("GET", "/api/runs/run_1/export-on-image-copy"),
    ("POST", "/api/runs/run_1/generate-916"),
    ("POST", "/api/runs/run_1/generate-916-selected"),
    ("POST", "/api/runs/run_1/generate-images-45"),
    ("POST", "/api/runs/run_1/generate-images-916-from-45"),
    ("POST", "/api/runs/run_1/import-on-image-copy"),
    ("POST", "/api/runs/run_1/mark-images-to-regenerate"),
    ("GET", "/api/runs/run_1/prompt-copies"),
    ("POST", "/api/runs/run_1/regenerate-queued-images"),
    ("POST", "/api/runs/run_1/replace-image"),
    ("POST", "/api/runs/run_1/restore-images-from-queue"),
    ("POST", "/api/runs/run_1/revise-image"),
)


class StatelessRenderControlPlaneTests(unittest.TestCase):
    def test_only_frontend_static_assets_are_mounted(self) -> None:
        source = (ROOT / "dashboard/backend/control_app.py").read_text(
            encoding="utf-8"
        )
        for route in ("/generated_images", "/output", "/storage", "/input"):
            self.assertNotIn(f'app.mount("{route}"', source)
        self.assertIn('app.mount("/", StaticFiles(', source)

    def test_startup_does_not_create_or_scan_runtime_content(self) -> None:
        source = (ROOT / "dashboard/backend/control_app.py").read_text(
            encoding="utf-8"
        )
        startup = source[source.index("def startup()"):source.index("@app.get(\"/healthz\")")]
        self.assertNotIn("ensure_dirs()", startup)
        self.assertNotIn("_build_opencode_catalog_cached", startup)

    def test_render_manifest_declares_no_content_storage_provider(self) -> None:
        source = (ROOT / "render.yaml").read_text(encoding="utf-8").lower()
        self.assertNotIn("storage_provider", source)
        self.assertNotIn("cloudinary", source)
        self.assertNotIn("disk:", source)
        self.assertIn("dashboard.backend.control_app:app", source)

    def test_legacy_content_routes_are_explicitly_blocked(self) -> None:
        policy = importlib.import_module(
            "dashboard.backend.control_plane_policy"
        )
        for method, path in LEGACY_CONTENT_REQUESTS:
            self.assertTrue(
                policy.is_render_content_route(method, path),
                f"{method} {path} must be blocked",
            )
        self.assertFalse(policy.is_render_content_route("GET", "/api/runs"))
        self.assertFalse(policy.is_render_content_route("POST", "/api/runs/allocate"))
        self.assertFalse(
            policy.is_render_content_route(
                "POST", "/api/runs/run_1/reference-generation"
            )
        )

    def test_app_control_surface_writes_nothing_with_read_only_content_dirs(
        self,
    ) -> None:
        from fastapi.testclient import TestClient
        from dashboard.backend import control_app as app_module

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directories = {
                name: root / name
                for name in ("generated_images", "output", "storage", "input")
            }
            for directory in directories.values():
                directory.mkdir()
                os.chmod(directory, 0o555)
            before = {
                name: (directory.stat().st_mtime_ns, tuple(directory.iterdir()))
                for name, directory in directories.items()
            }
            original_auth = app_module.get_current_user_from_cookie
            try:
                app_module.get_current_user_from_cookie = lambda _token: {
                    "user_id": "usr_test"
                }
                client = TestClient(app_module.app)
                self.assertEqual(client.get("/healthz").status_code, 200)
                self.assertEqual(client.get("/api/version").status_code, 200)
                for method, path in LEGACY_CONTENT_REQUESTS:
                    response = client.request(
                        method,
                        path,
                        cookies={"session": "test"},
                        json={} if method in {"POST", "PUT"} else None,
                    )
                    self.assertEqual(response.status_code, 410, f"{method} {path}")
            finally:
                app_module.get_current_user_from_cookie = original_auth
                for directory in directories.values():
                    os.chmod(directory, 0o755)
            after = {
                name: (directory.stat().st_mtime_ns, tuple(directory.iterdir()))
                for name, directory in directories.items()
            }
            self.assertEqual(before, after)

    def test_mongo_metadata_validator_rejects_content_fields(self) -> None:
        policy = importlib.import_module(
            "dashboard.backend.control_plane_policy"
        )
        forbidden = (
            {"content": "prompt body"},
            {"files": {"product_master_doc": {"content": "body"}}},
            {"payload": {"image_base64": "AAAA"}},
            {"config": {"api_key": "secret"}},
            {"local_path": "/tmp/output.png"},
            {"url": "http://127.0.0.1:8765/resource"},
            {"comment": "revision instruction"},
            {"document_body": "full product document"},
            {"config_body": "full user configuration"},
            {"llm_request": {"messages": ["full request"]}},
            {"llm_response": {"text": "full response"}},
            {"local_capability": "permanent-local-capability"},
            {"absolute_local_path": r"C:\Users\owner\output.png"},
            {"browser_log": "raw browser console output"},
        )
        for document in forbidden:
            with self.assertRaises(ValueError):
                policy.validate_metadata_document("runs", document)

    def test_readyz_reports_no_control_plane_content_storage(self) -> None:
        from unittest.mock import patch

        from fastapi.testclient import TestClient
        from dashboard.backend import control_app as app_module

        class _DB:
            @staticmethod
            def command(name: str) -> dict[str, int]:
                return {"ok": 1} if name == "ping" else {}

        with (
            patch("dashboard.backend.db.client.get_sync_db", return_value=_DB()),
            patch.object(
                app_module,
                "get_current_user_from_cookie",
                return_value={"user_id": "usr_readyz"},
            ),
        ):
            response = TestClient(app_module.app).get(
                "/api/readyz", cookies={"session": "test"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "ready", "mongodb": True, "content_storage": False},
        )

    def test_mongo_metadata_validator_accepts_target_projections(self) -> None:
        policy = importlib.import_module(
            "dashboard.backend.control_plane_policy"
        )
        documents = {
            "runs": {
                "run_id": "run_1",
                "owner_type": "user",
                "owner_id": "usr_1",
                "device_id": "dev_0123456789abcdef0123456789abcdef",
                "status": "queued",
                "prompt_count": 4,
            },
            "prompts": {
                "prompt_id": "prm_1",
                "run_id": "run_1",
                "resource_id": "res_1",
                "resource_version": 2,
                "sha256": "a" * 64,
                "status": "ready",
            },
            "images": {
                "artifact_id": "art_1",
                "run_id": "run_1",
                "resource_id": "res_2",
                "resource_version": 1,
                "bytes": 12,
                "status": "available",
            },
            "agent_jobs": {
                "job_id": "job_1",
                "run_id": "run_1",
                "command": "generate_images",
                "parameters": {"engine": "gemini", "mode": "both"},
                "status": "pending",
            },
            "audit_logs": {
                "event_id": "evt_1",
                "event_type": "run_queued",
                "target_type": "run",
                "target_id": "run_1",
                "metadata": {"count": 4, "status": "queued"},
            },
        }
        for collection, document in documents.items():
            self.assertEqual(
                policy.validate_metadata_document(collection, document), document
            )

    def test_metadata_only_route_modules_do_not_import_direct_workers(self) -> None:
        paths = (
            "dashboard/backend/routes/defaults.py",
            "dashboard/backend/routes/execute.py",
            "dashboard/backend/routes/generate.py",
            "dashboard/backend/routes/batch.py",
            "dashboard/backend/routes/export_import.py",
            "dashboard/backend/routes/runs.py",
        )
        for relative in paths:
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("from dashboard.backend.app import", source, relative)
            self.assertNotIn("UploadFile", source, relative)
            self.assertNotIn("File(", source, relative)

    def test_config_and_blob_routes_cannot_write_content_to_mongo(self) -> None:
        paths = (
            "dashboard/backend/services/provider_routes.py",
            "dashboard/backend/services/blob_routes.py",
            "dashboard/backend/services/user_config_routes.py",
        )
        for relative in paths:
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("set_json_blob", source, relative)
            self.assertNotIn("set_user_config", source, relative)
            self.assertNotIn("set_provider_config", source, relative)
            self.assertIn("status_code=410", source, relative)

    def test_terminal_jobs_have_ttl_index(self) -> None:
        source = (ROOT / "dashboard/backend/db/indexes.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"purge_at", ASCENDING', source)
        self.assertIn("expireAfterSeconds=0", source)

    def test_readiness_covers_stateless_control_plane_boundary(self) -> None:
        source = (ROOT / "dashboard/backend/admin/admin_routes.py").read_text(
            encoding="utf-8"
        )
        for key in (
            "protocol_compatibility",
            "metadata_only_jobs",
            "ttl_indexes",
            "online_devices",
            "resource_references",
            "content_storage_absent",
        ):
            self.assertIn(f'"key": "{key}"', source)

    def test_docs_do_not_claim_render_content_storage(self) -> None:
        source = (ROOT / "README.md").read_text(encoding="utf-8").lower()
        self.assertNotIn("storage_provider=cloudinary", source)
        self.assertNotIn("cloudinary uploads happen", source)
        self.assertNotIn("all files are stored locally on the server", source)

    def test_phase_ledger_records_completed_repository_phases(self) -> None:
        source = (ROOT / "LOCAL_DATA_PLANE_IMPLEMENTATION.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "| Local lifecycle parity | Complete (repository) | `5eac75f` |",
            source,
        )
        self.assertIn(
            "| Stateless Render cleanup | Complete (repository) | `ae469c4` |",
            source,
        )
        self.assertIn(
            "| Migration | Complete (repository) | `145d7fc` |",
            source,
        )
        self.assertIn(
            "| Full verification | Complete (repository) | `1761077` |",
            source,
        )
        self.assertIn(
            "| Operations documentation | Complete (repository) | `1440df0` |",
            source,
        )

    def test_feature_parity_checklist_is_complete(self) -> None:
        source = (ROOT / "LOCAL_DATA_PLANE_IMPLEMENTATION.md").read_text(
            encoding="utf-8"
        )
        checklist = source[
            source.index("## Feature-Parity Checklist"):
            source.index("## Required Test Suites")
        ]
        self.assertNotIn("- [ ]", checklist)
        self.assertEqual(checklist.count("- [x]"), 35)


if __name__ == "__main__":
    unittest.main()
