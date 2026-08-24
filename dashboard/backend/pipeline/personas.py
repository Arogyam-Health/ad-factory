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

def _resolve_starting_prompt_path() -> Path:
    user_prompt = _resolve_user_config("starting_prompt")
    if user_prompt:
        tmp = RUNTIME_ROOT / "user_starting_prompt.txt"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(user_prompt, encoding="utf-8")
        return tmp
    return STARTING_PROMPT_PATH

def _extract_persona_slug_from_prompt_filename(prompt_path: str) -> str:
    """Extract the persona slug from a prompt filename (e.g. 'always_hungry' from
    'BA_always_hungry_EN_pain_point.txt'). Returns '' if no slug is found.
    For legacy 'BA_P03_EN_pain_point.txt' prompts, returns '' (caller should
    fall back to P<NN>)."""
    if not prompt_path:
        return ""
    name = Path(prompt_path).name
    patterns = [
        r"^(?:OUTPUT_|FINAL_)?[A-Z]+_(?P<slug>[a-z0-9][a-z0-9]*(?:_[a-z0-9]+)*)_(?:EN|HI|HINGLISH)_",
        r"^(?:OUTPUT_|FINAL_)?[A-Z]+_(?P<slug>[a-z0-9][a-z0-9]*(?:_[a-z0-9]+)*)_(?:EN|HI|HINGLISH)(?:\.txt|$)",
    ]
    for pat in patterns:
        m = re.match(pat, name, re.IGNORECASE)
        if m:
            return m.group("slug").lower()
    return ""

def parse_persona_number_from_prompt(prompt_text: str) -> int | None:
    match = re.search(r"\(\s*Persona\s*(\d+)\s*\)", prompt_text, flags=re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))
