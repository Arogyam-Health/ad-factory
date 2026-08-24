from __future__ import annotations

import json
import unittest
from pathlib import Path

from dashboard.backend.services.generate_ads import (
    CopyBlock,
    DEFAULT_PROOF_BAR_TEXT,
    default_visual_archetype,
    pick_background_slot,
    render_prompt,
)


ROOT = Path(__file__).resolve().parents[1]


def _hero_prompt(*, creative_concept=None) -> str:
    return render_prompt(
        "HERO",
        "EN",
        "4:5",
        {
            "number": 3,
            "name": "Stress Snacker",
            "pain_en": "Stress creates food urges.",
            "desire_en": "A practical routine.",
            "friction_en": "Old plans were difficult.",
            "proof_needed_en": "Use verified product facts only.",
            "tone_cue_en": "Practical and calm.",
        },
        CopyBlock(
            headline="Stay consistent",
            cta="Learn more",
            support_line="A practical routine.",
        ),
        {"concept_angle": "desired_outcome"},
        {"id": "bg_test", "title": "Studio"},
        1,
        "A quiet table in soft daylight.",
        default_visual_archetype("HERO"),
        creative_concept=creative_concept,
    )


class ConceptCatalogTests(unittest.TestCase):
    def test_repo_catalog_parses_labels_from_path_keys(self) -> None:
        from dashboard.backend.services.user_config import (
            CONFIG_KEYS,
            parse_concept_catalog,
            resolve_selected_concept,
        )

        raw = (ROOT / "concept.json").read_text(encoding="utf-8")
        catalog = parse_concept_catalog(raw)
        ids = {item["id"] for item in catalog}
        labels = {item["label"] for item in catalog}
        self.assertIn("concept", CONFIG_KEYS)
        self.assertGreaterEqual(len(catalog), 40)
        self.assertIn("Concept/X_Reasons_Why", ids)
        self.assertIn("X Reasons Why", labels)
        self.assertIn("Concept/IG_Stories", ids)
        self.assertIn("IG Stories", labels)
        self.assertIsNone(resolve_selected_concept(raw, ""))
        self.assertIsNone(resolve_selected_concept(raw, "Concept/Not_A_Real_Id"))
        resolved = resolve_selected_concept(raw, "Concept/IG_Stories")
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved["label"], "IG Stories")
        self.assertIn("Instagram Story", resolved["description"])

    def test_studio_payload_exposes_concepts(self) -> None:
        from dashboard.backend.routes.defaults import _studio_payload

        payload = _studio_payload(
            {
                "concept": json.dumps(
                    {
                        "Concept/Dont_Buy_This": {
                            "description": "Reverse psychology hook."
                        }
                    }
                )
            },
            source="generic",
        )
        self.assertEqual(
            payload["concepts"],
            [
                {
                    "id": "Concept/Dont_Buy_This",
                    "label": "Dont Buy This",
                    "description": "Reverse psychology hook.",
                }
            ],
        )

    def test_render_prompt_accepts_unknown_format_id(self) -> None:
        text = render_prompt(
            "STORY",
            "EN",
            "4:5",
            {
                "number": 3,
                "name": "Stress Snacker",
                "pain_en": "Stress creates food urges.",
                "desire_en": "A practical routine.",
                "friction_en": "Old plans were difficult.",
                "proof_needed_en": "Use verified product facts only.",
                "tone_cue_en": "Practical and calm.",
            },
            CopyBlock(
                headline="Stay consistent",
                cta="Learn more",
                support_line="A practical routine.",
            ),
            {"concept_angle": "desired_outcome"},
            {"id": "bg_test", "title": "Studio"},
            1,
            "A quiet table in soft daylight.",
            default_visual_archetype("STORY"),
        )
        self.assertIn("- Headline: Stay consistent", text)
        self.assertIn("- Support line: A practical routine.", text)
        self.assertIn("- CTA: Learn more", text)
        slot = pick_background_slot(
            {
                "variants": [
                    {
                        "id": "hero_only",
                        "formats": ["HERO"],
                        "title": "Studio",
                    }
                ]
            },
            "STORY",
            1,
        )
        self.assertEqual(slot["id"], "hero_only")

    def test_render_prompt_omits_concept_block_when_none(self) -> None:
        text = _hero_prompt()
        self.assertIn("PERSONA INPUT BLOCK", text)
        self.assertIn("- Concept angle: desired_outcome", text)
        self.assertNotIn("CONCEPT INPUT BLOCK", text)

    def test_render_prompt_includes_selected_concept_block(self) -> None:
        text = _hero_prompt(
            creative_concept={
                "id": "Concept/IG_Stories",
                "label": "IG Stories",
                "description": "Casual story format.",
            }
        )
        self.assertIn("- Concept angle: desired_outcome", text)
        self.assertIn("CONCEPT INPUT BLOCK", text)
        self.assertIn("- Concept: IG Stories", text)
        self.assertIn("- Description: Casual story format.", text)

    def test_render_prompt_uses_configured_proof_bar_text(self) -> None:
        text = _hero_prompt()
        self.assertIn(f"- Proof bar: {DEFAULT_PROOF_BAR_TEXT}", text)
        self.assertIn(f"- Exact proof bar text: {DEFAULT_PROOF_BAR_TEXT}", text)
        custom = render_prompt(
            "HERO",
            "EN",
            "4:5",
            {
                "number": 3,
                "name": "Stress Snacker",
                "pain_en": "Stress creates food urges.",
                "desire_en": "A practical routine.",
                "friction_en": "Old plans were difficult.",
                "proof_needed_en": "Use verified product facts only.",
                "tone_cue_en": "Practical and calm.",
            },
            CopyBlock(
                headline="Stay consistent",
                cta="Learn more",
                support_line="A practical routine.",
            ),
            {"concept_angle": "desired_outcome"},
            {"id": "bg_test", "title": "Studio"},
            1,
            "A quiet table in soft daylight.",
            default_visual_archetype("HERO"),
            templates={
                **json.loads((ROOT / "dashboard" / "backend" / "copy_system" / "prompt_assembler_templates.json").read_text(encoding="utf-8")),
                "proof_bar_text": "12,000+ Users | Exact brand lock",
            },
        )
        self.assertIn("- Proof bar: 12,000+ Users | Exact brand lock", custom)
        self.assertIn("- Exact proof bar text: 12,000+ Users | Exact brand lock", custom)
        self.assertNotIn(DEFAULT_PROOF_BAR_TEXT, custom)

    def test_planner_and_bundle_attach_selected_concept(self) -> None:
        from dashboard.backend.services.render_structured_copy import (
            _planned_ads,
            generate_structured_prompt_bundle,
        )

        catalog = json.dumps(
            {
                "Concept/IG_Stories": {
                    "description": "Casual story format.",
                }
            }
        )
        planned = _planned_ads(
            {
                "selected_personas": [3],
                "global_formats": ["HERO"],
                "multiplier": 1,
                "selected_concept": "Concept/IG_Stories",
            },
            {
                "persona_seeds": json.dumps(
                    [{"persona_number": 3, "persona_name": "Stress Snacker"}]
                ),
                "concept": catalog,
            },
        )
        self.assertNotIn("concept_angle", planned[0])
        self.assertEqual(
            planned[0]["creative_concept"],
            {
                "id": "Concept/IG_Stories",
                "label": "IG Stories",
                "description": "Casual story format.",
            },
        )

        calls: list[dict] = []

        def generate(request: dict, repair: bool = False) -> dict:
            calls.append(request)
            return {
                "ads": [
                    {
                        "concept_angle": "desired_outcome",
                        "copy": {
                            "EN": {
                                "headline": "Stay consistent",
                                "support_line": "A practical routine.",
                                "cta": "Learn more",
                            }
                        },
                    }
                ]
            }

        result = generate_structured_prompt_bundle(
            run_id="run-concept",
            run_number=3,
            settings={
                "selected_personas": [3],
                "global_formats": ["HERO"],
                "formats_by_persona": {},
                "multiplier": 1,
                "language_mode": "EN",
                "selected_concept": "Concept/IG_Stories",
            },
            effective_config={
                "product_master_doc": "Verified product facts.",
                "persona_seeds": json.dumps(
                    [{"persona_number": 3, "persona_name": "Stress Snacker"}]
                ),
                "concept": catalog,
                "background_variant": json.dumps(
                    {
                        "variants": [
                            {
                                "id": "hero_server",
                                "title": "Server background",
                                "base": "a clean product arrangement",
                                "formats": ["HERO"],
                            }
                        ]
                    }
                ),
                "prompt_assembler_templates": "{}",
            },
            provider_name="opencode",
            provider_model="opencode/big-pickle",
            generate=generate,
        )

        self.assertEqual(result["status"], "completed")
        self.assertNotIn("creative_concept", calls[0])
        self.assertEqual(
            calls[0]["planned_ads"][0]["creative_concept"]["id"],
            "Concept/IG_Stories",
        )
        self.assertIn("CONCEPT INPUT BLOCK", result["prompts"][0]["text"])
        self.assertIn("- Concept: IG Stories", result["prompts"][0]["text"])

        none_planned = _planned_ads(
            {
                "selected_personas": [3],
                "global_formats": ["HERO"],
                "multiplier": 1,
                "selected_concept": "",
            },
            {
                "persona_seeds": json.dumps(
                    [{"persona_number": 3, "persona_name": "Stress Snacker"}]
                ),
                "concept": catalog,
            },
        )
        self.assertNotIn("creative_concept", none_planned[0])


if __name__ == "__main__":
    unittest.main()
