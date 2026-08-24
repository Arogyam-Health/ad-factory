from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from dashboard.backend.services.render_copy_jobs import validate_copy_settings
from dashboard.backend.services.render_structured_copy import (
    ProviderCallError,
    assemble_browser_chunk_request,
    assemble_browser_warmup_message,
    assemble_copy_llm_request,
    generate_browser_structured_prompt_bundle,
    generate_structured_prompt_bundle,
    parse_browser_copy_json,
    reject_legacy_copy_llm_request,
)
from local_agent_runtime.browser_copy import execute_browser_copy, handle_provider_payload


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


def _backgrounds(formats: list[str]) -> str:
    return json.dumps(
        {
            "variants": [
                {
                    "id": f"{fmt.lower()}_slot",
                    "title": "Studio",
                    "base": "a clean product arrangement",
                    "formats": [fmt],
                }
                for fmt in formats
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


def _settings(formats: list[str], **extra: object) -> dict:
    payload = {
        "selected_personas": [3],
        "global_formats": formats,
        "formats_by_persona": {},
        "multiplier": 1,
        "language_mode": "EN",
        "batch_size": 2,
        "provider": "browser",
        "model": "chatgpt",
    }
    payload.update(extra)
    return payload


def _config(formats: list[str]) -> dict:
    return {
        "product_master_doc": "Verified product facts.",
        "copy_starting_prompt": "Write like a careful operator.",
        "persona_seeds": _seeds(),
        "background_variant": _backgrounds(formats),
        "prompt_assembler_templates": "{}",
    }


class ParseBrowserCopyJsonTests(unittest.TestCase):
    def test_parses_br_fenced_entities_and_extra_product_truths(self) -> None:
        raw = (
            "ChatGPT said:<br>```json<br>{<br>"
            "&quot;product_truths&quot;: [&quot;ignored&quot;],<br>"
            "&quot;ads&quot;: [{&quot;copy&quot;: {&quot;EN&quot;: {"
            "&quot;headline&quot;: &quot;A &amp; B&quot;}}}]<br>"
            "}<br>```"
        )
        parsed = parse_browser_copy_json(raw)
        self.assertEqual(parsed["product_truths"], ["ignored"])
        self.assertEqual(parsed["ads"][0]["copy"]["EN"]["headline"], "A & B")


class BrowserAssemblerTests(unittest.TestCase):
    def test_warmup_includes_product_doc_and_starting_prompt(self) -> None:
        message = assemble_browser_warmup_message(
            product_document="Verified product facts.",
            starting_prompt="Write like a careful operator.",
        )
        self.assertIn("product_document", message)
        self.assertEqual(message["starting_prompt"], "Write like a careful operator.")
        self.assertIn("Do not generate ads yet", message["task"])

    def test_chunk_request_omits_product_document(self) -> None:
        request = assemble_browser_chunk_request(
            planned=[
                {
                    "format": "HERO",
                    "persona": {"number": 3, "name": "Stress Snacker", "pain": "x"},
                }
            ],
            languages=("EN",),
            effective_config=_config(["HERO"]),
            product_document="Verified product facts.",
            starting_prompt="Write like a careful operator.",
        )
        self.assertNotIn("product_document", request)
        self.assertNotIn("starting_prompt", request)
        self.assertIn("planned_ads", request)
        self.assertIn("guardrails", request)

    def test_api_assembler_still_requires_product_document(self) -> None:
        request = assemble_copy_llm_request(
            planned=[
                {
                    "format": "HERO",
                    "persona": {"number": 3, "name": "Stress Snacker", "pain": "x"},
                }
            ],
            languages=("EN",),
            effective_config=_config(["HERO"]),
            product_document="Verified product facts.",
            starting_prompt="Write like a careful operator.",
        )
        self.assertIn("product_document", request)
        reject_legacy_copy_llm_request(request)
        with self.assertRaises(ValueError):
            reject_legacy_copy_llm_request({**request, "product_document": ""})


class BrowserBundleTests(unittest.TestCase):
    def test_warms_up_then_chunks_without_resending_product_doc(self) -> None:
        formats = ["HERO", "BA", "TEST", "FEAT", "UGC"]
        calls: list[dict] = []

        def transport(payload: dict) -> dict:
            calls.append(payload)
            action = payload["action"]
            if action == "new":
                return {
                    "http_status": 200,
                    "body": "I have read the product context.",
                    "transport_error": "",
                }
            if action == "close":
                return {"http_status": 200, "body": "", "transport_error": ""}
            request = json.loads(payload["prompt"])
            ads = [
                {"copy": {"EN": _copy_for(str(item["format"]["id"]))}}
                for item in request["planned_ads"]
            ]
            return {
                "http_status": 200,
                "body": json.dumps({"product_truths": ["ignored"], "ads": ads}),
                "transport_error": "",
            }

        result = generate_browser_structured_prompt_bundle(
            run_id="run-browser",
            run_number=1,
            settings=_settings(formats),
            effective_config=_config(formats),
            provider_name="browser",
            provider_model="chatgpt",
            transport=transport,
        )
        actions = [item["action"] for item in calls]
        self.assertEqual(actions[0], "new")
        self.assertEqual(actions.count("continue"), 3)
        self.assertEqual(actions[-1], "close")
        warmup = json.loads(calls[0]["prompt"])
        self.assertIn("product_document", warmup)
        self.assertEqual(warmup["starting_prompt"], "Write like a careful operator.")
        self.assertTrue(calls[0]["session_id"].startswith("bcs_"))
        session_ids = {item["session_id"] for item in calls}
        self.assertEqual(len(session_ids), 1)
        for item in calls:
            if item["action"] != "continue":
                continue
            prompt = json.loads(item["prompt"])
            self.assertNotIn("product_document", prompt)
            self.assertNotIn("starting_prompt", prompt)
        self.assertEqual(result["copy_count"], 5)
        self.assertEqual(result["batch_size"], 2)
        self.assertEqual(result["repair_count"], 0)
        self.assertEqual(result["provider"], "browser")

    def test_invalid_chunk_repairs_once_then_fails(self) -> None:
        calls: list[dict] = []

        def transport(payload: dict) -> dict:
            calls.append(payload)
            action = payload["action"]
            if action in {"new", "close"}:
                return {"http_status": 200, "body": "ok", "transport_error": ""}
            return {
                "http_status": 200,
                "body": json.dumps({"ads": [{"copy": {"EN": {"headline": ""}}}]}),
                "transport_error": "",
            }

        with self.assertRaises(ProviderCallError) as raised:
            generate_browser_structured_prompt_bundle(
                run_id="run-browser-repair",
                run_number=1,
                settings=_settings(["HERO"], batch_size=10),
                effective_config=_config(["HERO"]),
                provider_name="browser",
                provider_model="gemini",
                transport=transport,
            )
        self.assertEqual(raised.exception.code, "provider_invalid_output")
        self.assertEqual([item["action"] for item in calls], ["new", "continue", "repair", "close"])

    def test_opencode_bundle_chunks_and_resends_product_document(self) -> None:
        formats = ["HERO", "BA", "TEST", "FEAT", "UGC"]
        calls: list[dict] = []

        def generate(request: dict, repair: bool = False) -> dict:
            calls.append(request)
            return {
                "ads": [
                    {"copy": {"EN": _copy_for(str(item["format"]["id"]))}}
                    for item in request["planned_ads"]
                ]
            }

        result = generate_structured_prompt_bundle(
            run_id="run-opencode",
            run_number=1,
            settings=_settings(formats, provider="opencode", model="opencode/big-pickle", batch_size=2),
            effective_config=_config(formats),
            provider_name="opencode",
            provider_model="opencode/big-pickle",
            generate=generate,
        )
        self.assertEqual(len(calls), 3)
        self.assertEqual([len(item["planned_ads"]) for item in calls], [2, 2, 1])
        for request in calls:
            self.assertIn("product_document", request)
            self.assertEqual(request["starting_prompt"], "Write like a careful operator.")
        self.assertEqual(result["copy_count"], 5)
        self.assertEqual(result["batch_size"], 2)


class BrowserSettingsAndAgentTests(unittest.TestCase):
    def test_settings_accept_browser_engines(self) -> None:
        settings = validate_copy_settings(
            {
                "selected_personas": [3],
                "global_formats": ["HERO"],
                "language_mode": "EN",
                "provider": "browser",
                "model": "ChatGPT",
            }
        )
        self.assertEqual(settings["provider"], "browser")
        self.assertEqual(settings["model"], "chatgpt")
        gemini = validate_copy_settings(
            {
                "selected_personas": [3],
                "global_formats": ["HERO"],
                "language_mode": "EN",
                "provider": "browser",
                "model": "gemini",
            }
        )
        self.assertEqual(gemini["model"], "gemini")
        with self.assertRaises(ValueError):
            validate_copy_settings(
                {
                    "selected_personas": [3],
                    "global_formats": ["HERO"],
                    "language_mode": "EN",
                    "provider": "browser",
                    "model": "claude",
                }
            )

    def test_execute_browser_copy_validates_payload(self) -> None:
        with self.assertRaises(ValueError):
            execute_browser_copy({"engine": "claude", "action": "new", "session_id": "bcs_1"})
        with self.assertRaises(ValueError):
            execute_browser_copy(
                {
                    "engine": "chatgpt",
                    "action": "continue",
                    "session_id": "bcs_missing",
                    "prompt": "{}",
                }
            )
        closed = execute_browser_copy(
            {
                "engine": "chatgpt",
                "action": "close",
                "session_id": "bcs_missing",
            }
        )
        self.assertEqual(closed["http_status"], 200)

    def test_handle_provider_payload_dispatches_browser(self) -> None:
        with patch(
            "local_agent_runtime.browser_copy.execute_browser_copy",
            return_value={"http_status": 200, "body": "ok", "transport_error": ""},
        ) as browser:
            result = handle_provider_payload(
                {
                    "provider": "browser",
                    "engine": "chatgpt",
                    "action": "close",
                    "session_id": "bcs_test",
                }
            )
        self.assertEqual(result["body"], "ok")
        browser.assert_called_once()

    def test_handle_provider_payload_keeps_http_relay(self) -> None:
        with patch(
            "local_agent_runtime.provider_relay.execute_provider_call",
            return_value={"http_status": 200, "body": "{}", "transport_error": ""},
        ) as http:
            result = handle_provider_payload(
                {
                    "provider": "opencode",
                    "endpoint": "https://opencode.ai/zen/v1/chat/completions",
                    "api_key": "secret",
                    "request_body": {"model": "x"},
                }
            )
        self.assertEqual(result["http_status"], 200)
        http.assert_called_once()


if __name__ == "__main__":
    unittest.main()
