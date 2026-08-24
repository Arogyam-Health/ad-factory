from __future__ import annotations

import json
import unittest

from dashboard.backend.services.copy_system import (
    COPY_SYSTEM_KEYS,
    HYPOTHESIS_FILES,
    format_layer,
    format_output_fields,
    hypothesis_catalog,
    hypothesis_layer,
)
from dashboard.backend.services.render_structured_copy import (
    generate_structured_prompt_bundle,
)


def _seeds() -> str:
    return json.dumps(
        [
            {
                "persona_number": 3,
                "persona_name": "Stress Snacker",
                "core_pattern": "Stress creates food urges.",
                "relevant_ok_kit_role": "A practical routine.",
                "why_it_failed": "Old plans were difficult.",
                "guardrail": "Do not claim treatment.",
            }
        ]
    )


def _background(fmt: str) -> str:
    return json.dumps(
        {
            "variants": [
                {
                    "id": f"{fmt.lower()}_slot",
                    "title": "Studio",
                    "base": "a clean product arrangement",
                    "formats": [fmt],
                }
            ]
        }
    )


def _copy_for(fmt: str) -> dict:
    if fmt in {"BA", "FEAT"}:
        return {
            "headline": "Keep the routine simple",
            "bullets": ["Before: guesswork", "After: a guided next step"],
            "cta": "Learn more",
        }
    if fmt == "TEST":
        return {
            "headline": "The routine finally fit",
            "attribution": "Representative user experience",
            "trust_line": "Use verified product facts only",
            "cta": "Learn more",
        }
    return {
        "headline": "Stay consistent",
        "support_line": "A practical next step",
        "cta": "Learn more",
    }


def _compile(
    *,
    formats: list[str],
    settings: dict | None = None,
    extra_config: dict | None = None,
    selected_concept: str = "",
) -> tuple[dict, dict]:
    calls: list[dict] = []
    planned_formats = list(formats)

    def generate(request: dict, repair: bool = False) -> dict:
        calls.append(request)
        return {
            "ads": [
                {"copy": {"EN": _copy_for(fmt)}}
                for fmt in planned_formats
            ]
        }

    payload_settings = {
        "selected_personas": [3],
        "global_formats": planned_formats,
        "formats_by_persona": {},
        "multiplier": 1,
        "language_mode": "EN",
        **(settings or {}),
    }
    if selected_concept:
        payload_settings["selected_concept"] = selected_concept
    config = {
        "product_master_doc": "Verified product facts.",
        "persona_seeds": _seeds(),
        "background_variant": _background(planned_formats[0]),
        "prompt_assembler_templates": "{}",
        **(extra_config or {}),
    }
    result = generate_structured_prompt_bundle(
        run_id="run-copy-system",
        run_number=1,
        settings=payload_settings,
        effective_config=config,
        provider_name="opencode",
        provider_model="opencode/big-pickle",
        generate=generate,
    )
    return calls[0], result


class CopySystemLoaderTests(unittest.TestCase):
    def test_registered_keys_and_hypothesis_types(self) -> None:
        self.assertEqual(
            COPY_SYSTEM_KEYS,
            [
                "ad_formats",
                "ad_hooks",
                "ad_angles",
                "ad_frameworks",
                "ad_proof",
                "ad_objections",
                "ad_value_props",
                "ad_awareness",
                "ad_emotions",
                "ad_specificity",
                "ad_feature_focus",
                "ad_support_shapes",
                "ad_guardrails",
            ],
        )
        catalog = hypothesis_catalog({})
        self.assertIn("none", catalog)
        for hyp_type in HYPOTHESIS_FILES:
            self.assertIn(hyp_type, catalog)
            self.assertTrue(catalog[hyp_type]["options"])

    def test_format_layer_omits_blank_fields(self) -> None:
        hero = format_layer({}, "HERO")
        self.assertEqual(hero["id"], "HERO")
        self.assertIn("description", hero)
        self.assertIn("skeleton", hero)
        self.assertEqual(
            hero["output_fields"],
            ["headline", "support_line", "trust_line", "cta"],
        )
        empty = format_layer({"ad_formats": "{}"}, "HERO")
        self.assertEqual(empty, {"id": "HERO"})
        self.assertNotIn("description", empty)

    def test_hypothesis_none_is_omitted(self) -> None:
        self.assertIsNone(hypothesis_layer({}, "none", ""))
        self.assertIsNone(hypothesis_layer({}, "", "pas"))
        layer = hypothesis_layer({}, "concept_angle", "pain_point")
        assert layer is not None
        self.assertEqual(layer["type"], "concept_angle")
        self.assertEqual(layer["style"], "pain_point")
        self.assertIn("definition", layer)
        self.assertIn("frustration", layer["definition"].lower())


class CopySystemRequestTests(unittest.TestCase):
    def test_hero_and_ba_payloads_differ_by_description_and_skeleton(self) -> None:
        request, result = _compile(formats=["HERO", "BA"])
        self.assertEqual(result["status"], "completed")
        hero = request["planned_ads"][0]["format"]
        ba = request["planned_ads"][1]["format"]
        self.assertEqual(hero["id"], "HERO")
        self.assertEqual(ba["id"], "BA")
        self.assertNotEqual(hero.get("description"), ba.get("description"))
        self.assertNotEqual(hero.get("skeleton"), ba.get("skeleton"))
        self.assertIn("support_line", hero["output_fields"])
        self.assertIn("bullets", ba["output_fields"])
        self.assertNotIn("bullets", hero["output_fields"])
        hero_schema = request["output_schema"]["ads"][0]["copy"]["EN"]
        ba_schema = request["output_schema"]["ads"][1]["copy"]["EN"]
        self.assertIn("support_line", hero_schema)
        self.assertIn("bullets", ba_schema)
        self.assertNotIn("support_line", ba_schema)

    def test_none_omits_hypothesis_and_concept_angle(self) -> None:
        request, result = _compile(formats=["HERO"])
        self.assertEqual(result["status"], "completed")
        planned = request["planned_ads"][0]
        self.assertNotIn("hypothesis", planned)
        self.assertNotIn("concept_angle", planned)
        self.assertNotIn("concept_angle", request)
        self.assertNotIn("desired_outcome", json.dumps(request))
        self.assertNotIn("background_group_key", json.dumps(request))
        self.assertNotIn("share_background_across_personas", json.dumps(request))
        self.assertTrue(
            any("NO HYPOTHESIS" in line for line in request.get("guardrails", []))
        )
        self.assertEqual(result["prompts"][0]["concept_angle"], "none")

    def test_pain_point_includes_definition_text(self) -> None:
        request, result = _compile(
            formats=["HERO"],
            settings={
                "hypothesis": {"type": "concept_angle", "variant": "pain_point"}
            },
        )
        self.assertEqual(result["status"], "completed")
        hypothesis = request["planned_ads"][0]["hypothesis"]
        self.assertEqual(hypothesis["type"], "concept_angle")
        self.assertEqual(hypothesis["style"], "pain_point")
        self.assertIn("definition", hypothesis)
        self.assertIn("frustration", hypothesis["definition"].lower())
        self.assertIn("instruction", hypothesis)
        self.assertEqual(result["prompts"][0]["concept_angle"], "pain_point")

    def test_empty_hook_style_still_generates(self) -> None:
        request, result = _compile(
            formats=["HERO"],
            settings={
                "hypothesis": {
                    "type": "hook_structure",
                    "variant": "question_led",
                }
            },
            extra_config={
                "ad_hooks": json.dumps({"question_led": {}}),
            },
        )
        self.assertEqual(result["status"], "completed")
        hypothesis = request["planned_ads"][0]["hypothesis"]
        self.assertEqual(hypothesis["type"], "hook_structure")
        self.assertEqual(hypothesis["style"], "question_led")
        self.assertNotIn("definition", hypothesis)
        self.assertNotIn("instruction", hypothesis)

    def test_creative_concept_appears_once_on_the_planned_ad(self) -> None:
        catalog = json.dumps(
            {
                "Concept/iPhone_Notes": {
                    "description": "Handwritten notes layout.",
                }
            }
        )
        request, result = _compile(
            formats=["HERO"],
            selected_concept="Concept/iPhone_Notes",
            extra_config={"concept": catalog},
        )
        self.assertEqual(result["status"], "completed")
        self.assertNotIn("creative_concept", request)
        self.assertEqual(
            request["planned_ads"][0]["creative_concept"]["id"],
            "Concept/iPhone_Notes",
        )
        encoded = json.dumps(request)
        self.assertEqual(encoded.count("Concept/iPhone_Notes"), 1)

    def test_test_format_uses_own_fields_and_no_fabricate_note(self) -> None:
        request, result = _compile(formats=["TEST"])
        self.assertEqual(result["status"], "completed")
        fmt = request["planned_ads"][0]["format"]
        self.assertEqual(fmt["id"], "TEST")
        self.assertIn("do not invent", fmt["description"].lower())
        self.assertEqual(
            format_output_fields({}, "TEST"),
            ["headline", "attribution", "trust_line", "cta"],
        )
        schema = request["output_schema"]["ads"][0]["copy"]["EN"]
        self.assertIn("attribution", schema)
        self.assertNotIn("support_line", schema)
        self.assertNotIn("bullets", schema)

    def test_support_shape_includes_definition(self) -> None:
        request, result = _compile(
            formats=["HERO"],
            settings={
                "hypothesis": {"type": "support_shape", "variant": "contrast"}
            },
        )
        self.assertEqual(result["status"], "completed")
        hypothesis = request["planned_ads"][0]["hypothesis"]
        self.assertEqual(hypothesis["type"], "support_shape")
        self.assertEqual(hypothesis["style"], "contrast")
        self.assertIn("fails", hypothesis["definition"].lower())
        self.assertEqual(result["prompts"][0]["concept_angle"], "contrast")

    def test_copy_starting_prompt_is_sent_when_non_empty(self) -> None:
        request, result = _compile(
            formats=["HERO"],
            extra_config={"copy_starting_prompt": "Prefer short headlines."},
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(request["starting_prompt"], "Prefer short headlines.")
        bare, _ = _compile(formats=["HERO"])
        self.assertNotIn("starting_prompt", bare)


if __name__ == "__main__":
    unittest.main()
