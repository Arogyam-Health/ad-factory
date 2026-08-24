from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import importlib.util
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import httpx
import psutil
from fastapi import Body, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse

from dashboard.backend.pipeline.paths import (
    CONVERT_916_TEMPLATE_PATH,
    COPY_ARCH_PATH,
    COPY_PROMPTS_PATH,
    DEFAULT_GOOGLE_API_URL,
    DEFAULT_GOOGLE_MODEL,
    DEFAULT_IMAGE_SOURCES_FILE,
    DEFAULT_PRODUCT_MASTER,
    ENV_PATH,
    FORMATS,
    GENERATED_IMAGES_ROOT,
    INPUT_IMAGES_DIR,
    INPUT_ROOT,
    LEGACY_ACTIVE_IMAGES_FILE,
    LLM_TRACES_DIR,
    OPENCODE_AD_TIMEOUT_SECONDS,
    OPENCODE_ADS_PER_SESSION_SCHEDULE,
    OPENCODE_MAX_CONCURRENT,
    OPENCODE_QUEUE_DIR,
    PERSONA_SEEDS_PATH,
    ROOT,
    RUNS_ROOT,
    RUNTIME_ROOT,
    STARTING_PROMPT_PATH,
    STORAGE_ROOT,
)
from dashboard.backend.pipeline.user_overrides import (
    clear_user_config_overrides as _clear_user_config_overrides,
    resolve_user_config as _resolve_user_config,
    set_user_config_overrides as _set_user_config_overrides,
)
from dashboard.backend.pipeline.clock import ensure_dirs, load_env_file, make_run_id, now_iso
from dashboard.backend.pipeline.run_control import (
    _cancel_current_run,
    _cancel_events,
    _close_active_httpx_clients,
    _kill_tracked_subprocesses,
    _register_httpx_client,
    _register_subprocess,
    _unregister_httpx_client,
    _unregister_subprocess,
    cancel_event_for_run,
    signal_cancel_current_run,
    signal_cancel_run,
)
from dashboard.backend.pipeline.run_owners import (
    _extract_run_id_from_generated_path,
    _extract_run_id_from_output_path,
    _get_run_owner,
    _manifest_fields_for_db,
    _parse_prompt_meta,
    _persist_run_manifest_db,
    _record_run_owner,
    _resolve_file_owner,
    _resolve_run_owner_scope,
    _store_output_mapping,
    _update_run_status_db,
)
from dashboard.backend.pipeline.copy_text import (
    COPY_ARCH,
    COPY_PROMPTS,
    HYPOTHESIS_VARIABLES,
    PERSONA_SEED_INPUTS,
    _PERSONA_SEED_MAPPING,
    _TESTIMONIAL_GUIDANCE,
    _build_hypothesis_variables,
    _build_persona_payload_field,
    _compact_creative_entry,
    _compact_product_truth,
    _entry_direction,
    _framework_item,
    _headline_architecture_group,
    _hypothesis_guidance,
    _hypothesis_variant_label,
    _invalidate_config_cache,
    _load_copy_architecture,
    _load_copy_prompts,
    _load_persona_seeds,
    _normalize_how_kit_solves,
    _persona_name_to_slug,
    _persona_theme,
    _resolve_copy_architecture,
    _resolve_copy_prompts,
    _resolve_persona_seeds,
    _select_headline_architecture,
    assembler_language_mode,
    build_ad_copy_system_prompt,
    build_ad_prompt_tail,
    build_copy_requirements,
    build_generation_payload_for_llm,
    build_persona_payload,
    build_response_skeleton,
    build_strict_schema_note,
    classify_hook_structure,
    compact_format_rules_for_copy,
    copy_text_for_candidate,
    cta_for_candidate,
    detect_template_leakage,
    extract_generated_ad_candidate,
    filter_valid_ads,
    headline_for_candidate,
    hook_structure_mismatch,
    hydrate_generated_ad_candidate,
    hypothesis_mismatch,
    load_format_visual_archetypes,
    parse_persona_library,
    persona_number_from_slug,
    persona_slug,
    resolve_language_mode,
    validate_generated_copy_payload,
    validate_single_ad,
)
from dashboard.backend.pipeline.input_assets import (
    default_image_sources_file,
    default_product_doc_info,
    list_input_images,
    read_active_images,
    store_uploaded_input_images,
)
from dashboard.backend.pipeline.browser_env import (
    dashboard_subprocess_env,
    debugger_endpoint_reachable,
)
from dashboard.backend.pipeline.text_scrub import (
    PROOF_NOTE_MARKERS,
    choose_text,
    enforce_unique_ctas,
    scrub_on_image_copy,
    shorten_copy_line,
    strip_ansi,
    strip_ba_panel_label,
    strip_internal_marker,
    strip_internal_markers_from_payload,
    strip_price_tokens,
)
from dashboard.backend.services.opencode_catalog import (
    DEFAULT_OPENCODE_API_URL,
    build_opencode_catalog,
    choose_openai_gpt52,
    list_opencode_models,
    sanitize_dashboard_model,
)
from dashboard.backend.db.settings import settings as app_settings

def _bundle_text_file(path: Path, *, root: Path | None = None) -> dict[str, str]:
    rel = path.name if root is None else str(path.relative_to(root)).replace("\\", "/")
    return {"name": rel, "content": path.read_text(encoding="utf-8", errors="replace")}

def _load_prompt_text_for_generation(user_id: str, run_id: str, rel_path: str) -> str | None:
    if user_id and run_id:
        try:
            from dashboard.backend.db.client import get_sync_db
            from dashboard.backend.db.collections import COLL_PROMPTS
            doc = get_sync_db()[COLL_PROMPTS].find_one({"user_id": user_id, "run_id": run_id, "file_path": rel_path})
            if doc and str(doc.get("content") or "").strip():
                return str(doc.get("content") or "")
        except Exception:
            pass
    src = Path(rel_path)
    if not src.is_absolute():
        src = ROOT / src
    src = src.resolve()
    if src.exists() and src.is_file():
        return src.read_text(encoding="utf-8", errors="replace")
    return None

def _write_generation_prompt(
    *,
    user_id: str,
    run_id: str,
    rel_path: str,
    prompt_work_dir: Path,
    starting_prompt: str,
) -> str | None:
    prompt_text = _load_prompt_text_for_generation(user_id, run_id, rel_path)
    if prompt_text is None:
        return None
    original_name = Path(rel_path).name or f"prompt_{uuid.uuid4().hex[:8]}.txt"
    name = _local_prompt_filename(run_id, original_name)
    dest = prompt_work_dir / name
    combined = f"{starting_prompt}\n\n{prompt_text.strip()}\n" if starting_prompt else prompt_text
    dest.write_text(combined, encoding="utf-8")

    src = Path(rel_path)
    if not src.is_absolute():
        src = ROOT / src
    sidecar = src.resolve().with_suffix(".json")
    if sidecar.exists() and sidecar.is_file():
        dest.with_suffix(".json").write_text(sidecar.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    return str(dest)

def _local_prompt_filename(run_id: str, original_name: str) -> str:
    if not run_id:
        return Path(original_name).name
    scope = hashlib.sha256(run_id.encode()).hexdigest()[:12]
    return f"run_{scope}__{Path(original_name).name}"

def _local_prompt_item(run_id: str, rel_path: str, local_path: str, batch: str) -> dict[str, Any]:
    match = re.match(r"^v(\d+)(?:-|$)", str(batch or ""), flags=re.IGNORECASE)
    return {
        "item_id": "item_" + hashlib.sha256(f"{run_id}:{rel_path}".encode()).hexdigest()[:20],
        "run_id": run_id,
        "run_number": int(match.group(1)) if match else 0,
        "prompt_id": hashlib.sha256(str(rel_path).encode()).hexdigest()[:16],
        "prompt_path": str(rel_path),
        "name": Path(local_path).name,
    }

def _bundle_binary_file(path: Path, *, root: Path | None = None) -> dict[str, str]:
    rel = path.name if root is None else str(path.relative_to(root)).replace("\\", "/")
    return {"name": rel, "base64": base64.b64encode(path.read_bytes()).decode("ascii")}

def _bundle_input_images(max_total_bytes: int = 10 * 1024 * 1024) -> list[dict[str, str]]:
    if not INPUT_IMAGES_DIR.exists():
        return []
    files: list[dict[str, str]] = []
    total = 0
    for path in sorted(INPUT_IMAGES_DIR.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            continue
        size = path.stat().st_size
        if total + size > max_total_bytes:
            raise HTTPException(status_code=400, detail="Input images are too large to send to the local agent")
        total += size
        files.append(_bundle_binary_file(path, root=INPUT_IMAGES_DIR))
    return files

def _latest_online_agent_for_user(user_id: str) -> dict[str, Any]:
    if not user_id:
        raise HTTPException(status_code=401, detail="Sign in before using the local agent")
    from dashboard.backend.agent.service import get_recent_active_agent

    agent = get_recent_active_agent(user_id)
    if not agent:
        raise HTTPException(
            status_code=400,
            detail="No local agent is online. Run: python scripts/start_local_agent.py --api-base https://ad-factory-pzgh.onrender.com --token <agent-token>",
        )
    return agent

def _queue_local_chatgpt_job(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    from dashboard.backend.agent.service import create_job
    from dashboard.backend.db.client import get_sync_db
    from dashboard.backend.db.collections import COLL_RUNS

    run_ids = [
        str(run_id)
        for run_id in (payload.get("run_ids") or [])
        if str(run_id).strip()
    ]
    if not run_ids:
        raise HTTPException(status_code=400, detail="A local run ID is required")
    prompt_ids = [
        str(prompt_id)
        for prompt_id in (payload.get("prompt_ids") or [])
        if str(prompt_id).strip()
    ]
    jobs: list[dict[str, Any]] = []
    base_operation_id = str(
        payload.get("client_operation_id")
        or payload.get("operation_id")
        or f"web:{uuid.uuid4().hex}"
    )
    requests = [
        (run_id, prompt_id)
        for run_id in run_ids
        for prompt_id in (prompt_ids or [""])
    ]
    for index, (run_id, prompt_id) in enumerate(requests):
        run = get_sync_db()[COLL_RUNS].find_one(
            {"run_id": run_id, "user_id": user_id},
            {
                "_id": 0,
                "agent_id": 1,
                "device_id": 1,
                "owner_type": 1,
                "owner_id": 1,
            },
        )
        if not run or not run.get("agent_id") or not run.get("device_id"):
            raise HTTPException(
                status_code=409,
                detail="Run is not pinned to an authorized local agent device",
            )
        jobs.append(
            create_job(
                agent_id=str(run["agent_id"]),
                device_id=str(run["device_id"]),
                user_id=user_id,
                owner_type=str(run.get("owner_type") or "user"),
                owner_id=str(run.get("owner_id") or user_id),
                run_id=run_id,
                job_type="execute_run",
                command="generate_images",
                parameters={
                    "engine": str(payload.get("engine") or "chatgpt"),
                    "mode": str(payload.get("mode") or "45"),
                    "count": 1,
                    **({"prompt_version_id": prompt_id} if prompt_id else {}),
                },
                client_operation_id=f"{base_operation_id}:{index}",
            )
        )
    job = jobs[0]
    return {
        "status": "queued_local_agent",
        "job_id": job["job_id"],
        "job_ids": [item["job_id"] for item in jobs],
        "job_count": len(jobs),
        "agent_id": job["agent_id"],
        "device_id": job["device_id"],
        "message": "Queued for the run's authoritative local device.",
    }

def _bundle_916_prompt_files_for_batches(
    batch_names: list[str],
    run_id_by_batch: dict[str, str],
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    bundled: list[dict[str, str]] = []
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for batch in batch_names:
        prompt_dir = ROOT / "output" / batch / "96"
        run_id = str(run_id_by_batch.get(batch) or "")
        prompt_sources: list[tuple[str, str, str, Path | None]] = []
        if prompt_dir.exists():
            for path in sorted(prompt_dir.glob("*.txt")):
                prompt_sources.append((
                    path.name,
                    path.read_text(encoding="utf-8", errors="replace"),
                    str(path.relative_to(ROOT)),
                    path.with_suffix(".json"),
                ))
        elif run_id:
            try:
                from dashboard.backend.db.client import get_sync_db
                from dashboard.backend.db.collections import COLL_PROMPTS

                docs = get_sync_db()[COLL_PROMPTS].find(
                    {"run_id": run_id, "file_path": {"$regex": r"/(?:96|916)/"}},
                    {"filename": 1, "file_path": 1, "content": 1},
                )
                for doc in docs:
                    content = str(doc.get("content") or "")
                    rel_path = str(doc.get("file_path") or "")
                    name = str(doc.get("filename") or Path(rel_path).name)
                    if name and content:
                        prompt_sources.append((name, content, rel_path, None))
            except Exception:
                pass
        for name, content, rel_path, sidecar in prompt_sources:
            key = f"{batch}/96/{name}"
            if key in seen:
                continue
            seen.add(key)
            local_name = _local_prompt_filename(run_id, name)
            bundled.append({"name": local_name, "content": content})
            if run_id:
                items.append(_local_prompt_item(run_id, rel_path, local_name, batch))
            if sidecar is not None and sidecar.exists():
                bundled.append({"name": str(Path(local_name).with_suffix(".json")), "content": sidecar.read_text(encoding="utf-8", errors="replace")})
    return bundled, items

def gemini_debugger_args() -> list[str]:
    address = resolve_gemini_debugger_address()
    return ["--attach-debugger-address", address]

def resolve_gemini_debugger_address() -> str:
    configured = str(os.getenv("GEMINI_DEBUGGER_ADDRESS") or "").strip()
    candidates = [configured] if configured else []
    candidates.extend(["127.0.0.1:9222", "localhost:9222"])
    for candidate in candidates:
        if candidate and debugger_endpoint_reachable(candidate):
            return candidate
    # No reachable endpoint now: return preferred default so automation script
    # can auto-launch a debuggable Chrome session and continue.
    return configured or "127.0.0.1:9222"

def run_gemini_generation(
    *,
    batch: str,
    prompt_files: list[str],
    aspect_ratio: str,
    image_sources_file: str | None,
    prompt_reference_map: Path | None = None,
    headless: bool = False,
    run_dir: Path | None = None,
    prepend_starting_prompt: bool = True,
    first_tab_mode: str = "reuse-blank",
) -> subprocess.CompletedProcess[str]:
    from dashboard.backend.pipeline.personas import _resolve_starting_prompt_path
    aspect_folder = "9_16" if aspect_ratio == "9:16" else "4_5"
    prompt_work_dir = RUNTIME_ROOT / "gemini_selected_prompts" / f"{batch}_{aspect_folder}_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    prompt_work_dir.mkdir(parents=True, exist_ok=True)

    starting_prompt = ""
    if prepend_starting_prompt:
        starting_prompt_path = _resolve_starting_prompt_path()
        starting_prompt = starting_prompt_path.read_text(encoding="utf-8").strip() if starting_prompt_path.exists() else ""
    for prompt_file in prompt_files:
        source = Path(prompt_file)
        if not source.is_absolute():
            source = ROOT / source
        source = source.resolve()
        if not source.exists():
            raise RuntimeError(f"Prompt file not found: {source}")
        prompt_text = source.read_text(encoding="utf-8")
        combined = f"{starting_prompt}\n\n{prompt_text.strip()}\n" if starting_prompt else prompt_text
        (prompt_work_dir / source.name).write_text(combined, encoding="utf-8")
        sidecar = source.with_suffix(".json")
        if sidecar.exists():
            (prompt_work_dir / sidecar.name).write_text(sidecar.read_text(encoding="utf-8"), encoding="utf-8")

    out_dir = GENERATED_IMAGES_ROOT / batch / aspect_folder
    image_source_arg = image_sources_file
    if prompt_reference_map is not None:
        try:
            reference_payload = json.loads(prompt_reference_map.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Could not read prompt reference map: {exc}") from exc
        flattened_sources: list[str] = []
        if isinstance(reference_payload, dict):
            for value in reference_payload.values():
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, str) and item.strip() and item.strip() not in flattened_sources:
                            flattened_sources.append(item.strip())
        if flattened_sources:
            source_file = prompt_work_dir / "image_sources.txt"
            source_file.write_text("\n".join(flattened_sources) + "\n", encoding="utf-8")
            image_source_arg = str(source_file)

    cmd = [
        sys.executable,
        "local_agent_runtime/gemini_web_automation.py",
        "--prompt-dir",
        str(prompt_work_dir),
        "--prompt-glob",
        "*.txt",
        "--out-dir",
        str(out_dir),
        "--aspect-ratio",
        aspect_ratio,
        "--timeout",
        str(int(os.getenv("GEMINI_GENERATION_TIMEOUT_SECONDS") or "420")),
        "--manual-login-timeout",
        str(int(os.getenv("GEMINI_MANUAL_LOGIN_TIMEOUT_SECONDS") or "180")),
        "--upload-dir",
        str(INPUT_IMAGES_DIR),
    ]
    if headless:
        cmd.append("--headless")
    if first_tab_mode and first_tab_mode != "reuse-blank":
        cmd.extend(["--first-tab-mode", first_tab_mode])
    if image_source_arg:
        cmd.extend(["--image-source-file", image_source_arg])
    if run_dir is not None:
        hyp_path = run_dir / "context" / "hypothesis_config.json"
        if hyp_path.exists():
            cmd.extend(["--hypothesis-config", str(hyp_path)])

    log_dir = RUNTIME_ROOT / "generation_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"gen_{batch}_{aspect_folder}.log"

    env = dashboard_subprocess_env()

    with open(log_path, "w") as log_file:
        result = subprocess.run(cmd, cwd=str(ROOT), text=True, stdout=log_file, stderr=subprocess.STDOUT, check=False, env=env)

    full_output = log_path.read_text() if log_path.exists() else ""
    result.stdout = full_output
    result.stderr = ""
    return result

def run_chatgpt_generation(
    *,
    batch: str,
    prompt_files: list[str],
    aspect_ratio: str,
    image_sources_file: str | None,
    headless: bool = False,
    run_dir: Path | None = None,
    prepend_starting_prompt: bool = True,
    first_tab_mode: str = "reuse-blank",
    cdp_url: str = "",
    extension_cdp: bool = False,
) -> subprocess.CompletedProcess[str]:
    from dashboard.backend.pipeline.personas import _resolve_starting_prompt_path
    from dashboard.backend.pipeline.subprocesses import browser_automation_timeout_seconds
    aspect_folder = "9_16" if aspect_ratio == "9:16" else "4_5"
    prompt_work_dir = RUNTIME_ROOT / "chatgpt_selected_prompts" / f"{batch}_{aspect_folder}_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    prompt_work_dir.mkdir(parents=True, exist_ok=True)

    starting_prompt = ""
    if prepend_starting_prompt:
        starting_prompt_path = _resolve_starting_prompt_path()
        starting_prompt = starting_prompt_path.read_text(encoding="utf-8").strip() if starting_prompt_path.exists() else ""
    for prompt_file in prompt_files:
        source = Path(prompt_file)
        if not source.is_absolute():
            source = ROOT / prompt_file
        source = source.resolve()
        if not source.exists():
            raise RuntimeError(f"Prompt file not found: {source}")
        prompt_text = source.read_text(encoding="utf-8")
        combined = f"{starting_prompt}\n\n{prompt_text.strip()}\n" if starting_prompt else prompt_text
        (prompt_work_dir / source.name).write_text(combined, encoding="utf-8")
        sidecar = source.with_suffix(".json")
        if sidecar.exists():
            (prompt_work_dir / sidecar.name).write_text(sidecar.read_text(encoding="utf-8"), encoding="utf-8")

    out_dir = GENERATED_IMAGES_ROOT / batch / aspect_folder
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        "local_agent_runtime/chatgpt_web_sutomation.py",
        "--prompt-dir",
        str(prompt_work_dir),
        "--prompt-glob",
        "*.txt",
        "--out-dir",
        str(out_dir),
        "--timeout",
        str(int(os.getenv("CHATGPT_GENERATION_TIMEOUT_SECONDS") or "420")),
        "--download-timeout",
        str(int(os.getenv("CHATGPT_DOWNLOAD_TIMEOUT_SECONDS") or "90")),
        "--manual-login-timeout",
        str(int(os.getenv("CHATGPT_MANUAL_LOGIN_TIMEOUT_SECONDS") or "180")),
        "--upload-dir",
        str(INPUT_IMAGES_DIR),
    ]
    if headless:
        cmd.append("--headless")
    if first_tab_mode and first_tab_mode != "reuse-blank":
        cmd.extend(["--first-tab-mode", first_tab_mode])
    if image_sources_file:
        cmd.extend(["--image-source-file", image_sources_file])
    cmd.extend(["--aspect-ratio", aspect_ratio])

    if cdp_url:
        cmd.extend(["--cdp-url", cdp_url])
        if extension_cdp:
            cmd.append("--extension-cdp")

    env = dashboard_subprocess_env()

    timeout_seconds = browser_automation_timeout_seconds(len(prompt_files), "chatgpt")
    try:
        result = subprocess.run(
            cmd,
            cwd=str(ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            env=env,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        timeout_output = exc.stdout or ""
        if isinstance(timeout_output, bytes):
            timeout_output = timeout_output.decode("utf-8", errors="replace")
        result = subprocess.CompletedProcess(
            cmd,
            -1,
            timeout_output + f"\n[killed: browser automation timed out after {timeout_seconds}s]",
            "",
        )

    full_output = result.stdout or ""
    result.stdout = full_output
    result.stderr = ""
    return result

def _log_llm_trace(run_id: str, label: str, model: str, request_body: dict, response_body: Any, status_code: int, duration_s: float, error: str | None = None) -> None:
    try:
        user_id = _get_run_owner(run_id) or "unknown"
        from dashboard.backend.services.run_storage import save_llm_trace
        save_llm_trace(user_id, {
            "run_id": run_id,
            "batch": label,
            "provider": "opencode" if "opencode" in label.lower() else "google",
            "model": model,
            "prompt": json.dumps(request_body, ensure_ascii=False)[:15000],
            "response": json.dumps(response_body, ensure_ascii=False)[:30000] if response_body else "",
            "duration_ms": int(duration_s * 1000),
            "status": "error" if error else "completed",
        })
    except Exception:
        pass

def call_opencode_repair_copy(
    config: dict[str, Any],
    context: dict[str, Any],
    current_copy: dict[str, Any],
    collisions: list[dict[str, Any]],
    run_dir: Path,
) -> dict[str, Any] | None:
    from dashboard.backend.pipeline.copy_engine import parse_opencode_json_output
    api_key = (config.get("opencode_api_key") or "").strip() or os.getenv("OPENCODE_API_KEY", "").strip() or os.getenv("OPENCODE_SERVER_PASSWORD", "").strip()
    api_url = (config.get("opencode_api_url") or "").strip() or os.getenv("OPENCODE_API_URL", "").strip() or DEFAULT_OPENCODE_API_URL
    model = sanitize_dashboard_model((config.get("opencode_model") or "").strip(), list_opencode_models())
    api_model = model.split("/", 1)[-1] if "/" in model else model
    if not api_url:
        return None

    payload = {
        "task": "Repair uniqueness collisions only",
        "rules": [
            "Return valid JSON only",
            "Keep existing structure and fields",
            "Only change collided fields",
            "Write creative, fresh support lines that feel specific to this ad, not generic or templated",
            "Do not add internal tags or IDs",
        ],
        "collisions": collisions,
        "current_copy": current_copy,
        "context": build_generation_payload_for_llm(context),
    }
    prompt = (
        "You are fixing ad copy JSON after uniqueness collisions. "
        "Return only corrected JSON object with keys default_aspect_ratio and ads.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body = {
        "model": api_model,
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.3,
        "max_tokens": 4096,
    }

    run_id = run_dir.name if run_dir else "repair"
    t0 = time.time()
    client = httpx.Client(timeout=httpx.Timeout(120, connect=10))
    _register_httpx_client(client)
    try:
        resp = client.post(f"{api_url}/chat/completions", headers=headers, json=body)
    except (httpx.HTTPError, OSError) as exc:
        _log_llm_trace(run_id, "repair", model, body, None, -1, time.time() - t0, error=str(exc))
        (run_dir / "logs" / "opencode_repair_error.txt").write_text(f"Repair HTTP call failed: {exc}", encoding="utf-8")
        return None
    finally:
        _unregister_httpx_client()
        client.close()

    elapsed = time.time() - t0

    if resp.status_code != 200:
        _log_llm_trace(run_id, "repair", model, body, None, resp.status_code, elapsed, error=resp.text[:2000])
        (run_dir / "logs" / "opencode_repair_error.txt").write_text(f"Repair API error {resp.status_code}\n{resp.text}", encoding="utf-8")
        return None

    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError) as exc:
        _log_llm_trace(run_id, "repair", model, body, None, resp.status_code, elapsed, error=str(exc))
        (run_dir / "logs" / "opencode_repair_error.txt").write_text(f"Repair parse error: {exc}\n{resp.text}", encoding="utf-8")
        return None

    _log_llm_trace(run_id, "repair", model, body, data, resp.status_code, elapsed)
    return parse_opencode_json_output(content)

def call_google_gemini(config: dict[str, Any], context: dict[str, Any], run_dir: Path, reserved_batch: str | None = None, language_mode: str | None = None) -> dict[str, Any] | None:
    from dashboard.backend.pipeline.copy_engine import parse_opencode_json_output
    from dashboard.backend.pipeline.subprocesses import _opencode_queue_slot
    api_key = (config.get("google_api_key") or "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()
    model = (config.get("google_model") or "").strip() or DEFAULT_GOOGLE_MODEL
    if not api_key:
        print("[call_google_gemini] No Google API key configured", file=sys.stderr)
        return None

    print(f"[call_google_gemini] model={model}", file=sys.stderr)

    language_mode = resolve_language_mode(config)
    product_file = Path(str(context.get("product_file_path") or DEFAULT_PRODUCT_MASTER))
    generated_ads: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    if not product_file.exists() or not product_file.is_file():
        errors.append(f"Product master doc missing: {product_file}")
        (run_dir / "logs" / "opencode_error.txt").write_text("\n\n---\n\n".join(errors), encoding="utf-8")
        return None

    product_doc_content = product_file.read_text(encoding="utf-8")

    def call_llm(prompt: str, label: str = "ad_generation") -> tuple[dict[str, Any] | None, str, str, int]:
        sys_part = prompt.replace("SYSTEM:\n", "", 1).split("\nUSER_PAYLOAD_JSON:\n", 1)[0] if prompt.startswith("SYSTEM:\n") else prompt
        user_part = ""
        if "\nUSER_PAYLOAD_JSON:\n" in prompt:
            user_part = "USER_PAYLOAD_JSON:\n" + prompt.split("\nUSER_PAYLOAD_JSON:\n", 1)[1]

        body: dict[str, Any] = {
            "system_instruction": {"parts": [{"text": sys_part}]},
            "contents": [
                {"role": "user", "parts": [{"text": f"Product document:\n{product_doc_content}\n\n---\n\n{user_part}"}]},
            ],
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 8192,
            },
        }

        try:
            resp_raw = _gemini_generate(model, api_key, body, run_dir, label)
        except httpx.TimeoutException:
            return None, "", f"TIMEOUT after {OPENCODE_AD_TIMEOUT_SECONDS}s", -1
        except httpx.HTTPError as exc:
            return None, "", f"HTTP error: {exc}", -1

        if resp_raw is None:
            return None, "", "Gemini API returned empty", -1

        if hasattr(resp_raw, "status_code") and resp_raw.status_code != 200:
            try:
                err_body = resp_raw.json()
                err = err_body.get("error", {})
                code = err.get("code", resp_raw.status_code)
                msg = err.get("message", resp_raw.text[:500])
                status = err.get("status", "")
                detail = f"HTTP {code} ({status}): {msg}" if status else f"HTTP {code}: {msg}"
            except Exception:
                detail = f"HTTP {resp_raw.status_code}: {resp_raw.text[:300]}"
            return None, "", detail, -1

        try:
            data = resp_raw.json() if hasattr(resp_raw, "json") else resp_raw
        except (json.JSONDecodeError, AttributeError) as exc:
            return None, str(resp_raw), f"Parse error: {exc}", -1

        candidates = data.get("candidates") if isinstance(data, dict) else None
        if not candidates:
            err = data.get("error", {}).get("message", str(data))
            return None, json.dumps(data), f"Gemini error: {err}", -1

        try:
            content = candidates[0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            return None, json.dumps(data), f"Gemini parse error: {exc}", -1

        try:
            full = json.loads(content)
            if isinstance(full, list):
                return full, content, "", 0
        except json.JSONDecodeError:
            pass
        parsed = parse_opencode_json_output(content)
        return parsed, content, "", 0

    with _opencode_queue_slot(f"copy_session {run_dir.name}"):
        _cancel_current_run.clear()
        cancel_event_for_run(run_dir.name).clear()

        all_items = context.get("ads") or []
        if not all_items:
            return {"ads": [], "default_aspect_ratio": "4:5"}

        payload = build_generation_payload_for_llm(context)

        formats_in_batch = sorted({str(a.get("format", "")).strip().upper() for a in all_items if isinstance(a, dict)})
        single_format = formats_in_batch[0] if len(formats_in_batch) == 1 else "ALL"
        skeleton_tail = build_ad_prompt_tail(single_format, formats=formats_in_batch, total_ad_count=len(all_items))
        prompt = json.dumps(payload, ensure_ascii=False) + "\n\n" + skeleton_tail

        parsed, raw_content, err_msg, code = call_llm(prompt, label="ad_generation")

        if code == -1 and err_msg in ("CANCELLED",):
            pass
        elif parsed is None:
            errors.append(err_msg if err_msg and err_msg.strip() else "LLM returned empty response (model may be unavailable or token limit reached)")
        else:
            ads_out = parsed.get("ads") if isinstance(parsed, dict) else None
            if not isinstance(ads_out, list):
                if isinstance(parsed, list):
                    ads_out = parsed
                elif isinstance(parsed, dict) and any(k in parsed for k in {"headline", "format", "copy", "subheadline", "support_line", "cta", "body"}):
                    ads_out = [parsed]
                else:
                    ads_out = []
            for ad_idx, ad_item in enumerate(all_items):
                if ad_idx < len(ads_out):
                    generated_ads.append(ads_out[ad_idx])
                else:
                    warnings.append(f"No ad output for item {ad_idx}")

        result_payload: dict[str, Any] = {
            "ads": generated_ads,
            "default_aspect_ratio": "4:5",
        }
        if errors:
            result_payload["_opencode_failures"] = errors
        if warnings:
            result_payload["_opencode_warnings"] = warnings
        return result_payload

def _gemini_generate(model: str, api_key: str, body: dict[str, Any], run_dir: Path, label: str) -> Any:
    t0 = time.time()
    client = httpx.Client(timeout=httpx.Timeout(OPENCODE_AD_TIMEOUT_SECONDS, connect=5))
    _register_httpx_client(client)
    try:
        resp = client.post(
            f"{DEFAULT_GOOGLE_API_URL}/models/{model}:generateContent?key={api_key}",
            headers={"Content-Type": "application/json"},
            json=body,
        )
        elapsed = time.time() - t0
        if resp.status_code != 200:
            _log_llm_trace(run_dir.name, label, model, body, None, resp.status_code, elapsed, error=resp.text[:2000])
            return resp
        data = resp.json()
        _log_llm_trace(run_dir.name, label, model, body, data, resp.status_code, elapsed)
        return data
    except Exception:
        raise
    finally:
        _unregister_httpx_client()
        client.close()

def call_opencode_compatible(config: dict[str, Any], context: dict[str, Any], run_dir: Path, reserved_batch: str | None = None, language_mode: str | None = None) -> dict[str, Any] | None:
    from dashboard.backend.pipeline.copy_engine import parse_opencode_json_output
    from dashboard.backend.pipeline.subprocesses import _opencode_queue_slot
    api_url = (config.get("opencode_api_url") or "").strip() or os.getenv("OPENCODE_API_URL", "").strip() or DEFAULT_OPENCODE_API_URL
    api_key = (config.get("opencode_api_key") or "").strip() or os.getenv("OPENCODE_API_KEY", "").strip() or os.getenv("OPENCODE_SERVER_PASSWORD", "").strip()
    model = sanitize_dashboard_model((config.get("opencode_model") or "").strip(), list_opencode_models())
    config["opencode_model"] = model

    print(f"[call_opencode_compatible] api_url={api_url}, model={model}", file=sys.stderr)

    api_model = model.split("/", 1)[-1] if "/" in model else model
    language_mode = resolve_language_mode(config)
    product_file = Path(str(context.get("product_file_path") or DEFAULT_PRODUCT_MASTER))
    generated_ads: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    if not product_file.exists() or not product_file.is_file():
        errors.append(f"Product master doc missing: {product_file}")
        (run_dir / "logs" / "opencode_error.txt").write_text("\n\n---\n\n".join(errors), encoding="utf-8")
        return None

    product_doc_content = product_file.read_text(encoding="utf-8")

    def call_llm(prompt: str, label: str = "ad_generation") -> tuple[dict[str, Any] | None, str, str, int]:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        sys_part = prompt.replace("SYSTEM:\n", "", 1).split("\nUSER_PAYLOAD_JSON:\n", 1)[0] if prompt.startswith("SYSTEM:\n") else prompt
        user_part = ""
        if "\nUSER_PAYLOAD_JSON:\n" in prompt:
            user_part = "USER_PAYLOAD_JSON:\n" + prompt.split("\nUSER_PAYLOAD_JSON:\n", 1)[1]

        body = {
            "model": api_model,
            "messages": [
                {"role": "system", "content": sys_part},
                {"role": "user", "content": f"Product document:\n{product_doc_content}\n\n---\n\n{user_part}"},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.7,
            "max_tokens": 8192,
        }

        if cancel_event_for_run(run_dir.name).is_set() or _cancel_current_run.is_set():
            return None, "", "CANCELLED", -1

        t0 = time.time()
        client = httpx.Client(timeout=httpx.Timeout(OPENCODE_AD_TIMEOUT_SECONDS, connect=2))
        _register_httpx_client(client)
        try:
            try:
                resp = client.post(f"{api_url}/chat/completions", headers=headers, json=body)
            except httpx.TimeoutException:
                _log_llm_trace(run_dir.name, label, model, body, None, -1, time.time() - t0, error="TIMEOUT")
                return None, "", f"TIMEOUT after {OPENCODE_AD_TIMEOUT_SECONDS}s", -1
            except httpx.HTTPError as exc:
                _log_llm_trace(run_dir.name, label, model, body, None, -1, time.time() - t0, error=str(exc))
                return None, "", f"HTTP error: {exc}", -1

            elapsed = time.time() - t0

            if resp.status_code != 200:
                _log_llm_trace(run_dir.name, label, model, body, None, resp.status_code, elapsed, error=resp.text[:2000])
                try:
                    err_body = resp.json()
                    err_msg = err_body.get("error", {}).get("message", "") or err_body.get("message", "")
                    detail = f"HTTP {resp.status_code}: {err_msg}" if err_msg else f"HTTP {resp.status_code}: {resp.text[:200]}"
                except Exception:
                    detail = f"HTTP {resp.status_code}: {resp.text[:200]}"
                return None, resp.text, detail, resp.status_code

            try:
                data = resp.json()
                msg = data["choices"][0]["message"]
                content = msg.get("content") or msg.get("reasoning_content") or ""
            except (json.JSONDecodeError, KeyError, IndexError) as exc:
                _log_llm_trace(run_dir.name, label, model, body, None, resp.status_code, elapsed, error=str(exc))
                return None, resp.text, f"Parse error: {exc}", -1
        finally:
            _unregister_httpx_client()
            client.close()

        _log_llm_trace(run_dir.name, label, model, body, data, resp.status_code, elapsed)
        try:
            full = json.loads(content)
            if isinstance(full, list):
                return full, content, "", 0
        except json.JSONDecodeError:
            pass
        parsed = parse_opencode_json_output(content)
        return parsed, content, "", 0

    with _opencode_queue_slot(f"copy_session {run_dir.name}"):
        _cancel_current_run.clear()
        cancel_event_for_run(run_dir.name).clear()

        all_items = context.get("ads") or []
        if not all_items:
            return {"ads": [], "default_aspect_ratio": "4:5"}

        payload = build_generation_payload_for_llm(context)

        # Build response skeleton instruction so the LLM knows the expected JSON format
        formats_in_batch = sorted({str(a.get("format", "")).strip().upper() for a in all_items if isinstance(a, dict)})
        single_format = formats_in_batch[0] if len(formats_in_batch) == 1 else "ALL"
        skeleton_tail = build_ad_prompt_tail(single_format, formats=formats_in_batch, total_ad_count=len(all_items))
        prompt = json.dumps(payload, ensure_ascii=False) + "\n\n" + skeleton_tail

        parsed, raw_content, err_msg, code = call_llm(prompt, label="ad_generation")

        if code == -1 and err_msg in ("CANCELLED",):
            pass
        elif parsed is None:
            errors.append(err_msg if err_msg and err_msg.strip() else "LLM returned empty response (model may be unavailable or token limit reached)")
        else:
            ads_out = parsed.get("ads") if isinstance(parsed, dict) else None
            if not isinstance(ads_out, list):
                if isinstance(parsed, list):
                    ads_out = parsed
                elif isinstance(parsed, dict) and any(k in parsed for k in {"headline", "format", "copy", "subheadline", "support_line", "cta", "body"}):
                    ads_out = [parsed]
                else:
                    ads_out = []
            for ad_idx, ad_item in enumerate(all_items):
                if ad_idx < len(ads_out):
                    generated_ads.append(ads_out[ad_idx])
                else:
                    warnings.append(f"No ad output for item {ad_idx}")

        result_payload: dict[str, Any] = {
            "ads": generated_ads,
            "default_aspect_ratio": "4:5",
        }
        if errors:
            result_payload["_opencode_failures"] = errors
        if warnings:
            result_payload["_opencode_warnings"] = warnings
        return result_payload

def rerender_prompts_for_run(run_dir: Path, batch: str, copy_file: Path, language_mode: str) -> None:
    from dashboard.backend.pipeline.subprocesses import run_cmd
    result = run_cmd(
        [
            "python3",
            "dashboard/backend/services/generate_ads.py",
            "--copy-file",
            str(copy_file),
            "--batch",
            batch,
            "--language-mode",
            language_mode,
        ],
        cwd=ROOT,
        run_id=run_dir.name,
    )
    if result.returncode != 0:
        if cancel_event_for_run(run_dir.name).is_set() or _cancel_current_run.is_set():
            raise HTTPException(status_code=499, detail="Cancelled by user")
        error_text = result.stderr or result.stdout
        (run_dir / "logs" / "assembler_edit_error.txt").write_text(error_text, encoding="utf-8")
        short_error = "\n".join([line for line in error_text.splitlines() if line.strip()][-12:])
        raise HTTPException(status_code=500, detail=f"Prompt regeneration failed: {short_error}")

def generate_916_for_run(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    from dashboard.backend.pipeline.images import apply_visual_locks, collect_45_visual_locks, collect_run_result, force_aspect_ratio
    from dashboard.backend.pipeline.runs_db import load_run_language_mode, merge_manifest
    from dashboard.backend.pipeline.subprocesses import run_cmd
    copy_path = run_dir / "context" / "copy_batch.json"
    if not copy_path.exists():
        raise HTTPException(status_code=404, detail="copy_batch.json not found for run")

    batch = (manifest.get("batch") or "").strip()
    if not batch:
        raise HTTPException(status_code=400, detail="Run has no batch folder")

    copy_json = json.loads(copy_path.read_text(encoding="utf-8"))
    copy_916 = force_aspect_ratio(copy_json, "9:16")
    visual_locks = collect_45_visual_locks(batch)
    if visual_locks:
        copy_916 = apply_visual_locks(copy_916, visual_locks)
    copy_916_path = run_dir / "context" / "copy_batch_916.json"
    copy_916_path.write_text(json.dumps(copy_916, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    assembler_mode = load_run_language_mode(run_dir)
    result = run_cmd(
        [
            "python3",
            "dashboard/backend/services/generate_ads.py",
            "--copy-file",
            str(copy_916_path),
            "--batch",
            batch,
            "--language-mode",
            assembler_mode,
            "--no-registry-write",
            "--skip-uniqueness-check",
        ],
        cwd=ROOT,
        run_id=run_dir.name,
    )

    if result.returncode != 0:
        if cancel_event_for_run(run_dir.name).is_set() or _cancel_current_run.is_set():
            raise HTTPException(status_code=499, detail="Cancelled by user")
        error_text = result.stderr or result.stdout
        (run_dir / "logs" / "assembler_916_error.txt").write_text(error_text, encoding="utf-8")
        short_error = "\n".join([line for line in error_text.splitlines() if line.strip()][-12:])
        raise HTTPException(status_code=500, detail=f"9:16 generation failed: {short_error}")

    refreshed = collect_run_result(run_dir, batch, bool(manifest.get("image_generated", False)))
    refreshed["generated_variant"] = "9:16"
    return merge_manifest(run_dir, manifest, refreshed)

def api_run_generate_916(run_id: str) -> dict[str, Any]:
    from dashboard.backend.pipeline.runs_db import load_manifest_for_run
    run_dir, manifest, has_storage_manifest = load_manifest_for_run(run_id)
    if not has_storage_manifest or run_dir is None:
        raise HTTPException(status_code=400, detail="This endpoint requires run context in dashboard_storage. Use generate-images-916-from-45 for output-only batches.")
    return generate_916_for_run(run_dir, manifest)

def api_run_generate_916_selected(run_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from dashboard.backend.pipeline.images import apply_visual_locks, collect_45_visual_locks, collect_run_result, force_aspect_ratio
    from dashboard.backend.pipeline.runs_db import load_manifest_for_run, load_run_language_mode, merge_manifest
    from dashboard.backend.pipeline.subprocesses import run_cmd
    run_dir, manifest, has_storage_manifest = load_manifest_for_run(run_id)
    if not has_storage_manifest or run_dir is None:
        raise HTTPException(status_code=400, detail="This endpoint requires run context in dashboard_storage for copy_batch filtering.")
    copy_path = run_dir / "context" / "copy_batch.json"
    if not copy_path.exists():
        raise HTTPException(status_code=404, detail="copy_batch.json not found for run")
    batch = str(manifest.get("batch") or "").strip()
    if not batch:
        raise HTTPException(status_code=400, detail="Run has no batch folder")

    prompt_files = payload.get("prompt_files")
    if not isinstance(prompt_files, list) or not prompt_files:
        raise HTTPException(status_code=400, detail="prompt_files must be a non-empty array")

    selected_45 = validate_selected_45_prompts(batch, prompt_files)
    if not selected_45:
        raise HTTPException(status_code=400, detail="No valid 4:5 prompt files selected")

    selected_keys = extract_selected_ad_keys_from_45_prompts(selected_45)
    if not selected_keys:
        raise HTTPException(status_code=400, detail="Could not resolve selected persona/format keys")

    copy_json = json.loads(copy_path.read_text(encoding="utf-8"))
    selected_copy = filter_copy_json_for_selected_ads(copy_json, selected_keys)
    ads = selected_copy.get("ads")
    if not isinstance(ads, list) or not ads:
        raise HTTPException(status_code=400, detail="No ads matched selected prompts")

    copy_916 = force_aspect_ratio(selected_copy, "9:16")
    visual_locks = collect_45_visual_locks(batch)
    if visual_locks:
        copy_916 = apply_visual_locks(copy_916, visual_locks)
    copy_916_path = run_dir / "context" / "copy_batch_916_selected.json"
    copy_916_path.write_text(json.dumps(copy_916, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    result = run_cmd(
        [
            "python3",
            "dashboard/backend/services/generate_ads.py",
            "--copy-file",
            str(copy_916_path),
            "--batch",
            batch,
            "--language-mode",
            load_run_language_mode(run_dir),
            "--no-registry-write",
            "--skip-uniqueness-check",
        ],
        cwd=ROOT,
        run_id=run_id,
    )
    if result.returncode != 0:
        if cancel_event_for_run(run_id).is_set() or _cancel_current_run.is_set():
            raise HTTPException(status_code=499, detail="Cancelled by user")
        error_text = result.stderr or result.stdout
        (run_dir / "logs" / "assembler_916_selected_error.txt").write_text(error_text, encoding="utf-8")
        short_error = "\n".join([line for line in error_text.splitlines() if line.strip()][-12:])
        raise HTTPException(status_code=500, detail=f"Selective 9:16 generation failed: {short_error}")

    refreshed = collect_run_result(run_dir, batch, bool(manifest.get("image_generated", False)))
    refreshed["generated_variant"] = "9:16"
    refreshed["generated_916_for_prompts"] = selected_45
    return merge_manifest(run_dir, manifest, refreshed)

def validate_selected_45_prompts(batch: str, prompt_files: list[Any]) -> list[str]:
    valid_prompt_files: list[str] = []
    for prompt_file in prompt_files:
        rel = str(prompt_file or "").strip().replace("\\", "/")
        if not rel or not rel.startswith("output/"):
            continue
        if "/45/" not in rel:
            continue
        candidate = ROOT / rel
        if not candidate.exists() or not candidate.is_file():
            continue
        if f"output/{batch}/" not in rel:
            continue
        valid_prompt_files.append(rel)
    return valid_prompt_files

def map_45_to_96_prompts(selected_45: list[str]) -> list[str]:
    out: list[str] = []
    for rel in selected_45:
        rel_96 = rel.replace("/45/", "/96/")
        file_96 = ROOT / rel_96
        if file_96.exists() and file_96.is_file():
            out.append(rel_96)
    return out

def extract_selected_ad_keys_from_45_prompts(selected_45: list[str]) -> set[tuple[str, int | None]]:
    from dashboard.backend.pipeline.images import parse_prompt_filename
    keys: set[tuple[str, int | None]] = set()
    for rel in selected_45:
        parsed = parse_prompt_filename(rel)
        if not parsed:
            continue
        fmt, _lang, persona_number = parsed
        keys.add((fmt, persona_number))
    return keys

def filter_copy_json_for_selected_ads(copy_json: dict[str, Any], selected_keys: set[tuple[str, int | None]]) -> dict[str, Any]:
    ads = copy_json.get("ads")
    if not isinstance(ads, list):
        return copy_json
    selected_ads: list[dict[str, Any]] = []
    for ad in ads:
        if not isinstance(ad, dict):
            continue
        fmt = str(ad.get("format") or "").strip().upper()
        persona_number = None
        persona = ad.get("persona")
        if isinstance(persona, dict) and isinstance(persona.get("number"), int):
            persona_number = int(persona.get("number"))
        if (fmt, persona_number) in selected_keys or (fmt, None) in selected_keys:
            selected_ads.append(ad)
    cloned = json.loads(json.dumps(copy_json, ensure_ascii=False))
    cloned["ads"] = selected_ads
    return cloned

def api_run_generate_images_45(
    run_id: str,
    payload: dict[str, Any] = Body(...),
    user_id: str = "",
) -> dict[str, Any]:
    prompt_ids = payload.get("prompt_ids")
    if not isinstance(prompt_ids, list) or not prompt_ids:
        raise HTTPException(status_code=400, detail="prompt_ids must be a non-empty array")
    engine = str(payload.get("engine") or "gemini").strip().lower()
    if engine not in {"gemini", "chatgpt"}:
        raise HTTPException(status_code=400, detail="engine must be gemini or chatgpt")
    return _queue_local_chatgpt_job(
        user_id,
        {
            "mode": "45",
            "engine": engine,
            "run_ids": [run_id],
            "prompt_ids": prompt_ids,
            "client_operation_id": str(
                payload.get("client_operation_id")
                or payload.get("operation_id")
                or f"generate45selected:{uuid.uuid4().hex}"
            ),
        },
    )

def api_run_generate_images_916_from_45(
    run_id: str,
    payload: dict[str, Any] = Body(...),
    user_id: str = "",
) -> dict[str, Any]:
    prompt_ids = payload.get("prompt_ids")
    if not isinstance(prompt_ids, list) or not prompt_ids:
        raise HTTPException(status_code=400, detail="prompt_ids must be a non-empty array")
    engine = str(payload.get("engine") or "gemini").strip().lower()
    if engine not in {"gemini", "chatgpt"}:
        raise HTTPException(status_code=400, detail="engine must be gemini or chatgpt")
    return _queue_local_chatgpt_job(
        user_id,
        {
            "mode": "916",
            "engine": engine,
            "run_ids": [run_id],
            "prompt_ids": prompt_ids,
            "client_operation_id": str(
                payload.get("client_operation_id")
                or payload.get("operation_id")
                or f"generate916selected:{uuid.uuid4().hex}"
            ),
        },
    )

def api_batch_generate_images_45(payload: dict[str, Any] = Body(...), user_id: str = "") -> dict[str, Any]:
    run_ids = payload.get("run_ids")
    if not isinstance(run_ids, list) or not run_ids:
        raise HTTPException(status_code=400, detail="run_ids must be a non-empty array")
    engine = str(payload.get("engine") or "gemini").strip().lower()
    if engine not in {"gemini", "chatgpt"}:
        raise HTTPException(status_code=400, detail="engine must be gemini or chatgpt")
    return _queue_local_chatgpt_job(
        user_id,
        {
            "mode": "45",
            "engine": engine,
            "run_ids": run_ids,
            "client_operation_id": str(
                payload.get("client_operation_id")
                or payload.get("operation_id")
                or f"generate45:{uuid.uuid4().hex}"
            ),
        },
    )

def api_batch_generate_images_both(payload: dict[str, Any] = Body(...), user_id: str = "") -> dict[str, Any]:
    """First generate 4:5 images, then generate 9:16 from them."""
    run_ids = payload.get("run_ids")
    if not isinstance(run_ids, list) or not run_ids:
        raise HTTPException(status_code=400, detail="run_ids must be a non-empty array")

    engine = str(payload.get("engine") or "gemini").strip().lower()
    if engine not in {"gemini", "chatgpt"}:
        raise HTTPException(status_code=400, detail="engine must be gemini or chatgpt")
    return _queue_local_chatgpt_job(
        user_id,
        {
            "mode": "both",
            "engine": engine,
            "run_ids": run_ids,
            "client_operation_id": str(
                payload.get("client_operation_id")
                or payload.get("operation_id")
                or f"generateboth:{uuid.uuid4().hex}"
            ),
        },
    )

def _resolve_916_generation_for_run(run_dir: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """For a single run, build the list of {prompt_96, image_sources} entries for 9:16 generation.

    Checks manifest for existing 9:16 prompt files, falls back to deriving from 4:5 prompts.
    Uses load_batch_image_summary to find existing 4:5 images as references.
    """
    from dashboard.backend.pipeline.images import generated_image_roots, load_batch_image_summary, parse_prompt_filename
    batch = (manifest.get("batch") or "").strip()
    if not batch:
        return []

    prompt_files_all = manifest.get("prompt_files") or []

    # First try to use existing 9:16 prompt files from the manifest
    prompt_files_96 = [p for p in prompt_files_all if "/96/" in str(p)]

    if prompt_files_96:
        # We have 9:16 prompts already; find their corresponding 4:5 images
        image_summary = load_batch_image_summary(batch)
        prompt_to_images: dict[str, list[str]] = {}
        for entry in image_summary:
            pf = entry.get("prompt_file") or ""
            saved = entry.get("saved_files") or []
            if pf and saved:
                prompt_to_images[pf] = saved

        entries: list[dict[str, Any]] = []
        for pf96 in prompt_files_96:
            rel_96 = str(pf96).replace("\\", "/")
            parsed = parse_prompt_filename(rel_96)
            if not parsed:
                continue
            fmt, lang, persona_num = parsed

            # Look for 4:5 image by matching format+persona
            image_sources: list[str] = []
            persona_slug_str = persona_slug(persona_num) if isinstance(persona_num, int) else ""
            for pf45, imgs in prompt_to_images.items():
                pf_upper = str(pf45).upper()
                if (f"{fmt}_P{persona_num:02d}" in pf_upper) or (persona_slug_str and persona_slug_str.upper() in pf_upper):
                    image_sources = list(imgs)
                    break

            # Fallback: search image roots directly for the 4:5 image
            if not image_sources:
                slug = persona_slug(persona_num) if isinstance(persona_num, int) else ""
                patterns = [f"*{slug}*", f"*p{persona_num:02d}*"] if slug else [f"*p{persona_num:02d}*"]
                for img_root in generated_image_roots():
                    ref_dir = img_root / batch / "4_5"
                    if not ref_dir.exists():
                        continue
                    for ext in ("png", "jpg", "jpeg", "webp"):
                        seen_in_pattern: set[str] = set()
                        for pattern in patterns:
                            for f in sorted(ref_dir.glob(f"**/{pattern}.{ext}")):
                                rel = str(f.relative_to(ROOT))
                                if rel in seen_in_pattern:
                                    continue
                                seen_in_pattern.add(rel)
                                if rel not in image_sources:
                                    image_sources.append(rel)
                        if image_sources:
                            break
                    if image_sources:
                        break

            if not image_sources:
                continue

            pf96_path = f"output/{batch}/96/{Path(pf96).name}"
            entries.append({
                "prompt_96": pf96_path,
                "image_sources": image_sources,
            })
        return entries

    # Fallback: derive 9:16 prompts from 4:5 prompts (if 96 outputs exist on disk)
    prompt_files_45 = [p for p in prompt_files_all if "/45/" in str(p)]
    image_summary = load_batch_image_summary(batch)
    prompt_to_images: dict[str, list[str]] = {}
    for entry in image_summary:
        pf = entry.get("prompt_file") or ""
        saved = entry.get("saved_files") or []
        if pf and saved:
            prompt_to_images[pf] = saved

    entries = []
    for pf in prompt_files_45:
        rel_45 = str(pf).replace("\\", "/")
        parsed = parse_prompt_filename(rel_45)
        if not parsed:
            continue
        fmt, lang, persona_num = parsed

        # 9:16 prompt expected at output/{batch}/96/
        persona_slug_str = persona_slug(persona_num) if isinstance(persona_num, int) else ""
        prompt_96_pattern = f"output/{batch}/96/{fmt}_{persona_slug_str}_{lang}*.txt"
        prompt_96_matches = sorted(ROOT.glob(prompt_96_pattern))
        if not prompt_96_matches:
            continue
        pf_filename = prompt_96_matches[0].name

        image_sources = list(prompt_to_images.get(rel_45, []))

        # Fallback: search image roots directly
        if not image_sources:
            slug = persona_slug(persona_num) if isinstance(persona_num, int) else ""
            patterns = [f"*{slug}*", f"*p{persona_num:02d}*"] if slug else [f"*p{persona_num:02d}*"]
            for img_root in generated_image_roots():
                ref_dir = img_root / batch / "4_5"
                if not ref_dir.exists():
                    continue
                for ext in ("png", "jpg", "jpeg", "webp"):
                    seen_in_pattern: set[str] = set()
                    found_one = False
                    for pattern in patterns:
                        for f in sorted(ref_dir.glob(f"**/{pattern}.{ext}")):
                            rel = str(f.relative_to(ROOT))
                            if rel in seen_in_pattern:
                                continue
                            seen_in_pattern.add(rel)
                            if rel not in image_sources:
                                image_sources.append(rel)
                            found_one = True
                        if found_one:
                            break
                    if image_sources:
                        break
                if image_sources:
                    break

        if not image_sources:
            continue

        entries.append({
            "prompt_96": prompt_96,
            "image_sources": image_sources,
        })

    return entries

def run_916_conversion_from_45_for_batch(
    *,
    batch: str,
    headless: bool,
    run_dir: Path | None,
    engine: str = "gemini",
    jobs: list[dict[str, Any]] | None = None,
    cdp_url: str = "",
    extension_cdp: bool = False,
) -> dict[str, Any]:
    from dashboard.backend.pipeline.images import build_916_conversion_prompt_job, collect_45_reference_jobs_for_batch, resolve_916_conversion_template_text
    resolved_jobs = jobs if isinstance(jobs, list) else collect_45_reference_jobs_for_batch(batch)
    if not resolved_jobs:
        raise HTTPException(status_code=400, detail=f"No usable 4:5 reference images found for batch {batch}")

    template_text = resolve_916_conversion_template_text()
    prompt_root = RUNTIME_ROOT / "conversion_916_prompts" / f"{batch}_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    prompt_root.mkdir(parents=True, exist_ok=True)

    failures: list[str] = []
    completed = 0
    prompt_files_used: list[str] = []

    for index, job in enumerate(resolved_jobs, start=1):
        source_stem = ""
        image_path = job.get("image_abs") or job.get("image_rel") or ""
        if image_path:
            raw_stem = Path(str(image_path)).stem
            # The 4:5 image stem carries the aspect suffix (e.g. ..._pain_point_4_5).
            # Strip it so the 9:16 prompt name matches the original 4:5 prompt stem.
            source_stem = re.sub(r"_(?:4_5|9_16)$", "", raw_stem)
        prompt_name = build_916_conversion_prompt_job(
            job["format"],
            int(job["persona_number"]),
            job["language"],
            index,
            source_stem=source_stem,
        )
        prompt_path = prompt_root / prompt_name
        prompt_path.write_text(template_text + "\n", encoding="utf-8")

        source_file = prompt_root / f"{prompt_path.stem}.images.txt"
        source_file.write_text(str(job["image_abs"]) + "\n", encoding="utf-8")

        if engine == "chatgpt":
            result = run_chatgpt_generation(
                batch=batch,
                prompt_files=[str(prompt_path)],
                aspect_ratio="9:16",
                image_sources_file=str(source_file),
                headless=headless,
                run_dir=run_dir,
                prepend_starting_prompt=False,
                first_tab_mode="new",
                cdp_url=cdp_url,
                extension_cdp=extension_cdp,
            )
        else:
            result = run_gemini_generation(
                batch=batch,
                prompt_files=[str(prompt_path)],
                aspect_ratio="9:16",
                image_sources_file=str(source_file),
                headless=headless,
                run_dir=run_dir,
                prepend_starting_prompt=False,
                first_tab_mode="new",
            )

        if result.returncode != 0:
            failures.append(f"{prompt_name}: {(result.stderr or result.stdout or '').strip()[:300]}")
            continue

        completed += 1
        prompt_files_used.append(str(prompt_path))

    if completed == 0:
        short = "\n".join(failures[:3])
        engine_label = "ChatGPT" if engine == "chatgpt" else "Gemini"
        raise HTTPException(status_code=500, detail=f"9:16 conversion failed for batch {batch} ({engine_label}). {short}")

    return {
        "batch": batch,
        "completed": completed,
        "attempted": len(resolved_jobs),
        "failures": failures,
        "prompt_files_used": prompt_files_used,
    }

def api_batch_generate_images_916(payload: dict[str, Any] = Body(...), user_id: str = "") -> dict[str, Any]:
    run_ids = payload.get("run_ids")
    if not isinstance(run_ids, list) or not run_ids:
        raise HTTPException(status_code=400, detail="run_ids must be a non-empty array")

    engine = str(payload.get("engine") or "gemini").strip().lower()
    if engine not in {"gemini", "chatgpt"}:
        raise HTTPException(status_code=400, detail="engine must be gemini or chatgpt")
    return _queue_local_chatgpt_job(
        user_id,
        {
            "mode": "916",
            "engine": engine,
            "run_ids": run_ids,
            "client_operation_id": str(
                payload.get("client_operation_id")
                or payload.get("operation_id")
                or f"generate916:{uuid.uuid4().hex}"
            ),
        },
    )


async def api_run_execute(
    config: str = Form(...),
    product_info_file: UploadFile | None = File(None),
    mechanism_file: UploadFile | None = File(None),
    faq_file: UploadFile | None = File(None),
    image_source_file: UploadFile | None = File(None),
    input_image_files: list[UploadFile] | None = File(None),
    clear_input_images: bool = Form(False),
    user_id: str = "dev_user",
    org_id: str = "",
) -> dict[str, Any]:
    from dashboard.backend.pipeline.files import coalesce_path, save_upload
    from dashboard.backend.pipeline.hypothesis import apply_visual_pattern_reuse_to_plan, collect_visual_pattern_reuse_locks, expand_plan_with_hypothesis, resolve_format_plan
    ensure_dirs()
    try:
        cfg = json.loads(config)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid config JSON") from exc
    if str((cfg.get("server_type") or "opencode")).strip().lower() != "opencode":
        raise HTTPException(status_code=400, detail="Unsupported server type. Use OpenCode.")

    run_id = make_run_id()
    run_dir = RUNS_ROOT / run_id

    # Record ownership + run config in MongoDB
    owner_type, owner_id = _resolve_run_owner_scope(user_id, org_id)
    _record_run_owner(run_id, user_id, config=cfg, owner_type=owner_type, owner_id=owner_id)
    (run_dir / "inputs").mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)
    (run_dir / "context").mkdir(parents=True, exist_ok=True)

    # Load per-user config overrides from MongoDB (optionally for a specific org)
    try:
        from dashboard.backend.services.user_config import resolve_effective_config
        user_cfg = resolve_effective_config(user_id, org_id=org_id or None)
        _set_user_config_overrides(user_cfg)
    except Exception:
        _set_user_config_overrides({})

    product_path = save_upload(run_dir / "inputs" / "product master doc.txt", product_info_file)
    image_sources_path = save_upload(run_dir / "inputs" / "image_sources.txt", image_source_file)
    saved_input_images = store_uploaded_input_images(input_image_files or [], clear_input_images)

    # Use user's product master doc if uploaded file is empty and user has custom config
    user_product_doc = _resolve_user_config("product_master_doc")
    if (product_path is None or not product_path.exists()) and user_product_doc:
        product_path = run_dir / "inputs" / "product master doc.txt"
        product_path.parent.mkdir(parents=True, exist_ok=True)
        product_path.write_text(user_product_doc, encoding="utf-8")
    product_file = coalesce_path(product_path, DEFAULT_PRODUCT_MASTER)
    image_sources_file_path = coalesce_path(image_sources_path, default_image_sources_file())

    try:
        base_plan = resolve_format_plan(cfg)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    hypothesis_cfg = cfg.get("hypothesis") or {}
    plan = expand_plan_with_hypothesis(base_plan, hypothesis_cfg)

    reuse_visual_patterns_from_run_id = str(cfg.get("reuse_visual_patterns_from_run_id") or "").strip()
    if reuse_visual_patterns_from_run_id:
        pattern_locks = collect_visual_pattern_reuse_locks(reuse_visual_patterns_from_run_id)
        plan, applied_patterns = apply_visual_pattern_reuse_to_plan(
            plan,
            pattern_locks,
            share_across_personas=bool(cfg.get("share_background_across_personas")),
        )
        (run_dir / "context" / "visual_pattern_reuse.json").write_text(
            json.dumps({"source_run_id": reuse_visual_patterns_from_run_id, "available_locks": len(pattern_locks), "applied_ads": applied_patterns, "share_background_across_personas": bool(cfg.get("share_background_across_personas"))}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )

    if hypothesis_cfg:
        (run_dir / "context" / "hypothesis_config.json").write_text(
            json.dumps(hypothesis_cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )

    product_ctx_source = "attached_product_master_doc"
    extractor_model = "none"
    provider = (cfg.get("provider") or "").strip().lower()
    if provider == "google":
        execution_model = (cfg.get("google_model") or "").strip() or DEFAULT_GOOGLE_MODEL
        cfg["opencode_model"] = ""
    else:
        execution_model = sanitize_dashboard_model((cfg.get("opencode_model") or "").strip(), list_opencode_models())
        cfg["opencode_model"] = execution_model

    persona_library = parse_persona_library()
    ads_context: list[dict[str, Any]] = []
    format_seen_counts: dict[str, int] = {}
    for item in plan:
        persona_no = item["persona"]
        fmt = item["format"]
        format_seen_counts[fmt] = format_seen_counts.get(fmt, 0) + 1
        persona_payload = build_persona_payload(persona_no, persona_library)
        format_payload = {"format": fmt, "rules": []}
        copy_req = build_copy_requirements(persona_no, fmt, format_seen_counts[fmt], run_id)
        hyp_meta = item.get("hypothesis")
        concept = {}
        hyp_type = str(hyp_meta.get("type") or "").strip().lower() if isinstance(hyp_meta, dict) else ""
        variant = str(hyp_meta.get("variant") or "").strip() if isinstance(hyp_meta, dict) else ""
        if isinstance(hyp_meta, dict) and hyp_type and hyp_type != "none":
            guidance = _hypothesis_guidance(hyp_type, variant) if variant else ""
            copy_req["hypothesis"] = {"type": hyp_type, "variant": variant, "hypothesis_id": hyp_meta.get("hypothesis_id") or f"{hyp_type}-{variant}", "intent": guidance, "do_not_force_template": True}
            concept = copy_req.get("concept_variation") or {}
            if hyp_type == "concept_angle" and variant:
                concept["concept_angle"] = _framework_item("concept_angle", variant)
            elif hyp_type == "hook_structure" and variant:
                concept["hook_structure_override"] = variant
            copy_req["concept_variation"] = concept
            copy_req["selection_mode"] = "locked"
        if not concept.get("concept_angle"):
            concept["concept_angle"] = {"id": "auto"}
        copy_req["concept_variation"] = concept
        ads_context.append({"persona": persona_payload, "format_rules": format_payload, "format": fmt, "copy_requirements": copy_req, "hypothesis": hyp_meta, "visual_archetype": item.get("visual_archetype"), "visual_pattern_reused_from_run_id": item.get("visual_pattern_reused_from_run_id"), "visual_pattern_reuse_key": item.get("visual_pattern_reuse_key"), "creative_index": item.get("creative_index", 1), "creative_total": item.get("creative_total", 1), "background_group_key": item.get("background_group_key"), "share_background_across_personas": item.get("share_background_across_personas", False)})

    full_context = {"generated_at": now_iso(), "run_id": run_id, "language_mode": resolve_language_mode(cfg), "context_source": product_ctx_source, "context_extractor_model": extractor_model, "opencode_model": execution_model, "product_file_path": str(product_file), "ads": ads_context}
    (run_dir / "context" / "run_context.json").write_text(json.dumps({k: full_context[k] for k in ["generated_at", "run_id", "language_mode", "context_source", "context_extractor_model", "opencode_model", "product_file_path"]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Run pipeline in background thread so frontend can poll partial results
    bg_kwargs = dict(run_dir=run_dir, cfg=cfg, full_context=full_context, image_sources_file_path=image_sources_file_path, saved_input_images=saved_input_images, reuse_visual_patterns_from_run_id=reuse_visual_patterns_from_run_id, product_ctx_source=product_ctx_source, extractor_model=extractor_model, execution_model=execution_model, ads_context=ads_context, user_id=user_id, user_config_overrides=_current_user_config.copy())
    threading.Thread(target=_run_pipeline_background, kwargs=bg_kwargs, daemon=True).start()

    _clear_user_config_overrides()
    return {"run_id": run_id, "status": "started"}

def _list_output_batches() -> list[int]:
    output_dir = ROOT / "output"
    if not output_dir.exists():
        return []
    out: list[int] = []
    for child in output_dir.iterdir():
        if child.is_dir():
            m = re.match(r"^v(\d+)$", child.name)
            if m:
                out.append(int(m.group(1)))
    return sorted(out)

def _list_user_mongo_batches(user_id: str) -> list[int]:
    if not user_id:
        return []
    try:
        from dashboard.backend.db.client import get_sync_db
        from dashboard.backend.db.collections import COLL_RUNS
        docs = get_sync_db()[COLL_RUNS].find({"user_id": user_id, "batch": {"$regex": r"^v\d+$"}}, {"batch": 1})
        batches: list[int] = []
        for doc in docs:
            match = re.match(r"^v(\d+)$", str(doc.get("batch") or ""), flags=re.IGNORECASE)
            if match:
                batches.append(int(match.group(1)))
        return sorted(batches)
    except Exception:
        return []

def _reserve_batch_name(user_id: str = "") -> str:
    batches = _list_user_mongo_batches(user_id)
    if not batches and not app_settings.is_production:
        batches = _list_output_batches()
    return "v1" if not batches else f"v{batches[-1] + 1}"

def _run_pipeline_background(
    run_dir: Path, cfg: dict, full_context: dict,
    image_sources_file_path: Path, saved_input_images: list,
    reuse_visual_patterns_from_run_id: str,
    product_ctx_source: str, extractor_model: str,
    execution_model: str,
    ads_context: list,
    user_id: str = "",
    user_config_overrides: dict | None = None,
) -> None:
    """Run the full pipeline in a background thread, writing results incrementally."""
    from dashboard.backend.pipeline.copy_engine import normalize_generated_copy
    from dashboard.backend.pipeline.hypothesis import apply_background_reuse_locks, collect_background_reuse_locks
    from dashboard.backend.pipeline.images import collect_run_result
    from dashboard.backend.pipeline.subprocesses import run_cmd
    if user_config_overrides:
        _set_user_config_overrides(user_config_overrides)
    run_id = run_dir.name
    _update_run_status_db(run_id, "running")
    try:
        print(f"[PIPELINE] Starting background pipeline for run {run_id}", file=sys.stderr)
        # Reserve batch number early so incremental assembler runs write to the same batch dir
        from dashboard.backend.services.run_storage import get_run

        run_doc = get_run(user_id, run_id) if user_id else None
        fallback_batch = f"{_reserve_batch_name(user_id)}-{hashlib.sha256(run_id.encode()).hexdigest()[:12]}"
        reserved_batch = str((run_doc or {}).get("batch") or fallback_batch)
        _update_run_status_db(run_id, "running", user_id=user_id, extra={"batch": reserved_batch})
        language_mode = assembler_language_mode(cfg)
        provider = (cfg.get("provider") or "").strip().lower()
        if provider == "google":
            llm_mode = "google_gemini"
            copy_json = call_google_gemini(cfg, full_context, run_dir, reserved_batch=reserved_batch, language_mode=language_mode)
        else:
            llm_mode = "opencode"
            copy_json = call_opencode_compatible(cfg, full_context, run_dir, reserved_batch=reserved_batch, language_mode=language_mode)
        if not copy_json:
            provider_label = "Google Gemini" if llm_mode == "google_gemini" else "OpenCode"
            error_msg = f"{provider_label} copy generation unavailable (no LLM response) and fallback template has been removed."
            (run_dir / "partial").mkdir(parents=True, exist_ok=True)
            (run_dir / "partial" / "error.txt").write_text(error_msg, encoding="utf-8")
            print(f"[PIPELINE ERROR] {error_msg}", file=sys.stderr)
            return
        opencode_failures = copy_json.pop("_opencode_failures", []) if isinstance(copy_json, dict) else []
        opencode_warnings = copy_json.pop("_opencode_warnings", []) if isinstance(copy_json, dict) else []
        opencode_session_rollovers = int(copy_json.pop("_opencode_session_rollovers", 0) or 0) if isinstance(copy_json, dict) else 0
        if opencode_failures:
            llm_mode = "opencode_partial_fallback"
        copy_json = normalize_generated_copy(copy_json, full_context, run_dir.name)
        copy_json = strip_internal_markers_from_payload(copy_json)
        copy_json = enforce_unique_ctas(copy_json, full_context)
        copy_json = scrub_on_image_copy(copy_json)
        reuse_backgrounds_from_run_id = str(cfg.get("reuse_backgrounds_from_run_id") or "").strip()
        if reuse_backgrounds_from_run_id:
            locks = collect_background_reuse_locks(reuse_backgrounds_from_run_id)
            copy_json, applied_locks = apply_background_reuse_locks(copy_json, locks, share_across_personas=bool(cfg.get("share_background_across_personas")))
            (run_dir / "context" / "background_reuse.json").write_text(json.dumps({"source_run_id": reuse_backgrounds_from_run_id, "available_locks": len(locks), "applied_ads": applied_locks, "share_background_across_personas": bool(cfg.get("share_background_across_personas"))}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        copy_json, failed_ads, validation_warnings = filter_valid_ads(copy_json, ads_context, language_mode)

        if failed_ads:
            failed_log = "\n".join(f"  - {item['error']}" for item in failed_ads)
            (run_dir / "logs" / "failed_ads.txt").write_text(
                f"{len(failed_ads)} ad(s) failed validation and were excluded:\n{failed_log}\n\n"
                f"Full payload:\n{json.dumps(copy_json, ensure_ascii=False, indent=2)}",
                encoding="utf-8",
            )
            print(f"[PIPELINE] {len(failed_ads)} ad(s) failed validation, continuing with {len(copy_json.get('ads', []))} valid ads", file=sys.stderr)

        if not copy_json.get("ads"):
            (run_dir / "partial").mkdir(parents=True, exist_ok=True)
            provider_label = "Google Gemini" if llm_mode == "google_gemini" else "OpenCode"
            error_msg = f"{provider_label} copy generation: all {len(failed_ads)} ads failed validation. No valid ads to assemble."
            if failed_ads:
                error_msg += f"\nFailed: {'; '.join(item['error'] for item in failed_ads[:5])}"
            (run_dir / "partial" / "error.txt").write_text(error_msg, encoding="utf-8")
            print(f"[PIPELINE ERROR] {error_msg}", file=sys.stderr)
            return

        copy_file = run_dir / "context" / "copy_batch.json"
        copy_file.write_text(json.dumps(copy_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        assembler_result = run_cmd(["python3", "dashboard/backend/services/generate_ads.py", "--copy-file", str(copy_file), "--batch", reserved_batch, "--language-mode", language_mode], cwd=ROOT, run_id=run_dir.name)
        if assembler_result.returncode != 0:
            if cancel_event_for_run(run_dir.name).is_set() or _cancel_current_run.is_set():
                print(f"[PIPELINE] Assembler cancelled by user for run {run_dir.name}", file=sys.stderr)
                return
            assembler_error = assembler_result.stderr or assembler_result.stdout
            (run_dir / "logs" / "assembler_error.txt").write_text(assembler_error, encoding="utf-8")
            (run_dir / "partial").mkdir(parents=True, exist_ok=True)
            (run_dir / "partial" / "error.txt").write_text(f"Prompt assembly failed: {assembler_error}", encoding="utf-8")
            print(f"[PIPELINE ERROR] Prompt assembly failed: {assembler_error}", file=sys.stderr)
            return

        batch = reserved_batch

        manifest = collect_run_result(run_dir, batch, image_generated=False)
        if run_doc:
            manifest["run_number"] = int(run_doc.get("run_number") or 0)
            manifest["display_batch"] = str(run_doc.get("display_batch") or "")
        manifest["llm_mode"] = llm_mode
        if llm_mode == "google_gemini":
            manifest["copy_source"] = f"google gemini — {execution_model}"
        else:
            manifest["copy_source"] = "opencode generated copy"
        if opencode_failures:
            manifest["copy_generation_failures"] = len(opencode_failures)
            manifest["copy_generation_notes"] = [str(e).splitlines()[0] if str(e).splitlines() else "(empty)" for e in opencode_failures[:5]]
        if opencode_warnings:
            manifest["copy_generation_warnings"] = len(opencode_warnings)
            manifest["copy_warning_log"] = str((run_dir / "logs" / "opencode_error.txt").relative_to(ROOT))
            manifest["copy_generation_notes"] = [str(item).splitlines()[0] if str(item).splitlines() else "(empty)" for item in opencode_warnings[:3]]
        if opencode_session_rollovers:
            manifest["copy_session_rollovers"] = opencode_session_rollovers
            manifest["copy_session_schedule"] = OPENCODE_ADS_PER_SESSION_SCHEDULE
            manifest["copy_session_log"] = str((run_dir / "logs" / "opencode_session.log").relative_to(ROOT))
        manifest["context_source"] = product_ctx_source
        manifest["context_extractor_model"] = extractor_model
        manifest["opencode_model"] = execution_model
        manifest["llm_mode"] = llm_mode
        manifest["image_sources_file"] = str(image_sources_file_path)
        manifest["input_images_dir"] = str(INPUT_IMAGES_DIR.relative_to(ROOT)).replace("\\", "/")
        manifest["input_images_uploaded"] = saved_input_images
        if failed_ads:
            manifest["failed_ads_count"] = len(failed_ads)
            manifest["failed_ads"] = [{"error": item["error"], "format": item["ad"].get("format", "?"), "persona": item["ad"].get("persona", {}).get("number", "?")} for item in failed_ads[:20]]
            manifest["failed_ads_log"] = str((run_dir / "logs" / "failed_ads.txt").relative_to(ROOT))
        if reuse_visual_patterns_from_run_id:
            manifest["visual_pattern_reuse_from_run_id"] = reuse_visual_patterns_from_run_id
        (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _persist_run_manifest_db(run_id, user_id, manifest, status="completed")
        _store_output_mapping(run_id, user_id, batch, manifest)
        partial_dir = run_dir / "partial"
        if partial_dir.exists():
            import shutil
            shutil.rmtree(partial_dir)
        print(f"[PIPELINE DONE] Run {run_id} completed, batch={batch}", file=sys.stderr)
    except Exception as exc:
        _update_run_status_db(run_id, "error", extra={"error": str(exc)[:500]})
        (run_dir / "logs" / "pipeline_error.txt").write_text(f"Pipeline background task failed: {exc}\n{traceback.format_exc()}", encoding="utf-8")
        (run_dir / "partial").mkdir(parents=True, exist_ok=True)
        (run_dir / "partial" / "error.txt").write_text(f"Pipeline failed: {exc}", encoding="utf-8")
        print(f"[PIPELINE ERROR] {exc}", file=sys.stderr)
    finally:
        _clear_user_config_overrides()

def _find_prompt_by_name(prompt_name: str, prompt_files: list[str]) -> str:
    """Find a prompt path in prompt_files whose filename matches prompt_name."""
    target = Path(prompt_name).name
    for pf in prompt_files:
        if Path(pf).name == target:
            return pf
    return ""

def _build_output_stem_from_prompt(prompt_path: str, engine: str, aspect_dir: str = "") -> str:
    name = Path(prompt_path).stem
    aspect_suffix = f"_{aspect_dir}" if aspect_dir in ("4_5", "9_16") else ""
    # New format: <FMT>_<slug>_<LANG>_<angle>[_A<NN>]
    new_match = re.match(
        r"^(?:OUTPUT_|FINAL_)?([A-Z]+)_([a-z0-9][a-z0-9]*(?:_[a-z0-9]+)*)_(EN|HI|HINGLISH)_(?P<angle>[a-z][a-z_]*?)(?:_([AV]\d+))?$",
        name,
        flags=re.IGNORECASE,
    )
    if new_match:
        fmt = new_match.group(1).upper()
        slug = new_match.group(2).lower()
        lang = new_match.group(3).upper()
        angle = new_match.group("angle")
        variant = new_match.group(5).upper() if new_match.group(5) else ""
        variant_suffix = f"_{variant}" if variant else ""
        return f"{fmt}_{slug}_{lang}_{angle}{variant_suffix}{aspect_suffix}"
    # Angle-less new format: <FMT>_<slug>_<LANG>[_A<NN>]
    new_no_angle = re.match(
        r"^(?:OUTPUT_|FINAL_)?([A-Z]+)_([a-z0-9][a-z0-9]*(?:_[a-z0-9]+)*)_(EN|HI|HINGLISH)(?:_([AV]\d+))?$",
        name,
        flags=re.IGNORECASE,
    )
    if new_no_angle:
        fmt = new_no_angle.group(1).upper()
        slug = new_no_angle.group(2).lower()
        lang = new_no_angle.group(3).upper()
        variant = new_no_angle.group(4).upper() if new_no_angle.group(4) else ""
        variant_suffix = f"_{variant}" if variant else ""
        return f"{fmt}_{slug}_{lang}{variant_suffix}{aspect_suffix}"
    # Legacy format: <FMT>_P<NN>_<LANG>[_<angle>][_A<NN>]
    legacy_match = re.match(
        r"^(?:OUTPUT_|FINAL_)?([A-Za-z0-9]+)_P(\d+)_([A-Za-z0-9]+)(?:_([AV]\d+))?(?:_[a-z_]+)?$",
        name,
        flags=re.IGNORECASE,
    )
    if legacy_match:
        fmt = legacy_match.group(1).upper()
        persona = f"P{int(legacy_match.group(2)):02d}"
        lang = legacy_match.group(3).upper()
        angle_match = re.search(r"_[a-z_]+$", name)
        angle = angle_match.group(0)[1:] if angle_match else ""
        variant = legacy_match.group(4).upper() if legacy_match.group(4) else ""
        variant_suffix = f"_{variant}" if variant else ""
        if angle:
            return f"{fmt}_{persona}_{lang}_{angle}{variant_suffix}{aspect_suffix}"
        return f"{fmt}_{persona}_{lang}{variant_suffix}{aspect_suffix}"
    return ""

def _build_expected_output_path(batch: str, prompt_path: str, aspect_dir: str, engine: str) -> Path | None:
    """Compute the expected full output path for a generated image."""
    stem = _build_output_stem_from_prompt(prompt_path, engine, aspect_dir=aspect_dir)
    if not stem:
        return None
    return GENERATED_IMAGES_ROOT / batch / aspect_dir / "generated images" / f"{stem}.png"

def _find_45_parent_for_prompt(batch: str, prompt_path: str, engine: str) -> Path | None:
    """Find the 4:5 reference image for a given prompt."""
    expected = _build_expected_output_path(batch, prompt_path, "4_5", engine)
    if expected and expected.exists() and expected.is_file():
        return expected
    # Try the other engine
    other_engine = "gemini" if engine == "chatgpt" else "chatgpt"
    expected_other = _build_expected_output_path(batch, prompt_path, "4_5", other_engine)
    if expected_other and expected_other.exists() and expected_other.is_file():
        return expected_other
    # Scan the directory for any image whose metadata links to this prompt
    four_five_dir = GENERATED_IMAGES_ROOT / batch / "4_5" / "generated images"
    if four_five_dir.exists() and four_five_dir.is_dir():
        target_name = Path(prompt_path).name
        for child in four_five_dir.iterdir():
            if child.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            meta = child.with_suffix(".json")
            if meta.exists() and meta.is_file():
                try:
                    meta_data = json.loads(meta.read_text(encoding="utf-8"))
                    if Path(meta_data.get("prompt_file", "")).name == target_name:
                        return child
                except Exception:
                    continue
    return None

def api_regenerate_queued_images(run_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Regenerate images already in the to_be_regenerated queue.

    Single endpoint that handles both 4:5 and 9:16 images in one call.
    New images stay in to_be_regenerated for user review before restore.
    """
    from dashboard.backend.pipeline.images import _mark_image_metadata_regenerated, _read_image_metadata, _unique_path, collect_run_result, parse_prompt_filename
    from dashboard.backend.pipeline.runs_db import collect_backfill_result, enrich_manifest_for_dashboard, load_manifest_for_run, merge_manifest
    run_dir, manifest, has_storage_manifest = load_manifest_for_run(run_id)
    batch = str(manifest.get("batch") or "").strip()
    if not batch:
        raise HTTPException(status_code=400, detail="Run has no batch folder")

    image_files = payload.get("image_files")
    if not isinstance(image_files, list) or not image_files:
        raise HTTPException(status_code=400, detail="image_files must be a non-empty array")

    headless = bool(payload.get("headless", False))
    engine = str(payload.get("engine") or "gemini").strip().lower()
    if engine not in {"gemini", "chatgpt"}:
        raise HTTPException(status_code=400, detail="engine must be gemini or chatgpt")

    prompt_files_list = list(manifest.get("prompt_files") or [])
    generated_root = GENERATED_IMAGES_ROOT.resolve()

    prompts_45: set[str] = set()
    jobs_916: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for raw in image_files:
        rel = str(raw or "").strip().replace("\\", "/")
        if not rel:
            continue
        if "/to_be_regenerated/" not in rel:
            skipped.append({"image_file": rel, "reason": "not in to_be_regenerated"})
            continue

        meta = _read_image_metadata(rel)
        prompt_name = str(meta.get("prompt_file") or "").strip()
        if not prompt_name:
            skipped.append({"image_file": rel, "reason": "no prompt_file in metadata"})
            continue
        prompt_path = _find_prompt_by_name(prompt_name, prompt_files_list)
        if not prompt_path:
            skipped.append({"image_file": rel, "reason": f"prompt {prompt_name} not found in manifest"})
            continue

        aspect_dir = "4_5" if "/4_5/" in rel else ("9_16" if "/9_16/" in rel else "")
        if not aspect_dir:
            skipped.append({"image_file": rel, "reason": "unknown aspect ratio"})
            continue

        if aspect_dir == "4_5":
            prompts_45.add(prompt_path)
        else:
            parent_path = _find_45_parent_for_prompt(batch, prompt_path, engine)
            if not parent_path:
                skipped.append({"image_file": rel, "reason": "no 4:5 parent image found"})
                continue
            parsed = parse_prompt_filename(prompt_path)
            if not parsed:
                skipped.append({"image_file": rel, "reason": "could not parse prompt filename"})
                continue
            p_fmt, p_lang, persona_num = parsed
            jobs_916.append({
                "format": p_fmt.upper(),
                "persona_number": int(persona_num) if persona_num else 0,
                "language": p_lang.upper(),
                "image_rel": str(parent_path.relative_to(ROOT)).replace("\\", "/"),
                "image_abs": str(parent_path.resolve()),
            })

    if not prompts_45 and not jobs_916:
        raise HTTPException(status_code=400, detail=f"No valid queued images to regenerate ({len(skipped)} skipped)")

    generated_files: list[str] = []

    # ── Regenerate 4:5 images ──────────────────────────────────────────
    if prompts_45:
        result: subprocess.CompletedProcess[str] | None = None
        try:
            if engine == "chatgpt":
                result = run_chatgpt_generation(
                    batch=batch,
                    prompt_files=list(prompts_45),
                    aspect_ratio="4:5",
                    image_sources_file=None,
                    headless=headless,
                    run_dir=run_dir,
                )
            else:
                result = run_gemini_generation(
                    batch=batch,
                    prompt_files=list(prompts_45),
                    aspect_ratio="4:5",
                    image_sources_file=None,
                    headless=headless,
                    run_dir=run_dir,
                )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"4:5 regeneration failed: {exc}")
        generation_error = ""
        if result is not None and result.returncode != 0:
            generation_error = (result.stderr or result.stdout or "").strip()

        for pf in prompts_45:
            expected = _build_expected_output_path(batch, pf, "4_5", engine)
            if expected and expected.exists() and expected.is_file():
                archive_dir = GENERATED_IMAGES_ROOT / batch / "4_5" / "to_be_regenerated"
                archive_dir.mkdir(parents=True, exist_ok=True)
                dest = _unique_path(archive_dir / expected.name)
                shutil.move(str(expected), str(dest))
                generated_files.append(str(dest.relative_to(ROOT)).replace("\\", "/"))
                # Move sidecar
                meta_src = expected.with_suffix(".json")
                if meta_src.exists() and meta_src.is_file():
                    meta_dest = dest.with_suffix(".json")
                    shutil.move(str(meta_src), str(meta_dest))
                    _mark_image_metadata_regenerated(meta_dest, dest)
            else:
                skipped.append({"image_file": pf, "reason": "regenerated image was not downloaded/found"})
        if generation_error and not generated_files:
            short = "\n".join([line for line in generation_error.splitlines() if line.strip()][-6:])
            raise HTTPException(status_code=500, detail=f"4:5 regeneration failed before any downloads were found. {short}")
        if generation_error:
            skipped.append({"image_file": "4:5 generation", "reason": "generator exited with errors after partial output"})

    # ── Regenerate 9:16 images ─────────────────────────────────────────
    if jobs_916:
        try:
            run_916_conversion_from_45_for_batch(
                batch=batch,
                headless=headless,
                run_dir=run_dir,
                engine=engine,
                jobs=jobs_916,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"9:16 regeneration failed: {exc}")

        for job in jobs_916:
            job_persona_slug = persona_slug(int(job["persona_number"])) if job.get("persona_number") is not None else ""
            prompt_path = str(ROOT / "output" / batch / "45" / f"{job['format']}_{job_persona_slug}_{job['language']}.txt")
            # Try to find exact prompt path
            pname = f"{job['format']}_{job_persona_slug}_{job['language']}"
            candidates = [pf for pf in prompt_files_list if pname in Path(pf).name]
            if candidates:
                prompt_path = candidates[0]
            expected = _build_expected_output_path(batch, prompt_path, "9_16", engine)
            if expected and expected.exists() and expected.is_file():
                archive_dir = GENERATED_IMAGES_ROOT / batch / "9_16" / "to_be_regenerated"
                archive_dir.mkdir(parents=True, exist_ok=True)
                dest = _unique_path(archive_dir / expected.name)
                shutil.move(str(expected), str(dest))
                generated_files.append(str(dest.relative_to(ROOT)).replace("\\", "/"))
                meta_src = expected.with_suffix(".json")
                if meta_src.exists() and meta_src.is_file():
                    meta_dest = dest.with_suffix(".json")
                    shutil.move(str(meta_src), str(meta_dest))
                    _mark_image_metadata_regenerated(meta_dest, dest)
            else:
                skipped.append({"image_file": str(job.get("prompt_96") or job.get("format") or "9:16"), "reason": "regenerated image was not downloaded/found"})

    refreshed = collect_backfill_result(run_id, batch)
    if has_storage_manifest and run_dir is not None:
        refreshed = collect_run_result(run_dir, batch, True)
        refreshed = merge_manifest(run_dir, manifest, refreshed)

    return {
        "status": "regenerated",
        "generated_files": generated_files,
        "skipped": skipped,
        "manifest": enrich_manifest_for_dashboard(refreshed),
    }
