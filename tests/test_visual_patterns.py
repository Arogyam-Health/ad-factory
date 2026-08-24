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

    def test_defaults_endpoint_returns_selectable_patterns_per_format(self) -> None:
        from dashboard.backend.routes.defaults import dashboard_defaults
        from dashboard.backend.services.visual_archetypes import FORMATS

        with patch(
            "dashboard.backend.routes.defaults.resolve_effective_config_for_user",
            return_value={"persona_seeds": "[]", "copy_prompt_templates": "{}"},
        ):
            payload = dashboard_defaults(user={"user_id": "usr_test"})

        self.assertEqual(payload["formats"], list(FORMATS))
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

    def test_generation_reads_the_same_restored_archetype_file(self) -> None:
        templates = json.loads(
            (ROOT / "dashboard/backend/copy_prompt_templates.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("visual_archetypes", templates)

        from scripts.generate_ads import FORMAT_VISUAL_ARCHETYPES

        self.assertTrue(FORMAT_VISUAL_ARCHETYPES.get("HERO"))


if __name__ == "__main__":
    unittest.main()
