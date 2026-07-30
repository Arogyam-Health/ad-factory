#!/usr/bin/env python3
from __future__ import annotations

"""Local Playwright agent that connects to Render backend and executes browser automation jobs."""

import argparse
import base64
import mimetypes
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional


AGENT_API_BASE = os.getenv("AGENT_API_BASE", "http://localhost:4090")
POLL_INTERVAL = float(os.getenv("AGENT_POLL_INTERVAL", "5"))
AGENT_TOKEN: str = ""
AGENT_SESSION_COOKIE: str = ""
AGENT_CDP_URL = os.getenv("AGENT_CDP_URL", "http://127.0.0.1:9222")
AGENT_ARTIFACT_PORT = int(os.getenv("AGENT_ARTIFACT_PORT", "8765"))
AGENT_ARTIFACT_BASE_URL = f"http://127.0.0.1:{AGENT_ARTIFACT_PORT}"
SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
ROOT = Path(__file__).resolve().parent.parent
LOCAL_OUTPUT_ROOT = Path(os.getenv("AGENT_OUTPUT_DIR", str(Path.home() / "ad-factory-agent-output"))).expanduser()


def api_request(method: str, path: str, data: Any = None, token: str = "") -> Any:
    url = f"{AGENT_API_BASE}{path}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if AGENT_SESSION_COOKIE:
        headers["Cookie"] = f"session={AGENT_SESSION_COOKIE}"
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"  [agent] API error {e.code} {method} {path}: {e.read().decode()}")
        return None
    except Exception as e:
        print(f"  [agent] Request failed: {e}")
        return None


def check_cdp() -> dict[str, Any]:
    port = int(AGENT_CDP_URL.rsplit(":", 1)[-1].split("/", 1)[0] or "9222")
    try:
        sock = socket.socket()
        sock.settimeout(2)
        sock.connect(("127.0.0.1", port))
        sock.close()
        req = urllib.request.Request(f"{AGENT_CDP_URL.rstrip('/')}/json/version")
        with urllib.request.urlopen(req, timeout=3) as resp:
            info = json.loads(resp.read())
        return {"available": True, "browser": info.get("Browser", ""), "url": AGENT_CDP_URL}
    except Exception:
        return {"available": False, "browser": "", "url": ""}


def _browser_candidates(browser: str) -> list[str]:
    browser = browser.lower().strip()
    home = Path.home()
    if browser == "chrome":
        return [
            # Linux
            "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
            "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium", "/usr/bin/chromium-browser",
            "/snap/bin/chromium",
            # macOS
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            str(home / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            # Windows
            str(home / "AppData/Local/Google/Chrome/Application/chrome.exe"),
            str(home / "AppData/Local/Google/Chrome SxS/Application/chrome.exe"),
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    return [
        # Linux
        "brave-browser", "brave", "brave-browser-stable",
        "/usr/bin/brave-browser", "/usr/bin/brave",
        "/snap/bin/brave",
        # macOS
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        str(home / "Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
        # Windows
        str(home / "AppData/Local/BraveSoftware/Brave-Browser/Application/brave.exe"),
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
    ]


def _resolve_browser_exe(candidates: list[str]) -> str | None:
    for c in candidates:
        try:
            if os.name != "nt":
                resolved = shutil.which(c)
                if resolved:
                    return resolved
                if Path(c).exists():
                    return c
            else:
                if Path(c).exists():
                    return c
        except Exception:
            continue
    return None


def _prompt_browser_path(browser: str) -> str | None:
    print(f"[agent] Could not find {browser} automatically. Looking in common locations.")
    try:
        path = input(f"[agent] Enter the full path to your {browser} executable (or press Enter to skip): ").strip()
        if path:
            p = Path(path)
            if p.exists() and (os.name != "nt" or p.suffix == ".exe"):
                return str(p.resolve())
            print(f"[agent] Path does not exist: {path}")
    except (EOFError, KeyboardInterrupt):
        pass
    return None


class ArtifactHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/health":
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            return
        if not self.path.startswith("/files/"):
            self.send_error(404)
            return
        rel = urllib.parse.unquote(self.path.removeprefix("/files/")).split("?", 1)[0]
        if not rel or ".." in Path(rel).parts:
            self.send_error(400)
            return
        path = (LOCAL_OUTPUT_ROOT / rel).resolve()
        try:
            path.relative_to(LOCAL_OUTPUT_ROOT.resolve())
        except ValueError:
            self.send_error(403)
            return
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def start_artifact_server(port: int) -> str:
    LOCAL_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    url = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=1) as resp:
            if resp.read() == b"ok":
                print(f"[agent] Reusing local artifact server: {url}")
                return url
    except Exception:
        pass
    server = ThreadingHTTPServer(("127.0.0.1", port), ArtifactHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"[agent] Local artifact server: {url} -> {LOCAL_OUTPUT_ROOT}")
    return url


def collect_local_artifacts(job_root: Path, artifact_base_url: str) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    for path in sorted(job_root.glob("generated_images/**/*")):
        if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        rel_output = path.relative_to(job_root).as_posix()
        rel_server = path.relative_to(LOCAL_OUTPUT_ROOT).as_posix()
        images.append({
            "name": path.name,
            "path": rel_output,
            "url": f"{artifact_base_url.rstrip('/')}/files/{urllib.parse.quote(rel_server)}",
            "bytes": path.stat().st_size,
        })
    return images


def launch_browser_cdp(browser: str, port: int = 9222) -> None:
    if check_cdp().get("available"):
        return
    profile_dir = Path.home() / f".ad-factory-{browser.lower()}-cdp"
    profile_dir.mkdir(parents=True, exist_ok=True)
    args = [
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "https://chatgpt.com/",
    ]
    candidates = _browser_candidates(browser)
    exe = _resolve_browser_exe(candidates)
    if not exe:
        exe = _prompt_browser_path(browser)
    if exe:
        try:
            subprocess.Popen([exe, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(30):
                time.sleep(1)
                if check_cdp().get("available"):
                    print(f"[agent] Launched {browser} with CDP on 127.0.0.1:{port}")
                    return
            print(f"[agent] {browser} launched but CDP not reachable on port {port} after 30s.")
        except Exception as exc:
            print(f"[agent] Failed to launch {browser}: {exc}")
    print(f"[agent] Start {browser} manually with --remote-debugging-port={port} then re-run.")


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

        elif job_type == "run_chatgpt_batch":
            _run_chatgpt_batch_job(job_id, payload)

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


def _write_text_bundle(root: Path, files: list[dict[str, str]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for item in files:
        name = str(item.get("name") or "").replace("\\", "/").lstrip("/")
        if not name or ".." in Path(name).parts:
            continue
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(item.get("content") or ""), encoding="utf-8")


def _write_binary_bundle(root: Path, files: list[dict[str, str]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for item in files:
        name = str(item.get("name") or "").replace("\\", "/").lstrip("/")
        if not name or ".." in Path(name).parts:
            continue
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(item.get("base64") or ""))


def _run_and_stream(job_id: str, cmd: list[str], cwd: Path) -> tuple[int, str]:
    print(f"  [agent] Running: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    lines: list[str] = []
    assert proc.stdout is not None
    for line in iter(proc.stdout.readline, ""):
        if not line:
            break
        clean = line.rstrip()
        if clean:
            print(clean)
            lines.append(clean)
            if len(lines) > 400:
                lines = lines[-400:]
            api_request("POST", f"/api/agents/jobs/{job_id}/progress", {"progress": clean}, token=AGENT_TOKEN)
    proc.wait()
    return proc.returncode, "\n".join(lines[-200:])


def _chatgpt_cmd(script_path: Path, prompt_dir: Path, out_dir: Path, upload_dir: Path, payload: dict[str, Any], aspect_ratio: str) -> list[str]:
    cmd = [
        sys.executable,
        str(script_path),
        "--prompt-dir", str(prompt_dir),
        "--prompt-glob", "*.txt",
        "--out-dir", str(out_dir),
        "--timeout", str(int(payload.get("timeout") or 420)),
        "--download-timeout", str(int(payload.get("download_timeout") or 90)),
        "--manual-login-timeout", str(int(payload.get("manual_login_timeout") or 180)),
        "--upload-dir", str(upload_dir),
        "--cdp-url", str(payload.get("cdp_url") or AGENT_CDP_URL),
        "--aspect-ratio", aspect_ratio,
        "--starting-prompt-file", "",
        "--browser-download-dir", str(out_dir / ".browser_downloads"),
    ]
    if payload.get("headless"):
        cmd.append("--headless")
    return cmd


def _run_chatgpt_batch_job(job_id: str, payload: dict[str, Any]) -> None:
    script_path = SCRIPT_DIR / "chatgpt_web_sutomation.py"
    if not script_path.exists():
        api_request("POST", f"/api/agents/jobs/{job_id}/fail", {"error": f"Script not found: {script_path}"}, token=AGENT_TOKEN)
        return

    cdp = check_cdp()
    if not cdp.get("available"):
        api_request(
            "POST",
            f"/api/agents/jobs/{job_id}/fail",
            {"error": f"No local browser CDP found at {AGENT_CDP_URL}. Start Chrome/Brave with --remote-debugging-port=9222."},
            token=AGENT_TOKEN,
        )
        return

    batch_name = str(payload.get("batch_name") or job_id).replace("/", "_")
    mode = str(payload.get("mode") or "45")
    job_root = LOCAL_OUTPUT_ROOT / batch_name / job_id
    prompt_45_dir = job_root / "prompts_45"
    prompt_916_dir = job_root / "prompts_916"
    input_dir = job_root / "input_images"
    out_45_dir = job_root / "generated_images" / batch_name / "4_5"
    out_916_dir = job_root / "generated_images" / batch_name / "9_16"

    _write_text_bundle(prompt_45_dir, list(payload.get("prompts_45") or []))
    _write_text_bundle(prompt_916_dir, list(payload.get("prompts_916") or []))
    _write_binary_bundle(input_dir, list(payload.get("input_images") or []))

    api_request("POST", f"/api/agents/jobs/{job_id}/progress", {"progress": f"local output: {job_root}"}, token=AGENT_TOKEN)

    logs: list[str] = []
    if mode in {"45", "both"}:
        code, tail = _run_and_stream(
            job_id,
            _chatgpt_cmd(script_path, prompt_45_dir, out_45_dir, input_dir, payload, "4:5"),
            ROOT,
        )
        logs.append(tail)
        if code != 0:
            api_request("POST", f"/api/agents/jobs/{job_id}/fail", {"error": tail or f"ChatGPT 4:5 exited {code}"}, token=AGENT_TOKEN)
            return

    if mode in {"916", "both"}:
        if not any(prompt_916_dir.glob("*.txt")):
            api_request("POST", f"/api/agents/jobs/{job_id}/fail", {"error": "No 9:16 prompt files were included in the local-agent job"}, token=AGENT_TOKEN)
            return
        upload_dir = out_45_dir if out_45_dir.exists() else input_dir
        code, tail = _run_and_stream(
            job_id,
            _chatgpt_cmd(script_path, prompt_916_dir, out_916_dir, upload_dir, payload, "9:16"),
            ROOT,
        )
        logs.append(tail)
        if code != 0:
            api_request("POST", f"/api/agents/jobs/{job_id}/fail", {"error": tail or f"ChatGPT 9:16 exited {code}"}, token=AGENT_TOKEN)
            return

    api_request(
        "POST",
        f"/api/agents/jobs/{job_id}/complete",
        {"result": {
            "local_output_dir": str(job_root),
            "artifact_base_url": AGENT_ARTIFACT_BASE_URL,
            "images": collect_local_artifacts(job_root, AGENT_ARTIFACT_BASE_URL),
            "log_tail": "\n".join(logs)[-4000:],
        }},
        token=AGENT_TOKEN,
    )


def register_and_run(args: argparse.Namespace) -> None:
    global AGENT_API_BASE, AGENT_TOKEN, AGENT_SESSION_COOKIE, POLL_INTERVAL, LOCAL_OUTPUT_ROOT, AGENT_CDP_URL, AGENT_ARTIFACT_PORT, AGENT_ARTIFACT_BASE_URL

    if args.api_base:
        AGENT_API_BASE = args.api_base
    if args.session_cookie:
        AGENT_SESSION_COOKIE = args.session_cookie
    if args.poll_interval:
        POLL_INTERVAL = args.poll_interval
    if args.output_dir:
        LOCAL_OUTPUT_ROOT = Path(args.output_dir).expanduser()
    AGENT_CDP_URL = f"http://127.0.0.1:{args.cdp_port}"
    AGENT_ARTIFACT_PORT = args.artifact_port
    AGENT_ARTIFACT_BASE_URL = start_artifact_server(AGENT_ARTIFACT_PORT)
    if args.launch_browser:
        launch_browser_cdp(args.browser, port=args.cdp_port)

    if args.token:
        AGENT_TOKEN = args.token
        print(f"[agent] Using existing token")
    else:
        print(f"[agent] Registering agent with {AGENT_API_BASE}...")
        result = api_request("POST", "/api/agents/register",
                            {"name": args.name, "description": f"Local agent on {socket.gethostname()}"})
        if result is None:
            print("[agent] Failed to register. Pass --session-cookie once, or reuse a saved --token.")
            sys.exit(1)
        AGENT_TOKEN = result["token"]
        print(f"[agent] Registered: {result['agent_id']}")
        print(f"[agent] Token: {AGENT_TOKEN}")
        print(f"[agent] Save this token for future runs with --token")

    print(f"[agent] Polling {AGENT_API_BASE} every {POLL_INTERVAL}s...")
    print(f"[agent] Saving generated images under {LOCAL_OUTPUT_ROOT}")
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
    parser.add_argument("--session-cookie", default=os.getenv("AD_FACTORY_SESSION", ""), help="Dashboard session cookie used only to register a new agent token")
    parser.add_argument("--name", default=f"agent-{socket.gethostname()}", help="Agent name")
    parser.add_argument("--poll-interval", type=float, default=POLL_INTERVAL)
    parser.add_argument("--output-dir", default=str(LOCAL_OUTPUT_ROOT), help="Local directory for generated images")
    parser.add_argument("--launch-browser", action="store_true", help="Start Brave/Chrome locally with CDP before polling")
    parser.add_argument("--browser", choices=["brave", "chrome"], default="brave", help="Browser to launch when --launch-browser is used")
    parser.add_argument("--cdp-port", type=int, default=9222, help="Local CDP port for --launch-browser")
    parser.add_argument("--artifact-port", type=int, default=AGENT_ARTIFACT_PORT, help="Local localhost port used to serve generated images to the dashboard")
    args = parser.parse_args()
    register_and_run(args)


if __name__ == "__main__":
    main()
