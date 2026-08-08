#!/usr/bin/env python3
from __future__ import annotations

"""Local Playwright agent that connects to Render backend and executes browser automation jobs."""

import argparse
import base64
import hashlib
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
import multiprocessing
from pathlib import Path
from typing import Any, Optional

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from local_agent_runtime.artifact_server import run_artifact_server
from local_agent_runtime.migration import format_inspection, inspect_legacy_root, migrate_legacy_root
from local_agent_runtime.storage import (
    AgentPaths,
    AgentState,
    ContentStore,
    InstanceLock,
    LockHeldError,
    artifact_access_token,
    resolve_data_root,
)
from local_agent_runtime.transport import AgentWebSocketClient, JobSignal


AGENT_API_BASE = os.getenv("AGENT_API_BASE", "http://localhost:4090")
POLL_INTERVAL = float(os.getenv("AGENT_POLL_INTERVAL", "5"))
AGENT_TOKEN: str = ""
AGENT_SESSION_COOKIE: str = ""
AGENT_CDP_URL = os.getenv("AGENT_CDP_URL", "http://127.0.0.1:9222")
AGENT_ARTIFACT_PORT = int(os.getenv("AGENT_ARTIFACT_PORT", "8765"))
AGENT_ARTIFACT_BASE_URL = f"http://127.0.0.1:{AGENT_ARTIFACT_PORT}"
SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
ROOT = Path(__file__).resolve().parent.parent
AGENT_PATHS = AgentPaths(resolve_data_root())
LOCAL_OUTPUT_ROOT = AGENT_PATHS.staging
AGENT_STATE: AgentState | None = None
CONTENT_STORE: ContentStore | None = None
JOB_SIGNAL = JobSignal()
WS_CLIENT: AgentWebSocketClient | None = None
LAST_API_ERROR = ""
_API_SESSIONS = threading.local()


def _api_session() -> requests.Session:
    session = getattr(_API_SESSIONS, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"Content-Type": "application/json"})
        _API_SESSIONS.session = session
    return session


def api_request(method: str, path: str, data: Any = None, token: str = "", timeout: int = 10, quiet: bool = False) -> Any:
    global LAST_API_ERROR
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
            LAST_API_ERROR = f"HTTP {response.status_code}: {response.text.replace(chr(10), ' ')[:200]}"
            if not quiet:
                detail = response.text.replace("\n", " ")[:500]
                print(f"  [agent] API error {response.status_code} {method} {path}: {detail}", flush=True)
            return None
        LAST_API_ERROR = ""
        return response.json()
    except requests.RequestException as exc:
        LAST_API_ERROR = f"{type(exc).__name__}: {exc}"
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


def report_job_terminal(job_id: str, action: str, payload: dict[str, Any]) -> bool:
    result = api_request_retry(
        "POST",
        f"/api/agents/jobs/{job_id}/{action}",
        payload,
        token=AGENT_TOKEN,
        attempts=3,
        timeout=20,
    )
    status = "completed" if action == "complete" else "failed"
    if result is not None:
        if AGENT_STATE is not None:
            AGENT_STATE.update_job_status(job_id, status)
        return True
    if AGENT_STATE is not None:
        AGENT_STATE.record_terminal_outbox(
            job_id,
            status,
            action,
            {"job_id": job_id, "payload": payload},
        )
        print(f"  [agent] Render unavailable; queued durable {action} event for {job_id}", flush=True)
    return False


def flush_terminal_outbox() -> None:
    if AGENT_STATE is None:
        return
    for event in AGENT_STATE.pending_outbox():
        payload = event.get("payload") or {}
        job_id = str(payload.get("job_id") or "")
        body = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        if not job_id:
            AGENT_STATE.mark_outbox_delivered(str(event["event_id"]))
            continue
        acknowledged = api_request(
            "POST",
            f"/api/agents/jobs/{job_id}/{event['event_type']}",
            body,
            token=AGENT_TOKEN,
            timeout=20,
            quiet=True,
        )
        if acknowledged is None:
            return
        AGENT_STATE.mark_outbox_delivered(str(event["event_id"]))


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


def publish_local_artifacts(job_root: Path, payload: dict[str, Any], job_id: str) -> list[dict[str, Any]]:
    if AGENT_STATE is None:
        return []
    prompt_items = [item for item in (payload.get("prompt_items") or []) if isinstance(item, dict)]
    items_by_stem = {
        Path(str(item.get("name") or "")).stem: item
        for item in prompt_items
        if str(item.get("name") or "").strip()
    }
    run_ids = [str(value) for value in (payload.get("run_ids") or []) if str(value).strip()]
    owner_key = str(payload.get("owner_key") or "local")
    access_token = artifact_access_token(AGENT_PATHS, owner_key)
    access_query = urllib.parse.urlencode({"owner": owner_key, "token": access_token})
    published: list[dict[str, Any]] = []
    for path in sorted(job_root.glob("generated_images/**/*")):
        if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        if any(part in {"debug", ".browser_downloads", "to_be_regenerated"} for part in path.parts):
            continue
        output_stem = re.sub(r"_(?:4_5|9_16)$", "", path.stem)
        item = items_by_stem.get(output_stem)
        if item is None and len(prompt_items) == 1:
            item = prompt_items[0]
        if item is None and len(run_ids) == 1:
            fallback_name = output_stem + ".txt"
            item = {
                "item_id": "item_" + hashlib.sha256(f"{run_ids[0]}:{fallback_name}".encode()).hexdigest()[:20],
                "run_id": run_ids[0],
                "run_number": _batch_number(str(payload.get("batch_name") or "")),
                "prompt_id": hashlib.sha256(fallback_name.encode()).hexdigest()[:16],
                "name": fallback_name,
            }
        if item is None:
            print(f"  [agent] Skipping unmapped artifact {path.name}; no unique prompt item", flush=True)
            continue
        aspect_ratio = "9:16" if "9_16" in path.parts else "4:5"
        artifact = AGENT_STATE.publish_artifact(
            source=path,
            owner_key=owner_key,
            run_id=str(item.get("run_id") or "unknown-run"),
            run_number=int(item.get("run_number") or _batch_number(str(payload.get("batch_name") or ""))),
            job_id=job_id,
            item_id=str(item.get("item_id") or "unknown-item"),
            prompt_id=str(item.get("prompt_id") or "unknown-prompt"),
            aspect_ratio=aspect_ratio,
            filename=path.name,
        )
        published.append({
            "artifact_id": artifact.artifact_id,
            "name": artifact.path.name,
            "path": artifact.path.relative_to(AGENT_PATHS.root).as_posix(),
            "url": f"{AGENT_ARTIFACT_BASE_URL.rstrip('/')}/files/{artifact.artifact_id}?{access_query}",
            "bytes": artifact.path.stat().st_size,
            "modified_at": artifact.path.stat().st_mtime_ns,
            "run_id": str(item.get("run_id") or ""),
            "run_ids": [str(item.get("run_id") or "")],
            "prompt_id": str(item.get("prompt_id") or ""),
            "item_id": str(item.get("item_id") or ""),
            "aspect_ratio": aspect_ratio,
        })
    return published


def _batch_number(batch: str) -> int:
    match = re.match(r"^v(\d+)(?:-|$)", batch, flags=re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _generated_artifact_signature(job_root: Path) -> tuple[tuple[str, int, int], ...]:
    signature = []
    for path in sorted(job_root.glob("generated_images/**/*")):
        if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        if any(part in {"debug", ".browser_downloads", "to_be_regenerated"} for part in path.parts):
            continue
        stat = path.stat()
        signature.append((path.relative_to(job_root).as_posix(), stat.st_size, stat.st_mtime_ns))
    return tuple(signature)


class JobProgressReporter:
    """Coalesce remote updates so Render latency never blocks terminal output."""

    def __init__(self, job_id: str, artifact_root: Path | None = None, payload: dict[str, Any] | None = None) -> None:
        self.job_id = job_id
        self.artifact_root = artifact_root
        self.payload = payload or {}
        self._latest = ""
        self._last_progress = ""
        self._last_artifacts: tuple[tuple[str, int], ...] = ()
        self._last_artifact_sources: tuple[tuple[str, int, int], ...] = ()
        self._last_report_at = 0.0
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
            source_signature = self._last_artifact_sources
            signature = self._last_artifacts
            if self.artifact_root is not None:
                source_signature = _generated_artifact_signature(self.artifact_root)
                if source_signature != self._last_artifact_sources:
                    images = publish_local_artifacts(self.artifact_root, self.payload, self.job_id)
                    signature = tuple((str(item.get("url") or ""), int(item.get("modified_at") or 0)) for item in images)
                    artifacts_changed = True
                    payload["result"] = {
                        "local_output_dir": str(self.artifact_root),
                        "artifact_base_url": AGENT_ARTIFACT_BASE_URL,
                        "images": images,
                    }
            if not artifacts_changed and (not progress or progress == self._last_progress) and time.time() - self._last_report_at < 30:
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
                self._last_report_at = time.time()
                if artifacts_changed:
                    self._last_artifact_sources = source_signature
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


def _prepare_persisted_916_sources(
    *,
    prompt_916_dir: Path,
    source_916_dir: Path,
    payload: dict[str, Any],
) -> list[dict[str, str]]:
    if AGENT_STATE is None:
        return []
    owner_key = str(payload.get("owner_key") or "local")
    artifacts = [
        item for item in AGENT_STATE.manifest(owner_key=owner_key)["images"]
        if str(item.get("aspect_ratio") or "") == "4:5"
    ]
    prompt_items = [item for item in (payload.get("prompt_items") or []) if isinstance(item, dict)]
    prepared: list[dict[str, str]] = []
    source_916_dir.mkdir(parents=True, exist_ok=True)
    for item in prompt_items:
        prompt_path = prompt_916_dir / Path(str(item.get("name") or "")).name
        if not prompt_path.is_file():
            continue
        run_id = str(item.get("run_id") or "")
        prompt_stem = prompt_path.stem
        unscoped_stem = prompt_stem.split("__", 1)[-1]
        candidates = [artifact for artifact in artifacts if str(artifact.get("run_id") or "") == run_id]
        matched = next(
            (
                artifact for artifact in candidates
                if re.sub(r"_(?:4_5|9_16)$", "", Path(str(artifact.get("filename") or "")).stem) == prompt_stem
            ),
            None,
        )
        if matched is None:
            matched = next(
                (
                    artifact for artifact in candidates
                    if re.sub(r"_(?:4_5|9_16)$", "", Path(str(artifact.get("filename") or "")).stem).split("__", 1)[-1]
                    == unscoped_stem
                ),
                None,
            )
        if matched is None and len(candidates) == 1:
            matched = candidates[0]
        if matched is None:
            continue
        image_path = AGENT_STATE.artifact_path(str(matched["artifact_id"]))
        if image_path is None or not image_path.is_file():
            continue
        source_path = source_916_dir / f"{prompt_stem}.images.txt"
        source_path.write_text(str(image_path.resolve()) + "\n", encoding="utf-8")
        prepared.append({"prompt_path": str(prompt_path), "source_file": str(source_path)})
    return prepared


def launch_browser_cdp(browser: str, port: int = 9222, profile_dir: Path | None = None) -> None:
    if check_cdp().get("available"):
        return
    profile_dir = profile_dir or (AGENT_PATHS.browser / f"{browser.lower()}-profile")
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

    if AGENT_STATE is not None:
        AGENT_STATE.record_job(job_id, str(payload.get("owner_key") or "local"), "pending", payload)

    claim_id = "claim_" + hashlib.sha256(f"{AGENT_PATHS.root}:{job_id}".encode()).hexdigest()[:24]
    claim_result = api_request(
        "POST",
        f"/api/agents/jobs/{job_id}/claim",
        {"claim_id": claim_id},
        token=AGENT_TOKEN,
        timeout=20,
    )
    if claim_result is None:
        print(f"  [agent] Failed to claim job {job_id}")
        return
    if AGENT_STATE is not None:
        AGENT_STATE.update_job_status(job_id, "running")

    try:
        if job_type == "check_cdp":
            result = check_cdp()
            report_job_terminal(job_id, "complete", {"result": result})

        elif job_type == "run_gemini":
            _run_script_job(job_id, "gemini_web_automation.py", payload)

        elif job_type == "run_chatgpt":
            _run_script_job(job_id, "chatgpt_web_sutomation.py", payload)

        elif job_type == "run_chatgpt_batch":
            _run_browser_batch_job(job_id, payload)

        elif job_type == "run_browser_batch":
            _run_browser_batch_job(job_id, payload)

        elif job_type == "run_916_conversion":
            _run_script_job(job_id, "gemini_web_automation.py", {**payload, "aspect_ratio": "9:16"})

        else:
            report_job_terminal(job_id, "fail", {"error": f"Unknown job type: {job_type}"})

    except Exception as e:
        print(f"  [agent] Job {job_id} failed: {e}")
        report_job_terminal(job_id, "fail", {"error": str(e)})


def _run_script_job(job_id: str, script_name: str, payload: dict[str, Any]) -> None:
    script_path = SCRIPT_DIR / script_name
    if not script_path.exists():
        report_job_terminal(job_id, "fail", {"error": f"Script not found: {script_path}"})
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
            report_job_terminal(job_id, "complete", {"result": {"stdout": stdout[:5000]}})
        else:
            report_job_terminal(job_id, "fail", {"error": stderr[:5000]})
    except Exception as e:
        report_job_terminal(job_id, "fail", {"error": str(e)})


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
        decoded = base64.b64decode(item.get("base64") or "")
        temporary = AGENT_PATHS.staging / f".input-{uuid.uuid4().hex}.tmp"
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(decoded)
        try:
            if CONTENT_STORE is None:
                path.write_bytes(decoded)
            else:
                stored = CONTENT_STORE.put_file(temporary)
                path.unlink(missing_ok=True)
                try:
                    os.link(stored.path, path)
                except OSError:
                    shutil.copy2(stored.path, path)
        finally:
            temporary.unlink(missing_ok=True)


def _run_and_stream(
    job_id: str,
    cmd: list[str],
    cwd: Path,
    artifact_root: Path | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[int, str]:
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
    reporter = JobProgressReporter(job_id, artifact_root=artifact_root, payload=payload)

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
    if JOB_SIGNAL.cancel_requested(job_id):
        return True
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


def _gemini_cmd(
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
        "--download-timeout", str(int(payload.get("download_timeout") or 180)),
        "--manual-login-timeout", str(int(payload.get("manual_login_timeout") or 180)),
        "--upload-dir", str(upload_dir),
        "--aspect-ratio", aspect_ratio,
        "--starting-prompt-file", "",
        "--browser-download-dir", str(out_dir / ".browser_downloads"),
        "--user-data-dir", str(AGENT_PATHS.browser / "gemini-profile"),
    ]
    if image_source_file is not None:
        cmd.extend(["--image-source-file", str(image_source_file)])
    if payload.get("headless"):
        cmd.append("--headless")
    return cmd


def _browser_automation_cmd(
    engine: str,
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
    if engine == "gemini":
        return _gemini_cmd(
            script_path,
            prompt_dir,
            out_dir,
            upload_dir,
            payload,
            aspect_ratio,
            prompt_glob=prompt_glob,
            image_source_file=image_source_file,
        )
    return _chatgpt_cmd(
        script_path,
        prompt_dir,
        out_dir,
        upload_dir,
        payload,
        aspect_ratio,
        prompt_glob=prompt_glob,
        image_source_file=image_source_file,
    )


def _run_browser_batch_job(job_id: str, payload: dict[str, Any]) -> None:
    engine = str(payload.get("engine") or "chatgpt").strip().lower()
    if engine not in {"chatgpt", "gemini"}:
        raise ValueError(f"Unsupported local browser engine: {engine}")
    engine_label = "Gemini" if engine == "gemini" else "ChatGPT"
    script_path = SCRIPT_DIR / ("gemini_web_automation.py" if engine == "gemini" else "chatgpt_web_sutomation.py")
    if not script_path.exists():
        report_job_terminal(job_id, "fail", {"error": f"Script not found: {script_path}"})
        return

    cdp = check_cdp()
    for _ in range(30 if engine == "chatgpt" else 1):
        if cdp.get("available") or engine == "gemini":
            break
        time.sleep(1)
        cdp = check_cdp()
    if engine == "chatgpt" and not cdp.get("available"):
        report_job_terminal(job_id, "fail", {"error": f"No local browser CDP found at {AGENT_CDP_URL}. Start Chrome/Brave with --remote-debugging-port=9222."})
        return

    batch_name = str(payload.get("batch_name") or job_id).replace("/", "_")
    mode = str(payload.get("mode") or "45")
    job_root = AGENT_PATHS.staging / "jobs" / job_id
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
            "owner_key": str(payload.get("owner_key") or "local"),
            "prompt_items": payload.get("prompt_items") or [],
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
            report_job_terminal(job_id, "fail", {"error": "No 4:5 prompt files were included in the local-agent job. Refresh the dashboard after deployment and try again."})
            return
        code, tail = _run_and_stream(
            job_id,
            _browser_automation_cmd(engine, script_path, prompt_45_dir, out_45_dir, input_dir, payload, "4:5"),
            ROOT,
            artifact_root=job_root,
            payload=payload,
        )
        logs.append(tail)
        if code != 0:
            report_job_terminal(job_id, "fail", {"error": tail or f"{engine_label} 4:5 exited {code}"})
            return

    if mode in {"916", "both"}:
        prepared_916 = _prepare_persisted_916_sources(
            prompt_916_dir=prompt_916_dir,
            source_916_dir=source_916_dir,
            payload=payload,
        ) if mode == "916" else []
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
                report_job_terminal(job_id, "fail", {"error": "No 9:16 prompt files were included in the local-agent job"})
                return
        elif mode == "916" and not prepared_916:
            report_job_terminal(job_id, "fail", {"error": "No persisted local 4:5 artifacts matched the selected 9:16 prompts"})
            return
        elif prepared_916:
            for item in prepared_916:
                prompt_path = Path(item["prompt_path"])
                source_file = Path(item["source_file"])
                code, tail = _run_and_stream(
                    job_id,
                    _browser_automation_cmd(
                        engine,
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
                    payload=payload,
                )
                logs.append(tail)
                if code != 0:
                    report_job_terminal(job_id, "fail", {"error": tail or f"{engine_label} 9:16 exited {code}"})
                    return
        else:
            upload_dir = out_45_dir if out_45_dir.exists() else input_dir
            code, tail = _run_and_stream(
                job_id,
                _browser_automation_cmd(engine, script_path, prompt_916_dir, out_916_dir, upload_dir, payload, "9:16"),
                ROOT,
                artifact_root=job_root,
                payload=payload,
            )
            logs.append(tail)
            if code != 0:
                report_job_terminal(job_id, "fail", {"error": tail or f"{engine_label} 9:16 exited {code}"})
                return

    log_tail = "\n".join(logs)[-4000:]
    (AGENT_PATHS.logs / f"{job_id}.log").write_text(log_tail + ("\n" if log_tail else ""), encoding="utf-8")
    report_job_terminal(
        job_id,
        "complete",
        {"result": {
            "local_output_dir": str(AGENT_PATHS.artifacts),
            "artifact_base_url": AGENT_ARTIFACT_BASE_URL,
            "images": publish_local_artifacts(job_root, payload, job_id),
            "warnings": warnings,
            "log_tail": log_tail,
        }},
    )
    shutil.rmtree(job_root, ignore_errors=True)


def _execute_next_local_revision() -> bool:
    if AGENT_STATE is None:
        return False
    revision = AGENT_STATE.claim_next_revision()
    if revision is None:
        return False
    revision_id = str(revision["revision_id"])
    artifact_id = str(revision["artifact_id"])
    try:
        artifact = AGENT_STATE.artifact_record(artifact_id)
        image_path = AGENT_STATE.artifact_path(artifact_id)
        if artifact is None or image_path is None or not image_path.is_file():
            raise RuntimeError("Original artifact is unavailable")
        engine = str(revision.get("engine") or "chatgpt").lower()
        if engine == "chatgpt" and not check_cdp().get("available"):
            raise RuntimeError(f"No local Chrome CDP browser is available at {AGENT_CDP_URL}")
        work_root = AGENT_PATHS.staging / "revisions" / revision_id
        prompt_dir = work_root / "prompts"
        output_dir = work_root / "output"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        source_file = work_root / "source.images.txt"
        source_file.write_text(str(image_path.resolve()) + "\n", encoding="utf-8")
        prompt_path = prompt_dir / f"{Path(str(artifact['filename'])).stem}.txt"
        prompt_path.write_text(
            "Edit the uploaded current ad image. Apply this revision exactly while preserving everything not requested:\n\n"
            + str(revision["comment"]).strip()
            + "\n\nReturn only the revised image.\n",
            encoding="utf-8",
        )
        script_path = SCRIPT_DIR / ("gemini_web_automation.py" if engine == "gemini" else "chatgpt_web_sutomation.py")
        command = _browser_automation_cmd(
            engine,
            script_path,
            prompt_dir,
            output_dir,
            work_root,
            {},
            str(artifact.get("aspect_ratio") or "4:5"),
            prompt_glob=prompt_path.name,
            image_source_file=source_file,
        )
        print(f"  [agent] Running local revision {revision_id} with {engine}", flush=True)
        result = subprocess.run(
            command,
            cwd=str(ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=900,
        )
        if result.stdout:
            print(result.stdout[-4000:], flush=True)
        if result.returncode != 0:
            raise RuntimeError(f"{engine} revision exited {result.returncode}")
        candidates = [
            path for path in output_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
            and not any(part in {"debug", ".browser_downloads"} for part in path.parts)
        ]
        if not candidates:
            raise RuntimeError("Revision completed without producing an image")
        history_dir = image_path.parent / "revisions" / revision_id
        history_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(image_path, history_dir / f"original{image_path.suffix}")
        replacement = max(candidates, key=lambda path: path.stat().st_mtime_ns)
        temporary = image_path.with_name(f".{image_path.name}.{uuid.uuid4().hex}.tmp")
        shutil.copy2(replacement, temporary)
        os.replace(temporary, image_path)
        AGENT_STATE.refresh_artifact(artifact_id)
        AGENT_STATE.finish_revision(revision_id)
        shutil.rmtree(work_root, ignore_errors=True)
    except Exception as exc:
        AGENT_STATE.finish_revision(revision_id, error=str(exc))
        print(f"  [agent] Revision {revision_id} failed: {exc}", flush=True)
    return True


def _configure_runtime(args: argparse.Namespace) -> None:
    global AGENT_API_BASE, AGENT_SESSION_COOKIE, POLL_INTERVAL, LOCAL_OUTPUT_ROOT, AGENT_CDP_URL, AGENT_ARTIFACT_PORT, AGENT_ARTIFACT_BASE_URL
    global AGENT_PATHS, AGENT_STATE, CONTENT_STORE

    if args.api_base:
        AGENT_API_BASE = args.api_base.rstrip("/")
    if args.session_cookie:
        AGENT_SESSION_COOKIE = args.session_cookie
    if args.poll_interval:
        POLL_INTERVAL = args.poll_interval
    configured_root = args.data_dir or args.output_dir or None
    AGENT_PATHS = AgentPaths(resolve_data_root(configured_root))
    AGENT_PATHS.ensure()
    AGENT_STATE = AgentState(AGENT_PATHS)
    CONTENT_STORE = ContentStore(AGENT_PATHS)
    LOCAL_OUTPUT_ROOT = AGENT_PATHS.staging
    AGENT_CDP_URL = f"http://127.0.0.1:{args.cdp_port}"
    AGENT_ARTIFACT_PORT = args.artifact_port
    AGENT_ARTIFACT_BASE_URL = f"http://127.0.0.1:{AGENT_ARTIFACT_PORT}"


def _token_config_path() -> Path:
    return AGENT_PATHS.config / "agent.json"


def _load_saved_token(api_base: str) -> str:
    path = _token_config_path()
    if not path.is_file():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if str(payload.get("api_base") or "").rstrip("/") != api_base.rstrip("/"):
        return ""
    return str(payload.get("token") or "")


def _save_agent_token(api_base: str, agent_id: str, token: str) -> None:
    path = _token_config_path()
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps({"api_base": api_base.rstrip("/"), "agent_id": agent_id, "token": token}, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def register_and_run(args: argparse.Namespace) -> None:
    global AGENT_TOKEN, AGENT_SESSION_COOKIE, WS_CLIENT

    _configure_runtime(args)

    saved_token = _load_saved_token(AGENT_API_BASE)
    if args.token or saved_token:
        AGENT_TOKEN = args.token or saved_token
        print("[agent] Using saved agent credential")
    else:
        print(f"[agent] Registering agent with {AGENT_API_BASE}...")
        result = api_request("POST", "/api/agents/register",
                            {"name": args.name, "description": f"Local agent on {socket.gethostname()}"})
        if result is None:
            print("[agent] Failed to register. Pass --session-cookie once, or reuse a saved --token.")
            sys.exit(1)
        AGENT_TOKEN = result["token"]
        print(f"[agent] Registered: {result['agent_id']}")
        _save_agent_token(AGENT_API_BASE, str(result["agent_id"]), AGENT_TOKEN)
        print(f"[agent] Credential saved to {_token_config_path()} (mode 0600)")
    AGENT_SESSION_COOKIE = ""

    print(f"[agent] Render control plane: {AGENT_API_BASE}")
    print(f"[agent] Canonical data root: {AGENT_PATHS.root}")
    print(f"[agent] CDP status: {check_cdp()}")

    def websocket_status(status: str) -> None:
        if status == "connected":
            print("[agent] Render job WebSocket connected", flush=True)
        elif not status.startswith("disconnected: ConnectionClosed"):
            print(f"[agent] Render job WebSocket {status}", flush=True)

    WS_CLIENT = AgentWebSocketClient(AGENT_API_BASE, AGENT_TOKEN, JOB_SIGNAL, status_callback=websocket_status)
    WS_CLIENT.start()

    last_heartbeat = 0.0
    last_connection_warning = 0.0
    connection_was_down = False
    next_http_poll = 0.0
    while True:
        try:
            signaled = JOB_SIGNAL.wait(1.0)
            if _execute_next_local_revision():
                continue
            now = time.time()
            if not signaled and now < next_http_poll:
                continue
            next_http_poll = now + POLL_INTERVAL
            flush_terminal_outbox()
            if not WS_CLIENT.connected and now - last_heartbeat >= 30:
                api_request("POST", "/api/agents/heartbeat", token=AGENT_TOKEN, timeout=20, quiet=True)
                last_heartbeat = now
            jobs = api_request("GET", "/api/agents/jobs/poll", token=AGENT_TOKEN, timeout=30, quiet=True)
            if jobs is None:
                connection_was_down = True
                if now - last_connection_warning >= 30:
                    print(f"[agent] Render unavailable ({LAST_API_ERROR or 'unknown error'}); local work remains available. Retrying...", flush=True)
                    last_connection_warning = now
                continue
            if connection_was_down:
                print("[agent] Render API connection restored.", flush=True)
                connection_was_down = False
            if jobs:
                for job in jobs:
                    execute_job(job)
        except Exception as e:
            print(f"[agent] Poll error: {e}")


def _worker_process(args_dict: dict[str, Any]) -> None:
    register_and_run(argparse.Namespace(**args_dict))


def _wait_for_artifact_server(port: int, expected_root: Path, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/healthz"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                payload = json.loads(response.read())
            if Path(str(payload.get("data_root") or "")).resolve() != expected_root.resolve():
                raise RuntimeError(f"Port {port} belongs to another data root: {payload.get('data_root')}")
            return
        except urllib.error.URLError:
            time.sleep(0.1)
    raise RuntimeError(f"Artifact server did not become ready on {url}")


def run_supervisor(args: argparse.Namespace) -> None:
    _configure_runtime(args)
    artifact_access_token(AGENT_PATHS, "bootstrap")
    lock = InstanceLock(AGENT_PATHS)
    try:
        lock.acquire()
    except LockHeldError as exc:
        raise SystemExit(str(exc)) from exc

    origin = urllib.parse.urlparse(AGENT_API_BASE)
    allowed_origins = tuple(dict.fromkeys([
        f"{origin.scheme}://{origin.netloc}",
        "http://localhost:4090",
        "http://127.0.0.1:4090",
    ]))
    context = multiprocessing.get_context("spawn")
    artifact_process = context.Process(
        target=run_artifact_server,
        args=(str(AGENT_PATHS.root), args.artifact_port, allowed_origins),
        name="ad-factory-artifacts",
    )
    worker_args = vars(args).copy()
    worker_args["component"] = "worker"
    worker_args["launch_browser"] = False
    worker_process = context.Process(target=_worker_process, args=(worker_args,), name="ad-factory-worker")
    artifact_process.start()
    try:
        _wait_for_artifact_server(args.artifact_port, AGENT_PATHS.root)
        print(f"[agent] Artifact service: {AGENT_ARTIFACT_BASE_URL} -> {AGENT_PATHS.artifacts}")
        if args.launch_browser:
            threading.Thread(
                target=launch_browser_cdp,
                args=(args.browser, args.cdp_port, AGENT_PATHS.browser / f"{args.browser}-profile"),
                name="browser-launcher",
                daemon=True,
            ).start()
        worker_process.start()
        while worker_process.is_alive():
            worker_process.join(timeout=1)
            if not artifact_process.is_alive():
                raise RuntimeError("Artifact service exited unexpectedly")
        if worker_process.exitcode not in {0, None}:
            raise RuntimeError(f"Automation worker exited with code {worker_process.exitcode}")
    except KeyboardInterrupt:
        print("\n[agent] Stopping local agent...", flush=True)
    finally:
        for process in (worker_process, artifact_process):
            if process.pid and process.is_alive():
                process.terminate()
                process.join(timeout=5)
            if process.pid and process.is_alive():
                process.kill()
        lock.release()


def _run_storage_command(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="local_agent.py storage")
    parser.add_argument("action", choices=["inspect", "migrate", "gc"])
    parser.add_argument("--legacy-root", default=str(Path.home() / "ad-factory-agent-output"))
    parser.add_argument("--data-dir", default=str(resolve_data_root()))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    report = inspect_legacy_root(Path(args.legacy_root))
    if args.action == "inspect":
        print(format_inspection(report))
        return
    if args.action == "migrate":
        migration = migrate_legacy_root(
            Path(args.legacy_root),
            AgentPaths(resolve_data_root(args.data_dir)),
            apply=args.apply,
        )
        print(format_inspection(migration))
        if not args.apply:
            print("Dry run only. Re-run with --apply after reviewing the report.")
        else:
            print("Legacy source files were preserved. Unmapped jobs are under legacy/unassigned.")
        return
    print(format_inspection({**report, "gc_candidates": 0, "mutated": False}))
    print("No referenced data was deleted. Garbage collection requires verified migrated ownership.")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "storage":
        _run_storage_command(sys.argv[2:])
        return
    parser = argparse.ArgumentParser(description="Ad Factory Local Playwright Agent")
    parser.add_argument("--api-base", default=AGENT_API_BASE, help="Render backend URL")
    parser.add_argument("--token", default="", help="Existing agent token (skip registration)")
    parser.add_argument("--session-cookie", default=os.getenv("AD_FACTORY_SESSION", ""), help="Dashboard session cookie used only to register a new agent token")
    parser.add_argument("--name", default=f"agent-{socket.gethostname()}", help="Agent name")
    parser.add_argument("--poll-interval", type=float, default=25.0, help="HTTP fallback interval when WebSocket notifications are unavailable")
    parser.add_argument("--data-dir", default=str(resolve_data_root()), help="Canonical local agent data root")
    parser.add_argument("--output-dir", default="", help=argparse.SUPPRESS)
    parser.add_argument("--launch-browser", action="store_true", help="Start Brave/Chrome locally with CDP before polling")
    parser.add_argument("--browser", choices=["brave", "chrome"], default="brave", help="Browser to launch when --launch-browser is used")
    parser.add_argument("--cdp-port", type=int, default=9222, help="Local CDP port for --launch-browser")
    parser.add_argument("--artifact-port", type=int, default=AGENT_ARTIFACT_PORT, help="Local localhost port used to serve generated images to the dashboard")
    parser.add_argument("--component", choices=["all", "worker", "artifacts"], default="all", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.component == "worker":
        register_and_run(args)
    elif args.component == "artifacts":
        _configure_runtime(args)
        run_artifact_server(str(AGENT_PATHS.root), args.artifact_port)
    else:
        run_supervisor(args)


if __name__ == "__main__":
    main()
