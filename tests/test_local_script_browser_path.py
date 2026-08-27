from __future__ import annotations

import inspect
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LocalScriptBrowserPathTests(unittest.TestCase):
    def test_local_script_browser_does_not_use_repo_scripts_folder(self) -> None:
        source = (ROOT / "local_agent_runtime" / "structured_browser.py").read_text(encoding="utf-8")
        self.assertNotIn(' / "scripts" /', source)
        self.assertIn("chatgpt_web_sutomation.py", source)
        self.assertIn("was not found next to the local agent", source)

    def test_default_script_dir_is_local_agent_runtime(self) -> None:
        from local_agent_runtime.structured_browser import LocalScriptBrowser

        browser = LocalScriptBrowser()
        self.assertEqual(browser.project_root, Path(inspect.getfile(LocalScriptBrowser)).resolve().parent)
        self.assertTrue((browser.project_root / "chatgpt_web_sutomation.py").is_file())
        self.assertTrue((browser.project_root / "gemini_web_automation.py").is_file())

    def test_chatgpt_does_not_read_zip_persona_seeds(self) -> None:
        source = (ROOT / "local_agent_runtime" / "chatgpt_web_sutomation.py").read_text(encoding="utf-8")
        self.assertNotIn("persona_seeds.json", source)
        self.assertIn("_persona_from_sidecar", source)

    def test_agent_dispatches_up_to_three_local_jobs(self) -> None:
        source = (ROOT / "local_agent_runtime" / "local_agent.py").read_text(encoding="utf-8")
        self.assertIn("MAX_LOCAL_JOB_SLOTS = 3", source)
        self.assertIn("def _dispatch_job(", source)
        self.assertIn("_dispatch_job(job)", source)
        self.assertIn("leaving {job_id} queued", source)
