#!/usr/bin/env python3
"""Ask for the dashboard session cookie, then start the local agent with Chrome.

Replaces the shell flow of exporting AD_FACTORY_SESSION and calling
scripts/local_agent.py. The cookie is read hidden and passed only through
the child process environment, never as a command-line argument.
"""

from __future__ import annotations

import argparse
import getpass
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_API_BASE = "https://ad-factory-3rn5.onrender.com"


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def venv_python(root: Path) -> Path | None:
    if os.name == "nt":
        candidate = root / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = root / ".venv" / "bin" / "python"
    return candidate if candidate.is_file() else None


def ensure_project_venv(root: Path) -> None:
    python = venv_python(root)
    if python is None:
        return
    if Path(sys.executable).resolve() == python.resolve():
        return
    raise SystemExit(
        subprocess.call([str(python), str(Path(__file__).resolve()), *sys.argv[1:]])
    )


def agent_command(
    python: str,
    root: Path,
    *,
    api_base: str,
    data_dir: str,
    browser: str,
) -> list[str]:
    return [
        python,
        str(root / "scripts" / "local_agent.py"),
        "--api-base", api_base,
        "--data-dir", data_dir,
        "--launch-browser",
        "--browser", browser,
    ]


def prompt_session_cookie() -> str:
    print("Paste the dashboard session cookie. Input is hidden and is not stored in shell history.")
    print("Leave blank to reuse a previously registered agent on this machine.")
    try:
        return getpass.getpass("Session cookie: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit("Cancelled.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start the Ad Factory local agent after asking for a dashboard session cookie.",
    )
    parser.add_argument(
        "--api-base",
        default=os.getenv("AGENT_API_BASE", DEFAULT_API_BASE),
        help="Render dashboard URL",
    )
    parser.add_argument(
        "--data-dir",
        default=os.getenv("AGENT_DATA_DIR", ""),
        help="Local agent data root. Default: ~/ad-factory-agent on this machine",
    )
    parser.add_argument("--browser", choices=["chrome", "brave"], default="chrome")
    parser.add_argument(
        "--chrome-path",
        default=os.getenv("CHROME_PATH", ""),
        help="Full path to Chrome or Brave if auto-detect fails. Sets CHROME_PATH for this run.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = repo_root()
    ensure_project_venv(root)

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from local_agent_runtime.storage import resolve_data_root

    data_dir = str(resolve_data_root(args.data_dir or None))
    cookie = prompt_session_cookie()
    env = os.environ.copy()
    chrome_path = str(args.chrome_path or "").strip()
    if chrome_path:
        env["CHROME_PATH"] = chrome_path
    if cookie:
        env["AD_FACTORY_SESSION"] = cookie
    else:
        env.pop("AD_FACTORY_SESSION", None)
        print("No cookie entered; using saved agent credentials if they exist.")

    api_base = str(args.api_base).rstrip("/")
    command = agent_command(
        sys.executable,
        root,
        api_base=api_base,
        data_dir=data_dir,
        browser=args.browser,
    )
    print("Starting local agent")
    print(f"  API:     {api_base}")
    print(f"  Data:    {data_dir}")
    print(f"  Browser: {args.browser}")
    print(f"  Chrome:  {chrome_path or 'auto-detect (set --chrome-path or CHROME_PATH to override)'}")
    return subprocess.call(command, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
