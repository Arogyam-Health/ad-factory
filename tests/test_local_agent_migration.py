from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class LegacyStorageInspectionTests(unittest.TestCase):
    def test_inspection_reports_duplicate_inputs_and_missing_metadata(self) -> None:
        from local_agent_runtime.migration import inspect_legacy_root

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "legacy"
            for job_id in ("job_a", "job_b"):
                job = root / "v1" / job_id
                (job / "input_images").mkdir(parents=True)
                (job / "generated_images").mkdir()
                (job / "input_images" / "product.png").write_bytes(b"same-input")
                (job / "generated_images" / f"{job_id}.png").write_bytes(b"output")

            report = inspect_legacy_root(root)

            self.assertEqual(report["job_count"], 2)
            self.assertEqual(report["jobs_missing_metadata"], 2)
            self.assertEqual(report["duplicate_object_groups"], 1)
            self.assertEqual(report["duplicate_bytes"], len(b"same-input"))
            self.assertFalse(report["mutated"])

    def test_apply_is_idempotent_and_preserves_unmapped_files(self) -> None:
        from local_agent_runtime.migration import migrate_legacy_root
        from local_agent_runtime.storage import AgentPaths, AgentState

        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "legacy"
            job = legacy / "v2" / "job_known"
            image = job / "generated_images" / "v2" / "4_5" / "generated images" / "creative_4_5.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"output")
            (job / ".agent-job.json").write_text(
                '{"job_id":"job_known","run_ids":["run-2"],"batch":"v2"}',
                encoding="utf-8",
            )
            unknown = legacy / "v3" / "job_unknown" / "generated_images" / "orphan.png"
            unknown.parent.mkdir(parents=True)
            unknown.write_bytes(b"orphan")
            paths = AgentPaths(Path(tmp) / "agent")

            first = migrate_legacy_root(legacy, paths, apply=True)
            second = migrate_legacy_root(legacy, paths, apply=True)

            self.assertEqual(first["imported_artifacts"], 1)
            self.assertEqual(second["imported_artifacts"], 1)
            self.assertTrue(any(paths.legacy.rglob("orphan.png")))
            manifest = AgentState(paths).manifest()
            self.assertEqual(len(manifest["images"]), 1)
            self.assertEqual(manifest["images"][0]["run_id"], "run-2")


if __name__ == "__main__":
    unittest.main()
