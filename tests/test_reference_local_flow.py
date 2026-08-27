from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


class ReferenceLocalFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        from local_agent_runtime.storage import AgentPaths, AgentState

        self.temp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self.temp.name))
        self.state = AgentState(self.paths)
        self.owner = "user:reference-owner"
        self.run_id = "run-reference-local"
        self.state.create_run(
            run_id=self.run_id,
            owner_key=self.owner,
            device_id="dev_" + "c" * 32,
            workspace_id="wrk-reference",
            run_number=10,
            flow_type="reference",
            operation_id="create-reference-run",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _put(self, logical_key: str, content: bytes, kind: str, *, owner: str | None = None, filename: str | None = None):
        source = self.paths.staging / f"{logical_key}.bin"
        source.write_bytes(content)
        return self.state.put_resource(
            source=source,
            owner_key=owner or self.owner,
            kind=kind,
            logical_key=logical_key,
            operation_id=f"put:{owner or self.owner}:{logical_key}",
            media_type="image/png" if kind.endswith("_image") else "text/plain; charset=utf-8",
            metadata={"filename": filename} if filename else None,
        )

    def _add_entry(self, role: str, resource, position: int) -> None:
        self.state.add_run_entry(
            run_id=self.run_id,
            entry_id=f"entry-{role}",
            resource_id=resource.resource_id,
            resource_version=resource.version,
            role=role,
            position=position,
            operation_id=f"entry:{role}",
        )

    def _configured_job(
        self,
        *,
        job_id: str,
        engine: str = "chatgpt",
        mode: str = "45",
        reference_count: int = 1,
        reference_owner: str | None = None,
        language_mode: str = "EN",
        settings_extra: dict | None = None,
    ) -> dict:
        references = [
            self._put(
                f"reference-{index}",
                b"\x89PNG\r\n\x1a\n" + f"reference-{index}".encode(),
                "reference_image",
                owner=reference_owner,
            )
            for index in range(reference_count)
        ]
        products = [
            self._put(
                f"selected-product-{index}",
                b"\x89PNG\r\n\x1a\n" + f"product-{index}".encode(),
                "product_image",
            )
            for index in range(2)
        ]
        excluded = self._put(
            "excluded-product",
            b"\x89PNG\r\n\x1a\nexcluded",
            "product_image",
        )
        product_doc = self._put("product-document", b"LOCAL PRODUCT DOCUMENT", "product_document")
        starting_prompt = self._put("starting-prompt", b"LOCAL STARTING PROMPT", "config_file")
        persona_config = self._put(
            "personas",
            json.dumps(
                [
                    {"persona_id": "persona-a", "persona_name": "Persona A"},
                    {"persona_id": "persona-b", "persona_name": "Persona B"},
                ]
            ).encode(),
            "config_file",
        )
        comments = [
            self._put(
                f"reference-comment-{index}",
                f"LOCAL REFERENCE COMMENT {index}".encode(),
                "config_file",
            )
            for index in range(reference_count)
        ]
        conversion = self._put("conversion-prompt", b"LOCAL 9:16 CONVERSION", "config_file")
        settings = self._put(
            "reference-settings",
            json.dumps(
                {
                    "references": [
                        {
                            "resource_id": reference.resource_id,
                            "version": reference.version,
                            "comment_resource_id": comments[index].resource_id,
                            "comment_version": comments[index].version,
                        }
                        for index, reference in enumerate(references)
                    ],
                    "products": [
                        {"resource_id": product.resource_id, "version": product.version}
                        for product in products
                    ],
                    "persona_ids": ["persona-b"],
                    "language_mode": language_mode,
                    "product_document": {
                        "resource_id": product_doc.resource_id,
                        "version": product_doc.version,
                    },
                    "starting_prompt": {
                        "resource_id": starting_prompt.resource_id,
                        "version": starting_prompt.version,
                    },
                    "persona_config": {
                        "resource_id": persona_config.resource_id,
                        "version": persona_config.version,
                    },
                    "conversion_prompt": {
                        "resource_id": conversion.resource_id,
                        "version": conversion.version,
                    },
                    **(settings_extra or {}),
                }
            ).encode(),
            "config_file",
        )
        for position, (role, resource) in enumerate(
            (
                ("reference_settings", settings),
                ("reference_product_document", product_doc),
                ("reference_starting_prompt", starting_prompt),
                ("reference_persona_config", persona_config),
                ("conversion_prompt", conversion),
            ),
            start=1,
        ):
            self._add_entry(role, resource, position)
        self.state.record_job(
            job_id,
            self.owner,
            "pending",
            {
                "run_id": self.run_id,
                "command": "generate_reference",
                "parameters": {"engine": engine, "mode": mode},
            },
        )
        return {
            "references": references,
            "products": products,
            "excluded": excluded,
            "conversion": conversion,
        }

    def test_reference_executor_builds_reference_first_selected_product_only_jobs(self) -> None:
        from local_agent_runtime.reference_workflow import ReferenceWorkflowExecutor
        from local_agent_runtime.structured_browser import DeterministicFakeBrowser

        reference = self._put("reference-one", b"\x89PNG\r\n\x1a\nreference", "reference_image")
        selected_product = self._put(
            "product-selected", b"\x89PNG\r\n\x1a\nselected", "product_image"
        )
        self._put("product-not-selected", b"\x89PNG\r\n\x1a\nexcluded", "product_image")
        product_doc = self._put("product-document", b"LOCAL PRODUCT DOCUMENT", "product_document")
        starting_prompt = self._put("starting-prompt", b"LOCAL STARTING PROMPT", "config_file")
        persona_config = self._put(
            "personas",
            json.dumps(
                [
                    {"persona_id": "persona-a", "persona_name": "Persona A"},
                    {"persona_id": "persona-b", "persona_name": "Persona B"},
                ]
            ).encode(),
            "config_file",
        )
        comment = self._put("reference-comment", b"LOCAL REFERENCE COMMENT", "config_file")
        conversion = self._put("conversion-prompt", b"LOCAL 9:16 CONVERSION", "config_file")
        settings = self._put(
            "reference-settings",
            json.dumps(
                {
                    "references": [
                        {
                            "resource_id": reference.resource_id,
                            "version": reference.version,
                            "comment_resource_id": comment.resource_id,
                            "comment_version": comment.version,
                        }
                    ],
                    "products": [
                        {
                            "resource_id": selected_product.resource_id,
                            "version": selected_product.version,
                        }
                    ],
                    "persona_ids": ["persona-b"],
                    "product_document": {
                        "resource_id": product_doc.resource_id,
                        "version": product_doc.version,
                    },
                    "starting_prompt": {
                        "resource_id": starting_prompt.resource_id,
                        "version": starting_prompt.version,
                    },
                    "persona_config": {
                        "resource_id": persona_config.resource_id,
                        "version": persona_config.version,
                    },
                    "conversion_prompt": {
                        "resource_id": conversion.resource_id,
                        "version": conversion.version,
                    },
                }
            ).encode(),
            "config_file",
        )
        for position, (role, resource) in enumerate(
            (
                ("reference_settings", settings),
                ("reference_product_document", product_doc),
                ("reference_starting_prompt", starting_prompt),
                ("reference_persona_config", persona_config),
                ("conversion_prompt", conversion),
            ),
            start=1,
        ):
            self._add_entry(role, resource, position)
        self.state.record_job(
            "job-reference",
            self.owner,
            "pending",
            {
                "run_id": self.run_id,
                "command": "generate_reference",
                "parameters": {"engine": "chatgpt", "mode": "both"},
            },
        )

        browser = DeterministicFakeBrowser()
        result = ReferenceWorkflowExecutor(self.state, browser=browser).execute("job-reference")

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["output_count"], 2)
        call_45, call_916 = browser.calls
        self.assertEqual(
            [
                (entry["resource_id"], entry["version"], entry["role"])
                for entry in call_45["manifest"]["entries"]
            ],
            [
                (reference.resource_id, reference.version, "reference"),
                (selected_product.resource_id, selected_product.version, "product"),
            ],
        )
        prompt = Path(call_45["prompt_path"]).read_text(encoding="utf-8")
        for expected in (
            "LOCAL STARTING PROMPT",
            "LOCAL PRODUCT DOCUMENT",
            "LOCAL REFERENCE COMMENT",
            "persona-b",
            "Persona B",
            "Create the ad in English.",
        ):
            self.assertIn(expected, prompt)
        self.assertEqual(
            [entry["role"] for entry in call_916["manifest"]["entries"]],
            ["source_creative"],
        )
        self.assertIn(
            "LOCAL 9:16 CONVERSION",
            Path(call_916["prompt_path"]).read_text(encoding="utf-8"),
        )

    def test_selected_library_images_resolve_across_owners(self) -> None:
        from local_agent_runtime.reference_workflow import ReferenceWorkflowExecutor
        from local_agent_runtime.structured_browser import DeterministicFakeBrowser

        configured = self._configured_job(
            job_id="job-library-images",
            reference_owner="org:other-org",
        )
        browser = DeterministicFakeBrowser()
        result = ReferenceWorkflowExecutor(self.state, browser=browser).execute(
            "job-library-images"
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            [entry["resource_id"] for entry in browser.calls[0]["manifest"]["entries"]],
            [
                configured["references"][0].resource_id,
                *[item.resource_id for item in configured["products"]],
            ],
        )

    def test_gemini_executes_selected_persona_and_exact_order(self) -> None:
        from local_agent_runtime.reference_workflow import ReferenceWorkflowExecutor
        from local_agent_runtime.structured_browser import DeterministicFakeBrowser

        configured = self._configured_job(job_id="job-gemini", engine="gemini")
        browser = DeterministicFakeBrowser()
        result = ReferenceWorkflowExecutor(self.state, browser=browser).execute("job-gemini")

        self.assertEqual(result["status"], "completed")
        self.assertEqual(browser.calls[0]["engine"], "gemini")
        self.assertEqual(
            [entry["resource_id"] for entry in browser.calls[0]["manifest"]["entries"]],
            [
                configured["references"][0].resource_id,
                *[item.resource_id for item in configured["products"]],
            ],
        )
        serialized = json.dumps(browser.calls[0]["manifest"])
        self.assertNotIn(configured["excluded"].resource_id, serialized)
        prompt = Path(browser.calls[0]["prompt_path"]).read_text(encoding="utf-8")
        self.assertIn("persona-b", prompt)
        self.assertNotIn("Persona A", prompt)

    def test_partial_progress_and_restart_resume_without_duplicate_outputs(self) -> None:
        from local_agent_runtime.reference_workflow import ReferenceWorkflowExecutor
        from local_agent_runtime.structured_browser import DeterministicFakeBrowser

        self._configured_job(job_id="job-restart", engine="chatgpt", reference_count=2)
        first = DeterministicFakeBrowser(
            outcomes=[b"first-output", RuntimeError("interrupted")]
        )
        failed = ReferenceWorkflowExecutor(
            self.state, browser=first, max_attempts=1
        ).execute("job-restart")
        resumed = DeterministicFakeBrowser(outcomes=[b"second-output"])
        completed = ReferenceWorkflowExecutor(self.state, browser=resumed).execute(
            "job-restart"
        )
        replay_browser = DeterministicFakeBrowser()
        replay = ReferenceWorkflowExecutor(self.state, browser=replay_browser).execute(
            "job-restart"
        )

        self.assertEqual(failed["completed_count"], 1)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(replay, completed)
        self.assertEqual(len(resumed.calls), 1)
        self.assertEqual(replay_browser.calls, [])
        with self.state._connect() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM outputs WHERE run_id = ?", (self.run_id,)
                ).fetchone()[0],
                2,
            )
        events = self.state.pending_outbox(100)
        self.assertTrue(
            any(
                event["event_type"] == "reference_generation_progress"
                and event["payload"].get("completed_count") == 1
                for event in events
            )
        )

    def test_reference_projections_contain_only_bounded_metadata(self) -> None:
        from local_agent_runtime.reference_workflow import ReferenceWorkflowExecutor
        from local_agent_runtime.structured_browser import DeterministicFakeBrowser

        self._configured_job(job_id="job-metadata", mode="both")
        ReferenceWorkflowExecutor(
            self.state, browser=DeterministicFakeBrowser()
        ).execute("job-metadata")
        serialized = json.dumps(self.state.pending_outbox(100)).lower()
        for forbidden in (
            "local product document",
            "local starting prompt",
            "local reference comment",
            "local 9:16 conversion",
            "reference-workflow/job",
            "127.0.0.1",
            "localhost",
            "\"path\"",
            "\"comment\"",
            "\"content\"",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertLess(len(serialized), 8192)

    def test_cancel_check_stops_reference_before_browser_work(self) -> None:
        from local_agent_runtime.reference_workflow import ReferenceWorkflowExecutor
        from local_agent_runtime.structured_browser import DeterministicFakeBrowser

        self._configured_job(job_id="job-cancel-early")
        browser = DeterministicFakeBrowser()
        result = ReferenceWorkflowExecutor(
            self.state,
            browser=browser,
            cancel_check=lambda: True,
        ).execute("job-cancel-early")
        self.assertEqual(result["status"], "canceled")
        self.assertEqual(result["error_code"], "user_canceled")
        self.assertEqual(browser.calls, [])

    def test_cancel_during_generate_is_not_retried(self) -> None:
        from local_agent_runtime.reference_workflow import ReferenceWorkflowExecutor
        from local_agent_runtime.structured_browser import JobCanceled

        self._configured_job(job_id="job-cancel-generate")
        calls = {"n": 0}

        class CancelingBrowser:
            def generate(self, **kwargs):
                calls["n"] += 1
                raise JobCanceled("Canceled by user")

        result = ReferenceWorkflowExecutor(
            self.state,
            browser=CancelingBrowser(),
            max_attempts=2,
        ).execute("job-cancel-generate")
        self.assertEqual(result["status"], "canceled")
        self.assertEqual(calls["n"], 1)

    def test_mongo_metadata_reference_run_is_visible_in_normal_listing_shape(self) -> None:
        from dashboard.backend.app import _mongo_run_to_manifest

        manifest = _mongo_run_to_manifest(
            {
                "run_id": self.run_id,
                "run_number": 10,
                "display_batch": "v10",
                "batch": "v10-local",
                "flow_type": "reference",
                "status": "completed",
                "image_count": 2,
                "prompt_count": 1,
                "image_generation": {
                    "status": "completed",
                    "completed_count": 2,
                },
            }
        )
        self.assertEqual(manifest["flow_type"], "reference")
        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(manifest["image_count"], 2)

    def test_dashboard_reference_execution_has_no_reachable_render_content_worker(self) -> None:
        root = Path(__file__).resolve().parents[1]
        execute = (root / "dashboard/backend/routes/execute.py").read_text(encoding="utf-8")
        frontend = (root / "dashboard/web/src/pages/studio/ReferencePanel.tsx").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("api_run_execute_reference_workspace_v2", execute)
        self.assertNotIn("api_upload_reference_images", execute)
        self.assertIn("status_code=410", execute)
        self.assertIn("/reference-generation", frontend)
        self.assertNotIn("/reference-status", frontend)
        self.assertNotIn("/generated_images/", frontend)
        self.assertNotIn("Local generation is not enabled", frontend)

    def test_dashboard_queues_only_pinned_reference_metadata(self) -> None:
        from dashboard.backend.agent.routes import queue_reference_generation
        from tests.test_agent_metadata_jobs import _DB

        db = _DB()
        device_id = "dev_" + "d" * 32
        db["agents"].insert_one(
            {
                "agent_id": "agent-reference",
                "user_id": "user-reference",
                "device_id": device_id,
                "is_active": True,
            }
        )
        db["runs"].insert_one(
            {
                "job_id": "not-a-job",
                "run_id": "run-reference-control",
                "user_id": "user-reference",
                "owner_type": "user",
                "owner_id": "user-reference",
                "agent_id": "agent-reference",
                "device_id": device_id,
                "flow_type": "reference",
            }
        )
        with (
            patch("dashboard.backend.db.client.get_sync_db", return_value=db),
            patch("dashboard.backend.agent.service.get_sync_db", return_value=db),
        ):
            result = queue_reference_generation(
                "run-reference-control",
                {
                    "operation_id": "reference-control-operation",
                    "engine": "gemini",
                    "mode": "both",
                },
                {"user_id": "user-reference"},
            )

        self.assertEqual(result["device_id"], device_id)
        job = db["agent_jobs"].docs[0]
        self.assertEqual(job["command"], "generate_reference")
        self.assertEqual(job["parameters"], {"engine": "gemini", "mode": "both"})
        serialized = json.dumps(job).lower()
        for forbidden in (
            "reference_resource_ids",
            "product_resource_ids",
            "persona_ids",
            "comment",
            "prompt",
            "body",
            "content",
            "path",
            "localhost",
            "127.0.0.1",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_all_language_mode_names_prompts_after_persona_and_reference(self) -> None:
        from local_agent_runtime.reference_workflow import ReferenceWorkflowExecutor
        from local_agent_runtime.structured_browser import DeterministicFakeBrowser

        reference = self._put(
            "reference-one",
            b"\x89PNG\r\n\x1a\nstress",
            "reference_image",
            filename="stress_snacker.png",
        )
        selected_product = self._put(
            "product-selected", b"\x89PNG\r\n\x1a\nselected", "product_image"
        )
        product_doc = self._put("product-document", b"LOCAL PRODUCT DOCUMENT", "product_document")
        starting_prompt = self._put("starting-prompt", b"LOCAL STARTING PROMPT", "config_file")
        persona_config = self._put(
            "personas",
            json.dumps(
                [{"persona_id": "persona-12", "number": 12, "name": "Stuck Scale Dieter"}]
            ).encode(),
            "config_file",
        )
        settings = self._put(
            "reference-settings",
            json.dumps(
                {
                    "references": [
                        {"resource_id": reference.resource_id, "version": reference.version}
                    ],
                    "products": [
                        {
                            "resource_id": selected_product.resource_id,
                            "version": selected_product.version,
                        }
                    ],
                    "persona_ids": ["persona-12"],
                    "language_mode": "ALL",
                    "product_document": {
                        "resource_id": product_doc.resource_id,
                        "version": product_doc.version,
                    },
                    "starting_prompt": {
                        "resource_id": starting_prompt.resource_id,
                        "version": starting_prompt.version,
                    },
                    "persona_config": {
                        "resource_id": persona_config.resource_id,
                        "version": persona_config.version,
                    },
                }
            ).encode(),
            "config_file",
        )
        for position, (role, resource) in enumerate(
            (
                ("reference_settings", settings),
                ("reference_product_document", product_doc),
                ("reference_starting_prompt", starting_prompt),
                ("reference_persona_config", persona_config),
            ),
            start=1,
        ):
            self._add_entry(role, resource, position)
        self.state.record_job(
            "job-named-langs",
            self.owner,
            "pending",
            {
                "run_id": self.run_id,
                "command": "generate_reference",
                "parameters": {"engine": "chatgpt", "mode": "45"},
            },
        )
        browser = DeterministicFakeBrowser()
        result = ReferenceWorkflowExecutor(self.state, browser=browser).execute("job-named-langs")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["prompt_count"], 3)
        self.assertEqual(result["output_count"], 3)
        stems = sorted(Path(call["prompt_path"]).stem for call in browser.calls)
        self.assertEqual(
            set(stems),
            {
                "stuck_scale_dieter_stress_snacker_EN",
                "stuck_scale_dieter_stress_snacker_HI",
                "stuck_scale_dieter_stress_snacker_HINGLISH",
            },
        )
        texts = [Path(call["prompt_path"]).read_text(encoding="utf-8") for call in browser.calls]
        self.assertTrue(any("Create the ad in English." in text for text in texts))
        self.assertTrue(any("Create the ad in Hindi." in text for text in texts))
        self.assertTrue(any("Create the ad in Hinglish." in text for text in texts))
        with self.state._connect() as conn:
            names = sorted(
                json.loads(row["metadata_json"]).get("display_name") or ""
                for row in conn.execute(
                    """
                    SELECT rv.metadata_json FROM outputs out
                    JOIN output_versions ov
                      ON ov.output_id = out.output_id AND ov.version = out.current_version
                    JOIN resource_versions rv
                      ON rv.resource_id = ov.resource_id AND rv.version = ov.resource_version
                    WHERE out.run_id = ?
                    """,
                    (self.run_id,),
                )
            )
        self.assertEqual(
            set(names),
            {
                "stuck_scale_dieter_stress_snacker_EN_4_5",
                "stuck_scale_dieter_stress_snacker_HI_4_5",
                "stuck_scale_dieter_stress_snacker_HINGLISH_4_5",
            },
        )

    def test_selected_concept_appends_target_concept_block(self) -> None:
        from local_agent_runtime.reference_workflow import ReferenceWorkflowExecutor
        from local_agent_runtime.structured_browser import DeterministicFakeBrowser

        self._configured_job(
            job_id="job-concept",
            settings_extra={
                "selected_concept": "Concept/IG_Stories",
                "creative_concept": {
                    "id": "Concept/IG_Stories",
                    "label": "IG Stories",
                    "description": "Casual story format.",
                },
            },
        )
        browser = DeterministicFakeBrowser()
        result = ReferenceWorkflowExecutor(self.state, browser=browser).execute("job-concept")
        self.assertEqual(result["status"], "completed")
        text = Path(browser.calls[0]["prompt_path"]).read_text(encoding="utf-8")
        self.assertIn("TARGET PERSONA:", text)
        self.assertIn("TARGET CONCEPT:", text)
        self.assertIn("IG Stories", text)
        self.assertIn("Casual story format.", text)

    def test_none_concept_omits_target_concept_block(self) -> None:
        from local_agent_runtime.reference_workflow import ReferenceWorkflowExecutor
        from local_agent_runtime.structured_browser import DeterministicFakeBrowser

        self._configured_job(job_id="job-no-concept")
        browser = DeterministicFakeBrowser()
        result = ReferenceWorkflowExecutor(self.state, browser=browser).execute("job-no-concept")
        self.assertEqual(result["status"], "completed")
        text = Path(browser.calls[0]["prompt_path"]).read_text(encoding="utf-8")
        self.assertIn("TARGET PERSONA:", text)
        self.assertNotIn("TARGET CONCEPT:", text)

    def test_reference_stores_raw_output_when_browser_returns_tuple(self) -> None:
        from local_agent_runtime.lifecycle import build_output_zip
        from local_agent_runtime.reference_workflow import ReferenceWorkflowExecutor
        from local_agent_runtime.structured_browser import DeterministicFakeBrowser

        cropped = b"\x89PNG\r\n\x1a\n" + b"CROPPED"
        raw = b"\x89PNG\r\n\x1a\n" + b"RAWGPT"
        self._configured_job(job_id="job-raw-tuple", mode="45")
        result = ReferenceWorkflowExecutor(
            self.state,
            browser=DeterministicFakeBrowser(outcomes=[(cropped, raw)]),
        ).execute("job-raw-tuple")
        self.assertEqual(result["status"], "completed")
        with self.state._connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM resources WHERE kind = 'output_raw'"
            ).fetchone()["n"]
        self.assertEqual(int(count), 1)
        archive = build_output_zip(self.state, self.owner, self.run_id, include_raw=True)
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            raw_names = [name for name in zipped.namelist() if name.startswith("raw/")]
            self.assertTrue(raw_names)
            self.assertEqual(zipped.read(raw_names[0]), raw)


if __name__ == "__main__":
    unittest.main()
