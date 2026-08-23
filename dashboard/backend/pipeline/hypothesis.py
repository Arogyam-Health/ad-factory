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

def _background_reuse_keys(fmt: str, persona_no: int | None, visual_archetype: str, share_across_personas: bool) -> list[str]:
    fmt = fmt.strip().upper()
    persona = f"P{persona_no:02d}" if isinstance(persona_no, int) else ""
    arch = visual_archetype.strip()
    if share_across_personas:
        return [key for key in [f"{fmt}::{arch}" if arch else "", fmt] if key]
    return [key for key in [f"{fmt}::{persona}::{arch}" if persona and arch else "", f"{fmt}::{persona}" if persona else ""] if key]

def collect_background_reuse_locks(source_run_id: str) -> dict[str, dict[str, Any]]:
    from dashboard.backend.pipeline.images import _parse_prompt_field, parse_background_lock_from_prompt, parse_prompt_filename
    from dashboard.backend.pipeline.runs_db import load_manifest_for_run
    source_run_id = str(source_run_id or "").strip()
    if not source_run_id:
        return {}
    _source_dir, manifest, _has_storage_manifest = load_manifest_for_run(source_run_id)
    locks: dict[str, dict[str, Any]] = {}
    for rel_path in manifest.get("prompt_files") or []:
        rel = str(rel_path).replace("\\", "/")
        if "/916/" in rel or "/96/" in rel:
            continue
        parsed = parse_prompt_filename(rel)
        if not parsed:
            continue
        fmt, _lang, persona_no = parsed
        prompt_path = ROOT / rel
        if not prompt_path.exists():
            continue

        slot = ""
        seed: int | None = None
        visual_archetype = ""
        sidecar = prompt_path.with_suffix(".json")
        if sidecar.exists():
            try:
                meta = json.loads(sidecar.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
            bg = meta.get("background") if isinstance(meta.get("background"), dict) else {}
            slot = str(bg.get("slot") or "").strip()
            raw_seed = bg.get("seed")
            if isinstance(raw_seed, int):
                seed = raw_seed
            visual = meta.get("visual_archetype") if isinstance(meta.get("visual_archetype"), dict) else {}
            visual_archetype = str(visual.get("id") or "").strip()

        if not slot or not isinstance(seed, int):
            text = prompt_path.read_text(encoding="utf-8", errors="ignore")
            lock = parse_background_lock_from_prompt(text)
            if lock:
                slot, seed = lock
            if not visual_archetype:
                visual_archetype = _parse_prompt_field(text, "Selected visual archetype").split(" - ", 1)[0].strip()

        if not slot or not isinstance(seed, int):
            continue

        lock_payload = {
            "background_slot": slot,
            "background_seed": seed,
            "background_reused_from_run_id": source_run_id,
        }
        for key in _background_reuse_keys(fmt, persona_no, visual_archetype, False):
            locks.setdefault(key, lock_payload)
        for key in _background_reuse_keys(fmt, persona_no, visual_archetype, True):
            locks.setdefault(key, lock_payload)
    return locks

def apply_background_reuse_locks(
    copy_json: dict[str, Any],
    locks: dict[str, dict[str, Any]],
    *,
    share_across_personas: bool,
) -> tuple[dict[str, Any], int]:
    cloned = json.loads(json.dumps(copy_json, ensure_ascii=False))
    ads = cloned.get("ads")
    if not isinstance(ads, list) or not locks:
        return cloned, 0
    applied = 0
    for ad in ads:
        if not isinstance(ad, dict):
            continue
        fmt = str(ad.get("format") or "").strip().upper()
        persona_no = None
        persona = ad.get("persona")
        if isinstance(persona, dict) and isinstance(persona.get("number"), int):
            persona_no = int(persona["number"])
        visual_archetype = str(ad.get("visual_archetype") or "").strip()
        lock = None
        reuse_key = ""
        for key in _background_reuse_keys(fmt, persona_no, visual_archetype, share_across_personas):
            if key in locks:
                lock = locks[key]
                reuse_key = key
                break
        if not lock:
            continue
        ad["background_slot"] = lock["background_slot"]
        ad["background_seed"] = lock["background_seed"]
        ad["background_reused_from_run_id"] = lock.get("background_reused_from_run_id", "")
        ad["background_reuse_key"] = reuse_key
        applied += 1
    return cloned, applied

def collect_visual_pattern_reuse_locks(source_run_id: str) -> dict[str, dict[str, Any]]:
    from dashboard.backend.pipeline.images import _parse_prompt_field, parse_prompt_filename
    from dashboard.backend.pipeline.runs_db import load_manifest_for_run
    source_run_id = str(source_run_id or "").strip()
    if not source_run_id:
        return {}
    _source_dir, manifest, _has_storage_manifest = load_manifest_for_run(source_run_id)
    locks: dict[str, dict[str, Any]] = {}
    for rel_path in manifest.get("prompt_files") or []:
        rel = str(rel_path).replace("\\", "/")
        if "/916/" in rel or "/96/" in rel:
            continue
        parsed = parse_prompt_filename(rel)
        if not parsed:
            continue
        fmt, _lang, persona_no = parsed
        prompt_path = ROOT / rel
        if not prompt_path.exists():
            continue

        visual_archetype = ""
        sidecar = prompt_path.with_suffix(".json")
        if sidecar.exists():
            try:
                meta = json.loads(sidecar.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
            visual = meta.get("visual_archetype") if isinstance(meta.get("visual_archetype"), dict) else {}
            visual_archetype = str(visual.get("id") or "").strip()

        if not visual_archetype:
            text = prompt_path.read_text(encoding="utf-8", errors="ignore")
            visual_archetype = _parse_prompt_field(text, "Selected visual archetype").split(" - ", 1)[0].strip()

        if not visual_archetype:
            continue

        lock_payload = {
            "visual_archetype": visual_archetype,
            "visual_pattern_reused_from_run_id": source_run_id,
        }
        for key in _background_reuse_keys(fmt, persona_no, visual_archetype, False):
            locks.setdefault(key, lock_payload)
        for key in _background_reuse_keys(fmt, persona_no, visual_archetype, True):
            locks.setdefault(key, lock_payload)
    return locks

def apply_visual_pattern_reuse_to_plan(
    plan: list[dict[str, Any]],
    locks: dict[str, dict[str, Any]],
    *,
    share_across_personas: bool,
) -> tuple[list[dict[str, Any]], int]:
    if not locks:
        return plan, 0
    out: list[dict[str, Any]] = []
    applied = 0
    for item in plan:
        entry = dict(item)
        fmt = str(entry.get("format") or "").strip().upper()
        persona_no = int(entry.get("persona")) if entry.get("persona") is not None else None
        lock = None
        reuse_key = ""
        keys = []
        if share_across_personas:
            keys.append(fmt)
        else:
            keys.append(f"{fmt}::P{persona_no:02d}" if isinstance(persona_no, int) else fmt)
        for key in keys:
            if key in locks:
                lock = locks[key]
                reuse_key = key
                break
        if lock:
            entry["visual_archetype"] = lock["visual_archetype"]
            entry["visual_pattern_reused_from_run_id"] = lock.get("visual_pattern_reused_from_run_id", "")
            entry["visual_pattern_reuse_key"] = reuse_key
            applied += 1
        out.append(entry)
    return out, applied

def resolve_format_plan(config: dict[str, Any]) -> list[dict[str, Any]]:
    personas = config.get("selected_personas") or []
    if not personas:
        raise RuntimeError("selected_personas is required")

    all_formats = [fmt for fmt in (config.get("global_formats") or []) if fmt in FORMATS]
    format_map = config.get("formats_by_persona") or {}
    archetype_map = config.get("visual_archetypes_by_format") or {}
    share_bg_across_personas = bool(config.get("share_background_across_personas"))
    try:
        multiplier = max(1, min(20, int(config.get("multiplier") or 1)))
    except (TypeError, ValueError):
        multiplier = 1

    out: list[dict[str, Any]] = []
    for raw_persona in personas:
        persona_num = int(raw_persona)
        per_formats = [fmt for fmt in (format_map.get(str(persona_num)) or format_map.get(persona_num) or []) if fmt in FORMATS]
        formats = per_formats if per_formats else all_formats
        if not formats:
            formats = ["HERO"]
        for fmt in formats:
            forced_archetype = str(archetype_map.get(fmt) or "").strip()
            background_group_key = fmt if share_bg_across_personas else f"{fmt}::P{persona_num:02d}"
            for creative_index in range(1, multiplier + 1):
                item = {
                    "persona": persona_num,
                    "format": fmt,
                    "creative_index": creative_index,
                    "creative_total": multiplier,
                    "background_group_key": background_group_key,
                    "share_background_across_personas": share_bg_across_personas,
                }
                if forced_archetype:
                    item["visual_archetype"] = forced_archetype
                out.append(item)
    return out

def expand_plan_with_hypothesis(plan: list[dict[str, Any]], hypothesis_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand ad plan to include hypothesis style.

    When a hypothesis is active, generates ads using that specific style/variant.
    """
    hyp_type = str(hypothesis_cfg.get("type") or "none").strip().lower()
    if hyp_type == "none" or hyp_type not in HYPOTHESIS_VARIABLES:
        return plan

    variable_def = HYPOTHESIS_VARIABLES[hyp_type]
    selected_variant = str(hypothesis_cfg.get("variant") or "").strip()
    available_options = [opt["id"] for opt in variable_def.get("options", [])]

    if not available_options:
        return plan

    # Use the selected variant if valid, otherwise use first available
    variant_to_use = selected_variant if selected_variant in available_options else available_options[0]

    out: list[dict[str, Any]] = []
    for item in plan:
        entry = dict(item)
        entry["hypothesis"] = {
            "type": hyp_type,
            "variable_label": variable_def["label"],
            "variant": variant_to_use,
            "hypothesis_id": f"{hyp_type}-{variant_to_use}",
        }
        base_group_key = str(entry.get("background_group_key") or f"{entry.get('format')}::P{int(entry.get('persona')):02d}")
        entry["background_group_key"] = f"{base_group_key}::{hyp_type}::{variant_to_use}"
        out.append(entry)
    return out
