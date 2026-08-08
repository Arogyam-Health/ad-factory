from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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
        blocked = (
            ("POST", "/api/runs/execute"),
            ("POST", "/api/batch/generate-images-both"),
            ("POST", "/api/runs/run_1/generate-916-selected"),
            ("PUT", "/api/user/config"),
            ("PUT", "/api/user/json-blobs/persona_seeds"),
            ("GET", "/api/files/download/image/image_1"),
            ("POST", "/api/runs/run_1/replace-image"),
            ("GET", "/api/generic-config"),
        )
        for method, path in blocked:
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
                requests = (
                    ("POST", "/api/runs/execute"),
                    ("POST", "/api/batch/generate-images-both"),
                    ("POST", "/api/runs/run_1/generate-916-selected"),
                    ("PUT", "/api/user/config"),
                    ("PUT", "/api/user/json-blobs/persona_seeds"),
                    ("GET", "/api/files/download/image/image_1"),
                    ("POST", "/api/runs/run_1/replace-image"),
                    ("GET", "/api/generic-config"),
                )
                for method, path in requests:
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
        )
        for document in forbidden:
            with self.assertRaises(ValueError):
                policy.validate_metadata_document("runs", document)

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

    def test_phase_ledger_records_lifecycle_commit_and_stateless_completion(self) -> None:
        source = (ROOT / "LOCAL_DATA_PLANE_IMPLEMENTATION.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "| Local lifecycle parity | Complete (repository) | `5eac75f` |",
            source,
        )
        self.assertIn(
            "| Stateless Render cleanup | Complete (repository) |  |",
            source,
        )


if __name__ == "__main__":
    unittest.main()
