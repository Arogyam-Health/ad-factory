from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


class LocalDataPlaneSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        from local_agent_runtime.storage import AgentPaths, AgentState

        self.temp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self.temp.name) / "agent")
        self.state = AgentState(self.paths)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _source(self, name: str, content: bytes) -> Path:
        path = self.paths.staging / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_schema_contains_all_versioned_local_data_plane_tables(self) -> None:
        from local_agent_runtime.storage import SCHEMA_VERSION

        expected = {
            "objects",
            "resources",
            "resource_versions",
            "runs",
            "run_entries",
            "upload_sets",
            "upload_set_entries",
            "outputs",
            "output_versions",
            "revisions",
            "change_log",
            "outbox",
            "operations",
        }
        with sqlite3.connect(self.paths.database) as conn:
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        marker = json.loads((self.paths.state / "schema-version.json").read_text(encoding="utf-8"))

        self.assertTrue(expected.issubset(tables))
        self.assertEqual(version, SCHEMA_VERSION)
        self.assertEqual(marker["schema_version"], SCHEMA_VERSION)

    def test_existing_v1_database_is_backed_up_before_transactional_upgrade(self) -> None:
        from local_agent_runtime.storage import AgentPaths, AgentState, SCHEMA_VERSION

        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp) / "agent")
            paths.ensure()
            with sqlite3.connect(paths.database) as conn:
                conn.execute("CREATE TABLE jobs(job_id TEXT PRIMARY KEY, owner_key TEXT, status TEXT)")
                conn.execute("INSERT INTO jobs VALUES ('job-before-upgrade', 'owner-1', 'queued')")
                conn.execute("PRAGMA user_version = 1")

            AgentState(paths)

            backups = list(paths.state.glob("agent.sqlite3.pre-v*-*.bak"))
            self.assertEqual(len(backups), 1)
            with sqlite3.connect(backups[0]) as backup:
                row = backup.execute("SELECT job_id FROM jobs").fetchone()
                backup_version = backup.execute("PRAGMA user_version").fetchone()[0]
            with sqlite3.connect(paths.database) as upgraded:
                current_version = upgraded.execute("PRAGMA user_version").fetchone()[0]
                preserved = upgraded.execute("SELECT job_id FROM jobs").fetchone()
            self.assertEqual(row[0], "job-before-upgrade")
            self.assertEqual(preserved[0], "job-before-upgrade")
            self.assertEqual(backup_version, 1)
            self.assertEqual(current_version, SCHEMA_VERSION)

    def test_failed_migration_rolls_back_schema_and_keeps_backup(self) -> None:
        from local_agent_runtime.storage import AgentPaths, SchemaMigrationError, migrate_database

        with tempfile.TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp) / "agent")
            paths.ensure()
            with sqlite3.connect(paths.database) as conn:
                conn.execute("CREATE TABLE durable(value TEXT)")
                conn.execute("INSERT INTO durable VALUES ('preserved')")
                conn.execute("PRAGMA user_version = 1")

            with self.assertRaises(SchemaMigrationError):
                migrate_database(
                    paths,
                    target_version=2,
                    migrations={2: ("CREATE TABLE transient(value TEXT)", "INVALID SQL")},
                )

            with sqlite3.connect(paths.database) as conn:
                tables = {
                    row[0]
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                }
                value = conn.execute("SELECT value FROM durable").fetchone()[0]
                version = conn.execute("PRAGMA user_version").fetchone()[0]
            self.assertNotIn("transient", tables)
            self.assertEqual(value, "preserved")
            self.assertEqual(version, 1)
            self.assertEqual(len(list(paths.state.glob("agent.sqlite3.pre-v*-*.bak"))), 1)

    def test_content_hashing_deduplicates_objects_and_versions_are_immutable(self) -> None:
        first = self.state.put_resource(
            source=self._source("first.png", b"identical"),
            owner_key="owner-1",
            kind="product_image",
            logical_key="products/hero",
            operation_id="op-resource-1",
        )
        retry = self.state.put_resource(
            source=self._source("retry.png", b"identical"),
            owner_key="owner-1",
            kind="product_image",
            logical_key="products/hero",
            operation_id="op-resource-1",
        )
        second = self.state.put_resource(
            source=self._source("second.png", b"changed"),
            owner_key="owner-1",
            kind="product_image",
            logical_key="products/hero",
            resource_id=first.resource_id,
            expected_version=1,
            operation_id="op-resource-2",
        )

        self.assertEqual(retry, first)
        self.assertEqual(second.version, 2)
        with sqlite3.connect(self.paths.database) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM resource_versions").fetchone()[0], 2)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE resource_versions SET content_hash = 'mutated' "
                    "WHERE resource_id = ? AND version = 1",
                    (first.resource_id,),
                )

    def test_stale_resource_write_is_rejected_without_creating_a_version(self) -> None:
        from local_agent_runtime.storage import VersionConflictError

        version = self.state.put_resource(
            source=self._source("document.txt", b"v1"),
            owner_key="owner-1",
            kind="product_document",
            logical_key="brief",
            operation_id="op-document-1",
        )
        with self.assertRaises(VersionConflictError):
            self.state.put_resource(
                source=self._source("stale.txt", b"stale"),
                owner_key="owner-1",
                kind="product_document",
                logical_key="brief",
                resource_id=version.resource_id,
                expected_version=0,
                operation_id="op-document-stale",
            )
        self.assertEqual(len(self.state.resource_versions(version.resource_id)), 1)

    def test_run_manifest_entries_and_upload_set_preserve_explicit_order(self) -> None:
        one = self.state.put_resource(
            source=self._source("reference.png", b"reference"),
            owner_key="owner-1",
            kind="reference_image",
            logical_key="references/one",
            operation_id="op-reference",
        )
        two = self.state.put_resource(
            source=self._source("product.png", b"product"),
            owner_key="owner-1",
            kind="product_image",
            logical_key="products/one",
            operation_id="op-product",
        )
        self.state.create_run(
            run_id="run-1",
            owner_key="owner-1",
            device_id="device-1",
            workspace_id="workspace-1",
            run_number=4,
            flow_type="reference",
            operation_id="op-run",
        )
        self.state.add_run_entry(
            run_id="run-1",
            entry_id="entry-1",
            resource_id=one.resource_id,
            resource_version=one.version,
            role="reference",
            position=1,
            operation_id="op-entry",
        )
        upload_set = self.state.create_upload_set(
            upload_set_id="upload-1",
            run_id="run-1",
            prompt_id="prompt-1",
            phase="4:5",
            version=1,
            entries=[
                (one.resource_id, one.version, "reference"),
                (two.resource_id, two.version, "product"),
            ],
            operation_id="op-upload",
        )

        self.assertEqual([entry["position"] for entry in upload_set["entries"]], [1, 2])
        self.assertEqual(
            [entry["resource_id"] for entry in upload_set["entries"]],
            [one.resource_id, two.resource_id],
        )
        manifest = self.state.run_manifest("run-1")
        self.assertEqual(manifest["entries"][0]["entry_id"], "entry-1")

    def test_output_versions_and_revisions_retain_lineage(self) -> None:
        source = self.state.put_resource(
            source=self._source("output-1.png", b"output-one"),
            owner_key="owner-1",
            kind="output_image",
            logical_key="outputs/one",
            operation_id="op-output-resource-1",
        )
        result = self.state.put_resource(
            source=self._source("output-2.png", b"output-two"),
            owner_key="owner-1",
            kind="output_image",
            logical_key="outputs/two",
            operation_id="op-output-resource-2",
        )
        self.state.create_run(
            run_id="run-output",
            owner_key="owner-1",
            device_id="device-1",
            workspace_id="workspace-output",
            run_number=5,
            flow_type="structured",
            operation_id="op-output-run",
        )
        output = self.state.create_output(
            output_id="output-1",
            run_id="run-output",
            prompt_id="prompt-1",
            item_id="item-1",
            aspect_ratio="4:5",
            resource_id=source.resource_id,
            resource_version=source.version,
            operation_id="op-create-output",
        )
        revision = self.state.record_revision(
            revision_id="revision-1",
            output_id=output["output_id"],
            source_output_version=1,
            result_resource_id=result.resource_id,
            result_resource_version=result.version,
            engine="chatgpt",
            status="completed",
            attempt=1,
            operation_id="op-revision",
        )

        self.assertEqual(revision["source_output_version"], 1)
        self.assertEqual(revision["result_output_version"], 2)
        versions = self.state.output_versions("output-1")
        self.assertEqual([version["version"] for version in versions], [1, 2])
        self.assertEqual(versions[1]["revision_id"], "revision-1")

    def test_change_log_sequence_is_monotonic_and_resumable(self) -> None:
        first = self.state.put_resource(
            source=self._source("one.txt", b"one"),
            owner_key="owner-1",
            kind="config_file",
            logical_key="config/one",
            operation_id="op-change-1",
        )
        sequence = self.state.change_sequence()
        self.state.put_resource(
            source=self._source("two.txt", b"two"),
            owner_key="owner-1",
            kind="config_file",
            logical_key="config/two",
            operation_id="op-change-2",
        )

        changes = self.state.changes(after=sequence)
        self.assertEqual(len(changes), 1)
        self.assertGreater(changes[0]["sequence"], sequence)
        self.assertNotEqual(changes[0]["resource_id"], first.resource_id)

    def test_outbox_uses_stable_ids_for_idempotent_operation_retries(self) -> None:
        first = self.state.queue_projection(
            owner_key="owner-1",
            operation_id="op-project-1",
            event_type="resource.updated",
            payload={"resource_id": "resource-1", "version": 2},
        )
        second = self.state.queue_projection(
            owner_key="owner-1",
            operation_id="op-project-1",
            event_type="resource.updated",
            payload={"resource_id": "resource-1", "version": 2},
        )
        self.assertEqual(first, second)
        self.assertEqual(len(self.state.pending_outbox()), 1)

    def test_reference_aware_gc_keeps_referenced_objects_then_deletes_orphans(self) -> None:
        version = self.state.put_resource(
            source=self._source("kept.bin", b"kept"),
            owner_key="owner-1",
            kind="import",
            logical_key="imports/kept",
            operation_id="op-gc-resource",
        )
        self.assertEqual(self.state.garbage_collect_objects(), [])

        with self.state._connect() as conn:
            conn.execute("DELETE FROM resource_versions WHERE resource_id = ?", (version.resource_id,))
            conn.execute("DELETE FROM resources WHERE resource_id = ?", (version.resource_id,))
        deleted = self.state.garbage_collect_objects()

        self.assertEqual(deleted, [version.object_sha256])
        self.assertFalse(version.path.exists())

    def test_run_deletion_is_transactional_and_purges_unshared_resources(self) -> None:
        version = self.state.put_resource(
            source=self._source("run-only.png", b"run-only"),
            owner_key="owner-1",
            kind="product_image",
            logical_key="runs/delete/product",
            operation_id="op-delete-resource",
        )
        self.state.create_run(
            run_id="run-delete",
            owner_key="owner-1",
            device_id="device-1",
            workspace_id="workspace-delete",
            run_number=9,
            flow_type="structured",
            operation_id="op-delete-run",
        )
        self.state.add_run_entry(
            run_id="run-delete",
            entry_id="entry-delete",
            resource_id=version.resource_id,
            resource_version=version.version,
            role="product",
            position=1,
            operation_id="op-delete-entry",
        )

        receipt = self.state.delete_run(
            "run-delete",
            operation_id="op-delete-run-local",
            purge_resources=True,
        )
        retry = self.state.delete_run(
            "run-delete",
            operation_id="op-delete-run-local",
            purge_resources=True,
        )

        self.assertEqual(retry, receipt)
        self.assertIsNone(self.state.run_manifest("run-delete"))
        self.assertEqual(self.state.resource_versions(version.resource_id), [])
        self.assertFalse(version.path.exists())
        events = self.state.pending_outbox()
        self.assertEqual(events[-1]["event_type"], "run.deleted")


if __name__ == "__main__":
    unittest.main()
