#!/usr/bin/env python3
from __future__ import annotations

"""Local Playwright agent that connects to Render backend and executes browser automation jobs."""

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Optional


AGENT_API_BASE = os.getenv("AGENT_API_BASE", "http://localhost:4090")
POLL_INTERVAL = float(os.getenv("AGENT_POLL_INTERVAL", "5"))
AGENT_TOKEN: str = ""
SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"


def api_request(method: str, path: str, data: Any = None, token: str = "") -> Any:
    url = f"{AGENT_API_BASE}{path}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"  [agent] API error {e.code}: {e.read().decode()}")
        return None
    except Exception as e:
        print(f"  [agent] Request failed: {e}")
        return None


def check_cdp() -> dict[str, Any]:
    try:
        sock = socket.socket()
        sock.settimeout(2)
        sock.connect(("127.0.0.1", 9222))
        sock.close()
        req = urllib.request.Request("http://127.0.0.1:9222/json/version")
        with urllib.request.urlopen(req, timeout=3) as resp:
            info = json.loads(resp.read())
        return {"available": True, "browser": info.get("Browser", ""), "url": "http://127.0.0.1:9222"}
    except Exception:
        return {"available": False, "browser": "", "url": ""}


def execute_job(job: dict[str, Any]) -> None:
    job_id = job["job_id"]
    job_type = job["job_type"]
    payload = job.get("payload", {})

    print(f"  [agent] Executing job {job_id}: {job_type}")

    claim_result = api_request("POST", f"/api/agents/jobs/{job_id}/claim", token=AGENT_TOKEN)
    if claim_result is None:
        print(f"  [agent] Failed to claim job {job_id}")
        return

    try:
        if job_type == "check_cdp":
            result = check_cdp()
            api_request("POST", f"/api/agents/jobs/{job_id}/complete",
                       {"result": result}, token=AGENT_TOKEN)

        elif job_type == "run_gemini":
            _run_script_job(job_id, "gemini_web_automation.py", payload)

        elif job_type == "run_chatgpt":
            _run_script_job(job_id, "chatgpt_web_sutomation.py", payload)

        elif job_type == "run_916_conversion":
            _run_script_job(job_id, "gemini_web_automation.py", {**payload, "aspect_ratio": "9:16"})

        else:
            api_request("POST", f"/api/agents/jobs/{job_id}/fail",
                       {"error": f"Unknown job type: {job_type}"}, token=AGENT_TOKEN)

    except Exception as e:
        print(f"  [agent] Job {job_id} failed: {e}")
        api_request("POST", f"/api/agents/jobs/{job_id}/fail",
                   {"error": str(e)}, token=AGENT_TOKEN)


def _run_script_job(job_id: str, script_name: str, payload: dict[str, Any]) -> None:
    script_path = SCRIPT_DIR / script_name
    if not script_path.exists():
        api_request("POST", f"/api/agents/jobs/{job_id}/fail",
                   {"error": f"Script not found: {script_path}"}, token=AGENT_TOKEN)
        return

    prompt_dir = payload.get("prompt_dir", "")
    out_dir = payload.get("out_dir", "")
    cdp_url = payload.get("cdp_url", "http://127.0.0.1:9222")

    cmd = [sys.executable, str(script_path)]
    if prompt_dir:
        cmd.extend(["--prompt-dir", prompt_dir])
    if out_dir:
        cmd.extend(["--out-dir", out_dir])
    cmd.extend(["--cdp-url", cdp_url])

    api_request("POST", f"/api/agents/jobs/{job_id}/progress",
               {"progress": "starting"}, token=AGENT_TOKEN)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for line in iter(proc.stdout.readline, ""):
            if line:
                api_request("POST", f"/api/agents/jobs/{job_id}/progress",
                           {"progress": line.strip()}, token=AGENT_TOKEN)
        stdout, stderr = proc.communicate()
        if proc.returncode == 0:
            api_request("POST", f"/api/agents/jobs/{job_id}/complete",
                       {"result": {"stdout": stdout[:5000]}}, token=AGENT_TOKEN)
        else:
            api_request("POST", f"/api/agents/jobs/{job_id}/fail",
                       {"error": stderr[:5000]}, token=AGENT_TOKEN)
    except Exception as e:
        api_request("POST", f"/api/agents/jobs/{job_id}/fail",
                   {"error": str(e)}, token=AGENT_TOKEN)


def register_and_run(args: argparse.Namespace) -> None:
    global AGENT_API_BASE, AGENT_TOKEN

    if args.api_base:
        AGENT_API_BASE = args.api_base

    if args.token:
        AGENT_TOKEN = args.token
        print(f"[agent] Using existing token")
    else:
        print(f"[agent] Registering agent with {AGENT_API_BASE}...")
        result = api_request("POST", "/api/agents/register",
                           {"name": args.name, "description": f"Local agent on {socket.gethostname()}"})
        if result is None:
            print("[agent] Failed to register. Make sure the dashboard is running.")
            sys.exit(1)
        AGENT_TOKEN = result["token"]
        print(f"[agent] Registered: {result['agent_id']}")
        print(f"[agent] Token: {AGENT_TOKEN}")
        print(f"[agent] Save this token for future runs with --token")

    print(f"[agent] Polling {AGENT_API_BASE} every {POLL_INTERVAL}s...")
    print(f"[agent] CDP status: {check_cdp()}")

    while True:
        try:
            api_request("POST", "/api/agents/heartbeat", token=AGENT_TOKEN)
            jobs = api_request("GET", "/api/agents/jobs/poll", token=AGENT_TOKEN)
            if jobs:
                for job in jobs:
                    execute_job(job)
        except Exception as e:
            print(f"[agent] Poll error: {e}")
        time.sleep(POLL_INTERVAL)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ad Factory Local Playwright Agent")
    parser.add_argument("--api-base", default=AGENT_API_BASE, help="Render backend URL")
    parser.add_argument("--token", default="", help="Existing agent token (skip registration)")
    parser.add_argument("--name", default=f"agent-{socket.gethostname()}", help="Agent name")
    parser.add_argument("--poll-interval", type=float, default=POLL_INTERVAL)
    args = parser.parse_args()
    register_and_run(args)


if __name__ == "__main__":
    main()
