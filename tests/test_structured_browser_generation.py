from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class StructuredBrowserGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        from local_agent_runtime.storage import AgentPaths, AgentState

        self.temp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self.temp.name))
        self.state = AgentState(self.paths)
        self.owner = "user:user-1"
        self.run_id = "run-structured-browser"
        self.state.create_run(
            run_id=self.run_id,
            owner_key=self.owner,
            device_id="dev_" + "b" * 32,
            workspace_id="wrk-browser",
            run_number=9,
            flow_type="structured",
            operation_id="create-run",
        )
        self.products = [
            self._put_resource(f"product-{index}.png", b"\x89PNG\r\n\x1a\n" + bytes([index]), "product_image")
            for index in (2, 1)
        ]
        self.logo = self._put_resource(
            "brand-logo.png", b"\x89PNG\r\n\x1a\nlogo", "product_image"
        )
        self.conversion_prompt_text = (
            "LOCAL VERSIONED 9:16 CONVERSION PROMPT. Preserve the source creative."
        )
        self.conversion_prompt = self._put_text(
            "conversion-916",
            self.conversion_prompt_text,
            "config_file",
        )
        settings = self._put_json(
            "settings",
            {
                "product_assets": [
                    {"resource_id": resource.resource_id, "version": resource.version}
                    for resource in self.products
                ],
                "logo_assets": [
                    {"resource_id": self.logo.resource_id, "version": self.logo.version}
                ],
                "conversion_prompt": {
                    "resource_id": self.conversion_prompt.resource_id,
                    "version": self.conversion_prompt.version,
                },
            },
            kind="config_file",
        )
        self.state.add_run_entry(
            run_id=self.run_id,
            entry_id="settings-entry",
            resource_id=settings.resource_id,
            resource_version=settings.version,
            role="structured_settings",
            position=1,
            operation_id="settings-entry",
        )
        self.state.add_run_entry(
            run_id=self.run_id,
            entry_id="conversion-prompt-entry",
            resource_id=self.conversion_prompt.resource_id,
            resource_version=self.conversion_prompt.version,
            role="conversion_prompt",
            aspect_ratio="9:16",
            position=2,
            operation_id="conversion-prompt-entry",
        )
        self.prompt_ids = ["prm_alpha", "prm_beta"]
        for position, prompt_id in enumerate(self.prompt_ids, start=3):
            prompt = self._put_text(
                prompt_id,
                f"ORIGINAL 4:5 PROMPT BODY {prompt_id}",
                "prompt",
            )
            self.state.add_run_entry(
                run_id=self.run_id,
                entry_id=f"prompt-entry-{position}",
                resource_id=prompt.resource_id,
                resource_version=prompt.version,
                role="prompt",
                prompt_id=prompt_id,
                item_id=f"item-{prompt_id}",
                aspect_ratio="4:5",
                position=position,
                operation_id=f"prompt-entry-{position}",
            )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _put_resource(self, name: str, content: bytes, kind: str):
        source = self.paths.staging / name
        source.write_bytes(content)
        return self.state.put_resource(
            source=source,
            owner_key=self.owner,
            kind=kind,
            logical_key=name,
            operation_id=f"put-{name}",
            media_type="image/png",
        )

    def _put_text(self, logical_key: str, content: str, kind: str):
        source = self.paths.staging / f"{logical_key}.txt"
        source.write_text(content, encoding="utf-8")
        return self.state.put_resource(
            source=source,
            owner_key=self.owner,
            kind=kind,
            logical_key=logical_key,
            operation_id=f"put-{logical_key}",
            media_type="text/plain; charset=utf-8",
            metadata={"prompt_id": logical_key, "aspect_ratio": "4:5"},
        )

    def _put_json(self, logical_key: str, value: dict, *, kind: str):
        source = self.paths.staging / f"{logical_key}.json"
        source.write_text(json.dumps(value), encoding="utf-8")
        return self.state.put_resource(
            source=source,
            owner_key=self.owner,
            kind=kind,
            logical_key=logical_key,
            operation_id=f"put-{logical_key}",
            media_type="application/json",
        )

    def _record_job(
        self,
        job_id: str,
        *,
        engine: str,
        mode: str,
        prompt_id: str = "",
    ) -> None:
        parameters = {"engine": engine, "mode": mode}
        if prompt_id:
            parameters["prompt_version_id"] = prompt_id
        self.state.record_job(
            job_id,
            self.owner,
            "pending",
            {
                "run_id": self.run_id,
                "command": "generate_images",
                "parameters": parameters,
            },
        )

    def _clear_outputs(self) -> None:
        with self.state._connect() as conn:
            conn.execute("DELETE FROM outputs")

    def test_selected_45_uses_declared_product_and_logo_order_for_both_engines(self) -> None:
        from local_agent_runtime.structured_browser import (
            DeterministicFakeBrowser,
            StructuredBrowserExecutor,
        )

        expected = [
            (self.products[0].resource_id, self.products[0].version, "product"),
            (self.products[1].resource_id, self.products[1].version, "product"),
            (self.logo.resource_id, self.logo.version, "logo"),
        ]
        for index, engine in enumerate(("chatgpt", "gemini")):
            with self.subTest(engine=engine):
                if index:
                    self._clear_outputs()
                job_id = f"job-selected-{engine}"
                self._record_job(
                    job_id,
                    engine=engine,
                    mode="45",
                    prompt_id=self.prompt_ids[1],
                )
                browser = DeterministicFakeBrowser()
                result = StructuredBrowserExecutor(self.state, browser=browser).execute(job_id)

                self.assertEqual(result["status"], "completed")
                self.assertEqual(len(browser.calls), 1)
                call = browser.calls[0]
                self.assertEqual(call["engine"], engine)
                self.assertEqual(call["prompt_id"], self.prompt_ids[1])
                self.assertEqual(call["aspect_ratio"], "4:5")
                self.assertEqual(
                    [
                        (entry["resource_id"], entry["version"], entry["role"])
                        for entry in call["manifest"]["entries"]
                    ],
                    expected,
                )
                self.assertFalse(
                    (self.paths.staging / "structured-browser" / job_id).exists()
                )

    def test_batch_45_processes_each_prompt_with_the_same_explicit_set(self) -> None:
        from local_agent_runtime.structured_browser import (
            DeterministicFakeBrowser,
            StructuredBrowserExecutor,
        )

        for index, engine in enumerate(("chatgpt", "gemini")):
            with self.subTest(engine=engine):
                if index:
                    self._clear_outputs()
                job_id = f"job-batch-{engine}"
                self._record_job(job_id, engine=engine, mode="45")
                browser = DeterministicFakeBrowser()
                result = StructuredBrowserExecutor(self.state, browser=browser).execute(job_id)

                self.assertEqual(result["output_count"], 2)
                self.assertEqual([call["prompt_id"] for call in browser.calls], self.prompt_ids)
                for call in browser.calls:
                    self.assertEqual(
                        [entry["role"] for entry in call["manifest"]["entries"]],
                        ["product", "product", "logo"],
                    )

    def test_both_mode_links_each_916_upload_to_its_matching_45_output_version(self) -> None:
        from local_agent_runtime.structured_browser import (
            DeterministicFakeBrowser,
            StructuredBrowserExecutor,
        )

        for index, engine in enumerate(("chatgpt", "gemini")):
            with self.subTest(engine=engine):
                if index:
                    self._clear_outputs()
                job_id = f"job-both-{engine}"
                self._record_job(job_id, engine=engine, mode="both")
                browser = DeterministicFakeBrowser()
                result = StructuredBrowserExecutor(self.state, browser=browser).execute(job_id)

                self.assertEqual(result["output_count"], 4)
                calls_45 = [call for call in browser.calls if call["aspect_ratio"] == "4:5"]
                calls_916 = [call for call in browser.calls if call["aspect_ratio"] == "9:16"]
                self.assertEqual(len(calls_45), 2)
                self.assertEqual(len(calls_916), 2)
                with self.state._connect() as conn:
                    outputs = {
                        (row["prompt_id"], row["aspect_ratio"]): dict(row)
                        for row in conn.execute("SELECT * FROM outputs").fetchall()
                    }
                    versions = {
                        row["output_id"]: dict(row)
                        for row in conn.execute(
                            "SELECT * FROM output_versions WHERE version = 1"
                        ).fetchall()
                    }
                for call in calls_916:
                    source = call["manifest"]["entries"]
                    self.assertEqual(len(source), 1)
                    self.assertEqual(source[0]["role"], "source_creative")
                    conversion_text = call["prompt_text"]
                    self.assertIn(self.conversion_prompt_text, conversion_text)
                    self.assertNotIn("ORIGINAL 4:5 PROMPT BODY", conversion_text)
                    self.assertIn(call["prompt_id"], conversion_text)
                    self.assertIn(self.conversion_prompt.resource_id, conversion_text)
                    self.assertIn(
                        f"conversion_prompt_version={self.conversion_prompt.version}",
                        conversion_text,
                    )
                    self.assertIn("source_output_version=1", conversion_text)
                    output_45 = outputs[(call["prompt_id"], "4:5")]
                    output_916 = outputs[(call["prompt_id"], "9:16")]
                    self.assertEqual(
                        source[0]["resource_id"],
                        versions[output_45["output_id"]]["resource_id"],
                    )
                    self.assertEqual(
                        source[0]["version"],
                        versions[output_45["output_id"]]["resource_version"],
                    )
                    self.assertEqual(
                        versions[output_916["output_id"]]["source_output_version"],
                        output_45["current_version"],
                    )

    def test_standalone_916_uses_existing_exact_45_version(self) -> None:
        from local_agent_runtime.structured_browser import (
            DeterministicFakeBrowser,
            StructuredBrowserExecutor,
        )

        for index, engine in enumerate(("chatgpt", "gemini")):
            with self.subTest(engine=engine):
                if index:
                    self._clear_outputs()
                first_job = f"job-first-45-{engine}"
                self._record_job(
                    first_job,
                    engine=engine,
                    mode="45",
                    prompt_id=self.prompt_ids[0],
                )
                StructuredBrowserExecutor(
                    self.state, browser=DeterministicFakeBrowser()
                ).execute(first_job)
                conversion_job = f"job-only-916-{engine}"
                self._record_job(
                    conversion_job,
                    engine=engine,
                    mode="916",
                    prompt_id=self.prompt_ids[0],
                )
                browser = DeterministicFakeBrowser()
                result = StructuredBrowserExecutor(
                    self.state, browser=browser
                ).execute(conversion_job)

                self.assertEqual(result["output_count"], 1)
                self.assertEqual(browser.calls[0]["aspect_ratio"], "9:16")
                self.assertEqual(
                    [
                        entry["role"]
                        for entry in browser.calls[0]["manifest"]["entries"]
                    ],
                    ["source_creative"],
                )
                conversion_text = browser.calls[0]["prompt_text"]
                self.assertIn(self.conversion_prompt_text, conversion_text)
                self.assertNotIn("ORIGINAL 4:5 PROMPT BODY", conversion_text)
                self.assertIn(self.prompt_ids[0], conversion_text)
                self.assertIn(self.conversion_prompt.resource_id, conversion_text)
                self.assertIn(
                    f"conversion_prompt_version={self.conversion_prompt.version}",
                    conversion_text,
                )
                self.assertIn("source_output_version=1", conversion_text)

    def test_partial_output_commits_before_bounded_projection(self) -> None:
        from local_agent_runtime.structured_browser import (
            DeterministicFakeBrowser,
            StructuredBrowserExecutor,
        )

        self._record_job("job-partial", engine="chatgpt", mode="45")
        browser = DeterministicFakeBrowser(outcomes=[b"first-image", RuntimeError("stopped")])
        result = StructuredBrowserExecutor(self.state, browser=browser, max_attempts=1).execute(
            "job-partial"
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["completed_count"], 1)
        with self.state._connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM outputs").fetchone()[0], 1)
        events = [event["payload"] for event in self.state.pending_outbox(100)]
        serialized = json.dumps(events).lower()
        self.assertIn("structured_images_progress", [event["event_type"] for event in self.state.pending_outbox(100)])
        for forbidden in ("first-image", "local prompt", "local_path", "127.0.0.1", "localhost"):
            self.assertNotIn(forbidden, serialized)
        self.assertTrue(any(event.get("completed_count") == 1 for event in events))

    def test_restart_resumes_without_duplicate_outputs_or_browser_calls(self) -> None:
        from local_agent_runtime.structured_browser import (
            DeterministicFakeBrowser,
            StructuredBrowserExecutor,
        )

        self._record_job("job-restart", engine="gemini", mode="45")
        first = DeterministicFakeBrowser(outcomes=[b"first", RuntimeError("interrupt")])
        StructuredBrowserExecutor(self.state, browser=first, max_attempts=1).execute("job-restart")
        resumed = DeterministicFakeBrowser(outcomes=[b"second"])
        result = StructuredBrowserExecutor(self.state, browser=resumed).execute("job-restart")
        again = StructuredBrowserExecutor(self.state, browser=DeterministicFakeBrowser()).execute(
            "job-restart"
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(again, result)
        self.assertEqual([call["prompt_id"] for call in resumed.calls], [self.prompt_ids[1]])
        with self.state._connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM outputs").fetchone()[0], 2)

    def test_transient_browser_failure_retries_locally(self) -> None:
        from local_agent_runtime.structured_browser import (
            DeterministicFakeBrowser,
            StructuredBrowserExecutor,
        )

        self._record_job(
            "job-retry",
            engine="chatgpt",
            mode="45",
            prompt_id=self.prompt_ids[0],
        )
        browser = DeterministicFakeBrowser(outcomes=[RuntimeError("temporary"), b"success"])
        result = StructuredBrowserExecutor(self.state, browser=browser, max_attempts=2).execute(
            "job-retry"
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(browser.calls), 2)
        self.assertEqual(result["retry_count"], 1)

    def test_automation_scripts_accept_the_same_ordered_json_manifest(self) -> None:
        from scripts.chatgpt_web_sutomation import parse_upload_manifest as chatgpt_manifest
        from scripts.gemini_web_automation import parse_upload_manifest as gemini_manifest

        first = self.paths.staging / "first.png"
        second = self.paths.staging / "second.png"
        first.write_bytes(b"\x89PNG\r\n\x1a\nfirst")
        second.write_bytes(b"\x89PNG\r\n\x1a\nsecond")
        manifest = self.paths.staging / "uploads.json"
        manifest.write_text(
            json.dumps(
                {
                    "upload_set_id": "ups_test",
                    "prompt_id": "prm_alpha",
                    "entries": [
                        {"position": 1, "role": "product", "path": str(second)},
                        {"position": 2, "role": "logo", "path": str(first)},
                    ],
                }
            ),
            encoding="utf-8",
        )

        expected = [second.resolve(), first.resolve()]
        self.assertEqual(chatgpt_manifest(manifest), expected)
        self.assertEqual(gemini_manifest(manifest), expected)

    def test_constructor_assets_work_without_persisted_run_settings(self) -> None:
        from local_agent_runtime.structured_browser import (
            DeterministicFakeBrowser,
            StructuredBrowserExecutor,
        )

        with self.state._connect() as conn:
            conn.execute(
                "DELETE FROM run_entries WHERE role IN ('structured_settings', 'conversion_prompt')"
            )
            conn.commit()
        job_id = "job-ephemeral-settings"
        self._record_job(job_id, engine="chatgpt", mode="45", prompt_id=self.prompt_ids[0])
        result = StructuredBrowserExecutor(
            self.state,
            browser=DeterministicFakeBrowser(),
            product_assets=[
                {
                    "resource_id": self.products[0].resource_id,
                    "version": self.products[0].version,
                }
            ],
            conversion_prompt_text=self.conversion_prompt_text,
        ).execute(job_id)
        self.assertEqual(result["status"], "completed")
        self.assertFalse((self.paths.staging / "structured-browser" / job_id).exists())


if __name__ == "__main__":
    unittest.main()
