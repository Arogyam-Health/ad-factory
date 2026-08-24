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
        self.assertIn("LOCAL_DASHBOARD_ORIGINS", agent)
        self.assertIn("http://127.0.0.1:4090", agent)
        self.assertIn("_browser_allowed_origins", agent)
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
            os.environ.pop("CHROME_PATH", None)
            args = parse_args([])
        self.assertEqual(args.api_base, "https://ad-factory-pzgh.onrender.com")
        with patch.dict(os.environ, {"AGENT_API_BASE": "https://ad-factory-3rn5.onrender.com"}, clear=False):
            args = parse_args([])
        self.assertEqual(args.api_base, "https://ad-factory-pzgh.onrender.com")
        args = parse_args(["--api-base", "https://ad-factory-3rn5.onrender.com"])
        self.assertEqual(args.api_base, "https://ad-factory-3rn5.onrender.com")
        self.assertEqual(args.browser, "chrome")
        args = parse_args(["--chrome-path", "/opt/custom/chrome"])
        self.assertEqual(args.chrome_path, "/opt/custom/chrome")
        starter = (Path(__file__).resolve().parents[1] / "scripts" / "start_local_agent.py").read_text(encoding="utf-8")
        self.assertIn("except KeyboardInterrupt", starter)
        self.assertIn("subprocess.Popen", starter)
        self.assertIn("process.wait(timeout=20)", starter)
        self.assertIn("return 130", starter)
        agent = (Path(__file__).resolve().parents[1] / "scripts" / "local_agent.py").read_text(encoding="utf-8")
        self.assertIn("-signal.SIGINT", agent)
        self.assertIn("-signal.SIGTERM", agent)

        from scripts.start_local_agent import _run_subprocess

        class _FakeProcess:
            def __init__(self) -> None:
                self.calls = 0

            def wait(self, timeout: float | None = None) -> int:
                self.calls += 1
                if self.calls == 1:
                    raise KeyboardInterrupt
                return 0

            def terminate(self) -> None:
                return None

            def kill(self) -> None:
                return None

        with patch("scripts.start_local_agent.subprocess.Popen", return_value=_FakeProcess()):
            self.assertEqual(_run_subprocess(["python", "-c", "pass"]), 130)

    def test_local_agent_zip_keeps_repo_relative_paths(self) -> None:
        import zipfile

        from scripts.pack_local_agent_zip import ZIP_PREFIX, included_files, write_zip

        required = {
            "scripts/local_agent.py",
            "scripts/start_local_agent.py",
            "scripts/generate_ads.py",
            "scripts/prompt_assembler_templates.json",
            "local_agent_runtime/storage.py",
            "local_agent_runtime/browser.py",
            "background_variant.json",
            "persona_seeds.json",
            "concept.json",
            "dashboard/backend/copy_prompt_templates.json",
            "requirements-local-agent.txt",
            "docs/LOCAL_AGENT_WINDOWS.md",
            "docs/LOCAL_AGENT_MAC.md",
        }
        self.assertTrue(required.issubset(set(included_files())))

        with TemporaryDirectory() as tmp:
            zip_path = write_zip(Path(tmp) / "ad-factory-local-agent.zip")
            with zipfile.ZipFile(zip_path) as archive:
                names = set(archive.namelist())
            self.assertIn(f"{ZIP_PREFIX}/scripts/local_agent.py", names)
            self.assertIn(f"{ZIP_PREFIX}/local_agent_runtime/browser.py", names)
            self.assertIn(f"{ZIP_PREFIX}/start_local_agent.bat", names)
            self.assertIn(f"{ZIP_PREFIX}/start_local_agent.sh", names)
            joined = "\n".join(names)
            self.assertNotIn("/home/mylappy", joined)
            self.assertNotIn("myspace/info", joined)


if __name__ == "__main__":
    unittest.main()
