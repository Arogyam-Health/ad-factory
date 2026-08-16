from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


PNG_A = b"\x89PNG\r\n\x1a\n" + b"A" * 32
PNG_B = b"\x89PNG\r\n\x1a\n" + b"B" * 32


class LocalOutputLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        from local_agent_runtime.storage import AgentPaths, AgentState

        self.temp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self.temp.name) / "agent")
        self.state = AgentState(self.paths)
        self.state.create_run(
            run_id="run-11",
            owner_key="org:shared",
            device_id="dev_authority",
            workspace_id="workspace-11",
            run_number=11,
            flow_type="structured",
            operation_id="create-run-11",
        )
        self.prompt = self._resource(
            "prompt",
            "prompt-11",
            b"Original full prompt",
            "text/plain; charset=utf-8",
            {"display_stem": "HERO_busy_professional_EN_desired_outcome"},
        )
        self.state.add_run_entry(
            run_id="run-11",
            entry_id="prompt-entry",
            resource_id=self.prompt.resource_id,
            resource_version=1,
            role="prompt",
            prompt_id="prompt-11",
            position=1,
            operation_id="add-prompt",
            metadata={"display_stem": "HERO_busy_professional_EN_desired_outcome"},
        )
        image = self._resource(
            "output_image",
            "output-11/v1",
            PNG_A,
            "image/png",
            {"display_name": "HERO_busy_professional_EN_desired_outcome_4_5"},
        )
        self.state.create_output(
            output_id="output-11",
            run_id="run-11",
            prompt_id="prompt-11",
            item_id="item-11",
            aspect_ratio="4:5",
            resource_id=image.resource_id,
            resource_version=1,
            operation_id="create-output",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _resource(self, kind: str, key: str, body: bytes, media_type: str, metadata: dict | None = None):
        path = self.paths.staging / (key.replace("/", "_") + ".tmp")
        path.write_bytes(body)
        return self.state.put_resource(
            source=path,
            owner_key="org:shared",
            kind=kind,
            logical_key=key,
            operation_id="put-" + key,
            media_type=media_type,
            metadata=metadata,
        )

    def test_direct_replacement_preserves_exact_source_and_result_lineage(self) -> None:
        result = self.state.replace_output(
            output_id="output-11",
            source_output_version=1,
            source=self._write("replacement.png", PNG_B),
            operation_id="replace-output",
            media_type="image/png",
        )
        self.assertEqual(result["source_output_version"], 1)
        self.assertEqual(result["result_output_version"], 2)
        versions = self.state.output_versions("output-11")
        self.assertEqual(versions[1]["source_output_version"], 1)
        self.assertEqual(versions[1]["version"], 2)

    def test_revision_stores_full_local_prompt_and_recovers_after_restart(self) -> None:
        queued = self.state.queue_output_revision(
            output_id="output-11",
            source_output_version=1,
            comment="Increase contrast",
            engine="chatgpt",
            operation_id="revise-output",
        )
        self.assertNotIn("Increase contrast", json.dumps(queued))
        self.state._connect().execute(
            "UPDATE revisions SET status = 'running' WHERE revision_id = ?",
            (queued["revision_id"],),
        ).connection.commit()
        from local_agent_runtime.storage import AgentState

        restarted = AgentState(self.paths)
        revision = restarted.claim_next_output_revision()
        self.assertEqual(revision["status"], "running")
        prompt_path = restarted.resource_path(
            revision["prompt_resource_id"], revision["prompt_resource_version"]
        )
        full_prompt = prompt_path.read_text(encoding="utf-8")
        self.assertIn("Original full prompt", full_prompt)
        self.assertIn("Increase contrast", full_prompt)

    def test_45_revision_appends_editable_safezone_rules(self) -> None:
        templates = {
            "safezone_45": "SAFE-ZONE ENFORCEMENT (NON-NEGOTIABLE)\n- Frame: 1080 x 1350",
            "safezone_916": "SAFE-ZONE 9:16 SHOULD NOT APPEAR",
        }
        self._resource(
            "config_file",
            "prompt_assembler_templates",
            json.dumps(templates).encode("utf-8"),
            "application/json",
        )
        queued = self.state.queue_output_revision(
            output_id="output-11",
            source_output_version=1,
            comment="Brighten the background",
            engine="chatgpt",
            operation_id="revise-45-safezone",
        )
        prompt = self.state.resource_path(
            *self._revision_prompt_ids(queued["revision_id"])
        ).read_text(encoding="utf-8")
        self.assertIn("Brighten the background", prompt)
        self.assertIn("Original full prompt", prompt)
        self.assertIn("Frame: 1080 x 1350", prompt)
        self.assertNotIn("SAFE-ZONE 9:16 SHOULD NOT APPEAR", prompt)

    def _revision_prompt_ids(self, revision_id: str) -> tuple[str, int]:
        row = self.state._connect().execute(
            """
            SELECT prompt_resource_id, prompt_resource_version
            FROM revisions WHERE revision_id = ?
            """,
            (revision_id,),
        ).fetchone()
        return str(row["prompt_resource_id"]), int(row["prompt_resource_version"])

    def test_916_revision_does_not_reuse_the_45_generation_prompt(self) -> None:
        templates = {
            "safezone_45": "4:5 SAFEZONE MUST NOT APPEAR",
            "safezone_916": "SAFE-ZONE ENFORCEMENT 9:16\n- Keep content in the 14%-65% band.",
        }
        self._resource(
            "config_file",
            "prompt_assembler_templates",
            json.dumps(templates).encode("utf-8"),
            "application/json",
        )
        conversion = (
            "Convert to 9:16.\n"
            "Canvas is 1080(length) x 1920(height) (9:16 aspect ratio).\n"
            "STRICT META/REELS SAFE FIELD: x=86 to x=994."
        )
        self._resource(
            "config_file",
            "conversion_916_prompt",
            conversion.encode("utf-8"),
            "text/plain; charset=utf-8",
        )
        image = self._resource(
            "output_image",
            "output-916/v1",
            PNG_B,
            "image/png",
            {"display_name": "HERO_busy_professional_EN_desired_outcome_9_16"},
        )
        self.state.create_output(
            output_id="output-916",
            run_id="run-11",
            prompt_id="prompt-11",
            item_id="item-11",
            aspect_ratio="9:16",
            resource_id=image.resource_id,
            resource_version=1,
            operation_id="create-output-916",
        )
        queued = self.state.queue_output_revision(
            output_id="output-916",
            source_output_version=1,
            comment="Lift the headline",
            engine="gemini",
            operation_id="revise-916",
        )
        prompt = self.state.resource_path(
            *self._revision_prompt_ids(queued["revision_id"])
        ).read_text(encoding="utf-8")
        self.assertIn("Lift the headline", prompt)
        self.assertIn("1080(length) x 1920(height)", prompt)
        self.assertIn("Keep content in the 14%-65% band.", prompt)
        self.assertNotIn("Original full prompt", prompt)
        self.assertNotIn("4:5 SAFEZONE MUST NOT APPEAR", prompt)

    def test_revision_uploads_a_real_image_file_not_the_raw_cas_blob(self) -> None:
        import importlib

        local_agent = importlib.import_module("scripts.local_agent")
        version = self.state.output_versions("output-11")[0]
        source = self.state.resource_path(
            version["resource_id"], version["resource_version"]
        )
        # Content-addressed objects live at objects/<ab>/<sha256>.blob, and the
        # browser scripts refuse to upload anything that is not an image file.
        self.assertEqual(source.suffix, ".blob")

        work_root = Path(self.temp.name) / "work"
        manifest_path = local_agent._write_revision_upload_manifest(
            work_root,
            revision_id="rev_test",
            image_path=source,
            media_type="image/png",
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = manifest["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["position"], 1)
        self.assertEqual(entries[0]["role"], "source_creative")

        uploaded = Path(entries[0]["path"])
        self.assertTrue(uploaded.is_absolute())
        self.assertTrue(uploaded.is_file())
        self.assertIn(uploaded.suffix.lower(), {".png", ".jpg", ".jpeg", ".webp"})
        self.assertEqual(uploaded.read_bytes(), PNG_A)

        from scripts.chatgpt_web_sutomation import parse_upload_manifest

        self.assertEqual(parse_upload_manifest(manifest_path), [uploaded.resolve()])

    def test_revision_command_passes_the_manifest_instead_of_a_blob_path(self) -> None:
        import importlib

        local_agent = importlib.import_module("scripts.local_agent")
        manifest = Path(self.temp.name) / "uploads.manifest.json"
        for engine in ("chatgpt", "gemini"):
            command = local_agent._browser_automation_cmd(
                engine,
                Path("/scripts/engine.py"),
                Path("/work/prompts"),
                Path("/work/output"),
                Path("/work"),
                {},
                "4:5",
                prompt_glob="revision.txt",
                upload_manifest=manifest,
            )
            self.assertIn("--upload-manifest", command)
            self.assertEqual(command[command.index("--upload-manifest") + 1], str(manifest))
            self.assertNotIn("--upload-dir", command)
            self.assertNotIn("--image-source-file", command)

    def test_activate_archive_restore_delete_and_gc_are_transactional(self) -> None:
        self.state.replace_output(
            output_id="output-11",
            source_output_version=1,
            source=self._write("replacement.png", PNG_B),
            operation_id="replace-output",
            media_type="image/png",
        )
        self.state.activate_output("output-11", 1, operation_id="activate-v1")
        self.assertEqual(self.state.output("output-11")["current_version"], 1)
        self.state.set_output_status("output-11", "archived", operation_id="archive")
        self.state.set_output_status("output-11", "available", operation_id="restore")
        receipt = self.state.delete_output("output-11", operation_id="delete-output")
        self.assertEqual(receipt["status"], "deleted")
        self.assertTrue(receipt["event_id"].startswith("evt_"))
        self.assertEqual(receipt["run_id"], "run-11")
        with self.state._connect() as conn:
            live = conn.execute(
                "SELECT output_id FROM outputs WHERE status != 'deleted'"
            ).fetchall()
            stored = conn.execute("SELECT status FROM outputs").fetchone()
        self.assertEqual(live, [])
        self.assertEqual(stored["status"], "deleted")

    def test_prompt_xlsx_round_trip_creates_immutable_versions(self) -> None:
        from local_agent_runtime.lifecycle import export_prompt_xlsx, import_prompt_xlsx

        workbook = export_prompt_xlsx(self.state, "org:shared", "run-11")
        imported = import_prompt_xlsx(
            self.state,
            "org:shared",
            "run-11",
            workbook,
            operation_id="xlsx-import",
        )
        self.assertEqual(imported["updated"], 1)
        self.assertEqual(len(self.state.resource_versions(self.prompt.resource_id)), 2)

    def test_backup_restore_verifies_hashes_and_restores_offline(self) -> None:
        from local_agent_runtime.lifecycle import backup_local_data, restore_local_data
        from local_agent_runtime.storage import AgentPaths, AgentState

        backup = Path(self.temp.name) / "backup.zip"
        receipt = backup_local_data(self.paths, backup)
        self.assertEqual(receipt["status"], "verified")
        restored_paths = AgentPaths(Path(self.temp.name) / "restored")
        restore_local_data(restored_paths, backup)
        restored = AgentState(restored_paths)
        self.assertEqual(restored.run_manifest("run-11")["run_id"], "run-11")

    def test_shared_config_replication_is_encrypted_and_device_verified(self) -> None:
        from local_agent_runtime.lifecycle import export_shared_config, import_shared_config

        config = self._resource("config_file", "shared-main", b"private config", "application/json")
        package = export_shared_config(
            self.state,
            owner_key="org:shared",
            logical_key="shared-main",
            authority_device_id="dev_authority",
            approved_device_id="dev_replica",
            replication_secret=b"R" * 32,
        )
        self.assertNotIn(b"private config", package)
        replica_paths = type(self.paths)(Path(self.temp.name) / "replica")
        from local_agent_runtime.storage import AgentState

        replica = AgentState(replica_paths)
        result = import_shared_config(
            replica,
            package,
            importing_device_id="dev_replica",
            replication_secret=b"R" * 32,
            operation_id="replicate-main",
        )
        self.assertEqual(result["authority_device_id"], "dev_authority")
        restored = replica.resource_path(result["resource_id"], result["version"]).read_bytes()
        self.assertEqual(restored, b"private config")
        self.assertEqual(config.version, 1)

    def test_run_deletion_receipt_survives_restart_for_offline_reconciliation(self) -> None:
        receipt = self.state.delete_run(
            "run-11", operation_id="offline-tombstone-11", purge_resources=True
        )
        from local_agent_runtime.storage import AgentState

        restarted = AgentState(self.paths)
        pending = restarted.pending_outbox()
        self.assertEqual(pending[0]["event_id"], receipt["event_id"])
        self.assertEqual(pending[0]["payload"]["run_id"], "run-11")
        self.assertNotIn("Original full prompt", json.dumps(pending))

    def test_batch_zip_contains_only_active_outputs_and_single_content_is_rangeable(self) -> None:
        from local_agent_runtime.lifecycle import build_output_zip

        archive = build_output_zip(self.state, "org:shared", "run-11")
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            outputs = [name for name in zipped.namelist() if not name.startswith("prompts/")]
            self.assertEqual(len(outputs), 1)
            self.assertEqual(outputs[0], "4_5/HERO_busy_professional_EN_desired_outcome_4_5.png")
            self.assertIn("prompts/HERO_busy_professional_EN_desired_outcome.txt", zipped.namelist())
            self.assertEqual(zipped.read(outputs[0]), PNG_A)

    def _write(self, name: str, body: bytes) -> Path:
        path = self.paths.staging / name
        path.write_bytes(body)
        return path


if __name__ == "__main__":
    unittest.main()
