from __future__ import annotations

import json
import hashlib
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests


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

    def test_render_copy_job_is_metadata_only_and_idempotent(self) -> None:
        from tests.test_agent_metadata_jobs import _DB
        from dashboard.backend.services.render_copy_jobs import (
            enqueue_render_copy_job,
        )

        db = _DB()
        run = {
            "run_id": "run-server",
            "user_id": "user-1",
            "owner_type": "user",
            "owner_id": "user-1",
            "agent_id": "agent-1",
            "device_id": "dev_" + "a" * 32,
            "run_number": 7,
        }
        db["runs"].insert_one({"job_id": "not-a-job", **run})
        settings = {
            "selected_personas": [3],
            "global_formats": ["TEST"],
            "formats_by_persona": {},
            "multiplier": 1,
            "language_mode": "EN",
            "provider": "opencode",
            "model": "opencode/big-pickle",
            "org_id": "",
        }
        with (
            patch(
                "dashboard.backend.services.render_copy_jobs.get_sync_db",
                return_value=db,
            ),
            patch(
                "dashboard.backend.services.render_copy_jobs.wake_render_copy_worker"
            ),
        ):
            created = enqueue_render_copy_job(
                run=run,
                user_id="user-1",
                settings=settings,
                client_operation_id="run-server-copy",
            )

        stored = db["render_copy_jobs"].docs[0]
        self.assertEqual(created["status"], "queued")
        self.assertEqual(stored["settings"], settings)
        self.assertNotIn("prompt", json.dumps(stored).lower())
        self.assertNotIn("api_key", json.dumps(stored).lower())
        self.assertNotIn("product_master_doc", json.dumps(stored).lower())

    def test_render_copy_run_allocation_does_not_require_a_running_agent(self) -> None:
        from tests.test_agent_metadata_jobs import _DB
        from dashboard.backend.services.render_copy_jobs import (
            allocate_render_copy_run,
        )

        db = _DB()
        with (
            patch(
                "dashboard.backend.services.render_copy_jobs.get_sync_db",
                return_value=db,
            ),
            patch(
                "dashboard.backend.services.render_copy_jobs.reserve_run_number",
                return_value=3,
            ),
        ):
            run = allocate_render_copy_run(
                user_id="user-1",
                owner_type="user",
                owner_id="user-1",
            )

        self.assertEqual(run["display_batch"], "v3")
        self.assertEqual(db["runs"].docs[0]["agent_id"], "")
        self.assertEqual(db["runs"].docs[0]["device_id"], "")

    def test_render_copy_queue_accepts_run_without_local_device(self) -> None:
        from tests.test_agent_metadata_jobs import _DB
        from dashboard.backend.agent.routes import queue_structured_copy

        db = _DB()
        db["runs"].insert_one(
            {
                "job_id": "not-a-job",
                "run_id": "run-server",
                "user_id": "user-1",
                "owner_type": "user",
                "owner_id": "user-1",
                "agent_id": "",
                "device_id": "",
                "run_number": 4,
            }
        )
        queued = {
            "copy_job_id": "copy-test",
            "status": "queued",
            "progress_code": "queued_on_render",
        }
        with (
            patch("dashboard.backend.db.client.get_sync_db", return_value=db),
            patch(
                "dashboard.backend.services.render_copy_jobs.enqueue_render_copy_job",
                return_value=queued,
            ),
        ):
            response = queue_structured_copy(
                "run-server",
                {
                    "operation_id": "run-server-copy",
                    "settings": {},
                },
                {"user_id": "user-1"},
            )

        self.assertEqual(response["copy_job_id"], "copy-test")

    def test_provider_failure_exposes_safe_status_without_response_body(self) -> None:
        from dashboard.backend.services.render_structured_copy import (
            ProviderCallError,
            provider_generate_callable,
        )

        response = Mock()
        response.status_code = 401
        response.raise_for_status.side_effect = requests.HTTPError("secret upstream body")
        with patch(
            "dashboard.backend.services.render_structured_copy.requests.post",
            return_value=response,
        ):
            generate = provider_generate_callable(
                "opencode",
                "opencode/big-pickle",
                {"api_url": "https://provider.example/v1", "api_key": "test-key"},
            )
            with self.assertRaises(ProviderCallError) as raised:
                generate({"task": "copy"})

        self.assertEqual(raised.exception.code, "provider_http_error")
        self.assertEqual(raised.exception.http_status, 401)
        self.assertNotIn("secret upstream body", str(raised.exception))

    def test_opencode_http_request_strips_dashboard_provider_prefix(self) -> None:
        from dashboard.backend.services.render_structured_copy import (
            provider_generate_callable,
        )

        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "choices": [{"message": {"content": '{"ads":[]}'}}],
        }
        response.headers = {"content-type": "application/json"}
        with patch(
            "dashboard.backend.services.render_structured_copy.requests.post",
            return_value=response,
        ) as post:
            generate = provider_generate_callable(
                "opencode",
                "opencode/deepseek-v4-flash-free",
                {
                    "api_url": "https://opencode.ai/zen/v1",
                    "api_key": "test-key",
                },
            )
            generate({"task": "copy"})

        self.assertEqual(
            post.call_args.kwargs["json"]["model"],
            "deepseek-v4-flash-free",
        )

    def test_provider_trace_callback_receives_redacted_bounded_401_detail(
        self,
    ) -> None:
        from dashboard.backend.services.render_structured_copy import (
            ProviderCallError,
            provider_generate_callable,
        )

        response = Mock()
        response.status_code = 401
        response.text = (
            '{"error":{"message":"Invalid key sk-private-secret","type":"auth_error"}}'
        )
        response.headers = {"content-type": "application/json"}
        response.raise_for_status.side_effect = requests.HTTPError("unauthorized")
        traces: list[dict] = []
        with patch(
            "dashboard.backend.services.render_structured_copy.requests.post",
            return_value=response,
        ):
            generate = provider_generate_callable(
                "opencode",
                "opencode/deepseek-v4-flash-free",
                {
                    "api_url": "https://opencode.ai/zen/v1",
                    "api_key": "sk-private-secret",
                },
                trace_callback=traces.append,
            )
            with self.assertRaises(ProviderCallError):
                generate(
                    {
                        "task": "copy",
                        "product_document": "must not be traced",
                        "planned_ads": [{"format": "TEST"}],
                        "languages": ["EN"],
                    }
                )

        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0]["http_status"], 401)
        self.assertIn("Invalid key [REDACTED]", traces[0]["error_detail"])
        self.assertNotIn("sk-private-secret", json.dumps(traces))
        self.assertNotIn("product_document", json.dumps(traces))
        self.assertEqual(traces[0]["request"]["planned_ad_count"], 1)

    def test_provider_call_waits_without_a_client_side_timeout(self) -> None:
        from dashboard.backend.services.render_structured_copy import (
            provider_generate_callable,
        )

        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "choices": [{"message": {"content": '{"ads":[]}'}}],
        }
        response.headers = {"content-type": "application/json"}
        with patch(
            "dashboard.backend.services.render_structured_copy.requests.post",
            return_value=response,
        ) as post:
            generate = provider_generate_callable(
                "opencode",
                "opencode/deepseek-v4-flash-free",
                {
                    "api_url": "https://opencode.ai/zen/v1",
                    "api_key": "test-key",
                },
            )
            generate({"task": "copy"})

        self.assertIsNone(post.call_args.kwargs["timeout"])

    def test_invalid_model_output_exposes_bounded_sanitized_raw_response(
        self,
    ) -> None:
        from dashboard.backend.services.render_structured_copy import (
            ProviderCallError,
            provider_generate_callable,
        )

        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "choices": [{"message": {"content": "model returned broken JSON"}}],
        }
        response.text = (
            '{"choices":[{"message":{"content":"model returned broken JSON"}}]}'
        )
        response.headers = {"content-type": "application/json"}
        with patch(
            "dashboard.backend.services.render_structured_copy.requests.post",
            return_value=response,
        ):
            generate = provider_generate_callable(
                "opencode",
                "opencode/deepseek-v4-flash-free",
                {
                    "api_url": "https://opencode.ai/zen/v1",
                    "api_key": "test-key",
                },
            )
            with self.assertRaises(ProviderCallError) as raised:
                generate({"task": "copy"})

        self.assertEqual(
            raised.exception.code,
            "provider_invalid_response",
        )
        self.assertIn("model returned broken JSON", raised.exception.error_detail)

    def test_structurally_wrong_json_reports_raw_model_output(self) -> None:
        from dashboard.backend.services.render_structured_copy import (
            ProviderCallError,
            generate_structured_prompt_bundle,
        )

        invalid = {"ads": [{"copy": {"EN": {"headline": "missing fields"}}}]}
        with self.assertRaises(ProviderCallError) as raised:
            generate_structured_prompt_bundle(
                run_id="run-invalid",
                run_number=1,
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
                            }
                        ]
                    ),
                },
                provider_name="opencode",
                provider_model="opencode/deepseek-v4-flash-free",
                generate=lambda request, repair=False: invalid,
            )

        self.assertEqual(raised.exception.code, "provider_invalid_output")
        self.assertEqual(raised.exception.http_status, 200)
        self.assertIn("missing fields", raised.exception.error_detail)

    def test_traces_page_reads_recent_cloud_diagnostics_without_local_agent(
        self,
    ) -> None:
        source = (ROOT / "dashboard" / "frontend" / "traces.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('fetch(`/api/llm-traces?', source)
        self.assertNotIn("localDataPlane.listTraces", source)
        self.assertNotIn("localDataPlane.traceContent", source)

    def test_mongo_trace_history_keeps_only_five_sanitized_records(self) -> None:
        from dashboard.backend.services.llm_trace import (
            list_recent_llm_traces,
            record_recent_llm_trace,
        )

        class Cursor(list):
            def sort(self, key, direction):
                return Cursor(
                    sorted(
                        self,
                        key=lambda item: item.get(key, 0),
                        reverse=direction < 0,
                    )
                )

            def skip(self, count):
                return Cursor(self[count:])

            def limit(self, count):
                return Cursor(self[:count])

        class Collection:
            def __init__(self):
                self.docs = []

            def insert_one(self, doc):
                self.docs.append(copy.deepcopy(doc))

            def find(self, query, projection):
                rows = [
                    copy.deepcopy(doc)
                    for doc in self.docs
                    if all(doc.get(key) == value for key, value in query.items())
                ]
                if projection:
                    rows = [
                        {
                            key: value
                            for key, value in row.items()
                            if projection.get(key) == 1
                        }
                        for row in rows
                    ]
                return Cursor(rows)

            def delete_many(self, query):
                before = len(self.docs)
                trace_filter = query.get("trace_id")
                if isinstance(trace_filter, dict) and "$exists" in trace_filter:
                    self.docs = [
                        doc
                        for doc in self.docs
                        if not (
                            doc.get("user_id") == query.get("user_id")
                            and ("trace_id" in doc) is trace_filter["$exists"]
                        )
                    ]
                elif isinstance(trace_filter, dict) and "$in" in trace_filter:
                    values = set(trace_filter["$in"])
                    self.docs = [
                        doc
                        for doc in self.docs
                        if not (
                            doc.get("user_id") == query.get("user_id")
                            and doc.get("trace_id") in values
                        )
                    ]
                return SimpleNamespace(deleted_count=before - len(self.docs))

        collection = Collection()
        db = {"llm_traces": collection}
        event = {
            "provider": "opencode",
            "model": "opencode/deepseek-v4-flash-free",
            "api_model": "deepseek-v4-flash-free",
            "endpoint": "https://opencode.ai/zen/v1/chat/completions",
            "label": "copy",
            "status": "failed",
            "http_status": 401,
            "duration_ms": 20,
            "error_code": "provider_http_error",
            "error_detail": "Invalid API key",
            "request": {
                "task": "copy",
                "planned_ad_count": 1,
                "languages": ["EN"],
                "request_sha256": "a" * 64,
            },
            "response": {"usage": {}},
        }
        with patch(
            "dashboard.backend.services.llm_trace.get_sync_db",
            return_value=db,
        ):
            for index in range(7):
                record_recent_llm_trace(
                    user_id="user-1",
                    run_id=f"run-{index}",
                    batch=f"v{index}",
                    event=event,
                )
            traces = list_recent_llm_traces("user-1")

        self.assertEqual(len(collection.docs), 5)
        self.assertEqual(len(traces), 5)
        self.assertEqual(traces[0]["run_id"], "run-6")
        self.assertNotIn("api_key", json.dumps(traces).lower())

    def test_delivery_and_render_jobs_have_ttl_indexes(self) -> None:
        from dashboard.backend.db.collections import (
            COLL_PROMPT_DELIVERIES,
            COLL_RENDER_COPY_JOBS,
        )
        from dashboard.backend.db.indexes import INDEX_SPECS

        delivery_indexes = [
            index.document for index in INDEX_SPECS[COLL_PROMPT_DELIVERIES]
        ]
        copy_indexes = [
            index.document for index in INDEX_SPECS[COLL_RENDER_COPY_JOBS]
        ]
        self.assertTrue(
            any(index.get("expireAfterSeconds") == 0 for index in delivery_indexes)
        )
        self.assertTrue(
            any(index.get("expireAfterSeconds") == 0 for index in copy_indexes)
        )

    def test_local_agent_imports_final_prompts_before_acknowledging_delivery(
        self,
    ) -> None:
        import scripts.local_agent as local_agent
        from local_agent_runtime.storage import AgentPaths, AgentState

        text = "Final Render-assembled prompt"
        prompt = {
            "prompt_id": "prm_delivery",
            "text": text,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "format": "TEST",
            "persona_number": 3,
            "persona_name": "Stress Snacker",
            "language": "EN",
            "aspect_ratio": "4:5",
        }
        calls: list[tuple[str, str, object]] = []

        def request(method, path, data=None, **_kwargs):
            calls.append((method, path, data))
            if method == "GET":
                return [
                    {
                        "delivery_id": "dlv_test",
                        "run_id": "run-delivered",
                        "bundle": {
                            "run_id": "run-delivered",
                            "run_number": 9,
                            "owner_type": "user",
                            "owner_id": "user-1",
                            "prompts": [prompt],
                        },
                    }
                ]
            return {"status": "acknowledged"}

        with tempfile.TemporaryDirectory() as temporary:
            paths = AgentPaths(Path(temporary))
            state = AgentState(paths)
            old_paths, old_state = local_agent.AGENT_PATHS, local_agent.AGENT_STATE
            local_agent.AGENT_PATHS, local_agent.AGENT_STATE = paths, state
            try:
                with patch.object(local_agent, "api_request", side_effect=request):
                    local_agent.sync_prompt_deliveries()
                manifest = state.run_manifest("run-delivered")
                self.assertIsNotNone(manifest)
                entry = manifest["entries"][0]
                content = state.resource_path(
                    str(entry["resource_id"]),
                    int(entry["resource_version"]),
                ).read_text(encoding="utf-8")
            finally:
                local_agent.AGENT_PATHS, local_agent.AGENT_STATE = (
                    old_paths,
                    old_state,
                )

        self.assertEqual(content, text)
        self.assertEqual(calls[-1][0], "POST")
        self.assertEqual(calls[-1][2], {"prompt_ids": ["prm_delivery"]})

    def test_frontend_run_pipeline_does_not_stage_copy_inputs_on_localhost(self) -> None:
        source = (ROOT / "dashboard" / "frontend" / "js" / "main.js").read_text(
            encoding="utf-8"
        )
        start = source.index("async function runPipeline()")
        end = source.index(
            '\n}\n\n\ndocument.getElementById("cancelRunBtn")', start
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
