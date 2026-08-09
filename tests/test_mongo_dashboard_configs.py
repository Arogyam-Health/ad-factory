from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class _ConfigCollection:
    def __init__(self, document: dict):
        self.document = document
        self.queries: list[dict] = []

    def find_one(self, query: dict) -> dict:
        self.queries.append(query)
        return self.document

    def find(self, _query: dict) -> list[dict]:
        return []


class _DB:
    def __init__(self, document: dict):
        self.collection = _ConfigCollection(document)

    def __getitem__(self, _name: str) -> _ConfigCollection:
        return self.collection


class MongoDashboardConfigTests(unittest.TestCase):
    def test_studio_config_cards_have_no_legacy_content_route(self) -> None:
        main_js = (ROOT / "dashboard/frontend/js/main.js").read_text(
            encoding="utf-8"
        )
        index_html = (ROOT / "dashboard/frontend/index.html").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("/api/prompt-file-content", main_js)
        self.assertIn('/js/main.js?v=5', index_html)

    def test_frontend_assets_revalidate_after_deploy(self) -> None:
        from fastapi.testclient import TestClient
        from dashboard.backend.control_app import app

        response = TestClient(app).get("/js/main.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"), "no-cache")

    def test_dashboard_config_routes_remain_on_control_plane(self) -> None:
        from dashboard.backend.control_plane_policy import is_render_content_route

        allowed = (
            ("GET", "/api/config/sources"),
            ("GET", "/api/config/effective"),
            ("GET", "/api/config/persona-summary"),
            ("GET", "/api/config/cfg_1/versions"),
            ("PUT", "/api/user/config"),
            ("GET", "/api/orgs/org_1/config"),
            ("PUT", "/api/orgs/org_1/config"),
            ("POST", "/api/orgs/org_1/configs/copy"),
            ("GET", "/api/admin/configs"),
        )
        for method, path in allowed:
            self.assertFalse(
                is_render_content_route(method, path),
                f"{method} {path} must use MongoDB without a local agent",
            )

        self.assertTrue(
            is_render_content_route("PUT", "/api/user/provider-config/google")
        )
        self.assertTrue(is_render_content_route("POST", "/api/config/provider"))

    def test_generic_config_is_read_from_mongodb_system_document(self) -> None:
        from dashboard.backend.services.user_config import (
            CONFIG_KEYS,
            get_generic_config,
        )

        files = {
            key: {
                "content": f"mongo:{key}",
                "content_type": "text/plain",
                "updated_at": 1,
            }
            for key in CONFIG_KEYS
        }
        db = _DB(
            {
                "owner_type": "system",
                "owner_id": "generic",
                "is_active": True,
                "files": files,
            }
        )
        with patch(
            "dashboard.backend.services.user_config.get_sync_db", return_value=db
        ):
            config = get_generic_config()

        self.assertEqual(
            db.collection.queries,
            [{"owner_type": "system", "owner_id": "generic", "is_active": True}],
        )
        self.assertEqual(config["starting_prompt"], "mongo:starting_prompt")

    def test_only_eight_bounded_string_config_files_are_accepted(self) -> None:
        from dashboard.backend.services.user_config import (
            MAX_CONFIG_TOTAL_BYTES,
            validate_config_files,
        )

        self.assertEqual(
            validate_config_files({"starting_prompt": "hello"}),
            {"starting_prompt": "hello"},
        )
        with self.assertRaises(ValueError):
            validate_config_files({"unknown": "body"})
        with self.assertRaises(ValueError):
            validate_config_files({"starting_prompt": {"body": "not text"}})
        with self.assertRaises(ValueError):
            validate_config_files(
                {"starting_prompt": "x" * (MAX_CONFIG_TOTAL_BYTES + 1)}
            )

    def test_repository_defaults_fit_the_mongodb_config_budget(self) -> None:
        from bson import BSON

        from dashboard.backend.services.user_config import (
            _repository_generic_config,
            validate_config_files,
        )

        defaults = _repository_generic_config()
        self.assertEqual(validate_config_files(defaults), defaults)
        self.assertGreater(len(defaults["background_variant"]), 1024 * 1024)
        document = {
            "owner_type": "system",
            "owner_id": "generic",
            "is_active": True,
            "files": {
                key: {"content": value, "content_type": "text/plain"}
                for key, value in defaults.items()
            },
        }
        self.assertLess(len(BSON.encode(document)), 16 * 1024 * 1024)

    def test_login_time_config_endpoints_load_without_local_agent(self) -> None:
        from fastapi.testclient import TestClient

        from dashboard.backend.auth.service import require_user_dependency
        from dashboard.backend import control_app as control_app_module
        from dashboard.backend.services.user_config import CONFIG_KEYS

        files = {
            key: {
                "content": f"personal:{key}",
                "content_type": "text/plain",
                "updated_at": 1,
            }
            for key in CONFIG_KEYS
        }
        personal = {
            "config_id": "cfg_personal",
            "owner_type": "user",
            "owner_id": "usr_1",
            "is_active": True,
            "files": files,
        }
        db = _DB(personal)
        app = control_app_module.app
        app.dependency_overrides[require_user_dependency] = lambda: {
            "user_id": "usr_1",
            "email": "user@example.com",
        }

        try:
            with (
                patch.object(
                    control_app_module,
                    "get_current_user_from_cookie",
                    return_value={"user_id": "usr_1", "email": "user@example.com"},
                ),
                patch(
                    "dashboard.backend.services.user_config.get_sync_db",
                    return_value=db,
                ),
                patch(
                    "dashboard.backend.services.org_helper.get_sync_db",
                    return_value=db,
                ),
            ):
                client = TestClient(app)
                effective = client.get(
                    "/api/config/effective", cookies={"session": "test"}
                )
                sources = client.get(
                    "/api/config/sources", cookies={"session": "test"}
                )
        finally:
            app.dependency_overrides.pop(require_user_dependency, None)

        self.assertEqual(effective.status_code, 200)
        self.assertEqual(
            effective.json()["config"]["persona_seeds"],
            "personal:persona_seeds",
        )
        self.assertEqual(sources.status_code, 200)
        self.assertEqual(sources.json()["sources"][0]["type"], "personal")

    def test_content_migration_does_not_remove_dashboard_configs(self) -> None:
        from dashboard.backend.agent.content_migration import _COLLECTION_KINDS

        self.assertNotIn("user_configs", _COLLECTION_KINDS)
        self.assertNotIn("config_versions", _COLLECTION_KINDS)


if __name__ == "__main__":
    unittest.main()
