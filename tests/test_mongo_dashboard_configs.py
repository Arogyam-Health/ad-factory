from __future__ import annotations

from pathlib import Path
import json
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

    def update_one(self, _query: dict, update: dict) -> None:
        for dotted_key, value in update.get("$set", {}).items():
            target = self.document
            parts = dotted_key.split(".")
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = value


class _DB:
    def __init__(self, document: dict):
        self.collection = _ConfigCollection(document)

    def __getitem__(self, _name: str) -> _ConfigCollection:
        return self.collection


class MongoDashboardConfigTests(unittest.TestCase):
    def test_studio_config_cards_have_no_legacy_content_route(self) -> None:
        studio = (ROOT / "dashboard/web/src/pages/Studio.tsx").read_text(encoding="utf-8")
        keys = (ROOT / "dashboard/web/src/lib/config-keys.ts").read_text(encoding="utf-8")
        viewer = (ROOT / "dashboard/web/src/components/FileViewer.tsx").read_text(encoding="utf-8")

        self.assertNotIn("/api/prompt-file-content", studio)
        self.assertIn("saveConfigFile", keys)
        self.assertIn("Save file", viewer)

    def test_render_copy_uses_mongo_product_master_doc_without_local_fallback(self) -> None:
        studio = (ROOT / "dashboard/web/src/pages/Studio.tsx").read_text(encoding="utf-8")
        keys = (ROOT / "dashboard/web/src/lib/config-keys.ts").read_text(encoding="utf-8")
        render_copy = (
            ROOT
            / "dashboard"
            / "backend"
            / "services"
            / "render_structured_copy.py"
        ).read_text(encoding="utf-8")

        self.assertIn("CONFIG_SECTIONS", studio)
        self.assertIn("product_master_doc", keys)
        self.assertIn("Business rules", keys)
        self.assertIn("saveConfigFile", (ROOT / "dashboard/web/src/lib/config-keys.ts").read_text(encoding="utf-8"))
        self.assertNotIn("resolveProductDocumentText", studio)
        self.assertIn('effective_config.get("product_master_doc")', render_copy)
        self.assertIn("Product Master Doc is empty", render_copy)

    def test_frontend_assets_revalidate_after_deploy(self) -> None:
        from fastapi.testclient import TestClient
        from dashboard.backend.control_app import app

        response = TestClient(app).get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"), "no-cache")
        self.assertIn("text/html", response.headers.get("content-type", ""))

    def test_dashboard_config_routes_remain_on_control_plane(self) -> None:
        from dashboard.backend.control_plane_policy import is_render_content_route

        allowed = (
            ("GET", "/api/defaults"),
            ("GET", "/api/config/sources"),
            ("GET", "/api/config/effective"),
            ("GET", "/api/config/persona-summary"),
            ("GET", "/api/config/cfg_1/versions"),
            ("DELETE", "/api/config/cfg_1/versions/ver_1"),
            ("POST", "/api/config/cfg_1/prune-old-versions"),
            ("PUT", "/api/user/config"),
            ("GET", "/api/orgs/org_1/config"),
            ("PUT", "/api/orgs/org_1/config"),
            ("POST", "/api/orgs/org_1/configs/copy"),
            ("GET", "/api/admin/configs"),
            ("PUT", "/api/user/provider-config/opencode"),
            ("GET", "/api/admin/provider-configs"),
            ("GET", "/api/llm-traces"),
            ("POST", "/api/llm-traces/delete-batch"),
            ("GET", "/api/config/effective?org_id=org_1"),
        )
        for method, path in allowed:
            self.assertFalse(
                is_render_content_route(method, path),
                f"{method} {path} must use MongoDB without a local agent",
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

    def test_generic_strips_dead_copy_templates_without_changing_org_extract(self) -> None:
        from dashboard.backend.services.user_config import (
            _extract_flat_from_new_schema,
            get_generic_config,
        )

        dirty = json.dumps(
            {
                "system_prompt_base_rules": ["dead"],
                "visual_archetypes": {"HERO": [{"id": "hero_center_stage", "label": "Centered"}]},
            }
        )
        db = _DB(
            {
                "owner_type": "system",
                "owner_id": "generic",
                "is_active": True,
                "files": {
                    "copy_prompt_templates": {
                        "content": dirty,
                        "content_type": "application/json",
                    }
                },
            }
        )
        with patch(
            "dashboard.backend.services.user_config.get_sync_db", return_value=db
        ):
            config = get_generic_config()
        self.assertNotIn("system_prompt_base_rules", config["copy_prompt_templates"])
        self.assertIn("visual_archetypes", config["copy_prompt_templates"])

        org = _extract_flat_from_new_schema(
            {
                "files": {
                    "copy_prompt_templates": {
                        "content": dirty,
                        "content_type": "application/json",
                    }
                }
            }
        )
        self.assertIn("system_prompt_base_rules", org["copy_prompt_templates"])

    def test_reference_flow_prompts_are_separate_config_files(self) -> None:
        from dashboard.backend.services.user_config import (
            CONFIG_KEYS,
            _CONTENT_TYPES,
            _EMPTY_BY_KEY,
            validate_config_files,
        )

        for key in ("reference_starting_prompt", "reference_product_master_doc"):
            self.assertIn(key, CONFIG_KEYS)
            self.assertEqual(_CONTENT_TYPES[key], "text/plain")
            self.assertEqual(_EMPTY_BY_KEY[key], "")

        saved = validate_config_files(
            {
                "starting_prompt": "structured only",
                "reference_starting_prompt": "reference only",
                "product_master_doc": "structured doc",
                "reference_product_master_doc": "reference doc",
            }
        )
        self.assertEqual(saved["starting_prompt"], "structured only")
        self.assertEqual(saved["reference_starting_prompt"], "reference only")
        self.assertEqual(saved["product_master_doc"], "structured doc")
        self.assertEqual(saved["reference_product_master_doc"], "reference doc")

    def test_generic_bootstrap_backfills_config_keys_added_after_first_boot(self) -> None:
        from unittest.mock import patch

        from dashboard.backend.services import user_config

        stored_keys = [
            key
            for key in user_config.CONFIG_KEYS
            if not key.startswith("reference_")
        ]
        existing = {
            "owner_type": "system",
            "owner_id": "generic",
            "is_active": True,
            "files": {key: {"content": "old"} for key in stored_keys},
        }
        with (
            patch.object(user_config, "get_config_doc", return_value=existing),
            patch.object(user_config, "create_or_update_config") as write,
        ):
            user_config.ensure_generic_config()

        self.assertEqual(
            sorted(write.call_args.kwargs["files"]),
            ["reference_product_master_doc", "reference_starting_prompt"],
        )
        self.assertTrue(write.call_args.kwargs["files"]["reference_starting_prompt"])

        with (
            patch.object(user_config, "get_config_doc", return_value={
                **existing,
                "files": {
                    key: {"content": "old"} for key in user_config.CONFIG_KEYS
                },
            }),
            patch.object(user_config, "create_or_update_config") as write,
        ):
            user_config.ensure_generic_config()
        write.assert_not_called()

    def test_only_known_bounded_string_config_files_are_accepted(self) -> None:
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

    def test_expected_version_is_not_treated_as_a_config_file(self) -> None:
        from dashboard.backend.services.user_config import (
            extract_config_files,
            validate_config_files,
        )

        self.assertEqual(
            validate_config_files(
                extract_config_files(
                    {"starting_prompt": "hello", "expected_version": 4}
                )
            ),
            {"starting_prompt": "hello"},
        )
        self.assertEqual(
            validate_config_files(
                extract_config_files(
                    {
                        "config": {"persona_seeds": "[]"},
                        "expected_version": 4,
                    }
                )
            ),
            {"persona_seeds": "[]"},
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
        files["persona_seeds"]["content"] = (
            '[{"persona_number": 1, "persona_name": "Mongo Persona"}]'
        )
        personal = {
            "_id": "mongo_personal",
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
                patch(
                    "dashboard.backend.services.config_version_service.create_config_version_before_update",
                    return_value=None,
                ),
            ):
                client = TestClient(app)
                effective = client.get(
                    "/api/config/effective", cookies={"session": "test"}
                )
                sources = client.get(
                    "/api/config/sources", cookies={"session": "test"}
                )
                defaults = client.get(
                    "/api/defaults", cookies={"session": "test"}
                )
                saved = client.put(
                    "/api/user/config",
                    cookies={"session": "test"},
                    json={"starting_prompt": "Edited in MongoDB"},
                )
        finally:
            app.dependency_overrides.pop(require_user_dependency, None)

        self.assertEqual(effective.status_code, 200)
        self.assertEqual(
            effective.json()["config"]["persona_seeds"],
            '[{"persona_number": 1, "persona_name": "Mongo Persona"}]',
        )
        self.assertEqual(sources.status_code, 200)
        self.assertEqual(sources.json()["sources"][0]["type"], "personal")
        self.assertEqual(defaults.status_code, 200)
        self.assertEqual(
            defaults.json()["personas"][0]["name"],
            "Mongo Persona",
        )
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(
            db.collection.document["files"]["starting_prompt"]["content"],
            "Edited in MongoDB",
        )

    def test_content_migration_does_not_remove_dashboard_configs(self) -> None:
        from dashboard.backend.agent.content_migration import _COLLECTION_KINDS

        self.assertNotIn("user_configs", _COLLECTION_KINDS)
        self.assertNotIn("config_versions", _COLLECTION_KINDS)

    def test_personal_config_save_does_not_create_a_version(self) -> None:
        from dashboard.backend.services.user_config import create_or_update_config

        db = _DB(
            {
                "_id": "cfg_personal",
                "owner_type": "user",
                "owner_id": "usr_1",
                "is_active": True,
                "version": 1,
                "files": {
                    "starting_prompt": {
                        "content": "old",
                        "content_type": "text/plain",
                        "updated_at": 1,
                    }
                },
            }
        )
        with (
            patch("dashboard.backend.services.user_config.get_sync_db", return_value=db),
            patch(
                "dashboard.backend.services.config_version_service.create_config_version_before_update"
            ) as snapshot,
        ):
            create_or_update_config(
                owner_type="user",
                owner_id="usr_1",
                files={"starting_prompt": "new"},
                actor_user_id="usr_1",
            )
        snapshot.assert_not_called()
        self.assertEqual(
            db.collection.document["files"]["starting_prompt"]["content"],
            "new",
        )

    def test_org_config_save_creates_a_version(self) -> None:
        from dashboard.backend.services.user_config import create_or_update_config

        db = _DB(
            {
                "_id": "cfg_org",
                "owner_type": "org",
                "owner_id": "org_1",
                "is_active": True,
                "version": 1,
                "files": {
                    "starting_prompt": {
                        "content": "old",
                        "content_type": "text/plain",
                        "updated_at": 1,
                    }
                },
            }
        )
        with (
            patch("dashboard.backend.services.user_config.get_sync_db", return_value=db),
            patch(
                "dashboard.backend.services.config_version_service.create_config_version_before_update"
            ) as snapshot,
        ):
            create_or_update_config(
                owner_type="org",
                owner_id="org_1",
                files={"starting_prompt": "new"},
                actor_user_id="usr_1",
                org_id="org_1",
            )
        snapshot.assert_called_once()

    def test_delete_old_config_versions_keeps_newest(self) -> None:
        from dashboard.backend.services import config_version_service

        class _Versions:
            def __init__(self) -> None:
                self.docs = [
                    {"config_id": "cfg_1", "version_id": "ver_old", "created_at": 1},
                    {"config_id": "cfg_1", "version_id": "ver_new", "created_at": 9},
                    {"config_id": "cfg_1", "version_id": "ver_mid", "created_at": 5},
                ]

            def find_one(self, query, sort=None):
                matches = [doc for doc in self.docs if doc["config_id"] == query["config_id"]]
                if sort:
                    matches.sort(key=lambda doc: doc.get("created_at", 0), reverse=True)
                return matches[0] if matches else None

            def delete_one(self, query):
                before = len(self.docs)
                self.docs = [
                    doc
                    for doc in self.docs
                    if not (
                        doc["config_id"] == query["config_id"]
                        and doc["version_id"] == query["version_id"]
                    )
                ]
                return type("R", (), {"deleted_count": before - len(self.docs)})()

            def delete_many(self, query):
                kept = query.get("version_id", {}).get("$ne")
                before = len(self.docs)
                self.docs = [
                    doc
                    for doc in self.docs
                    if doc["config_id"] != query["config_id"] or doc["version_id"] == kept
                ]
                return type("R", (), {"deleted_count": before - len(self.docs)})()

        versions = _Versions()
        with patch.object(config_version_service, "get_sync_db", return_value={"config_versions": versions}):
            deleted = config_version_service.delete_config_version("cfg_1", "ver_mid")
            self.assertTrue(deleted)
            result = config_version_service.delete_old_config_versions("cfg_1")
        self.assertEqual(result["kept_version_id"], "ver_new")
        self.assertEqual(result["deleted"], 1)
        self.assertEqual([doc["version_id"] for doc in versions.docs], ["ver_new"])

    def test_stale_expected_version_is_rejected(self) -> None:
        from dashboard.backend.services.user_config import (
            ConfigVersionConflict,
            create_or_update_config,
        )

        db = _DB(
            {
                "_id": "cfg_personal",
                "owner_type": "user",
                "owner_id": "usr_1",
                "is_active": True,
                "version": 3,
                "files": {
                    "starting_prompt": {
                        "content": "old",
                        "content_type": "text/plain",
                        "updated_at": 1,
                    }
                },
            }
        )
        with patch(
            "dashboard.backend.services.user_config.get_sync_db", return_value=db
        ):
            with self.assertRaises(ConfigVersionConflict) as raised:
                create_or_update_config(
                    owner_type="user",
                    owner_id="usr_1",
                    files={"starting_prompt": "new"},
                    actor_user_id="usr_1",
                    expected_version=1,
                )
        self.assertEqual(raised.exception.current_version, 3)
        self.assertEqual(
            db.collection.document["files"]["starting_prompt"]["content"],
            "old",
        )

    def test_config_page_and_org_create_include_reference_keys(self) -> None:
        config = (ROOT / "dashboard/web/src/pages/Config.tsx").read_text(encoding="utf-8")
        keys = (ROOT / "dashboard/web/src/lib/config-keys.ts").read_text(encoding="utf-8")
        studio = (ROOT / "dashboard/web/src/pages/Studio.tsx").read_text(encoding="utf-8")
        reference = (ROOT / "dashboard/web/src/pages/studio/ReferencePanel.tsx").read_text(
            encoding="utf-8"
        )
        org_routes = (ROOT / "dashboard/backend/services/org_routes.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("reference_starting_prompt", keys)
        self.assertIn("reference_product_master_doc", keys)
        self.assertIn("Reference Starting Prompt", keys)
        self.assertIn("setFlow(\"reference\")", studio)
        self.assertIn("setFlow(\"structured\")", studio)
        self.assertIn("copy_config(", org_routes)
        self.assertIn('reason="create_org"', org_routes)
        self.assertIn("reference_starting_prompt", reference)
        self.assertIn("reference_product_master_doc", reference)
        self.assertIn("/api/config/effective", config)

    def test_empty_json_placeholders_do_not_override_generic_catalogs(self) -> None:
        from dashboard.backend.services.user_config import _has_config_override

        self.assertFalse(_has_config_override(""))
        self.assertFalse(_has_config_override("{}"))
        self.assertFalse(_has_config_override("[]"))
        self.assertFalse(_has_config_override({}))
        self.assertFalse(_has_config_override([]))
        self.assertTrue(_has_config_override('{"variants": []}'))
        self.assertTrue(_has_config_override('[{"persona_number": 1}]'))


if __name__ == "__main__":
    unittest.main()
