from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class _Collection:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    @staticmethod
    def _matches(document: dict, query: dict) -> bool:
        return all(document.get(key) == value for key, value in query.items())

    def find_one(self, query: dict, _projection: dict | None = None) -> dict | None:
        return next(
            (document for document in self.documents if self._matches(document, query)),
            None,
        )

    def find(self, query: dict, _projection: dict | None = None) -> list[dict]:
        return [
            document for document in self.documents if self._matches(document, query)
        ]

    def update_one(self, query: dict, update: dict, *, upsert: bool = False) -> None:
        document = self.find_one(query)
        if document is None:
            if not upsert:
                return
            document = dict(query)
            self.documents.append(document)
        document.update(update.get("$setOnInsert", {}))
        document.update(update.get("$set", {}))

    def delete_one(self, query: dict) -> None:
        self.documents = [
            document
            for document in self.documents
            if not self._matches(document, query)
        ]


class _DB:
    def __init__(self) -> None:
        self.collection = _Collection()

    def __getitem__(self, _name: str) -> _Collection:
        return self.collection


class MongoProviderConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = _DB()
        self.db_patch = patch(
            "dashboard.backend.services.provider_config.get_sync_db",
            return_value=self.db,
        )
        self.db_patch.start()

    def tearDown(self) -> None:
        self.db_patch.stop()

    def test_secret_is_encrypted_and_safe_views_never_expose_it(self) -> None:
        from dashboard.backend.services.provider_config import (
            get_all_provider_configs,
            get_materialized_provider_config,
            get_provider_config,
            set_provider_config,
        )

        saved = set_provider_config(
            "usr_1",
            "opencode",
            {
                "api_url": "https://opencode.example/v1",
                "api_key": "secret-provider-key",
                "default_model": "model-1",
            },
        )
        stored = self.db.collection.documents[0]

        self.assertEqual(stored["user_id"], "usr_1")
        self.assertEqual(stored["provider"], "opencode")
        self.assertNotIn("secret-provider-key", repr(stored))
        self.assertIn("encrypted_api_key", stored["config"])
        self.assertTrue(saved["config"]["has_secret"])
        self.assertNotIn("api_key", saved["config"])
        self.assertNotIn("encrypted_api_key", saved["config"])
        self.assertEqual(get_provider_config("usr_1", "opencode"), saved)
        self.assertEqual(get_all_provider_configs("usr_1"), [saved])
        self.assertEqual(
            get_materialized_provider_config("usr_1", "opencode")["api_key"],
            "secret-provider-key",
        )
        self.assertIsNone(get_provider_config("usr_2", "opencode"))

    def test_partial_updates_preserve_secret_and_validation_is_strict(self) -> None:
        from dashboard.backend.services.provider_config import set_provider_config

        set_provider_config(
            "usr_1",
            "opencode",
            {"api_url": "http://127.0.0.1:4090", "api_key": "first-key"},
        )
        set_provider_config(
            "usr_1", "opencode", {"default_model": "provider/model"}
        )
        stored = self.db.collection.documents[0]
        self.assertIn("encrypted_api_key", stored["config"])

        invalid = (
            ("unknown", {"api_key": "x"}),
            ("opencode", {"unexpected": "x"}),
            ("opencode", {"api_url": "file:///tmp/socket"}),
            ("opencode", {"api_key": "x" * 4097}),
        )
        for provider, config in invalid:
            with self.subTest(provider=provider, config=list(config)):
                with self.assertRaises(ValueError):
                    set_provider_config("usr_1", provider, config)

    def test_routes_are_authenticated_user_scoped_and_not_local_only(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from dashboard.backend.auth.service import require_user_dependency
        from dashboard.backend.control_plane_policy import is_render_content_route
        from dashboard.backend.services.provider_routes import router

        self.assertFalse(
            is_render_content_route("PUT", "/api/user/provider-config/opencode")
        )

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[require_user_dependency] = lambda: {
            "user_id": "usr_route"
        }
        client = TestClient(app)
        response = client.put(
            "/api/user/provider-config/opencode",
            json={
                "config": {
                    "api_url": "https://opencode.example/v1",
                    "api_key": "route-secret",
                }
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["config"]["has_secret"])
        self.assertNotIn("route-secret", response.text)

        materialized = client.post(
            "/api/user/provider-config/opencode/materialize"
        )
        self.assertEqual(materialized.status_code, 200)
        self.assertEqual(materialized.json()["api_key"], "route-secret")

    def test_catalog_uses_saved_url_and_decrypted_key(self) -> None:
        from dashboard.backend.services.provider_config import set_provider_config
        from dashboard.backend.services.provider_routes import user_opencode_catalog

        set_provider_config(
            "usr_catalog",
            "opencode",
            {
                "api_url": "https://opencode.ai/zen/v1",
                "api_key": "catalog-secret",
                "default_model": "opencode/model-b",
            },
        )
        with patch(
            "dashboard.backend.services.provider_routes.list_opencode_models",
            return_value=["opencode/model-a", "opencode/model-b"],
        ) as list_models:
            catalog = user_opencode_catalog({"user_id": "usr_catalog"})

        list_models.assert_called_once_with(
            api_url="https://opencode.ai/zen/v1",
            api_key="catalog-secret",
        )
        self.assertEqual(catalog["providers"], ["opencode"])
        self.assertEqual(
            catalog["models_by_provider"]["opencode"],
            ["opencode/model-a", "opencode/model-b"],
        )
        self.assertEqual(catalog["default_model"], "opencode/model-b")
        self.assertNotIn("catalog-secret", repr(catalog))

    def test_frontend_saves_cloud_config_for_render_execution(
        self,
    ) -> None:
        main = (ROOT / "dashboard/frontend/js/main.js").read_text(encoding="utf-8")
        profile = (ROOT / "dashboard/frontend/js/profile.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('fetchJSON("/api/user/provider-config"', main)
        self.assertIn(
            'fetchJSON("/api/user/provider-config/opencode/catalog"', main
        )
        self.assertNotIn("/materialize", main)
        render_jobs = (
            ROOT
            / "dashboard"
            / "backend"
            / "services"
            / "render_copy_jobs.py"
        ).read_text(encoding="utf-8")
        self.assertIn("get_materialized_provider_config", render_jobs)
        self.assertNotIn('fetchJSON("/api/opencode/catalog")', main)
        self.assertIn('fetchJSON("/api/user/provider-config")', profile)
        self.assertNotIn("localDataPlane.listProviderConfigs", profile)


if __name__ == "__main__":
    unittest.main()
