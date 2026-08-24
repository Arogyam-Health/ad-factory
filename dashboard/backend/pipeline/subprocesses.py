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
from dashboard.backend.pipeline.browser_env import dashboard_subprocess_env
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

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

def _append_opencode_queue_log(message: str) -> None:
    pass

@contextmanager
def _opencode_queue_slot(label: str) -> Iterator[None]:
    OPENCODE_QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    queued_at = time.time()
    logged_wait = False
    while True:
        for slot in range(OPENCODE_MAX_CONCURRENT):
            lock_path = OPENCODE_QUEUE_DIR / f"slot_{slot}.lock"
            lock_handle = lock_path.open("a+")
            acquired = False
            try:
                if sys.platform == "win32":
                    try:
                        msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
                        acquired = True
                    except OSError:
                        pass
                else:
                    try:
                        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired = True
                    except BlockingIOError:
                        pass
            except Exception:
                pass
            if not acquired:
                lock_handle.close()
                continue
            wait_seconds = time.time() - queued_at
            if wait_seconds >= 0.25:
                _append_opencode_queue_log(f"{label} started slot={slot} wait_seconds={wait_seconds:.1f}")
            try:
                yield
                return
            finally:
                try:
                    if sys.platform == "win32":
                        lock_handle.seek(0)
                        msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
                finally:
                    lock_handle.close()
        if not logged_wait:
            _append_opencode_queue_log(f"{label} queued max_concurrent={OPENCODE_MAX_CONCURRENT}")
            logged_wait = True
        time.sleep(0.25)

def run_cmd(
    cmd: list[str],
    cwd: Path,
    *,
    run_id: str | None = None,
    poll_cancel: bool = True,
    timeout_seconds: int | None = None,
) -> subprocess.CompletedProcess[str]:
    env = dashboard_subprocess_env()
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        encoding="utf-8",
        errors="replace",
    )
    _register_subprocess(run_id, proc)
    cancel_event = cancel_event_for_run(run_id) if run_id else _cancel_current_run
    poll_interval = 0.1
    started_at = time.monotonic()
    try:
        while True:
            if timeout_seconds and time.monotonic() - started_at > timeout_seconds:
                proc.kill()
                stdout, stderr = proc.communicate()
                return subprocess.CompletedProcess(
                    proc.args,
                    proc.returncode if proc.returncode is not None else -1,
                    stdout or "",
                    (stderr or "") + f"\n[killed: browser automation timed out after {timeout_seconds}s]",
                )
            if poll_cancel and cancel_event.is_set():
                proc.kill()
                stdout, stderr = proc.communicate()
                return subprocess.CompletedProcess(
                    proc.args,
                    proc.returncode if proc.returncode is not None else -1,
                    stdout or "",
                    (stderr or "") + "\n[killed: cancel signaled]",
                )
            try:
                stdout, stderr = proc.communicate(timeout=poll_interval)
                return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                continue
    finally:
        _unregister_subprocess(proc)

def browser_automation_timeout_seconds(prompt_count: int, provider: str = "chatgpt") -> int:
    configured = str(os.getenv("BROWSER_AUTOMATION_MAX_SECONDS") or "").strip()
    if configured.isdigit():
        return max(60, int(configured))
    prefix = "CHATGPT" if provider == "chatgpt" else "GEMINI"
    generation = int(os.getenv(f"{prefix}_GENERATION_TIMEOUT_SECONDS") or "420")
    manual_login = int(os.getenv(f"{prefix}_MANUAL_LOGIN_TIMEOUT_SECONDS") or "180")
    download = int(os.getenv(f"{prefix}_DOWNLOAD_TIMEOUT_SECONDS") or "90") if provider == "chatgpt" else 90
    per_prompt = generation + download + 120
    return max(300, manual_login + max(1, prompt_count) * per_prompt)

def parse_json_stdout(result: subprocess.CompletedProcess[str], context: str) -> Any:
    if result.returncode != 0:
        raise RuntimeError(f"{context} failed: {result.stderr.strip() or result.stdout.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{context} returned invalid JSON") from exc

def append_run_log(run_dir: Path, filename: str, message: str) -> None:
    log_path = run_dir / "logs" / filename
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")
