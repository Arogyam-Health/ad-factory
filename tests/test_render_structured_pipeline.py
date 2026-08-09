from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RenderStructuredPipelineTests(unittest.TestCase):
    def test_render_generates_and_assembles_final_prompts_without_local_assets(self) -> None:
        from dashboard.backend.services.render_structured_copy import (
            generate_structured_prompt_bundle,
        )

        calls: list[dict] = []

        def generate(request: dict, repair: bool = False) -> dict:
            calls.append({"request": request, "repair": repair})
            return {
                "ads": [
                    {
                        "concept_angle": "desired_outcome",
                        "copy": {
                            "EN": {
                                "headline": "Stay consistent",
                                "trust_line": "Built around verified product facts",
                                "cta": "Learn more",
                            }
                        },
                    }
                ]
            }

        result = generate_structured_prompt_bundle(
            run_id="run-server",
            run_number=7,
            settings={
                "selected_personas": [3],
                "global_formats": ["TEST"],
                "formats_by_persona": {},
                "multiplier": 1,
                "language_mode": "EN",
            },
            effective_config={
                "product_master_doc": "Verified product facts.",
                "persona_seeds": json.dumps(
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
                ),
                "background_variant": json.dumps(
                    {
                        "variants": [
                            {
                                "id": "test_server",
                                "title": "Server background",
                                "base": "a clean product arrangement",
                                "formats": ["TEST"],
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

        self.assertEqual(len(calls), 1)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["prompt_count"], 1)
        self.assertEqual(result["provider"], "opencode")
        self.assertEqual(result["model"], "opencode/big-pickle")
        self.assertEqual(len(result["prompts"]), 1)
        self.assertIn("Stay consistent", result["prompts"][0]["text"])
        self.assertNotIn("product_assets", calls[0]["request"])
        self.assertNotIn("image", json.dumps(calls[0]["request"]).lower())

    def test_prompt_delivery_ciphertext_round_trips_and_detects_tampering(self) -> None:
        from dashboard.backend.services.prompt_delivery import (
            decrypt_prompt_bundle,
            encrypt_prompt_bundle,
        )

        bundle = {
            "run_id": "run-server",
            "prompts": [{"prompt_id": "prm_one", "text": "Final prompt"}],
        }
        encrypted = encrypt_prompt_bundle(bundle)

        self.assertNotIn("Final prompt", encrypted["ciphertext"])
        self.assertEqual(decrypt_prompt_bundle(encrypted), bundle)
        with self.assertRaises(ValueError):
            decrypt_prompt_bundle(
                {**encrypted, "plaintext_sha256": "0" * 64}
            )

    def test_frontend_run_pipeline_does_not_stage_copy_inputs_on_localhost(self) -> None:
        source = (ROOT / "dashboard" / "frontend" / "js" / "main.js").read_text(
            encoding="utf-8"
        )
        start = source.index("async function runPipeline()")
        end = source.index(
            'document.getElementById("cancelRunBtn")', start
        )
        pipeline = source[start:end]

        self.assertNotIn("ensureStructuredLocal()", pipeline)
        self.assertNotIn("putProviderConfig(", pipeline)
        self.assertNotIn('putText("configs"', pipeline)
        self.assertNotIn('putText("documents"', pipeline)
        self.assertIn("/structured-copy", pipeline)

    def test_local_agent_no_longer_executes_structured_copy(self) -> None:
        source = (ROOT / "scripts" / "local_agent.py").read_text(encoding="utf-8")
        self.assertNotIn('== "generate_copy"', source)
        self.assertNotIn("StructuredCopyExecutor", source)


if __name__ == "__main__":
    unittest.main()
