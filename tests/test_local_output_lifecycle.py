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
            "prompt", "prompt-11", b"Original full prompt", "text/plain; charset=utf-8"
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
        )
        image = self._resource("output_image", "output-11/v1", PNG_A, "image/png")
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

    def _resource(self, kind: str, key: str, body: bytes, media_type: str):
        path = self.paths.staging / (key.replace("/", "_") + ".tmp")
        path.write_bytes(body)
        return self.state.put_resource(
            source=path,
            owner_key="org:shared",
            kind=kind,
            logical_key=key,
            operation_id="put-" + key,
            media_type=media_type,
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
            self.assertEqual(zipped.read(outputs[0]), PNG_A)

    def _write(self, name: str, body: bytes) -> Path:
        path = self.paths.staging / name
        path.write_bytes(body)
        return path


if __name__ == "__main__":
    unittest.main()
