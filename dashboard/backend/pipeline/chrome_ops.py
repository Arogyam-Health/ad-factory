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
    detect_wsl_user,
    detect_wsl_windows_host_ip,
    extension_browser_required_for_chatgpt,
    render_chatgpt_uses_local_agent,
    start_extension_cdp_proxy_for_user,
    wsl_chrome_cdp_url,
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

_chrome_process: subprocess.Popen | None = None

def api_launch_visible_browser() -> dict[str, Any]:
    """Launch a visible Chrome instance with CDP enabled so the user can log in
    before automation begins."""
    global _chrome_process

    # Determine CDP URL based on whether we're in WSL
    is_wsl = Path("/mnt/c").exists()
    if is_wsl:
        try:
            ip_route = subprocess.run(["ip", "route"], capture_output=True, text=True, timeout=5)
            gw_line = [l for l in ip_route.stdout.splitlines() if "default" in l]
            win_host_ip = gw_line[0].split()[2] if gw_line else "127.0.0.1"
        except Exception:
            win_host_ip = "127.0.0.1"
    else:
        win_host_ip = "127.0.0.1"

    cdp_base_url = f"http://{win_host_ip}:9222"
    cdp_url = f"{cdp_base_url}/json/version"

    # Check if CDP is already responding
    try:
        resp = urllib.request.urlopen(cdp_url, timeout=2)
        if resp.status == 200:
            return {
                "status": "already_running",
                "cdp_url": cdp_base_url,
                "message": "Chrome with CDP is already running. Log in to ChatGPT if needed, then trigger generation.",
            }
    except Exception:
        pass

    # Kill only the Chrome process holding port 9222 (if any) — do NOT nuke all Chrome instances.
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    port_held = False
    try:
        sock.bind(("127.0.0.1", 9222))
        sock.close()
    except OSError:
        port_held = True
        sock.close()

    if port_held and Path("/mnt/c").exists():
        try:
            netstat = subprocess.run(
                ["netstat.exe", "-ano", "-p", "TCP"],
                capture_output=True, text=True, timeout=10,
            )
            pids_to_kill: set[str] = set()
            for line in (netstat.stdout or "").splitlines():
                if ":9222" in line and "LISTENING" in line:
                    parts = line.split()
                    if parts:
                        pids_to_kill.add(parts[-1])
            for pid in pids_to_kill:
                try:
                    subprocess.run(
                        ["taskkill.exe", "/F", "/PID", pid],
                        capture_output=True, timeout=10,
                    )
                except Exception:
                    pass
            if pids_to_kill:
                time.sleep(2)
        except Exception:
            pass

    # Verify port 9222 is actually free
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 9222))
        sock.close()
    except OSError:
        sock.close()
        raise HTTPException(status_code=500, detail="Port 9222 is still in use. Wait 10 seconds and try again.")

    chrome_bin = None
    use_wsl_launch = False
    for candidate in [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/snap/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]:
        if Path(candidate).exists():
            chrome_bin = candidate
            break

    if not chrome_bin:
        wsl_user = detect_wsl_user()
        user_specific = (
            [f"/mnt/c/Users/{wsl_user}/AppData/Local/Google/Chrome/Application/chrome.exe"]
            if wsl_user
            else []
        )
        wsl_candidates = user_specific + [
            "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
            "/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe",
        ]
        for candidate in wsl_candidates:
            if Path(candidate).exists():
                chrome_bin = candidate
                use_wsl_launch = True
                break

    if not chrome_bin:
        raise HTTPException(status_code=500, detail="Chrome binary not found on system (tried Linux and WSL paths)")

    user_data_dir = os.path.expandvars("$HOME/.config/google-chrome-cdp")

    if use_wsl_launch:
        # Use PowerShell script to launch Chrome (handles WSL2 networking issues)
        script_path = Path(__file__).resolve().parent.parent.parent / "scripts" / "launch_chrome_cdp.ps1"
        if not script_path.exists():
            raise HTTPException(status_code=500, detail=f"Chrome launch script not found: {script_path}")

        # Convert to Windows path for PowerShell
        win_script_path = str(script_path).replace("/mnt/c/", "C:\\").replace("/", "\\")

        print(f"[chrome-launch] Running PowerShell script: {win_script_path}")

        ps_path = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        result = subprocess.run(
            [ps_path, "-ExecutionPolicy", "Bypass", "-File", win_script_path],
            capture_output=True, text=True, timeout=60,
        )

        print(f"[chrome-launch] PowerShell stdout: {result.stdout.strip()}")
        if result.stderr:
            print(f"[chrome-launch] PowerShell stderr: {result.stderr.strip()}")

        if result.returncode != 0 or "SUCCESS" not in result.stdout:
            raise HTTPException(
                status_code=500,
                detail=f"Chrome launch failed. stdout: {result.stdout}, stderr: {result.stderr}"
            )

        # Chrome is running and CDP is responding on Windows localhost
        # For WSL2, we need to use the Windows host IP to reach CDP
        try:
            ip_route = subprocess.run(["ip", "route"], capture_output=True, text=True, timeout=5)
            gw_line = [l for l in ip_route.stdout.splitlines() if "default" in l]
            win_host_ip = gw_line[0].split()[2] if gw_line else "127.0.0.1"
        except Exception:
            win_host_ip = "127.0.0.1"

        cdp_url = f"http://{win_host_ip}:9223/json/version"
        print(f"[chrome-launch] CDP URL: {cdp_url}")

        # Verify CDP is reachable from WSL
        try:
            resp = urllib.request.urlopen(cdp_url, timeout=5)
            if resp.status == 200:
                return {
                    "status": "launched",
                    "cdp_url": f"http://{win_host_ip}:9223",
                    "message": "Chrome launched. Log in to ChatGPT, then trigger image generation.",
                }
        except Exception as e:
            print(f"[chrome-launch] CDP verification failed: {e}")

        # If direct CDP fails, Chrome is still running - user can proceed manually
        return {
            "status": "launched",
            "cdp_url": f"http://{win_host_ip}:9223",
            "message": "Chrome launched. Log in to ChatGPT, then trigger image generation.",
        }
    else:
        cmd = [
            chrome_bin,
            "--remote-debugging-port=9222",
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        _chrome_process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Wait for Chrome to initialize
    time.sleep(5)
    for attempt in range(20):
        try:
            resp = urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=2)
            if resp.status == 200:
                return {
                    "status": "launched",
                    "cdp_url": "http://127.0.0.1:9222",
                    "message": "Chrome launched. Log in to ChatGPT, then trigger image generation.",
                }
        except Exception as e:
            print(f"[chrome-launch] CDP attempt {attempt+1} failed: {e}")
            time.sleep(1)

    proc_alive = _chrome_process.poll() is None if _chrome_process else False
    raise HTTPException(
        status_code=500,
        detail=f"Chrome launched but CDP not responding on port 9222. Process alive: {proc_alive}. Close all Chrome windows and try again."
    )

def api_kill_chrome() -> dict[str, Any]:
    """Kill the Chrome process started by launch-visible-browser and stop any running automation."""
    global _chrome_process
    killed = False
    if _chrome_process and _chrome_process.poll() is None:
        try:
            _chrome_process.terminate()
            try:
                _chrome_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _chrome_process.kill()
                _chrome_process.wait(timeout=3)
            killed = True
        except Exception:
            pass
        _chrome_process = None

    # Also kill any running gemini automation processes
    gemini_killed = 0
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            if any("gemini_web_automation" in c for c in cmdline):
                proc.kill()
                gemini_killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Also kill any running chatgpt automation processes
    chatgpt_killed = 0
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            if any("chatgpt_web_sutomation" in c for c in cmdline):
                proc.kill()
                chatgpt_killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Kill Windows Chrome CDP instances (only relevant when running inside WSL)
    win_chrome_killed = 0
    if Path("/mnt/c").exists():
        for candidate in ["taskkill.exe", "/mnt/c/Windows/System32/taskkill.exe"]:
            if Path(candidate).exists() or shutil.which(candidate):
                try:
                    subprocess.run(
                        [candidate, "/F", "/IM", "chrome.exe", "/FI", "WINDOWTITLE eq ChromeCDP*"],
                        capture_output=True, timeout=5,
                    )
                    win_chrome_killed = 1
                except Exception:
                    pass
                break

    return {"status": "killed", "chrome": killed, "gemini_processes": gemini_killed, "chatgpt_processes": chatgpt_killed, "windows_chrome": win_chrome_killed}

def api_stop_generation() -> dict[str, Any]:
    """Kill any running generation/assembly scripts (chatgpt, gemini, generate_ads, opencode)."""
    targets = ["chatgpt_web_sutomation", "gemini_web_automation", "generate_ads.py", "opencode"]
    counts: dict[str, int] = {t: 0 for t in targets}
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            joined = " ".join(cmdline)
            for target in targets:
                if target in joined:
                    proc.kill()
                    counts[target] += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return {"status": "killed", **counts}
