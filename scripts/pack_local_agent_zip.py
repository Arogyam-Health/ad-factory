"""Files that make up the local-agent zip. Paths are repo-relative and must stay that way."""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ZIP_PREFIX = "ad-factory-local-agent"
DEFAULT_ZIP_PATH = REPO_ROOT / "ad-factory-local-agent.zip"

RUNTIME_PY = sorted(
    path.relative_to(REPO_ROOT).as_posix()
    for path in (REPO_ROOT / "local_agent_runtime").glob("*.py")
)

INCLUDED_FILES = [
    *RUNTIME_PY,
    "scripts/__init__.py",
    "scripts/local_agent.py",
    "scripts/start_local_agent.py",
    "scripts/gemini_web_automation.py",
    "scripts/chatgpt_web_sutomation.py",
    "scripts/generate_ads.py",
    "scripts/prompt_assembler_templates.json",
    "background_variant.json",
    "persona_seeds.json",
    "dashboard/backend/copy_prompt_templates.json",
    "requirements-local-agent.txt",
    "docs/LOCAL_AGENT_README.md",
    "docs/LOCAL_AGENT_UBUNTU.md",
    "docs/LOCAL_AGENT_WINDOWS.md",
    "docs/LOCAL_AGENT_MAC.md",
]

README_TXT = """Ad Factory local agent
======================

Share this zip only. It already includes requirements-local-agent.txt
and the setup guides. Do not send extra files.

Unzip and leave these folders as they are:

  local_agent_runtime/
  scripts/
  dashboard/backend/
  docs/

Then open ONE guide and follow it:

  Windows:  docs/LOCAL_AGENT_WINDOWS.md
  Ubuntu:   docs/LOCAL_AGENT_UBUNTU.md
  macOS:    docs/LOCAL_AGENT_MAC.md

Install into a local .venv (do not pip install globally, do not activate):

  Windows:     py -3 -m venv .venv
               .venv\\Scripts\\python.exe -m pip install -r requirements-local-agent.txt
               .venv\\Scripts\\python.exe -m playwright install chromium
               .venv\\Scripts\\python.exe scripts\\start_local_agent.py
               or double-click start_local_agent.bat

  Ubuntu/Mac:  python3 -m venv .venv
               .venv/bin/python -m pip install -r requirements-local-agent.txt
               .venv/bin/python -m playwright install chromium
               .venv/bin/python scripts/start_local_agent.py
               or ./start_local_agent.sh
"""

START_BAT = """@echo off
cd /d "%~dp0"
if exist ".venv\\Scripts\\python.exe" (
  ".venv\\Scripts\\python.exe" scripts\\start_local_agent.py %*
) else (
  py -3 scripts\\start_local_agent.py %*
)
"""

START_SH = """#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then
  exec .venv/bin/python scripts/start_local_agent.py "$@"
fi
exec python3 scripts/start_local_agent.py "$@"
"""


def included_files() -> list[str]:
    return list(INCLUDED_FILES)


def write_zip(destination: Path | None = None) -> Path:
    zip_path = Path(destination or DEFAULT_ZIP_PATH)
    missing = [rel for rel in INCLUDED_FILES if not (REPO_ROOT / rel).is_file()]
    if missing:
        raise FileNotFoundError("Missing local-agent files:\n" + "\n".join(missing))

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in INCLUDED_FILES:
            archive.write(REPO_ROOT / relative, f"{ZIP_PREFIX}/{relative}")
        archive.writestr(f"{ZIP_PREFIX}/README.txt", README_TXT)
        archive.writestr(f"{ZIP_PREFIX}/start_local_agent.bat", START_BAT.replace("\n", "\r\n"))
        archive.writestr(f"{ZIP_PREFIX}/start_local_agent.sh", START_SH)
    return zip_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Pack the local-agent zip with repo-relative paths.")
    parser.add_argument("--out", default=str(DEFAULT_ZIP_PATH))
    args = parser.parse_args()
    path = write_zip(Path(args.out))
    print(f"Wrote {path} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
