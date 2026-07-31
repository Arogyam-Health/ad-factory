#!/usr/bin/env python3
from __future__ import annotations

"""Local Playwright agent that connects to Render backend and executes browser automation jobs."""

import argparse
import base64
import io
import mimetypes
import json
import os
import queue
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
import uuid
import zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

import requests


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
_API_SESSIONS = threading.local()
_LOCAL_REVISIONS: dict[str, dict[str, Any]] = {}
_LOCAL_REVISIONS_LOCK = threading.Lock()


def _api_session() -> requests.Session:
    session = getattr(_API_SESSIONS, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"Content-Type": "application/json"})
        _API_SESSIONS.session = session
    return session


def api_request(method: str, path: str, data: Any = None, token: str = "", timeout: int = 10, quiet: bool = False) -> Any:
    url = f"{AGENT_API_BASE}{path}"
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if AGENT_SESSION_COOKIE:
        headers["Cookie"] = f"session={AGENT_SESSION_COOKIE}"
    try:
        response = _api_session().request(
            method,
            url,
            headers=headers,
            json=data,
            timeout=(min(3, max(1, timeout)), timeout),
        )
        if response.status_code >= 400:
            if not quiet:
                detail = response.text.replace("\n", " ")[:500]
                print(f"  [agent] API error {response.status_code} {method} {path}: {detail}", flush=True)
            return None
        return response.json()
    except requests.RequestException as exc:
        if not quiet:
            print(f"  [agent] Request failed: {exc}", flush=True)
        return None


def api_request_retry(
    method: str,
    path: str,
    data: Any = None,
    token: str = "",
    *,
    attempts: int = 4,
    timeout: int = 15,
) -> Any:
    for attempt in range(1, attempts + 1):
        result = api_request(method, path, data, token=token, timeout=timeout, quiet=attempt < attempts)
        if result is not None:
            return result
        if attempt < attempts:
            time.sleep(min(2.0, 0.5 * attempt))
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

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Expose-Headers", "Content-Disposition")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Cache-Control", "no-store")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        request_path = urllib.parse.urlparse(self.path).path
        if request_path == "/health":
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self._cors_headers()
            self.end_headers()
            self.wfile.write(body)
            return
        if request_path == "/artifacts":
            body = json.dumps(collect_all_local_artifacts()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self._cors_headers()
            self.end_headers()
            self.wfile.write(body)
            return
        if request_path.startswith("/revisions/"):
            revision_id = request_path.rsplit("/", 1)[-1]
            with _LOCAL_REVISIONS_LOCK:
                payload = dict(_LOCAL_REVISIONS.get(revision_id) or {})
            if not payload:
                self.send_error(404, "Revision not found")
                return
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self._cors_headers()
            self.end_headers()
            self.wfile.write(body)
            return
        if request_path == "/download-batches":
            batches = [value for value in urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("batch", []) if value]
            archive = build_local_batch_archive(batches)
            if archive is None:
                self.send_error(404, "No local files found for selected batches")
                return
            body, filename = archive
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(body)))
            self._cors_headers()
            self.end_headers()
            self.wfile.write(body)
            return
        if not request_path.startswith("/files/"):
            self.send_error(404)
            return
        rel = urllib.parse.unquote(request_path.removeprefix("/files/"))
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
        self._cors_headers()
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        request_path = urllib.parse.urlparse(self.path).path
        if request_path != "/revisions":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            revision = start_local_image_revision(payload)
        except ValueError as exc:
            self.send_error(400, str(exc))
            return
        body = json.dumps(revision).encode("utf-8")
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_DELETE(self) -> None:
        request_path = urllib.parse.urlparse(self.path).path
        if not request_path.startswith("/files/"):
            self.send_error(404)
            return
        path = resolve_local_artifact_path(request_path)
        if path is None:
            self.send_error(400)
            return
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        path.unlink()
        for metadata_path in (path.with_suffix(".json"), path.with_suffix(path.suffix + ".json")):
            if metadata_path.exists() and metadata_path.is_file():
                metadata_path.unlink()
        body = json.dumps({"status": "deleted", "file": path.name}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)


def start_artifact_server(port: int) -> str:
    LOCAL_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    url = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(f"{url}/artifacts", timeout=1) as resp:
            manifest = json.loads(resp.read())
            if manifest.get("schema_version") == 2:
                print(f"[agent] Reusing local artifact server: {url}")
                return url
    except Exception:
        pass
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), ArtifactHandler)
    except OSError as exc:
        raise RuntimeError(f"Artifact port {port} is occupied by an older agent. Stop the old local_agent.py process and retry.") from exc
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
            "modified_at": path.stat().st_mtime_ns,
        })
    return images


def resolve_local_artifact_path(request_path: str) -> Path | None:
    rel = urllib.parse.unquote(request_path.removeprefix("/files/"))
    if not rel or ".." in Path(rel).parts:
        return None
    path = (LOCAL_OUTPUT_ROOT / rel).resolve()
    try:
        path.relative_to(LOCAL_OUTPUT_ROOT.resolve())
    except ValueError:
        return None
    return path


def build_local_batch_archive(batches: list[str]) -> tuple[bytes, str] | None:
    selected = {str(batch).strip() for batch in batches if str(batch).strip()}
    if not selected:
        return None
    buffer = io.BytesIO()
    count = 0
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for batch in sorted(selected):
            batch_root = (LOCAL_OUTPUT_ROOT / batch).resolve()
            if not batch_root.exists() or not batch_root.is_dir():
                continue
            for path in sorted(batch_root.rglob("*")):
                if not path.is_file() or any(part in {".browser_downloads", "debug"} for part in path.parts):
                    continue
                archive.write(path, arcname=f"{batch}/{path.relative_to(batch_root).as_posix()}")
                count += 1
    if not count:
        return None
    filename = f"ad_factory_{'_'.join(sorted(selected))}.zip"
    return buffer.getvalue(), filename


def start_local_image_revision(payload: dict[str, Any]) -> dict[str, Any]:
    image_url = str(payload.get("image_file") or "")
    comment = str(payload.get("comment") or "").strip()
    engine = str(payload.get("engine") or "chatgpt").lower()
    if not comment:
        raise ValueError("Revision comment is required")
    if engine != "chatgpt":
        raise ValueError("Local image revisions currently require ChatGPT")
    image_path = resolve_local_artifact_path(urllib.parse.urlparse(image_url).path)
    if image_path is None or not image_path.exists() or not image_path.is_file():
        raise ValueError("Local image file was not found")
    revision_id = f"local_rev_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    status_url = f"{AGENT_ARTIFACT_BASE_URL}/revisions/{revision_id}"
    with _LOCAL_REVISIONS_LOCK:
        _LOCAL_REVISIONS[revision_id] = {
            "revision_id": revision_id,
            "status": "queued",
            "message": "Local revision queued",
            "status_url": status_url,
        }
    threading.Thread(
        target=_run_local_image_revision,
        args=(revision_id, image_path, comment),
        daemon=True,
    ).start()
    return {"revision_id": revision_id, "status": "queued", "status_url": status_url}


def _set_local_revision_status(revision_id: str, **updates: Any) -> None:
    with _LOCAL_REVISIONS_LOCK:
        current = _LOCAL_REVISIONS.setdefault(revision_id, {"revision_id": revision_id})
        current.update(updates)


def _run_local_image_revision(revision_id: str, image_path: Path, comment: str) -> None:
    try:
        _set_local_revision_status(revision_id, status="running", message="Generating local revision")
        job_root = next((parent for parent in image_path.parents if parent.name.startswith("job_")), image_path.parent)
        work_root = job_root / "revisions" / revision_id
        prompt_dir = work_root / "prompts"
        output_dir = work_root / "output"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        source_file = work_root / "current_image.images.txt"
        source_file.write_text(str(image_path.resolve()) + "\n", encoding="utf-8")
        stem = re.sub(r"_(?:4_5|9_16)$", "", image_path.stem)
        prompt_path = prompt_dir / f"{stem}.txt"
        aspect_ratio = "9:16" if "/9_16/" in image_path.as_posix() else "4:5"
        prompt_path.write_text(
            f"Edit the uploaded current ad image in {aspect_ratio}. Apply this revision exactly while preserving everything not requested:\n\n{comment}\n\nReturn only the revised image.\n",
            encoding="utf-8",
        )
        cmd = _chatgpt_cmd(
            SCRIPT_DIR / "chatgpt_web_sutomation.py",
            prompt_dir,
            output_dir,
            work_root / "unused_uploads",
            {},
            aspect_ratio,
            prompt_glob=prompt_path.name,
            image_source_file=source_file,
        )
        result = subprocess.run(cmd, cwd=str(ROOT), text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ChatGPT revision exited {result.returncode}")
        candidates = [
            path for path in output_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
            and not any(part in {"debug", ".browser_downloads"} for part in path.parts)
        ]
        if not candidates:
            raise RuntimeError("Revised image was not produced")
        backup_dir = job_root / "revision_history" / revision_id
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(image_path, backup_dir / image_path.name)
        shutil.move(str(max(candidates, key=lambda path: path.stat().st_mtime_ns)), str(image_path))
        _set_local_revision_status(revision_id, status="completed", message="Local image revision completed")
    except Exception as exc:
        _set_local_revision_status(revision_id, status="error", message=f"Revision failed: {exc}", error=str(exc))


def collect_all_local_artifacts(max_jobs: int = 50, max_images: int = 500) -> dict[str, Any]:
    jobs: list[dict[str, Any]] = []
    if LOCAL_OUTPUT_ROOT.exists():
        for job_root in LOCAL_OUTPUT_ROOT.glob("*/job_*"):
            if not job_root.is_dir():
                continue
            images = collect_local_artifacts(job_root, AGENT_ARTIFACT_BASE_URL)
            if not images:
                continue
            metadata: dict[str, Any] = {}
            metadata_path = job_root / ".agent-job.json"
            if metadata_path.exists():
                try:
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                except Exception:
                    metadata = {}
            updated_at = max((path.stat().st_mtime for path in job_root.glob("generated_images/**/*") if path.is_file()), default=0)
            jobs.append({
                "job_id": job_root.name,
                "batch": job_root.parent.name,
                "run_ids": [str(item) for item in (metadata.get("run_ids") or [])],
                "local_output_dir": str(job_root),
                "updated_at": updated_at,
                "images": images,
            })
    jobs.sort(key=lambda item: float(item.get("updated_at") or 0), reverse=True)
    jobs = jobs[:max_jobs]
    images: list[dict[str, Any]] = []
    for job in jobs:
        for image in job["images"]:
            images.append({
                **image,
                "job_id": job["job_id"],
                "batch": job["batch"],
                "run_ids": job.get("run_ids") or [],
            })
            if len(images) >= max_images:
                break
        if len(images) >= max_images:
            break
    return {
        "schema_version": 2,
        "artifact_base_url": AGENT_ARTIFACT_BASE_URL,
        "local_output_dir": str(LOCAL_OUTPUT_ROOT),
        "jobs": jobs,
        "images": images,
    }


class JobProgressReporter:
    """Coalesce remote updates so Render latency never blocks terminal output."""

    def __init__(self, job_id: str, artifact_root: Path | None = None) -> None:
        self.job_id = job_id
        self.artifact_root = artifact_root
        self._latest = ""
        self._last_progress = ""
        self._last_artifacts: tuple[tuple[str, int], ...] = ()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def submit(self, progress: str) -> None:
        with self._lock:
            self._latest = progress

    def close(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(0.5):
            with self._lock:
                progress = self._latest
            payload: dict[str, Any] = {"progress": progress}
            artifacts_changed = False
            if self.artifact_root is not None:
                images = collect_local_artifacts(self.artifact_root, AGENT_ARTIFACT_BASE_URL)
                signature = tuple((str(item.get("url") or ""), int(item.get("bytes") or 0)) for item in images)
                if signature != self._last_artifacts:
                    artifacts_changed = True
                    payload["result"] = {
                        "local_output_dir": str(self.artifact_root),
                        "artifact_base_url": AGENT_ARTIFACT_BASE_URL,
                        "images": images,
                    }
            if not artifacts_changed and (not progress or progress == self._last_progress):
                continue
            acknowledged = api_request(
                "POST",
                f"/api/agents/jobs/{self.job_id}/progress",
                payload,
                token=AGENT_TOKEN,
                timeout=2,
                quiet=True,
            )
            if acknowledged is not None:
                self._last_progress = progress
                if artifacts_changed:
                    self._last_artifacts = signature


def _prepare_916_conversion_prompts(
    *,
    out_45_dir: Path,
    prompt_916_dir: Path,
    source_916_dir: Path,
    template_text: str,
) -> list[dict[str, str]]:
    template = str(template_text or "").strip()
    if not template:
        return []

    image_paths = [
        path
        for path in sorted(out_45_dir.rglob("*"))
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        and not any(part in {"debug", ".browser_downloads"} for part in path.parts)
    ]
    if not image_paths:
        return []

    prompt_916_dir.mkdir(parents=True, exist_ok=True)
    source_916_dir.mkdir(parents=True, exist_ok=True)

    created: list[dict[str, str]] = []
    seen_stems: set[str] = set()
    for index, image_path in enumerate(image_paths, start=1):
        stem = re.sub(r"_(?:4_5|9_16)$", "", image_path.stem)
        stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("_") or f"conversion_{index:02d}"
        if stem in seen_stems:
            stem = f"{stem}_{index:02d}"
        seen_stems.add(stem)

        prompt_path = prompt_916_dir / f"{stem}.txt"
        source_path = source_916_dir / f"{stem}.images.txt"
        prompt_path.write_text(template + "\n", encoding="utf-8")
        source_path.write_text(str(image_path.resolve()) + "\n", encoding="utf-8")
        created.append({"prompt_path": str(prompt_path), "source_file": str(source_path)})

    return created


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

    cmd = [sys.executable, "-u", str(script_path)]
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


def _run_and_stream(job_id: str, cmd: list[str], cwd: Path, artifact_root: Path | None = None) -> tuple[int, str]:
    print(f"  [agent] Running: {' '.join(cmd)}", flush=True)
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        start_new_session=(os.name != "nt"),
    )
    lines: list[str] = []
    assert proc.stdout is not None
    stdout_queue: queue.Queue[str | None] = queue.Queue()
    cancel_event = threading.Event()
    reporter = JobProgressReporter(job_id, artifact_root=artifact_root)

    def _read_stdout() -> None:
        try:
            for line in proc.stdout or []:
                stdout_queue.put(line)
        finally:
            stdout_queue.put(None)

    reader = threading.Thread(target=_read_stdout, daemon=True)
    reader.start()
    cancel_watcher = threading.Thread(
        target=_watch_and_cancel_process,
        args=(job_id, proc, cancel_event),
        daemon=True,
    )
    cancel_watcher.start()
    stream_done = False
    while True:
        if cancel_event.is_set():
            msg = "Canceled by user; local automation process terminated."
            lines.append(msg)
            reporter.close()
            return -15, "\n".join(lines[-200:])

        try:
            line = stdout_queue.get(timeout=0.5)
        except queue.Empty:
            line = None

        if line is None:
            stream_done = stream_done or proc.poll() is not None
        else:
            clean = line.rstrip()
            if clean:
                print(clean, flush=True)
                lines.append(clean)
                if len(lines) > 400:
                    lines = lines[-400:]
                reporter.submit(clean)

        if proc.poll() is not None and (stream_done or stdout_queue.empty()):
            break

    proc.wait()
    reporter.close()
    return proc.returncode, "\n".join(lines[-200:])


def _watch_and_cancel_process(job_id: str, proc: subprocess.Popen, cancel_event: threading.Event) -> None:
    while proc.poll() is None and not cancel_event.is_set():
        if _agent_job_cancel_requested(job_id):
            cancel_event.set()
            msg = "Cancel requested; killing local automation process now."
            print(f"  [agent] {msg}", flush=True)
            api_request("POST", f"/api/agents/jobs/{job_id}/progress", {"progress": "canceling"}, token=AGENT_TOKEN, timeout=1, quiet=True)
            _terminate_process_tree(proc)
            return
        time.sleep(0.5)


def _terminate_process_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:
            proc.terminate()
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            if os.name != "nt":
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass
    except ProcessLookupError:
        pass
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _agent_job_cancel_requested(job_id: str) -> bool:
    status = api_request("GET", f"/api/agents/jobs/{job_id}/status", token=AGENT_TOKEN, timeout=1, quiet=True)
    return bool(status and status.get("cancel_requested"))


def _chatgpt_cmd(
    script_path: Path,
    prompt_dir: Path,
    out_dir: Path,
    upload_dir: Path,
    payload: dict[str, Any],
    aspect_ratio: str,
    *,
    prompt_glob: str = "*.txt",
    image_source_file: Path | None = None,
) -> list[str]:
    cmd = [
        sys.executable,
        "-u",
        str(script_path),
        "--prompt-dir", str(prompt_dir),
        "--prompt-glob", prompt_glob,
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
    if image_source_file is not None:
        cmd.extend(["--image-source-file", str(image_source_file)])
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
    source_916_dir = job_root / "sources_916"
    input_dir = job_root / "input_images"
    out_45_dir = job_root / "generated_images" / batch_name / "4_5"
    out_916_dir = job_root / "generated_images" / batch_name / "9_16"
    job_root.mkdir(parents=True, exist_ok=True)
    (job_root / ".agent-job.json").write_text(
        json.dumps({
            "job_id": job_id,
            "batch": batch_name,
            "run_ids": [str(item) for item in (payload.get("run_ids") or [])],
            "created_at": time.time(),
        }, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )

    _write_text_bundle(prompt_45_dir, list(payload.get("prompts_45") or []))
    _write_text_bundle(prompt_916_dir, list(payload.get("prompts_916") or []))
    _write_binary_bundle(input_dir, list(payload.get("input_images") or []))

    api_request("POST", f"/api/agents/jobs/{job_id}/progress", {"progress": f"local output: {job_root}"}, token=AGENT_TOKEN)

    logs: list[str] = []
    warnings: list[str] = [str(item) for item in (payload.get("warnings") or []) if str(item).strip()]
    if mode in {"45", "both"}:
        if not any(prompt_45_dir.glob("*.txt")):
            api_request(
                "POST",
                f"/api/agents/jobs/{job_id}/fail",
                {"error": "No 4:5 prompt files were included in the local-agent job. Refresh the dashboard after deployment and try again."},
                token=AGENT_TOKEN,
            )
            return
        code, tail = _run_and_stream(
            job_id,
            _chatgpt_cmd(script_path, prompt_45_dir, out_45_dir, input_dir, payload, "4:5"),
            ROOT,
            artifact_root=job_root,
        )
        logs.append(tail)
        if code != 0:
            api_request("POST", f"/api/agents/jobs/{job_id}/fail", {"error": tail or f"ChatGPT 4:5 exited {code}"}, token=AGENT_TOKEN)
            return

    if mode in {"916", "both"}:
        prepared_916 = []
        if mode == "both" and not any(prompt_916_dir.glob("*.txt")):
            prepared_916 = _prepare_916_conversion_prompts(
                out_45_dir=out_45_dir,
                prompt_916_dir=prompt_916_dir,
                source_916_dir=source_916_dir,
                template_text=str(payload.get("conversion_916_template") or ""),
            )
            if prepared_916:
                msg = f"Prepared {len(prepared_916)} 9:16 conversion prompt(s) from local 4:5 output."
                api_request("POST", f"/api/agents/jobs/{job_id}/progress", {"progress": msg}, token=AGENT_TOKEN)

        if not any(prompt_916_dir.glob("*.txt")):
            if mode == "both":
                warning = "Skipped 9:16 phase: no generated 4:5 images or conversion template were available."
                warnings.append(warning)
                api_request("POST", f"/api/agents/jobs/{job_id}/progress", {"progress": warning}, token=AGENT_TOKEN)
            else:
                api_request("POST", f"/api/agents/jobs/{job_id}/fail", {"error": "No 9:16 prompt files were included in the local-agent job"}, token=AGENT_TOKEN)
                return
        elif prepared_916:
            for item in prepared_916:
                prompt_path = Path(item["prompt_path"])
                source_file = Path(item["source_file"])
                code, tail = _run_and_stream(
                    job_id,
                    _chatgpt_cmd(
                        script_path,
                        prompt_916_dir,
                        out_916_dir,
                        input_dir,
                        payload,
                        "9:16",
                        prompt_glob=prompt_path.name,
                        image_source_file=source_file,
                    ),
                    ROOT,
                    artifact_root=job_root,
                )
                logs.append(tail)
                if code != 0:
                    api_request("POST", f"/api/agents/jobs/{job_id}/fail", {"error": tail or f"ChatGPT 9:16 exited {code}"}, token=AGENT_TOKEN)
                    return
        else:
            upload_dir = out_45_dir if out_45_dir.exists() else input_dir
            code, tail = _run_and_stream(
                job_id,
                _chatgpt_cmd(script_path, prompt_916_dir, out_916_dir, upload_dir, payload, "9:16"),
                ROOT,
                artifact_root=job_root,
            )
            logs.append(tail)
            if code != 0:
                api_request("POST", f"/api/agents/jobs/{job_id}/fail", {"error": tail or f"ChatGPT 9:16 exited {code}"}, token=AGENT_TOKEN)
                return

    api_request_retry(
        "POST",
        f"/api/agents/jobs/{job_id}/complete",
        {"result": {
            "local_output_dir": str(job_root),
            "artifact_base_url": AGENT_ARTIFACT_BASE_URL,
            "images": collect_local_artifacts(job_root, AGENT_ARTIFACT_BASE_URL),
            "warnings": warnings,
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

    last_heartbeat = 0.0
    last_connection_warning = 0.0
    connection_was_down = False
    while True:
        try:
            now = time.time()
            if now - last_heartbeat >= 30:
                api_request("POST", "/api/agents/heartbeat", token=AGENT_TOKEN, timeout=5, quiet=True)
                last_heartbeat = now
            jobs = api_request("GET", "/api/agents/jobs/poll", token=AGENT_TOKEN, timeout=5, quiet=True)
            if jobs is None:
                connection_was_down = True
                if now - last_connection_warning >= 30:
                    print("[agent] Render API temporarily unreachable; local images remain available at the artifact server. Retrying...", flush=True)
                    last_connection_warning = now
                time.sleep(POLL_INTERVAL)
                continue
            if connection_was_down:
                print("[agent] Render API connection restored.", flush=True)
                connection_was_down = False
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
