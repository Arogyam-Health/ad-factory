from __future__ import annotations

import json
import unittest

from dashboard.backend.services.copy_system import (
    COPY_SYSTEM_KEYS,
    HYPOTHESIS_FILES,
    copy_repair_task,
    copy_task,
    format_catalog,
    format_layer,
    format_output_fields,
    hypothesis_catalog,
    hypothesis_layer,
    persona_source_map,
)
from dashboard.backend.services.render_structured_copy import (
    _planned_ads,
    assemble_copy_llm_request,
    generate_structured_prompt_bundle,
    reject_legacy_copy_llm_request,
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
                "ad_languages",
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
        self.assertEqual(format_output_fields({"ad_formats": "{}"}, "HERO"), ["headline", "cta"])

    def test_format_catalog_includes_custom_ids(self) -> None:
        catalog = format_catalog(
            {
                "ad_formats": json.dumps(
                    {
                        "HERO": {"label": "Hero"},
                        "STORY": {"label": "Story"},
                    }
                )
            }
        )
        self.assertEqual(
            catalog,
            [{"id": "HERO", "label": "Hero"}, {"id": "STORY", "label": "Story"}],
        )

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
    def test_language_layers_send_config_rules(self) -> None:
        request, result = _compile(formats=["HERO"])
        self.assertEqual(result["status"], "completed")
        languages = request["languages"]
        self.assertEqual(languages[0]["id"], "EN")
        self.assertEqual(languages[0]["label"], "English")
        self.assertTrue(
            any("fully English" in line for line in languages[0]["rules"])
        )
        self.assertFalse(
            any("image" in line.lower() for line in languages[0]["rules"])
        )

        custom, custom_result = _compile(
            formats=["HERO"],
            extra_config={
                "ad_languages": json.dumps(
                    {
                        "_modes": {"EN": {"label": "EN", "languages": ["EN"]}},
                        "EN": {
                            "label": "English",
                            "rules": ["Keep EN copy under six words."],
                        },
                    }
                )
            },
        )
        self.assertEqual(custom_result["status"], "completed")
        self.assertEqual(custom["languages"][0]["rules"], ["Keep EN copy under six words."])

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

    def test_auto_rotate_does_not_default_to_first_catalog_pattern(self) -> None:
        request, result = _compile(
            formats=["HERO"],
            extra_config={
                "copy_prompt_templates": json.dumps(
                    {
                        "visual_archetypes": {
                            "HERO": [
                                {"id": "hero_first", "label": "First", "layout_lines": ["- first"]},
                                {"id": "hero_second", "label": "Second", "layout_lines": ["- second"]},
                                {"id": "hero_third", "label": "Third", "layout_lines": ["- third"]},
                                {"id": "hero_fourth", "label": "Fourth", "layout_lines": ["- fourth"]},
                            ]
                        }
                    }
                )
            },
        )
        self.assertNotIn("format_pattern", request["planned_ads"][0])
        picked = {prompt["visual_archetype"] for prompt in result["prompts"]}
        self.assertTrue(picked <= {"hero_first", "hero_second", "hero_third", "hero_fourth"})

    def test_llm_decide_uses_visual_archetype_prompt_and_skips_copy_pattern(self) -> None:
        request, result = _compile(
            formats=["HERO"],
            settings={"visual_archetypes_by_format": {"HERO": "llm_decide"}},
            extra_config={
                "visual_archetype_llm_prompt": "Choose any clean product-led crop.",
            },
        )
        self.assertNotIn("format_pattern", request["planned_ads"][0])
        self.assertEqual(result["prompts"][0]["visual_archetype"], "llm_decide")
        self.assertIn("Choose any clean product-led crop.", result["prompts"][0]["text"])

    def test_custom_output_fields_are_required_and_extras_ignored(self) -> None:
        calls: list[tuple[dict, bool]] = []

        def generate(request: dict, repair: bool = False) -> dict:
            calls.append((request, repair))
            if repair:
                return {
                    "ads": [
                        {
                            "copy": {
                                "EN": {
                                    "headline": "Keep the note short",
                                    "note": "One practical next step",
                                    "cta": "Learn more",
                                    "support_line": "ignored extra",
                                }
                            }
                        }
                    ]
                }
            return {
                "ads": [
                    {
                        "copy": {
                            "EN": {
                                "headline": "Keep the note short",
                                "cta": "Learn more",
                                "support_line": "ignored extra",
                            }
                        }
                    }
                ]
            }

        result = generate_structured_prompt_bundle(
            run_id="run-custom-fields",
            run_number=1,
            settings={
                "selected_personas": [3],
                "global_formats": ["NOTE"],
                "formats_by_persona": {},
                "multiplier": 1,
                "language_mode": "EN",
            },
            effective_config={
                "product_master_doc": "Verified product facts.",
                "persona_seeds": _seeds(),
                "background_variant": _background("NOTE"),
                "prompt_assembler_templates": "{}",
                "ad_formats": json.dumps(
                    {
                        "NOTE": {
                            "label": "Note",
                            "output_fields": ["headline", "note", "cta"],
                        }
                    }
                ),
            },
            provider_name="opencode",
            provider_model="opencode/big-pickle",
            generate=generate,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual([repair for _, repair in calls], [False, True])
        schema = calls[0][0]["output_schema"]["ads"][0]["copy"]["EN"]
        self.assertEqual(schema, {"headline": "string", "note": "string", "cta": "string"})
        self.assertEqual(calls[0][0]["planned_ads"][0]["format"]["id"], "NOTE")

        def never_note(request: dict, repair: bool = False) -> dict:
            return {
                "ads": [
                    {
                        "copy": {
                            "EN": {
                                "headline": "Keep the note short",
                                "cta": "Learn more",
                                "support_line": "ignored extra",
                            }
                        }
                    }
                ]
            }

        with self.assertRaises(Exception) as raised:
            generate_structured_prompt_bundle(
                run_id="run-custom-fields-fail",
                run_number=1,
                settings={
                    "selected_personas": [3],
                    "global_formats": ["NOTE"],
                    "formats_by_persona": {},
                    "multiplier": 1,
                    "language_mode": "EN",
                },
                effective_config={
                    "product_master_doc": "Verified product facts.",
                    "persona_seeds": _seeds(),
                    "background_variant": _background("NOTE"),
                    "prompt_assembler_templates": "{}",
                    "ad_formats": json.dumps(
                        {
                            "NOTE": {
                                "label": "Note",
                                "output_fields": ["headline", "note", "cta"],
                            }
                        }
                    ),
                },
                provider_name="opencode",
                provider_model="opencode/big-pickle",
                generate=never_note,
            )
        self.assertEqual(raised.exception.code, "provider_invalid_output")
        self.assertIn("note_missing", raised.exception.error_detail)

    def test_copy_starting_prompt_is_sent_when_non_empty(self) -> None:
        request, result = _compile(
            formats=["HERO"],
            extra_config={"copy_starting_prompt": "Prefer short headlines."},
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(request["starting_prompt"], "Prefer short headlines.")
        bare, _ = _compile(formats=["HERO"])
        self.assertNotIn("starting_prompt", bare)

    def test_copy_task_and_persona_aliases_come_from_config(self) -> None:
        self.assertEqual(copy_task({}), "Generate structured advertising copy as JSON")
        self.assertEqual(
            copy_repair_task({}),
            "Repair structured copy validation errors and return JSON only",
        )
        aliases = persona_source_map({})
        self.assertIn("relevant_ok_kit_role", aliases["desire_en"])
        request, result = _compile(
            formats=["HERO"],
            extra_config={
                "ad_guardrails": json.dumps(
                    {
                        "task": "Write structured advertising copy as JSON",
                        "repair_task": "Fix structured copy validation errors and return JSON only",
                        "always": ["Use only supplied product facts."],
                    }
                )
            },
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(request["task"], "Write structured advertising copy as JSON")
        self.assertNotIn("image", json.dumps(request).lower())

    def test_legacy_plan_dump_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            reject_legacy_copy_llm_request(
                {
                    "task": "Generate structured advertising copy as JSON",
                    "product_document": "facts",
                    "planned_ads": [
                        {
                            "format": "HERO",
                            "concept_angle": "desired_outcome",
                            "background_group_key": "HERO::P01",
                            "hypothesis": {
                                "type": "concept_angle",
                                "variant": "desired_outcome",
                                "hypothesis_id": "concept_angle-desired_outcome",
                            },
                        }
                    ],
                    "languages": ["EN"],
                    "requirements": {"json_only": True},
                    "output_schema": {
                        "product_truths": ["string"],
                        "ads": [{"copy": {"EN": {"headline": "string"}}}],
                    },
                }
            )

    def test_studio_settings_compile_layered_request(self) -> None:
        from dashboard.backend.services.copy_system import resolve_language_ids
        from dashboard.backend.services.user_config import _repository_generic_config

        config = _repository_generic_config()
        settings = {
            "selected_personas": [1, 2, 3],
            "global_formats": ["FEAT"],
            "formats_by_persona": {
                "1": ["HERO", "FEAT"],
                "2": ["BA", "TEST"],
                "3": ["HERO", "UGC"],
            },
            "multiplier": 2,
            "language_mode": "EN",
            "hypothesis": {
                "type": "concept_angle",
                "variant": "desired_outcome",
            },
            "selected_concept": "Concept/Dont_Buy_This",
            "share_background_across_personas": False,
        }
        planned = _planned_ads(settings, config)
        self.assertEqual(len(planned), 12)
        request = assemble_copy_llm_request(
            planned=planned,
            languages=resolve_language_ids(config, "EN"),
            effective_config=config,
            product_document=str(config["product_master_doc"]),
        )
        encoded = json.dumps(request)
        self.assertNotIn("product_truths", encoded)
        self.assertNotIn("requirements", request)
        self.assertNotIn("background_group_key", encoded)
        self.assertNotIn("hypothesis_id", encoded)
        self.assertEqual(request["languages"][0]["id"], "EN")
        self.assertTrue(request["languages"][0]["rules"])
        self.assertTrue(request["guardrails"])
        self.assertEqual(len(request["planned_ads"]), 12)
        first = request["planned_ads"][0]
        self.assertEqual(first["format"]["id"], "HERO")
        self.assertIn("support_line", first["format"]["output_fields"])
        self.assertIsInstance(first["format"]["description"], str)
        self.assertEqual(first["hypothesis"]["type"], "concept_angle")
        self.assertEqual(first["hypothesis"]["style"], "desired_outcome")
        self.assertIn("Lead with what the person wants", first["hypothesis"]["definition"])
        self.assertEqual(first["creative_concept"]["id"], "Concept/Dont_Buy_This")
        self.assertNotIn("pain_hi", first["persona"])
        self.assertIn("pain", first["persona"])
        feat = next(
            item for item in request["planned_ads"] if item["format"]["id"] == "FEAT"
        )
        test_ad = next(
            item for item in request["planned_ads"] if item["format"]["id"] == "TEST"
        )
        self.assertEqual(feat["format"]["output_fields"], ["headline", "bullets", "cta"])
        self.assertEqual(
            test_ad["format"]["output_fields"],
            ["headline", "attribution", "trust_line", "cta"],
        )
        schemas = {
            item["format"]["id"]: schema["copy"]["EN"]
            for item, schema in zip(request["planned_ads"], request["output_schema"]["ads"])
        }
        self.assertIn("support_line", schemas["HERO"])
        self.assertIn("bullets", schemas["FEAT"])
        self.assertNotIn("support_line", schemas["FEAT"])
        self.assertIn("attribution", schemas["TEST"])
        self.assertNotIn("bullets", schemas["TEST"])
        self.assertIn("support_line", schemas["UGC"])
        self.assertNotIn("trust_line", schemas["UGC"])

        calls: list[dict] = []

        def generate(payload: dict, repair: bool = False) -> dict:
            calls.append(payload)
            return {
                "ads": [
                    {"copy": {"EN": _copy_for(str(item["format"]["id"]))}}
                    for item in payload["planned_ads"]
                ]
            }

        result = generate_structured_prompt_bundle(
            run_id="run-studio-compile",
            run_number=12,
            settings=settings,
            effective_config=config,
            provider_name="opencode",
            provider_model="opencode/nemotron-3.5-lightning-free",
            generate=generate,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["copy_count"], 12)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["planned_ads"][0]["format"]["id"], "HERO")
        self.assertNotIn("product_truths", json.dumps(calls[0]))


if __name__ == "__main__":
    unittest.main()
