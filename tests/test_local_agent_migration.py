from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LegacyStorageInspectionTests(unittest.TestCase):
    def test_legacy_output_root_import_path_is_retired(self) -> None:
        import local_agent_runtime.migration as migration

        agent = (ROOT / "scripts" / "local_agent.py").read_text(encoding="utf-8")
        self.assertFalse(hasattr(migration, "inspect_legacy_root"))
        self.assertFalse(hasattr(migration, "migrate_legacy_root"))
        self.assertNotIn("--legacy-root", agent)
        self.assertIn("reset-local-data", agent)


if __name__ == "__main__":
    unittest.main()
