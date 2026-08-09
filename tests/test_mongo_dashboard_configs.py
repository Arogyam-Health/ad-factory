from __future__ import annotations

import unittest
from unittest.mock import patch


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
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from dashboard.backend.auth.service import require_user_dependency
        from dashboard.backend.services.invite_routes import router as invite_router
        from dashboard.backend.services.user_config import CONFIG_KEYS
        from dashboard.backend.services.user_config_routes import (
            router as user_config_router,
        )

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
        app = FastAPI()
        app.include_router(invite_router)
        app.include_router(user_config_router)
        app.dependency_overrides[require_user_dependency] = lambda: {
            "user_id": "usr_1",
            "email": "user@example.com",
        }

        with (
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
            effective = client.get("/api/config/effective")
            sources = client.get("/api/config/sources")

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
