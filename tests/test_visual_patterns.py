from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class VisualPatternTests(unittest.TestCase):
    def test_bundled_templates_define_four_patterns_for_every_format(self) -> None:
        from dashboard.backend.services.visual_archetypes import (
            FORMATS,
            bundled_visual_archetypes,
        )

        archetypes = bundled_visual_archetypes()
        self.assertEqual(sorted(archetypes), sorted(FORMATS))
        for fmt in FORMATS:
            entries = archetypes[fmt]
            self.assertEqual(len(entries), 4, fmt)
            ids = [str(entry["id"]) for entry in entries]
            self.assertEqual(len(set(ids)), len(ids), fmt)
            for entry in entries:
                self.assertTrue(entry["id"] and entry["label"], fmt)
                self.assertTrue(entry["layout_lines"], fmt)
                self.assertTrue(entry["direction_lines"], fmt)

    def test_stored_config_without_patterns_falls_back_to_bundled_defaults(self) -> None:
        from dashboard.backend.services.visual_archetypes import (
            FORMATS,
            format_visual_archetypes,
        )

        for stored in ({}, "", {"visual_archetypes": {}}, "not json"):
            patterns = format_visual_archetypes(stored)
            self.assertEqual(sorted(patterns), sorted(FORMATS))
            for fmt in FORMATS:
                self.assertTrue(patterns[fmt], f"{fmt} empty for stored={stored!r}")
                for entry in patterns[fmt]:
                    self.assertEqual(sorted(entry), ["id", "label"])
                    self.assertTrue(entry["id"] and entry["label"])

    def test_stored_patterns_win_over_bundled_defaults(self) -> None:
        from dashboard.backend.services.visual_archetypes import (
            format_visual_archetypes,
        )

        patterns = format_visual_archetypes(
            json.dumps(
                {
                    "visual_archetypes": {
                        "HERO": [{"id": "custom_hero", "label": "Custom hero"}]
                    }
                }
            )
        )
        self.assertEqual(
            patterns["HERO"], [{"id": "custom_hero", "label": "Custom hero"}]
        )

    def test_guide_endpoint_returns_operator_markdown(self) -> None:
        from dashboard.backend.routes.defaults import operator_guide

        payload = operator_guide()
        self.assertIn("output_fields", payload["markdown"])
        self.assertIn("visual archetypes", payload["markdown"].lower())
        self.assertIn("https://github.com/Vinay-003/ad-factory/tree/render-setup/docs", payload["markdown"])
        self.assertIn("/docs/STRUCTURED_COPY_SYSTEM.md", payload["markdown"])

    def test_published_docs_endpoint_serves_structured_copy(self) -> None:
        from dashboard.backend.routes.defaults import published_doc
        from fastapi import HTTPException

        payload = published_doc("STRUCTURED_COPY_SYSTEM.md")
        self.assertIn("output_fields", payload["markdown"])
        self.assertIn("planned_ads", payload["markdown"])
        with self.assertRaises(HTTPException):
            published_doc("../.env")

    def test_defaults_endpoint_returns_selectable_patterns_per_format(self) -> None:
        from dashboard.backend.routes.defaults import dashboard_defaults
        from dashboard.backend.services.visual_archetypes import FORMATS

        with patch(
            "dashboard.backend.routes.defaults.resolve_effective_config_for_user",
            return_value={"persona_seeds": "[]", "copy_prompt_templates": "{}"},
        ):
            payload = dashboard_defaults(user={"user_id": "usr_test"})

        self.assertEqual([item["id"] for item in payload["formats"]], list(FORMATS))
        self.assertEqual(
            [item["id"] for item in payload["language_modes"]],
            ["ALL", "EN", "HI", "HINGLISH"],
        )
        for item in payload["formats"]:
            self.assertTrue(item["id"] and item["label"])
        for fmt in FORMATS:
            entries = payload["format_patterns"][fmt]
            self.assertTrue(entries, fmt)
            for entry in entries:
                self.assertIn("id", entry)
                self.assertIn("label", entry)
        self.assertIn("none", payload["hypothesis"]["variables"])
        self.assertIn("copy_framework", payload["hypothesis"]["variables"])

    def test_defaults_reload_hypothesis_from_effective_org_config(self) -> None:
        from dashboard.backend.routes.defaults import dashboard_defaults

        org_hooks = json.dumps(
            {
                "_meta": {"label": "Org Hooks"},
                "question_led": {"label": "Org Question"},
            }
        )
        with patch(
            "dashboard.backend.routes.defaults.resolve_effective_config",
            return_value={"ad_hooks": org_hooks, "copy_prompt_templates": "{}"},
        ) as resolve_org:
            payload = dashboard_defaults(
                user={"user_id": "usr_test"},
                org_id="org_team",
            )

        resolve_org.assert_called_once_with("usr_test", "org_team")
        hook_options = payload["hypothesis"]["variables"]["hook_structure"]["options"]
        self.assertEqual(hook_options, [{"id": "question_led", "label": "Org Question"}])

    def test_hollow_copy_prompt_templates_get_bundled_visual_archetypes(self) -> None:
        from dashboard.backend.services.visual_archetypes import (
            FORMATS,
            fill_missing_visual_archetypes,
            sanitize_copy_prompt_templates_text,
        )

        hollow = json.dumps(
            {
                "format": "v1",
                "_description": "Visual archetypes only.",
            }
        )
        filled = json.loads(fill_missing_visual_archetypes(hollow))
        self.assertEqual(sorted(filled["visual_archetypes"]), sorted(FORMATS))
        self.assertEqual(len(filled["visual_archetypes"]["BA"]), 4)
        cleaned = json.loads(sanitize_copy_prompt_templates_text(hollow))
        self.assertIn("ba_classic_split", json.dumps(cleaned))

    def test_retired_copy_prompt_blocks_are_stripped_on_generic_only(self) -> None:
        from dashboard.backend.services.user_config import (
            validate_config_files,
        )
        from dashboard.backend.services.visual_archetypes import (
            sanitize_copy_prompt_templates_text,
        )

        raw = json.dumps(
            {
                "format": "v1",
                "system_prompt_base_rules": ["dead"],
                "cta_variants": {"EN": {"HERO": ["Start Today"]}},
                "visual_archetypes": {
                    "HERO": [{"id": "hero_center_stage", "label": "Centered"}]
                },
            }
        )
        cleaned = json.loads(sanitize_copy_prompt_templates_text(raw))
        self.assertIn("visual_archetypes", cleaned)
        self.assertNotIn("system_prompt_base_rules", cleaned)
        self.assertNotIn("cta_variants", cleaned)
        saved = validate_config_files({"copy_prompt_templates": raw})
        self.assertIn("system_prompt_base_rules", saved["copy_prompt_templates"])

    def test_sync_adds_and_removes_visual_archetype_keys(self) -> None:
        from dashboard.backend.services.visual_archetypes import sync_visual_archetypes

        synced, added, removed = sync_visual_archetypes(
            json.dumps(
                {
                    "visual_archetypes": {
                        "HERO": [{"id": "hero_center_stage", "label": "Centered"}]
                    }
                }
            ),
            json.dumps({"HERO": {"label": "Hero"}, "STORY": {"label": "Story"}}),
        )
        parsed = json.loads(synced)
        self.assertEqual(added, ["STORY"])
        self.assertEqual(removed, [])
        self.assertEqual(parsed["visual_archetypes"]["STORY"][0]["id"], "story_default")
        dropped, added_again, removed_again = sync_visual_archetypes(
            synced,
            json.dumps({"STORY": {"label": "Story"}}),
        )
        self.assertEqual(added_again, [])
        self.assertEqual(removed_again, ["HERO"])
        self.assertNotIn("HERO", json.loads(dropped)["visual_archetypes"])

    def test_apply_format_sync_notice_uses_same_owner_templates(self) -> None:
        from dashboard.backend.services.user_config import apply_format_archetype_sync

        with (
            patch(
                "dashboard.backend.services.user_config.get_config_doc",
                return_value=None,
            ),
            patch(
                "dashboard.backend.services.user_config.get_generic_config",
                return_value={
                    "copy_prompt_templates": json.dumps(
                        {
                            "visual_archetypes": {
                                "HERO": [{"id": "hero_center_stage", "label": "Centered"}]
                            }
                        }
                    )
                },
            ),
        ):
            files, notice = apply_format_archetype_sync(
                "user",
                "usr_test",
                {
                    "ad_formats": json.dumps(
                        {"HERO": {"label": "Hero"}, "STORY": {"label": "Story"}}
                    )
                },
            )
        self.assertIn("Added default visual archetypes for STORY", notice)
        self.assertIn("story_default", files["copy_prompt_templates"])

    def test_auto_rotate_picks_random_not_first(self) -> None:
        from dashboard.backend.services.visual_archetypes import (
            pick_random_archetype,
        )

        items = [{"id": f"p{i}", "label": str(i)} for i in range(4)]
        first = pick_random_archetype(items, seed=1)
        later = pick_random_archetype(items, seed=99)
        self.assertIn(first["id"], {item["id"] for item in items})
        self.assertIn(later["id"], {item["id"] for item in items})
        unused = pick_random_archetype(items, seed=1, used_ids={"p0", "p1", "p2"})
        self.assertEqual(unused["id"], "p3")

    def test_llm_decide_uses_editable_prompt(self) -> None:
        from dashboard.backend.services.visual_archetypes import (
            LLM_DECIDE_ID,
            llm_decide_archetype,
        )

        archetype = llm_decide_archetype("Invent a split layout for this format.")
        self.assertEqual(archetype["id"], LLM_DECIDE_ID)
        self.assertIn("Invent a split layout", " ".join(archetype["layout_lines"]))

    def test_generation_reads_the_same_restored_archetype_file(self) -> None:
        templates = json.loads(
            (ROOT / "dashboard/backend/copy_prompt_templates.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("visual_archetypes", templates)
        self.assertNotIn("system_prompt_base_rules", templates)
        self.assertNotIn("cta_variants", templates)

        from dashboard.backend.services.generate_ads import FORMAT_VISUAL_ARCHETYPES

        self.assertTrue(FORMAT_VISUAL_ARCHETYPES.get("HERO"))


if __name__ == "__main__":
    unittest.main()
