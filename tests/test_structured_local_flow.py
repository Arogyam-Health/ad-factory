from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests


class StructuredLocalFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        from local_agent_runtime.storage import AgentPaths, AgentState

        self.temp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self.temp.name))
        self.state = AgentState(self.paths)
        self.owner = "user:user-1"
        self.run_id = "run-local-copy"
        self.state.create_run(
            run_id=self.run_id,
            owner_key=self.owner,
            device_id="dev_" + "a" * 32,
            workspace_id="wrk-copy",
            run_number=8,
            flow_type="structured",
            operation_id="create-run",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _put_json(self, logical_key: str, value: dict, operation_id: str):
        path = self.paths.staging / f"{logical_key}.json"
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return self.state.put_resource(
            source=path,
            owner_key=self.owner,
            kind="config_file",
            logical_key=logical_key,
            operation_id=operation_id,
            media_type="application/json",
        )

    def _stage_run(
        self, provider: str = "fake", *, include_product_asset: bool = True, language_mode: str = "EN"
    ) -> None:
        document = self.paths.staging / "product.txt"
        document.write_text("A local product document with verified product facts.", encoding="utf-8")
        product_doc = self.state.put_resource(
            source=document,
            owner_key=self.owner,
            kind="product_document",
            logical_key=f"{self.run_id}-product-document",
            operation_id="put-document",
            media_type="text/plain",
        )
        product_asset = None
        if include_product_asset:
            asset = self.paths.staging / "product.png"
            asset.write_bytes(b"\x89PNG\r\n\x1a\nlocal-product")
            product_asset = self.state.put_resource(
                source=asset,
                owner_key=self.owner,
                kind="product_image",
                logical_key="selected-product",
                operation_id="put-asset",
                media_type="image/png",
            )
        settings = self._put_json(
            f"{self.run_id}-structured-settings",
            {
                "execution": {
                    "provider": provider,
                    "model": "fake-copy-v1" if provider == "fake" else "provider-model-v1",
                    "language_mode": language_mode,
                    "seed": 818,
                    "max_repair_attempts": 1,
                },
                "product_document": {
                    "resource_id": product_doc.resource_id,
                    "version": product_doc.version,
                },
                "product_assets": (
                    [
                        {
                            "resource_id": product_asset.resource_id,
                            "version": product_asset.version,
                        }
                    ]
                    if product_asset
                    else []
                ),
                "planned_ads": [
                    {
                        "format": "HERO",
                        "persona": {
                            "number": 1,
                            "name": "Busy Professional",
                            "pain_en": "Busy days make routines inconsistent.",
                            "desire_en": "A simple daily wellness routine.",
                            "friction_en": "Complicated plans are abandoned.",
                            "proof_needed_en": "Clear product facts and practical proof.",
                            "tone_cue_en": "Direct and reassuring.",
                            "pain_hi": "व्यस्त दिन रूटीन बिगाड़ते हैं।",
                            "desire_hi": "एक आसान रोज़ाना रूटीन।",
                            "friction_hi": "जटिल प्लान छूट जाते हैं।",
                            "proof_needed_hi": "साफ तथ्य और व्यावहारिक प्रमाण।",
                            "tone_cue_hi": "सीधा और भरोसेमंद।",
                        },
                        "concept_angle": "desired_outcome",
                    }
                ],
                "prompt_assembler_templates": json.loads(
                    (
                        Path(__file__).resolve().parents[1]
                        / "scripts"
                        / "prompt_assembler_templates.json"
                    ).read_text(encoding="utf-8")
                ),
            },
            "put-settings",
        )
        backgrounds = self._put_json(
            f"{self.run_id}-backgrounds",
            {
                "variants": [
                    {
                        "id": "hero_local",
                        "title": "Local studio",
                        "base": "a premium product arrangement",
                        "formats": ["HERO"],
                        "surface": ["warm stone"],
                        "environment": ["minimal studio"],
                        "lighting": ["soft daylight"],
                        "mood": ["calm confidence"],
                        "camera": ["eye-level product shot"],
                        "composition": ["clear central hierarchy"],
                        "layout_intent": ["space for exact ad copy"],
                        "cta_safe_space": ["clean lower CTA zone"],
                        "crop_safety": ["all objects inside safe margins"],
                        "text_overlay_treatment": ["high-contrast typography"],
                        "edge_tone_control": ["quiet edges"],
                        "color_tone": ["warm neutrals"],
                    }
                ]
            },
            "put-backgrounds",
        )
        entries = [
            (product_doc, "product_document"),
            (settings, "structured_settings"),
            (backgrounds, "backgrounds"),
        ]
        if product_asset:
            entries.insert(1, (product_asset, "product"))
        for position, (resource, role) in enumerate(
            entries,
            start=1,
        ):
            self.state.add_run_entry(
                run_id=self.run_id,
                entry_id=f"entry-{position}",
                resource_id=resource.resource_id,
                resource_version=resource.version,
                role=role,
                position=position,
                operation_id=f"entry-{position}",
            )

    def _assert_http_secret_is_not_persisted(
        self,
        *,
        provider_name: str,
        secret: str,
        expected_header: str,
    ) -> None:
        from local_agent_runtime.structured_copy import LocalProviderStore, StructuredCopyExecutor

        self._stage_run(provider_name)
        config = {"api_key": secret, "default_model": "provider-model-v1"}
        if provider_name == "opencode":
            config["api_url"] = "https://provider.invalid/v1"
        LocalProviderStore(self.paths).set(self.owner, provider_name, config)
        job_id = f"job-{provider_name}"
        self.state.record_job(
            job_id,
            self.owner,
            "pending",
            {"run_id": self.run_id, "command": "generate_copy", "parameters": {}},
        )
        captured: dict[str, object] = {}

        def fail_request(url, **kwargs):
            captured["url"] = url
            captured["headers"] = kwargs.get("headers") or {}
            error = requests.RequestException(
                f"transport failed url={url} headers={kwargs.get('headers') or {}}"
            )
            captured["exception"] = error
            raise error

        with patch("local_agent_runtime.structured_copy.requests.post", side_effect=fail_request):
            result = StructuredCopyExecutor(self.state).execute(job_id)

        self.assertEqual(result["status"], "failed")
        self.assertNotIn(secret, str(captured["url"]))
        self.assertIn(secret, captured["headers"][expected_header])
        serialized_outbox = json.dumps(
            [event["payload"] for event in self.state.pending_outbox(100)]
        )
        self.assertNotIn(secret, serialized_outbox)
        local_bytes = b"".join(
            path.read_bytes()
            for path in self.paths.root.rglob("*")
            if path.is_file()
        )
        self.assertFalse(secret.encode() in local_bytes)
        self.assertFalse(any(self.paths.logs.iterdir()))
        trace_entry = next(
            entry
            for entry in self.state.resolve_job_context(job_id)["entries"]
            if entry["kind"] == "trace"
        )
        trace = json.loads(Path(trace_entry["local_path"]).read_text(encoding="utf-8"))
        self.assertEqual(trace["error_type"], "ProviderRequestError")
        self.assertEqual(trace["error_code"], "provider_request_failed")
        self.assertNotIn("error", trace)

    def test_google_secret_never_enters_urls_or_error_persistence(self) -> None:
        self._assert_http_secret_is_not_persisted(
            provider_name="google_gemini",
            secret="google-secret-test-value",
            expected_header="x-goog-api-key",
        )

    def test_opencode_authorization_never_enters_error_persistence(self) -> None:
        self._assert_http_secret_is_not_persisted(
            provider_name="opencode",
            secret="opencode-secret-test-value",
            expected_header="Authorization",
        )

    @staticmethod
    def _valid_response() -> dict:
        return {
            "ads": [
                {
                    "format": "HERO",
                    "persona": {"number": 1, "name": "Busy Professional"},
                    "concept_angle": "desired_outcome",
                    "copy": {
                        "EN": {
                            "headline": "Wellness that fits your day",
                            "support_line": "A clear routine built around real product facts.",
                            "cta": "Build your routine",
                        }
                    },
                }
            ]
        }

    def test_provider_secrets_are_encrypted_with_protected_local_files(self) -> None:
        from local_agent_runtime.structured_copy import LocalProviderStore

        store = LocalProviderStore(self.paths)
        metadata = store.set(
            self.owner,
            "opencode",
            {"api_url": "https://provider.invalid/v1", "api_key": "test-secret-value", "default_model": "m1"},
        )
        raw = store.secret_path(self.owner, "opencode").read_bytes()

        self.assertNotIn(b"test-secret-value", raw)
        self.assertEqual(os.stat(store.key_path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(store.secret_path(self.owner, "opencode")).st_mode & 0o777, 0o600)
        self.assertTrue(metadata["has_secret"])
        self.assertNotIn("api_key", metadata)
        self.assertEqual(store.get(self.owner, "opencode")["api_key"], "test-secret-value")

    def test_success_uses_local_manifest_and_stores_immutable_resources(self) -> None:
        from local_agent_runtime.structured_copy import DeterministicFakeProvider, StructuredCopyExecutor

        self._stage_run()
        self.state.record_job(
            "job-copy",
            self.owner,
            "pending",
            {"run_id": self.run_id, "command": "generate_copy", "parameters": {"manifest_version": 1}},
        )
        result = StructuredCopyExecutor(
            self.state, provider=DeterministicFakeProvider([self._valid_response()])
        ).execute("job-copy")

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["copy_count"], 1)
        self.assertEqual(result["prompt_count"], 1)
        self.assertEqual(result["asset_count"], 1)
        self.assertNotIn("wellness that fits your day", json.dumps(result).lower())
        context = self.state.resolve_job_context("job-copy")
        kinds = {entry["kind"] for entry in context["entries"]}
        self.assertTrue({"copy_batch", "trace", "prompt", "prompt_sidecar"}.issubset(kinds))
        prompt_entry = next(entry for entry in context["entries"] if entry["kind"] == "prompt")
        prompt_text = Path(prompt_entry["local_path"]).read_text(encoding="utf-8")
        self.assertIn("PRODUCT LOCK BLOCK", prompt_text)
        self.assertIn("Wellness that fits your day", prompt_text)
        with self.state._connect() as conn:
            prompt_meta = json.loads(
                conn.execute(
                    """
                    SELECT rv.metadata_json FROM run_entries re
                    JOIN resource_versions rv
                      ON rv.resource_id = re.resource_id
                     AND rv.version = re.resource_version
                    WHERE re.run_id = ? AND re.role = 'prompt'
                    """,
                    (self.run_id,),
                ).fetchone()["metadata_json"]
            )
        self.assertEqual(prompt_meta["display_stem"], "HERO_busy_professional_EN_desired_outcome")
        self.assertEqual(prompt_meta["language"], "EN")

        projection = self.state.pending_outbox()[0]["payload"]
        serialized = json.dumps(projection).lower()
        for forbidden in (
            "request_body",
            "response_body",
            "prompt_body",
            "test-secret-value",
            "wellness that fits your day",
            "local product document",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(projection["provider"], "fake")
        self.assertIn("request_sha256", projection)
        self.assertIn("response_sha256", projection)

    def test_copy_generation_does_not_require_product_images(self) -> None:
        from local_agent_runtime.structured_copy import (
            DeterministicFakeProvider,
            StructuredCopyExecutor,
        )

        self._stage_run(include_product_asset=False)
        self.state.record_job(
            "job-copy-without-images",
            self.owner,
            "pending",
            {
                "run_id": self.run_id,
                "command": "generate_copy",
                "parameters": {},
            },
        )
        result = StructuredCopyExecutor(
            self.state,
            provider=DeterministicFakeProvider([self._valid_response()]),
        ).execute("job-copy-without-images")

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["asset_count"], 0)
        self.assertEqual(result["prompt_count"], 1)

    def test_invalid_copy_is_repaired_locally_and_trace_is_not_logged(self) -> None:
        from local_agent_runtime.structured_copy import DeterministicFakeProvider, StructuredCopyExecutor

        self._stage_run()
        self.state.record_job(
            "job-repair",
            self.owner,
            "pending",
            {"run_id": self.run_id, "command": "generate_copy", "parameters": {}},
        )
        provider = DeterministicFakeProvider([{"ads": []}, self._valid_response()])
        result = StructuredCopyExecutor(self.state, provider=provider).execute("job-repair")

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["repair_count"], 1)
        self.assertEqual(provider.calls, 2)
        self.assertFalse((self.paths.logs / "job-repair.log").exists())

    def test_failure_retry_and_idempotency_do_not_duplicate_resources(self) -> None:
        from local_agent_runtime.structured_copy import DeterministicFakeProvider, StructuredCopyExecutor

        self._stage_run()
        self.state.record_job(
            "job-failure",
            self.owner,
            "pending",
            {"run_id": self.run_id, "command": "generate_copy", "parameters": {}},
        )
        failing = StructuredCopyExecutor(
            self.state, provider=DeterministicFakeProvider([RuntimeError("fake failure")])
        )
        failed = failing.execute("job-failure")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error_code"], "provider_failed")
        self.assertNotIn("fake failure", json.dumps(failed))

        successful = StructuredCopyExecutor(
            self.state, provider=DeterministicFakeProvider([self._valid_response()])
        )
        first = successful.execute("job-failure")
        for event in self.state.pending_outbox(100):
            self.state.mark_outbox_delivered(event["event_id"])
        second = successful.execute("job-failure")
        self.assertEqual(first, second)
        self.assertEqual(successful.provider.calls, 1)
        with self.state._connect() as conn:
            counts = {
                row["kind"]: row["count"]
                for row in conn.execute(
                    "SELECT kind, COUNT(*) AS count FROM resources GROUP BY kind"
                ).fetchall()
            }
        self.assertEqual(counts["copy_batch"], 1)
        self.assertEqual(counts["prompt"], 1)

    def test_dashboard_queues_only_pinned_structured_image_metadata(self) -> None:
        from dashboard.backend.agent.routes import queue_structured_image_generation
        from tests.test_agent_metadata_jobs import _DB

        db = _DB()
        device_id = "dev_" + "e" * 32
        db["agents"].insert_one(
            {
                "agent_id": "agent-images",
                "user_id": "user-images",
                "device_id": device_id,
                "is_active": True,
            }
        )
        db["runs"].insert_one(
            {
                "run_id": "run-images-control",
                "user_id": "user-images",
                "owner_type": "user",
                "owner_id": "user-images",
                "agent_id": "agent-images",
                "device_id": device_id,
                "flow_type": "structured",
            }
        )
        with (
            patch("dashboard.backend.db.client.get_sync_db", return_value=db),
            patch("dashboard.backend.agent.service.get_sync_db", return_value=db),
        ):
            result = queue_structured_image_generation(
                "run-images-control",
                {
                    "operation_id": "structured-images-operation",
                    "engine": "chatgpt",
                    "mode": "both",
                },
                {"user_id": "user-images"},
            )

        self.assertEqual(result["device_id"], device_id)
        job = db["agent_jobs"].docs[0]
        self.assertEqual(job["command"], "generate_images")
        self.assertEqual(job["parameters"], {"engine": "chatgpt", "mode": "both"})
        serialized = json.dumps(job).lower()
        for forbidden in (
            "prompt",
            "body",
            "content",
            "path",
            "localhost",
            "127.0.0.1",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_all_language_mode_writes_named_en_hi_hinglish_prompt_files(self) -> None:
        from local_agent_runtime.structured_copy import DeterministicFakeProvider, StructuredCopyExecutor

        self._stage_run(language_mode="ALL")
        self.state.record_job(
            "job-copy-all-langs",
            self.owner,
            "pending",
            {"run_id": self.run_id, "command": "generate_copy", "parameters": {}},
        )
        response = {
            "ads": [
                {
                    "format": "HERO",
                    "persona": {"number": 1, "name": "Busy Professional"},
                    "concept_angle": "desired_outcome",
                    "copy": {
                        "EN": {
                            "headline": "Wellness that fits your day",
                            "support_line": "A clear routine built around real product facts.",
                            "cta": "Build your routine",
                        },
                        "HI": {
                            "headline": "Roz ka wellness asaan",
                            "support_line": "Asli product facts par based routine.",
                            "cta": "Routine banayein",
                        },
                        "HINGLISH": {
                            "headline": "Daily wellness, sorted",
                            "support_line": "Simple routine with verified product facts.",
                            "cta": "Start now",
                        },
                    },
                }
            ]
        }
        result = StructuredCopyExecutor(
            self.state, provider=DeterministicFakeProvider([response])
        ).execute("job-copy-all-langs")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["prompt_count"], 3)
        with self.state._connect() as conn:
            stems = sorted(
                json.loads(row["metadata_json"]).get("display_stem")
                for row in conn.execute(
                    """
                    SELECT rv.metadata_json FROM run_entries re
                    JOIN resource_versions rv
                      ON rv.resource_id = re.resource_id
                     AND rv.version = re.resource_version
                    WHERE re.run_id = ? AND re.role = 'prompt'
                    """,
                    (self.run_id,),
                )
            )
        self.assertEqual(
            set(stems),
            {
                "HERO_busy_professional_EN_desired_outcome",
                "HERO_busy_professional_HI_desired_outcome",
                "HERO_busy_professional_HINGLISH_desired_outcome",
            },
        )


if __name__ == "__main__":
    unittest.main()
