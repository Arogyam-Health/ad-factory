from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path


def dashboard_subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    if sys.platform == "win32":
        sep = ";"
        venv_lib = Path(sys.executable).parent.parent / "Lib" / "site-packages"
    else:
        sep = ":"
        venv_lib = Path(sys.executable).parent.parent / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    playwright_path = str(venv_lib)
    if playwright_path not in env.get("PYTHONPATH", ""):
        env["PYTHONPATH"] = playwright_path + (sep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def debugger_endpoint_reachable(address: str) -> bool:
    if not address:
        return False
    url = f"http://{address}/json/version"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False
