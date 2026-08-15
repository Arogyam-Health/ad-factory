from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_agent_runtime.browser import browser_candidates, resolve_browser_executable


class BrowserDiscoveryTests(unittest.TestCase):
    def test_chrome_path_env_is_first_candidate(self) -> None:
        candidates = browser_candidates(
            "chrome",
            home=Path("/tmp/agent-home"),
            environ={"CHROME_PATH": "/opt/custom/chrome"},
        )
        self.assertEqual(candidates[0], "/opt/custom/chrome")

    def test_windows_paths_use_env_dirs_not_a_fixed_user(self) -> None:
        home = Path("/tmp/other-user")
        candidates = browser_candidates(
            "chrome",
            home=home,
            environ={
                "LOCALAPPDATA": r"D:\Users\Pat\AppData\Local",
                "PROGRAMFILES": r"D:\Apps",
                "PROGRAMFILES(X86)": r"D:\Apps (x86)",
            },
        )
        joined = "\n".join(candidates)
        self.assertNotIn("/home/mylappy", joined)
        self.assertNotIn("myspace/info", joined)
        self.assertIn(
            str(Path(r"D:\Users\Pat\AppData\Local") / "Google" / "Chrome" / "Application" / "chrome.exe"),
            candidates,
        )
        self.assertIn(
            str(Path(r"D:\Apps") / "Google" / "Chrome" / "Application" / "chrome.exe"),
            candidates,
        )
        self.assertFalse(any("C:\\Program Files" in item for item in candidates))
        self.assertIn(
            str(home / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome"),
            candidates,
        )

    def test_data_root_default_is_home_ad_factory_agent(self) -> None:
        from local_agent_runtime.storage import resolve_data_root

        with TemporaryDirectory() as tmp:
            home = Path(tmp) / "someone"
            self.assertEqual(
                resolve_data_root(home=home, environ={}),
                home.resolve() / "ad-factory-agent",
            )

    def test_resolve_uses_which_then_existing_file(self) -> None:
        with TemporaryDirectory() as tmp:
            binary = Path(tmp) / "chrome"
            binary.write_text("", encoding="utf-8")
            found = resolve_browser_executable(
                "chrome",
                candidates=["missing-on-path", str(binary)],
                which=lambda _name: None,
            )
            self.assertEqual(found, str(binary))

        found_which = resolve_browser_executable(
            "chrome",
            candidates=["chrome"],
            which=lambda name: "/usr/bin/google-chrome" if name == "chrome" else None,
        )
        self.assertEqual(found_which, "/usr/bin/google-chrome")

    def test_gemini_default_upload_dir_is_not_a_personal_path(self) -> None:
        root = Path(__file__).resolve().parents[1]
        gemini = (root / "scripts" / "gemini_web_automation.py").read_text(encoding="utf-8")
        chatgpt = (root / "scripts" / "chatgpt_web_sutomation.py").read_text(encoding="utf-8")
        agent = (root / "scripts" / "local_agent.py").read_text(encoding="utf-8")
        self.assertNotIn("myspace/info/input/images", gemini)
        self.assertIn("from local_agent_runtime.browser import resolve_browser_executable", gemini)
        self.assertIn("from local_agent_runtime.browser import resolve_browser_executable", chatgpt)
        self.assertIn("from local_agent_runtime.browser import resolve_browser_executable", agent)
        self.assertNotIn("def _browser_candidates", agent)
        from scripts.start_local_agent import agent_command, parse_args

        root = Path("/tmp/repo")
        command = agent_command(
            "/tmp/repo/.venv/bin/python",
            root,
            api_base="https://ad-factory-3rn5.onrender.com",
            data_dir=str(Path.home() / "ad-factory-agent"),
            browser="chrome",
        )
        self.assertEqual(command[1], str(root / "scripts" / "local_agent.py"))
        self.assertIn("--launch-browser", command)
        self.assertEqual(command[command.index("--browser") + 1], "chrome")
        self.assertNotIn("session", " ".join(command).lower())
        self.assertNotIn("--session-cookie", command)

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_API_BASE", None)
            args = parse_args([])
        self.assertEqual(args.api_base, "https://ad-factory-3rn5.onrender.com")
        self.assertEqual(args.browser, "chrome")


if __name__ == "__main__":
    unittest.main()
