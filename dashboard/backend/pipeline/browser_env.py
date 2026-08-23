from __future__ import annotations

import os
import subprocess
import sys
import urllib.request
from pathlib import Path

from fastapi import HTTPException


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


def detect_wsl_windows_host_ip() -> str:
    """Return the Windows host IP from WSL's perspective (e.g., 172.18.160.1).
    Returns '127.0.0.1' if not in WSL or detection fails."""
    if not Path("/mnt/c").exists():
        return "127.0.0.1"
    try:
        ip_route = subprocess.run(
            ["ip", "route"], capture_output=True, text=True, timeout=5
        )
        for line in (ip_route.stdout or "").splitlines():
            if "default" in line:
                parts = line.split()
                if len(parts) >= 3:
                    return parts[2]
    except Exception:
        pass
    return "127.0.0.1"


def detect_wsl_user() -> str:
    """Return the current WSL user (matches /mnt/c/Users/<name> for that user's home).
    Empty string if detection fails."""
    user = os.getenv("USER") or os.getenv("USERNAME")
    if user:
        return user
    home = str(Path.home())
    if home.startswith("/home/"):
        parts = home.split("/")
        if len(parts) >= 3:
            return parts[2]
    return ""


def wsl_chrome_cdp_url() -> str:
    """Return the CDP URL for Windows Chrome from WSL.
    Uses the portproxy on 9223 → Windows 9222. Set up via scripts/setup_cdp_proxy.ps1."""
    return f"http://{detect_wsl_windows_host_ip()}:9223"


def extension_browser_required_for_chatgpt(visible: bool) -> bool:
    mode = str(os.getenv("BROWSER_AUTOMATION_MODE") or "").strip().lower()
    is_render = str(os.getenv("RENDER") or "").strip().lower() == "true"
    return visible or mode in {"local-agent", "extension", "extension-bridge", "remote-extension"} or is_render


def start_extension_cdp_proxy_for_user(user_id: str, *, visible: bool) -> str:
    raise HTTPException(
        status_code=410,
        detail=(
            "The Chrome extension CDP bridge is retired. "
            "Pair the local agent and use a local browser profile."
        ),
    )


def render_chatgpt_uses_local_agent() -> bool:
    mode = str(os.getenv("BROWSER_AUTOMATION_MODE") or "").strip().lower()
    is_render = str(os.getenv("RENDER") or "").strip().lower() == "true"
    return is_render or mode in {"local-agent", "agent", "remote-agent"}
