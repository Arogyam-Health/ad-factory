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

def api_delete_input_image(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    rel_path = str(payload.get("path") or "").strip().replace("\\", "/")
    if not rel_path.startswith("input/images/"):
        raise HTTPException(status_code=400, detail="path must be under input/images")
    target = (ROOT / rel_path).resolve()
    images_root = INPUT_IMAGES_DIR.resolve()
    if images_root not in target.parents:
        raise HTTPException(status_code=400, detail="Invalid image path")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Input image not found")
    target.unlink()
    return {"status": "deleted", "path": rel_path}

def api_upload_input_images(
    files: list[UploadFile] = File(...),
    clear_existing: bool = Form(False),
) -> dict[str, Any]:
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    saved = store_uploaded_input_images(files, clear_existing)
    return {
        "status": "ok",
        "saved": saved,
        "input_images": list_input_images(),
    }

def generated_image_roots() -> list[Path]:
    return [GENERATED_IMAGES_ROOT]

def ensure_916_conversion_template() -> Path:
    CONVERT_916_TEMPLATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return CONVERT_916_TEMPLATE_PATH

def resolve_916_conversion_template_text(user_id: str = "", org_id: str | None = None) -> str:
    configured = str(_resolve_user_config("conversion_916_prompt") or "").strip()
    if not configured and user_id:
        try:
            from dashboard.backend.services.user_config import resolve_effective_config
            configured = str(resolve_effective_config(user_id, org_id=org_id).get("conversion_916_prompt") or "").strip()
        except Exception:
            configured = ""
    if configured:
        return configured
    template_path = ensure_916_conversion_template()
    return template_path.read_text(encoding="utf-8", errors="replace").strip()

def build_916_conversion_prompt_job(fmt: str, persona_num: int, lang: str, index: int, source_stem: str = "") -> str:
    """Build the prompt filename for a 9:16 conversion job.

    If ``source_stem`` is given (the stem of the source 4:5 image, e.g.
    ``BA_always_hungry_EN_pain_point``), the 9:16 prompt reuses it so the
    generated 9:16 image lands in ``9_16/`` with the same stem as its 4:5
    source. Otherwise falls back to a synthesized name with an index suffix.
    """
    if source_stem:
        clean = source_stem.strip()
        if clean:
            return f"{clean}.txt"
    fmt_clean = (fmt or "HERO").strip().upper() or "HERO"
    lang_clean = (lang or "EN").strip().upper() or "EN"
    persona_safe = max(0, int(persona_num or 0))
    persona_part = persona_slug(persona_safe) if persona_safe > 0 else f"persona_{int(index):02d}"
    if persona_safe > 0:
        return f"{fmt_clean}_{persona_part}_{lang_clean}_A{max(1, int(index)):02d}.txt"
    return f"{fmt_clean}_{persona_part}_{lang_clean}.txt"

def collect_45_reference_jobs_for_batch(batch: str) -> list[dict[str, Any]]:
    summary = load_batch_image_summary(batch)
    jobs: list[dict[str, Any]] = []
    seen_refs: set[str] = set()

    for entry in summary:
        prompt_file = str(entry.get("prompt_file") or "").strip().replace("\\", "/")
        saved_files = entry.get("saved_files") if isinstance(entry.get("saved_files"), list) else []
        if not prompt_file or not saved_files:
            continue

        parsed = parse_prompt_filename(prompt_file)
        if not parsed:
            continue
        fmt, lang, persona_num = parsed
        if persona_num is None:
            continue

        for candidate in saved_files:
            c = str(candidate or "").strip().replace("\\", "/")
            if not c:
                continue
            if c in seen_refs:
                continue
            image_abs = (ROOT / c).resolve()
            if not image_abs.exists() or not image_abs.is_file():
                continue

            seen_refs.add(c)
            jobs.append(
                {
                    "format": fmt.upper(),
                    "persona_number": int(persona_num),
                    "language": lang.upper(),
                    "image_rel": c,
                    "image_abs": str(image_abs),
                }
            )

    if jobs:
        return jobs

    # Fallback: derive from prompt files + filesystem scan under 4_5
    prompt_files = scan_prompt_files_for_batch(batch)
    for prompt_file in prompt_files:
        if "/45/" not in str(prompt_file):
            continue
        parsed = parse_prompt_filename(prompt_file)
        if not parsed:
            continue
        fmt, lang, persona_num = parsed
        if persona_num is None:
            continue
        # New filename format uses the persona slug (e.g. "always_hungry"), not "p01".
        # Match on the slug OR the legacy p{NN} pattern for older batches.
        slug = persona_slug(persona_num)
        patterns = [f"*{slug}*", f"*p{persona_num:02d}*"]
        for img_root in generated_image_roots():
            ref_dir = img_root / batch / "4_5"
            if not ref_dir.exists():
                continue
            for ext in ("png", "jpg", "jpeg", "webp"):
                seen_in_pattern: set[str] = set()
                for pattern in patterns:
                    for f in sorted(ref_dir.glob(f"**/{pattern}.{ext}")):
                        rel = str(f.relative_to(ROOT)).replace("\\", "/")
                        if rel in seen_in_pattern or rel in seen_refs:
                            continue
                        seen_in_pattern.add(rel)
                        image_abs = (ROOT / rel).resolve()
                        if not image_abs.exists() or not image_abs.is_file():
                            continue
                        seen_refs.add(rel)
                        jobs.append(
                            {
                                "format": fmt.upper(),
                                "persona_number": int(persona_num),
                                "language": lang.upper(),
                                "image_rel": rel,
                                "image_abs": str(image_abs),
                            }
                        )

    return jobs

def image_static_route_for_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    if normalized.startswith("generated_images/"):
        return f"/generated_images/{normalized.removeprefix('generated_images/')}"
    return f"/generated_images/{normalized}"

def load_batch_image_summary(batch: str) -> list[dict[str, Any]]:
    summary_path = GENERATED_IMAGES_ROOT / batch / "batch_run_summary.json"
    if not summary_path.exists():
        jobs_by_prompt: dict[str, dict[str, Any]] = {}
        for generated_root in generated_image_roots():
            generated_batch_dir = generated_root / batch
            if not generated_batch_dir.exists():
                continue
            for meta_file in sorted(generated_batch_dir.glob("**/*.json")):
                try:
                    payload = json.loads(meta_file.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(payload, dict):
                    continue
                rec_type = str(payload.get("type") or payload.get("record_type") or "").strip()
                if rec_type not in ("ad_image", "generated_image", "gemini_ad_image", "chatgpt_ad_image"):
                    continue

                prompt_file = str(payload.get("prompt_file_relative") or payload.get("prompt_file") or "").strip().replace("\\", "/")
                saved_file = str(payload.get("saved_file") or "").strip().replace("\\", "/")
                if not prompt_file or not saved_file:
                    continue

                existing = jobs_by_prompt.get(prompt_file)
                if not existing:
                    fmt = payload.get("format") or payload.get("format_id") or ""
                    lang = payload.get("language") or payload.get("lang_id") or ""
                    existing = {
                        "prompt_file": prompt_file,
                        "saved_files": [],
                        "format": fmt,
                        "language": lang,
                        "variation": payload.get("variation"),
                        "task_id": payload.get("task_id"),
                        "prompt_metadata": payload.get("prompt_metadata") or {},
                    }
                    jobs_by_prompt[prompt_file] = existing
                saved_files = existing.get("saved_files")
                if not isinstance(saved_files, list):
                    saved_files = []
                    existing["saved_files"] = saved_files
                if saved_file not in saved_files:
                    saved_files.append(saved_file)

        return list(jobs_by_prompt.values())
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    jobs = summary.get("jobs")
    if isinstance(jobs, list):
        return [job for job in jobs if isinstance(job, dict)]
    return []

def collect_run_result(run_dir: Path, batch_name: str, image_generated: bool) -> dict[str, Any]:
    prompt_files = scan_prompt_files_for_batch(batch_name)

    image_files: list[str] = []
    if image_generated:
        for generated_root in generated_image_roots():
            image_dir = generated_root / batch_name
            if not image_dir.exists():
                continue
            for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
                for file in sorted(image_dir.glob(f"**/{ext}")):
                    image_files.append(str(file.relative_to(ROOT)))

    result = {
        "run_id": run_dir.name,
        "batch": batch_name,
        "prompt_files": prompt_files,
        "image_files": image_files,
        "image_generated": image_generated,
        "updated_at": now_iso(),
    }
    (run_dir / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result

_IMAGE_PATH_SORT_RE = re.compile(r"p(\d+)", re.IGNORECASE)

def _image_path_sort_key(rel: str):
    name = rel.rsplit("/", 1)[-1]
    m = _IMAGE_PATH_SORT_RE.search(name)
    persona = int(m.group(1)) if m else 0
    aspect = 0 if "/4_5/" in rel else 1
    return (persona, aspect, name)

def _collect_aspect_ratio_images(batch_name: str, aspect_ratio: str) -> list[str]:
    """Collect generated image paths for a specific aspect ratio.

    Searches both legacy and new unified output roots, looking under
    generated_images/{batch}/{aspect}/ for image files recursively.
    """
    aspect_folder = "4_5" if aspect_ratio == "4:5" else "9_16"
    image_files: list[str] = []
    for generated_root in generated_image_roots():
        image_dir = generated_root / batch_name / aspect_folder
        if not image_dir.exists():
            continue
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
            for file in image_dir.glob(f"**/{ext}"):
                rel = str(file.relative_to(ROOT)).replace("\\", "/")
                if "/debug/" in rel or "/.browser_downloads/" in rel:
                    continue
                image_files.append(rel)
    image_files.sort(key=_image_path_sort_key)
    return image_files

def scan_prompt_files_for_batch(batch_name: str) -> list[str]:
    output_dir = ROOT / "output" / batch_name
    prompt_files: list[str] = []
    if not output_dir.exists():
        return prompt_files
    # Match both the new slug format (<FMT>_<slug>_<LANG>_<angle>.txt)
    # and the legacy P{NN} format (<FMT>_P<NN>_<LANG>[_<angle>].txt).
    for file in sorted(output_dir.glob("**/*.txt")):
        name = file.name
        if not re.match(r"^(?:OUTPUT_|FINAL_)?[A-Z]+_", name):
            continue
        prompt_files.append(str(file.relative_to(ROOT)))
    return prompt_files

def scan_image_files_for_batch(batch_name: str) -> list[str]:
    image_files: list[str] = []
    seen: set[str] = set()
    for generated_root in generated_image_roots():
        image_dir = generated_root / batch_name
        if not image_dir.exists():
            continue
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
            for file in image_dir.glob(f"**/{ext}"):
                rel = str(file.relative_to(ROOT)).replace("\\", "/")
                if "/debug/" in rel or "/.browser_downloads/" in rel or "/to_be_regenerated/" in rel:
                    continue
                if rel in seen:
                    continue
                seen.add(rel)
                image_files.append(rel)
    image_files.sort(key=_image_path_sort_key)
    return image_files

def scan_regeneration_queue_files_for_batch(batch_name: str) -> list[str]:
    queue_files: list[str] = []
    seen: set[str] = set()
    for generated_root in generated_image_roots():
        for tbr_dir in sorted(generated_root.glob(f"{batch_name}/**/to_be_regenerated")):
            if not tbr_dir.is_dir():
                continue
            for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
                for file in tbr_dir.glob(f"**/{ext}"):
                    rel = str(file.relative_to(ROOT)).replace("\\", "/")
                    if rel in seen:
                        continue
                    seen.add(rel)
                    queue_files.append(rel)
    queue_files.sort(key=_image_path_sort_key)
    return queue_files

def _read_image_metadata(image_rel_path: str) -> dict[str, Any]:
    image_path = ROOT / image_rel_path
    meta_path = image_path.with_suffix(".json")
    if not meta_path.exists() or not meta_path.is_file():
        return {}
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}

def _prompt_stem_for_image(image_rel_path: str) -> str:
    stem = Path(image_rel_path).stem
    if stem.startswith("gemini-"):
        stem = stem.removeprefix("gemini-")
    elif stem.startswith("chatgpt-"):
        stem = stem.removeprefix("chatgpt-")
    # Strip trailing _4_5 / _9_16 aspect suffix added by the image generation scripts.
    stem = re.sub(r"_(?:4_5|9_16)$", "", stem)
    return stem.replace("-", "_").upper()

def _find_prompt_for_image(
    image_rel_path: str,
    prompt_files: list[str],
    metadata: dict[str, Any],
) -> str:
    prompt_from_name = _find_prompt_from_image_name(image_rel_path, prompt_files)
    if prompt_from_name:
        return prompt_from_name

    prompt_name = str(metadata.get("prompt_file_relative") or metadata.get("prompt_file") or "").strip().replace("\\", "/")
    if prompt_name:
        if prompt_name.startswith("output/") and prompt_name in prompt_files:
            return prompt_name
        by_name = [p for p in prompt_files if Path(p).name == Path(prompt_name).name]
        if by_name:
            if "/45/" in image_rel_path:
                return next((p for p in by_name if "/45/" in p), by_name[0])
            if "/9_16/" in image_rel_path or "/916/" in image_rel_path or "/96/" in image_rel_path:
                return next((p for p in by_name if "/916/" in p or "/96/" in p), by_name[0])
            return by_name[0]

    stem_key = _prompt_stem_for_image(image_rel_path)
    scored: list[tuple[int, str]] = []
    for prompt_file in prompt_files:
        parsed = parse_prompt_filename(prompt_file)
        if not parsed:
            continue
        fmt, lang, persona_num = parsed
        if persona_num is None:
            continue
        creative_index = parse_prompt_creative_index(prompt_file)
        tokens = [fmt.upper(), f"P{persona_num:02d}", lang.upper()]
        score = sum(1 for token in tokens if token in stem_key)
        if creative_index > 1 and f"A{creative_index:02d}" in stem_key:
            score += 1
        if "/45/" in prompt_file:
            score += 1
        if score >= 3:
            scored.append((score, prompt_file))
    if not scored:
        return ""
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]

def _find_45_prompt_for_regeneration(prompt_file: str, prompt_files: list[str]) -> str:
    if not prompt_file:
        return ""
    if "/45/" in prompt_file:
        return prompt_file
    parsed = parse_prompt_filename(prompt_file)
    if not parsed:
        return ""
    fmt, lang, persona_num = parsed
    creative_index = parse_prompt_creative_index(prompt_file)
    for candidate in prompt_files:
        if "/45/" not in candidate:
            continue
        c_parsed = parse_prompt_filename(candidate)
        if not c_parsed:
            continue
        c_fmt, c_lang, c_persona_num = c_parsed
        if c_fmt == fmt and c_lang == lang and c_persona_num == persona_num and parse_prompt_creative_index(candidate) == creative_index:
            return candidate
    for candidate in prompt_files:
        if "/45/" in candidate and Path(candidate).name == Path(prompt_file).name:
            return candidate
    return ""

def _prompt_excerpt(prompt_file: str, max_chars: int | None = None) -> str:
    if not prompt_file:
        return ""
    prompt_path = ROOT / prompt_file
    if not prompt_path.exists() or not prompt_path.is_file():
        return ""
    text = prompt_path.read_text(encoding="utf-8", errors="ignore").strip()
    if max_chars is None or len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n..."

def _aspect_key_for_image(rel: str) -> str:
    return "9_16" if "/9_16/" in rel or "/916/" in rel or "/96/" in rel else "4_5"

def _prompt_matches_persona(prompt_file: str, fmt: str, lang: str, persona_num: int) -> bool:
    parsed = parse_prompt_filename(prompt_file)
    if not parsed:
        return False
    p_fmt, p_lang, p_persona = parsed
    return p_fmt.upper() == fmt.upper() and p_lang.upper() == lang.upper() and p_persona == persona_num

def _match_metadata_prompt(rel: str, prompt_files: list[str]) -> str:
    meta = _read_image_metadata(rel)
    prompt_name = str(meta.get("prompt_file") or "").strip()
    if not prompt_name:
        return ""
    matches = [p for p in prompt_files if Path(p).name == Path(prompt_name).name]
    if matches:
        return matches[0]
    return ""

def _find_prompt_for_group_bucket(
    rel: str,
    prompt_files: list[str],
    group_prompts: list[str],
    remaining: list[str],
) -> str:
    if not group_prompts and not remaining:
        return _match_metadata_prompt(rel, prompt_files)
    return ""

def _group_prompt_map_for_images(image_paths: list[str], prompt_files: list[str]) -> dict[str, dict[str, str]]:
    """Map generated images to prompts.

    Precedence:
      1. Sidecar metadata (the .json written alongside each image) stores the
         exact prompt_file name — use it when the file exists in prompt_files.
      2. Group-based heuristic for older images without metadata — assign within
         each (fmt, lang, persona, aspect) group so extra images don't shift later groups.
         Within a group, images and prompts are both sorted ascending by A-index.
    """
    direct: dict[str, dict[str, str]] = {}
    groups: dict[tuple[str, str, int, str], list[tuple[int, str]]] = {}
    out: dict[str, dict[str, str]] = {}

    for rel in image_paths:
        metadata_match = _match_metadata_prompt(rel, prompt_files)
        if metadata_match:
            direct[rel] = {"prompt_file": metadata_match, "mapping_status": ""}
            continue
        parsed = _parse_generated_image_name(rel)
        if not parsed:
            continue
        fmt = str(parsed.get("format") or "")
        lang = str(parsed.get("language") or "")
        persona_num = parsed.get("persona_number")
        image_index = parsed.get("image_index")
        if not fmt or not lang or not isinstance(persona_num, int):
            continue
        sort_index = int(image_index) if isinstance(image_index, int) else 0
        groups.setdefault((fmt, lang, persona_num, _aspect_key_for_image(rel)), []).append((sort_index, rel))

    for (fmt, lang, persona_num, _aspect), images in groups.items():
        prompts = sorted(
            [p for p in prompt_files if "/45/" in p and _prompt_matches_persona(p, fmt, lang, persona_num)],
            key=lambda p: (parse_prompt_creative_index(p), p),
        )
        if not prompts:
            prompts = sorted(
                [p for p in prompt_files if _prompt_matches_persona(p, fmt, lang, persona_num)],
                key=lambda p: (parse_prompt_creative_index(p), p),
            )
        if not prompts:
            continue

        images_sorted = [rel for _idx, rel in sorted(images, key=lambda item: (item[0], item[1]))]
        if len(images_sorted) > len(prompts):
            extras = images_sorted[: len(images_sorted) - len(prompts)]
            for rel in extras:
                out[rel] = {
                    "prompt_file": "",
                    "mapping_status": f"extra image: {len(images_sorted)} images for {len(prompts)} prompts in this persona",
                }
            images_sorted = images_sorted[-len(prompts):]

        for rel, prompt_file in zip(images_sorted, prompts):
            out[rel] = {"prompt_file": prompt_file, "mapping_status": ""}

    return {**direct, **out}

def _build_image_item(
    rel: str,
    prompt_files: list[str],
    *,
    is_queued: bool = False,
    prompt_file_override: str | None = None,
    mapping_status: str = "",
) -> dict[str, Any]:
    metadata = _read_image_metadata(rel)
    prompt_file = prompt_file_override if prompt_file_override is not None else _find_prompt_for_image(rel, prompt_files, metadata)
    regen_prompt_file = _find_45_prompt_for_regeneration(prompt_file, prompt_files) if not is_queued else prompt_file
    aspect = "9:16" if "/9_16/" in rel or "/916/" in rel or "/96/" in rel else "4:5"
    display_name = Path(rel).name
    if prompt_file:
        display_name = f"{Path(prompt_file).stem}{Path(rel).suffix}"
    return {
        "path": rel,
        "display_name": display_name,
        "aspect_ratio": aspect,
        "prompt_file": prompt_file,
        "regenerate_prompt_file": regen_prompt_file,
        "prompt_url": ("/output/" + prompt_file.replace("output/", "")) if prompt_file else "",
        "prompt_excerpt": _prompt_excerpt(prompt_file),
        "is_queued": is_queued,
        "mapping_status": mapping_status,
        "metadata": {
            "format": metadata.get("format", ""),
            "persona": metadata.get("persona", ""),
            "language": metadata.get("language", ""),
            "job_key": metadata.get("job_key", ""),
            "status": metadata.get("status", ""),
            "regenerated": bool(metadata.get("regenerated")) or (is_queued and "/to_be_regenerated/generated images/" in rel),
            "regenerated_at": metadata.get("regenerated_at", ""),
        },
    }

def _mark_image_metadata_regenerated(meta_path: Path, image_path: Path) -> None:
    if not meta_path.exists() or not meta_path.is_file():
        return
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    payload["regenerated"] = True
    payload["regenerated_at"] = now_iso()
    payload["regeneration_status"] = "pending_review"
    payload["saved_file"] = str(image_path)
    meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

def _image_sort_key(item: dict[str, Any]) -> tuple:
    """Sort key: aspect (4:5 before 9:16), persona number, creative index."""
    aspect = 0 if item.get("aspect_ratio") == "4:5" else 1
    prompt_file = item.get("prompt_file") or ""
    pf = parse_prompt_filename(prompt_file) if prompt_file else None
    persona = pf[2] if pf and pf[2] is not None else 999
    creative = parse_prompt_creative_index(prompt_file) if prompt_file else 999
    return (aspect, persona, creative)

def build_image_items_for_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    prompt_files = [str(path).replace("\\", "/") for path in (manifest.get("prompt_files") or [])]
    image_paths = [str(rel_raw).replace("\\", "/") for rel_raw in manifest.get("image_files") or []]
    prompt_map = _group_prompt_map_for_images(image_paths, prompt_files)
    image_items: list[dict[str, Any]] = []
    for rel in image_paths:
        mapped = prompt_map.get(rel, {})
        image_items.append(
            _build_image_item(
                rel,
                prompt_files,
                prompt_file_override=mapped.get("prompt_file") if mapped else None,
                mapping_status=str(mapped.get("mapping_status") or ""),
            )
        )
    image_items.sort(key=_image_sort_key)
    return image_items

def build_regeneration_queue_items_for_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    prompt_files = [str(path).replace("\\", "/") for path in (manifest.get("prompt_files") or [])]
    queue_paths = [str(rel_raw).replace("\\", "/") for rel_raw in manifest.get("regeneration_queue_files") or []]
    prompt_map = _group_prompt_map_for_images(queue_paths, prompt_files)
    queue_items: list[dict[str, Any]] = []
    for rel in queue_paths:
        mapped = prompt_map.get(rel, {})
        queue_items.append(
            _build_image_item(
                rel,
                prompt_files,
                is_queued=True,
                prompt_file_override=mapped.get("prompt_file") if mapped else None,
                mapping_status=str(mapped.get("mapping_status") or ""),
            )
        )
    queue_items.sort(key=_image_sort_key)
    return queue_items

def force_aspect_ratio(copy_json: dict[str, Any], aspect_ratio: str) -> dict[str, Any]:
    cloned = json.loads(json.dumps(copy_json, ensure_ascii=False))
    cloned["default_aspect_ratio"] = aspect_ratio
    ads = cloned.get("ads")
    if isinstance(ads, list):
        for ad in ads:
            if isinstance(ad, dict):
                ad["aspect_ratio"] = aspect_ratio
    return cloned

def _parse_prompt_field(prompt_text: str, label: str) -> str:
    match = re.search(rf"^\s*-\s*{re.escape(label)}:\s*(.+)$", prompt_text, flags=re.MULTILINE)
    return (match.group(1).strip() if match else "")

def parse_background_lock_from_prompt(prompt_text: str) -> tuple[str, int] | None:
    slot_match = re.search(r"^\s*-\s*Background\s+slot:\s*(BG-\d{3})\b", prompt_text, flags=re.MULTILINE | re.IGNORECASE)
    seed_match = re.search(r"^\s*-\s*Background\s+seed:\s*(\d+)\s*$", prompt_text, flags=re.MULTILINE | re.IGNORECASE)
    if not slot_match or not seed_match:
        return None
    return (slot_match.group(1).upper(), int(seed_match.group(1)))

def parse_prompt_filename(prompt_path: str) -> tuple[str, str, int | None] | None:
    """Parse a prompt file name. Returns (format, lang, persona_number) or None.

    Accepted canonical form:  <FMT>_<persona_slug>_<LANG>_<angle>[_A<NN>].txt
                              e.g. BA_always_hungry_EN_pain_point.txt,
                                   HERO_stress_snacker_HI_desired_outcome_A01.txt
    The persona_slug is looked up in persona_seeds.json to recover the persona_number.
    Also accepts forms without the concept_angle and/or variant.
    """
    name = Path(prompt_path).name
    patterns = [
        r"^(?:OUTPUT_|FINAL_)?([A-Z]+)_([a-z0-9][a-z0-9]*(?:_[a-z0-9]+)*)_(EN|HI|HINGLISH)_([a-z][a-z_]*?)(?:_(?:A|V)\d+)?\.txt$",
        r"^(?:OUTPUT_|FINAL_)?([A-Z]+)_([a-z0-9][a-z0-9]*(?:_[a-z0-9]+)*)_(EN|HI|HINGLISH)(?:_(?:A|V)\d+)?\.txt$",
        r"^(?:OUTPUT_|FINAL_)?([A-Z]+)_([a-z0-9][a-z0-9]*(?:_[a-z0-9]+)*)_(EN|HI|HINGLISH)\.txt$",
    ]
    for pat in patterns:
        m = re.match(pat, name, re.IGNORECASE)
        if m:
            slug = m.group(2).lower()
            pn = persona_number_from_slug(slug)
            return (m.group(1).upper(), m.group(3).upper(), pn)
    return None

def parse_prompt_filename_full(prompt_path: str) -> tuple[str, str, int, str, str] | None:
    """Like ``parse_prompt_filename`` but also extracts the concept_angle and variant.

    Returns ``(format, lang, persona_number, concept_angle, variant)`` or ``None``.
    ``concept_angle`` defaults to ``""`` if the filename has no angle component.
    ``variant`` is the ``A01``/``V01`` string, or ``""`` if absent.
    The persona_number is recovered from the persona slug via persona_seeds.json.
    """
    name = Path(prompt_path).name
    patterns = [
        # canonical: <FMT>_<slug>_<LANG>_<angle>[_A<NN>].txt
        r"^(?:OUTPUT_|FINAL_)?(?P<fmt>[A-Z]+)_(?P<slug>[a-z0-9][a-z0-9]*(?:_[a-z0-9]+)*)_(?P<lang>EN|HI|HINGLISH)_(?P<angle>[a-z][a-z_]*?)(?:_(?P<variant>A\d+|V\d+))?\.txt$",
        r"^(?:OUTPUT_|FINAL_)?(?P<fmt>[A-Z]+)_(?P<slug>[a-z0-9][a-z0-9]*(?:_[a-z0-9]+)*)_(?P<lang>EN|HI|HINGLISH)(?:_(?P<variant>A\d+|V\d+))?\.txt$",
    ]
    for pat in patterns:
        m = re.match(pat, name, re.IGNORECASE)
        if m:
            pn = persona_number_from_slug(m.group("slug").lower())
            if pn is None:
                return None
            return (
                m.group("fmt").upper(),
                m.group("lang").upper(),
                pn,
                m.groupdict().get("angle", "") or "",
                m.groupdict().get("variant", "") or "",
            )
    return None

def parse_prompt_creative_index(prompt_path: str) -> int:
    match = re.search(r"_A(\d+)\.txt$", Path(prompt_path).name, flags=re.IGNORECASE)
    return int(match.group(1)) if match else 1

def _parse_generated_image_name(image_rel_path: str) -> dict[str, Any]:
    """Parse a generated image filename. Returns dict with format, persona_number,
    language, concept_angle, image_index, aspect; missing keys mean the stem is unparseable.

    Canonical form: ``<FMT>_<persona_slug>_<LANG>_<angle>[_A<NN>][_<aspect>].<ext>``
                    e.g. BA_always_hungry_EN_pain_point_4_5.png,
                         HERO_stress_snacker_HI_desired_outcome_A01_9_16.jpg
    The optional trailing ``_4_5`` / ``_9_16`` marks the aspect ratio (added by
    the chatgpt/gemini scripts).  The persona_number is recovered from the persona
    slug via persona_seeds.json.
    """
    stem = Path(image_rel_path).stem
    # Strip a trailing _4_5 / _9_16 aspect suffix so the rest of the regex can
    # match the canonical prompt-derived stem.  This also lets us report the
    # detected aspect back to callers.
    detected_aspect = ""
    aspect_match = re.search(r"_(?P<aspect>4_5|9_16)$", stem)
    if aspect_match:
        detected_aspect = aspect_match.group("aspect")
        stem = stem[: aspect_match.start()]
    patterns = [
        # canonical: <FMT>_<slug>_<LANG>_<angle>[_A<NN>]
        r"^(?P<fmt>[A-Z]+)_(?P<slug>[a-z0-9][a-z0-9]*(?:_[a-z0-9]+)*)_(?P<lang>EN|HI|HINGLISH)_(?P<angle>[a-z][a-z_]*?)(?:_A(?P<image_index>\d+))?$",
        # angle-less: <FMT>_<slug>_<LANG>[_A<NN>]
        r"^(?P<fmt>[A-Z]+)_(?P<slug>[a-z0-9][a-z0-9]*(?:_[a-z0-9]+)*)_(?P<lang>EN|HI|HINGLISH)(?:_A(?P<image_index>\d+))?$",
    ]
    for pat in patterns:
        m = re.search(pat, stem, flags=re.IGNORECASE)
        if not m:
            continue
        pn = persona_number_from_slug(m.group("slug").lower())
        if pn is None:
            continue
        return {
            "format": m.group("fmt").upper(),
            "persona_number": int(pn),
            "language": m.group("lang").upper(),
            "concept_angle": m.groupdict().get("angle", "") or "",
            "image_index": int(m.group("image_index")) if m.groupdict().get("image_index") else None,
            "aspect": detected_aspect or "",
        }
    return {}

def _sorted_prompt_candidates(
    prompt_files: list[str],
    *,
    fmt: str,
    lang: str,
    prefer_45: bool,
) -> list[str]:
    candidates: list[str] = []
    for prompt_file in prompt_files:
        parsed = parse_prompt_filename(prompt_file)
        if not parsed:
            continue
        p_fmt, p_lang, persona_num = parsed
        if persona_num is None:
            continue
        if p_fmt.upper() != fmt.upper() or p_lang.upper() != lang.upper():
            continue
        if prefer_45 and "/45/" not in prompt_file:
            continue
        candidates.append(prompt_file)
    return sorted(
        candidates,
        key=lambda prompt_file: (
            parse_prompt_filename(prompt_file)[2] or 0,
            parse_prompt_creative_index(prompt_file),
            prompt_file,
        ),
    )

def _find_prompt_from_image_name(image_rel_path: str, prompt_files: list[str]) -> str:
    parsed_image = _parse_generated_image_name(image_rel_path)
    if not parsed_image:
        return ""

    fmt = str(parsed_image.get("format") or "")
    lang = str(parsed_image.get("language") or "")
    persona_num = parsed_image.get("persona_number")
    image_index = parsed_image.get("image_index")
    prefer_45 = True

    candidates = _sorted_prompt_candidates(prompt_files, fmt=fmt, lang=lang, prefer_45=prefer_45)
    if not candidates and prefer_45:
        candidates = _sorted_prompt_candidates(prompt_files, fmt=fmt, lang=lang, prefer_45=False)

    if isinstance(image_index, int):
        # Current/future naming: image a02 should map to persona Pxx prompt A02.
        for prompt_file in candidates:
            parsed_prompt = parse_prompt_filename(prompt_file)
            if not parsed_prompt:
                continue
            _fmt, _lang, prompt_persona = parsed_prompt
            if prompt_persona == persona_num and parse_prompt_creative_index(prompt_file) == image_index:
                return prompt_file

        # Older generated images used a global sequence in filenames: P02 a04
        # means the 4th prompt in sorted FORMAT/LANG order, not prompt A04.
        if 1 <= image_index <= len(candidates):
            global_prompt = candidates[image_index - 1]
            parsed_global = parse_prompt_filename(global_prompt)
            if parsed_global and parsed_global[2] == persona_num:
                return global_prompt

    persona_candidates = [
        prompt_file
        for prompt_file in candidates
        if (parse_prompt_filename(prompt_file) or (None, None, None))[2] == persona_num
    ]
    if len(persona_candidates) == 1:
        return persona_candidates[0]
    return ""

EXACT_COPY_BLOCK_RE = re.compile(
    r"EXACT ON-IMAGE COPY - DO NOT ALTER ANYTHING\s*\n(?P<block>.+?)\n\s*Render every character exactly as written",
    flags=re.DOTALL,
)

def extract_on_image_copy_lines(prompt_text: str) -> list[dict[str, str]]:
    """
    Legacy-ish extractor used by the dashboard editor.

    It DOES NOT preserve exact spacing/linebreaks; it trims lines into {label,value}.
    Keep this for backward compatibility.
    """
    block = EXACT_COPY_BLOCK_RE.search(prompt_text)
    if not block:
        return []

    out: list[dict[str, str]] = []
    for line in block.group("block").splitlines():
        raw = line.strip()
        if not raw:
            continue
        parsed = re.match(r"^-\s*([^:]+):\s*(.*)$", raw)
        if not parsed:
            continue
        out.append({"label": parsed.group(1).strip(), "value": parsed.group(2).strip()})
    return out

def extract_exact_on_image_copy_block(prompt_text: str, *, warn_log_path: Path | None = None) -> str | None:
    """
    Task 5: Extract ONLY the content inside:
      EXACT ON-IMAGE COPY - DO NOT ALTER ANYTHING
      ...
      Render every character exactly as written

    Rules:
    - preserve exact text including punctuation/case/spacing/line breaks
    - no normalization (no strip, no join)
    - if block missing: optionally log warning; return None
    """
    pattern = (
        r"EXACT ON-IMAGE COPY - DO NOT ALTER ANYTHING\s*\n"
        r"(?P<block>.+?)\n\s*Render every character exactly as written"
    )
    m = re.search(pattern, prompt_text, flags=re.DOTALL)
    if not m:
        if warn_log_path is not None:
            warn_log_path.parent.mkdir(parents=True, exist_ok=True)
            warn_log_path.write_text(
                "WARNING: EXACT ON-IMAGE COPY block missing; skipping this prompt.\n",
                encoding="utf-8",
            )
        return None

    # Return exactly what was captured: no strip().
    return m.group("block")

def collect_45_visual_locks(batch: str) -> dict[str, dict[str, Any]]:
    from dashboard.backend.pipeline.personas import parse_persona_number_from_prompt
    out: dict[str, dict[str, Any]] = {}
    ratio_dir = ROOT / "output" / batch / "45"
    if not ratio_dir.exists():
        return out
    for prompt_file in sorted(ratio_dir.glob("*_EN.txt")) + sorted(ratio_dir.glob("*_HI.txt")):
        parsed = parse_prompt_filename(prompt_file.name)
        if not parsed:
            continue
        fmt, _lang, persona_number = parsed
        key = f"{fmt}::P{persona_number}" if isinstance(persona_number, int) else fmt
        current = out.get(key, {})
        text = prompt_file.read_text(encoding="utf-8", errors="ignore")
        if persona_number is None:
            inferred = parse_persona_number_from_prompt(text)
            if isinstance(inferred, int):
                persona_number = inferred
                key = f"{fmt}::P{persona_number}"
                current = out.get(key, current)
        lock = parse_background_lock_from_prompt(text)
        if lock:
            current["background_slot"] = lock[0]
            current["background_seed"] = lock[1]

        visual_lock = {
            "seeded_background_direction": _parse_prompt_field(text, "Seeded background direction (single sentence, exact)"),
            "subject": _parse_prompt_field(text, "Subject"),
            "action": _parse_prompt_field(text, "Action"),
            "camera": _parse_prompt_field(text, "Camera"),
            "lighting": _parse_prompt_field(text, "Lighting"),
            "props": _parse_prompt_field(text, "Props"),
            "surfaces": _parse_prompt_field(text, "Surfaces"),
            "mood": _parse_prompt_field(text, "Mood"),
            "realism": _parse_prompt_field(text, "Realism"),
        }
        visual_lock = {k: v for k, v in visual_lock.items() if v}
        if visual_lock:
            current["visual_lock"] = visual_lock

        if current:
            out[key] = current
    return out

def apply_visual_locks(copy_json: dict[str, Any], locks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    cloned = json.loads(json.dumps(copy_json, ensure_ascii=False))
    ads = cloned.get("ads")
    if not isinstance(ads, list):
        return cloned
    for ad in ads:
        if not isinstance(ad, dict):
            continue
        fmt = str(ad.get("format") or "").strip().upper()
        persona_no = None
        persona = ad.get("persona")
        if isinstance(persona, dict):
            raw_no = persona.get("number")
            if isinstance(raw_no, int):
                persona_no = raw_no
        lock_key = f"{fmt}::P{persona_no}" if isinstance(persona_no, int) else ""
        lock = (locks.get(lock_key) if lock_key else None) or locks.get(fmt) or {}
        if not lock:
            continue
        if isinstance(lock.get("background_slot"), str):
            ad["background_slot"] = lock["background_slot"]
        if isinstance(lock.get("background_seed"), int):
            ad["background_seed"] = lock["background_seed"]
        if isinstance(lock.get("visual_lock"), dict):
            ad["visual_lock"] = lock["visual_lock"]
    return cloned

def _extract_vn_from_image_path(image_path: str) -> str:
    # Expected pattern: generated_images/v{N}/...
    m = re.search(r"/generated_images/(v\d+)(/|$)", image_path.replace("\\", "/"))
    return m.group(1) if m else ""

def _extract_aspect_from_image_path(image_path: str) -> str:
    # Extract aspect folder e.g. 4_5 or 9_16 from generated_images/v77/4_5/...
    m = re.search(r"/(\d+_\d+)/", image_path.replace("\\", "/"))
    return m.group(1) if m else ""

def _extract_created_at_iso_from_file(file_path: Path) -> str:
    try:
        ts = file_path.stat().st_mtime
        return datetime.fromtimestamp(ts, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except Exception:
        return ""

def api_delete_image(run_id: str, payload: dict[str, Any] = Body(...), user_id: str = "") -> dict[str, Any]:
    """Delete a generated image and its metadata JSON."""
    run_dir = RUNS_ROOT / run_id
    manifest_path = run_dir / "manifest.json"
    image_path = payload.get("image_file", "")
    if not image_path:
        raise HTTPException(status_code=400, detail="image_file is required")

    if not str(image_path).startswith(("http://", "https://")):
        full_path = ROOT / image_path
        if full_path.exists():
            full_path.unlink()

        # Also delete companion JSON metadata if it exists
        for json_path in (full_path.with_suffix(".json"), full_path.with_suffix(full_path.suffix + ".json")):
            if json_path.exists():
                json_path.unlink()

    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["image_files"] = [p for p in manifest.get("image_files", []) if p != image_path]
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if user_id:
        try:
            from dashboard.backend.db.client import get_sync_db
            from dashboard.backend.db.collections import COLL_IMAGES, COLL_RUNS
            db = get_sync_db()
            db[COLL_RUNS].update_one(
                {"user_id": user_id, "run_id": run_id},
                {"$pull": {"image_files": image_path, "local_artifacts": {"url": image_path}}, "$set": {"updated_at": time.time()}},
            )
            db[COLL_IMAGES].delete_many({
                "user_id": user_id,
                "run_id": run_id,
                "$or": [{"file_path": image_path}, {"local_path": image_path}, {"url": image_path}],
            })
        except Exception:
            pass

    return {"status": "deleted", "image_file": image_path}

def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 10_000):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise HTTPException(status_code=500, detail=f"Could not create unique path for {path.name}")

def _regeneration_archive_path(full_path: Path) -> Path:
    parent = full_path.parent
    if parent.name == "generated images":
        archive_dir = parent.parent / "to_be_regenerated"
    else:
        archive_dir = parent / "to_be_regenerated"
    archive_dir.mkdir(parents=True, exist_ok=True)
    return _unique_path(archive_dir / full_path.name)

def api_mark_images_to_regenerate(run_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Move bad generated images out of the active gallery before regeneration."""
    from dashboard.backend.pipeline.files import resolve_safe_path
    from dashboard.backend.pipeline.runs_db import collect_backfill_result, enrich_manifest_for_dashboard, load_manifest_for_run, merge_manifest
    run_dir, manifest, has_storage_manifest = load_manifest_for_run(run_id)
    batch = str(manifest.get("batch") or "").strip()
    if not batch:
        raise HTTPException(status_code=400, detail="Run has no batch folder")

    image_files = payload.get("image_files")
    if not isinstance(image_files, list) or not image_files:
        raise HTTPException(status_code=400, detail="image_files must be a non-empty array")

    moved: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    generated_root = GENERATED_IMAGES_ROOT.resolve()

    for raw in image_files:
        rel = str(raw or "").strip().replace("\\", "/")
        if not rel:
            continue
        full_path = resolve_safe_path(rel)
        resolved = full_path.resolve()
        if generated_root not in resolved.parents:
            skipped.append({"image_file": rel, "reason": "not under generated_images"})
            continue
        if f"/generated_images/{batch}/" not in f"/{rel}":
            skipped.append({"image_file": rel, "reason": "not in this run batch"})
            continue
        if "/to_be_regenerated/" in rel:
            skipped.append({"image_file": rel, "reason": "already archived"})
            continue
        if not full_path.exists() or not full_path.is_file():
            skipped.append({"image_file": rel, "reason": "missing"})
            continue

        archive_path = _regeneration_archive_path(full_path)
        shutil.move(str(full_path), str(archive_path))
        archive_rel = str(archive_path.relative_to(ROOT)).replace("\\", "/")
        moved.append({"image_file": rel, "archived_file": archive_rel})

        meta_path = full_path.with_suffix(".json")
        if meta_path.exists() and meta_path.is_file():
            meta_archive = archive_path.with_suffix(".json")
            shutil.move(str(meta_path), str(_unique_path(meta_archive)))

    refreshed = collect_backfill_result(run_id, batch)
    if has_storage_manifest and run_dir is not None:
        refreshed = collect_run_result(run_dir, batch, True)
        refreshed = merge_manifest(run_dir, manifest, refreshed)

    return {
        "status": "archived",
        "moved": moved,
        "skipped": skipped,
        "manifest": enrich_manifest_for_dashboard(refreshed),
    }

def _original_path_for_queued_image(rel: str) -> str | None:
    misplaced = re.match(r"^(generated_images/[^/]+)/to_be_regenerated/generated images/(.+)$", rel)
    if misplaced:
        metadata = _read_image_metadata(rel)
        aspect = str((metadata.get("test_variables") or {}).get("aspect_ratio") or "4:5") if isinstance(metadata.get("test_variables"), dict) else "4:5"
        aspect_dir = "9_16" if aspect == "9:16" else "4_5"
        return f"{misplaced.group(1)}/{aspect_dir}/generated images/{misplaced.group(2)}"
    original = rel.replace("/to_be_regenerated/", "/")
    if original == rel:
        return None
    return original

def api_restore_images_from_regeneration_queue(run_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Move images from to_be_regenerated back to their original location."""
    from dashboard.backend.pipeline.files import resolve_safe_path
    from dashboard.backend.pipeline.runs_db import collect_backfill_result, enrich_manifest_for_dashboard, load_manifest_for_run, merge_manifest
    run_dir, manifest, has_storage_manifest = load_manifest_for_run(run_id)
    batch = str(manifest.get("batch") or "").strip()
    if not batch:
        raise HTTPException(status_code=400, detail="Run has no batch folder")

    image_files = payload.get("image_files")
    if not isinstance(image_files, list) or not image_files:
        raise HTTPException(status_code=400, detail="image_files must be a non-empty array")

    restored: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    generated_root = GENERATED_IMAGES_ROOT.resolve()

    for raw in image_files:
        rel = str(raw or "").strip().replace("\\", "/")
        if not rel:
            continue
        if "/to_be_regenerated/" not in rel:
            skipped.append({"image_file": rel, "reason": "not in to_be_regenerated"})
            continue
        full_path = resolve_safe_path(rel)
        resolved = full_path.resolve()
        if generated_root not in resolved.parents:
            skipped.append({"image_file": rel, "reason": "not under generated_images"})
            continue
        if not full_path.exists() or not full_path.is_file():
            skipped.append({"image_file": rel, "reason": "missing"})
            continue

        original_rel = _original_path_for_queued_image(rel)
        if not original_rel:
            skipped.append({"image_file": rel, "reason": "could not resolve original path"})
            continue

        original_abs = (ROOT / original_rel).resolve()
        original_abs.parent.mkdir(parents=True, exist_ok=True)

        if original_abs.exists():
            # A new image already occupies this slot — move it aside
            backup = _unique_path(original_abs)
            shutil.move(str(original_abs), str(backup))

        shutil.move(str(resolved), str(original_abs))
        restored.append({
            "restored_file": original_rel,
            "archived_file": rel,
        })

        meta_path = full_path.with_suffix(".json")
        if meta_path.exists() and meta_path.is_file():
            original_meta = original_abs.with_suffix(".json")
            if original_meta.exists():
                backup_meta = _unique_path(original_meta)
                shutil.move(str(original_meta), str(backup_meta))
            shutil.move(str(meta_path), str(original_meta))

    refreshed = collect_backfill_result(run_id, batch)
    if has_storage_manifest and run_dir is not None:
        refreshed = collect_run_result(run_dir, batch, True)
        refreshed = merge_manifest(run_dir, manifest, refreshed)

    return {
        "status": "restored",
        "restored": restored,
        "skipped": skipped,
        "manifest": enrich_manifest_for_dashboard(refreshed),
    }

async def api_replace_image(run_id: str, image_file: str = Form(...), replacement_file: UploadFile = File(...)) -> dict[str, Any]:
    from dashboard.backend.pipeline.files import resolve_safe_path
    run_dir = RUNS_ROOT / run_id
    full_path = resolve_safe_path(image_file)
    generated_root = GENERATED_IMAGES_ROOT.resolve()
    if generated_root not in full_path.resolve().parents:
        raise HTTPException(status_code=400, detail="image_file must be under generated_images")
    if not full_path.exists() or not full_path.is_file():
        raise HTTPException(status_code=404, detail="Generated image not found")

    allowed = {".png", ".jpg", ".jpeg", ".webp"}
    upload_name = Path(replacement_file.filename or "").name
    upload_ext = Path(upload_name).suffix.lower()
    if upload_ext not in allowed:
        raise HTTPException(status_code=400, detail="Replacement must be png, jpg, jpeg, or webp")

    data = await replacement_file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Replacement file is empty")
    full_path.write_bytes(data)

    meta_path = full_path.with_suffix(".json")
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    else:
        meta = {"type": "ad_image", "status": "success", "saved_file": str(full_path)}
    replacements = meta.setdefault("replacements", [])
    if isinstance(replacements, list):
        replacements.append(
            {
                "timestamp": int(time.time()),
                "source_filename": upload_name,
                "size_bytes": len(data),
            }
        )
    meta["status"] = "success"
    meta["saved_file"] = str(full_path)
    meta["replaced"] = True
    meta["replacement_timestamp"] = int(time.time())
    meta["replacement_source_filename"] = upload_name
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.setdefault("image_files", [])
        if image_file not in manifest["image_files"]:
            manifest["image_files"].append(image_file)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {"status": "replaced", "image_file": image_file, "size_bytes": len(data)}

def _parse_image_naming(image_path_str: str, run_dir: Path | None) -> dict[str, str]:
    """Extract format, persona, language, concept_angle from an image's companion
    JSON metadata and build a human-readable stem for download naming.

    Stem format mirrors the canonical prompt filename (using the persona slug,
    e.g. ``HERO_stress_snacker_EN_pain_point`` for the new naming format, or
    ``HERO_P03_EN_pain_point`` for the legacy format when no slug is available):
        <FMT>_<persona>_<LANG>_<angle>[_A<NN>]_<aspect>.<ext>
    """
    from dashboard.backend.pipeline.personas import _extract_persona_slug_from_prompt_filename
    full_path = ROOT / image_path_str
    meta_path = full_path.with_suffix(".json")
    legacy_meta_path = full_path.with_suffix(full_path.suffix + ".json")
    base = {"format": "UNKNOWN", "persona": "00", "lang": "EN", "concept_angle": "", "stem": "image"}
    hyp_label = ""

    if meta_path.exists() or legacy_meta_path.exists():
        try:
            if not meta_path.exists() and legacy_meta_path.exists():
                meta_path = legacy_meta_path
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
        fmt_value = str(meta.get("format") or meta.get("format_id") or "").strip().upper()
        persona_value = str(meta.get("persona") or meta.get("persona_id") or "").strip()
        lang_value = str(meta.get("language") or meta.get("lang") or meta.get("lang_id") or "").strip().upper()
        angle_value = str(meta.get("concept_angle") or "").strip()
        if fmt_value:
            base["format"] = fmt_value
        if persona_value:
            # Prefer the persona slug (new format, e.g. "stress_snacker" or
            # "Always Hungry"). Fall back to P<NN> only if the metadata only
            # carries the legacy numeric form.
            if re.fullmatch(r"P\d+", persona_value, flags=re.IGNORECASE):
                base["persona"] = persona_value.upper()
            else:
                base["persona"] = persona_value.lower()
        if lang_value:
            base["lang"] = lang_value
        if angle_value:
            base["concept_angle"] = angle_value
        prompt_file = str(meta.get("prompt_file_relative") or meta.get("prompt_file") or "").strip().replace("\\", "/")
        prompt_slug = _extract_persona_slug_from_prompt_filename(prompt_file)
        if prompt_slug:
            base["persona"] = prompt_slug
        parsed = parse_prompt_filename_full(prompt_file)
        if parsed:
            fmt, lang, persona_num, angle, _variant = parsed
            base["format"] = fmt
            # Only override persona with P<NN> if we don't already have a slug
            # from the prompt filename (slug is the canonical, human-readable
            # form; P<NN> is a legacy fallback).
            if not prompt_slug:
                base["persona"] = f"P{persona_num:02d}" if persona_num else "P00"
            base["lang"] = lang
            if angle:
                base["concept_angle"] = angle
        creative_total = int(meta.get("creative_total") or 1) if str(meta.get("creative_total") or "1").isdigit() else 1
        creative_index = int(meta.get("creative_index") or 1) if str(meta.get("creative_index") or "1").isdigit() else 1
        if creative_total > 1:
            base["creative_suffix"] = f"_A{creative_index:02d}"

        if not hyp_label:
            htype = str(meta.get("hypothesis_type") or "")
            hvar = str(meta.get("hypothesis_variant") or "")
            if htype and htype != "none":
                parts = [htype]
                if hvar:
                    parts.append(hvar)
                hyp_label = "_" + "_".join(parts)

    if base["format"] == "UNKNOWN" or base["persona"] in {"00", "P00"}:
        name = Path(image_path_str).stem.lower()
        match = re.search(r"(?:gemini|chatgpt)-(?P<fmt>[a-z0-9]+)-p(?P<num>\d+)-(?P<lang>[a-z0-9]+)(?:-a(?P<creative>\d+))?(?:-(?P<angle>[a-z_]+))?", name)
        if match:
            base["format"] = match.group("fmt").upper()
            base["persona"] = f"P{int(match.group('num')):02d}"
            base["lang"] = match.group("lang").upper()
            if match.group("creative"):
                base["creative_suffix"] = f"_A{int(match.group('creative')):02d}"
            if match.group("angle"):
                base["concept_angle"] = match.group("angle")

    # Try hypothesis
    if run_dir is not None:
        hyp_path = run_dir / "context" / "hypothesis_config.json"
        if hyp_path.exists():
            try:
                hyp_cfg = json.loads(hyp_path.read_text(encoding="utf-8"))
                htype = hyp_cfg.get("type", "")
                hvar = hyp_cfg.get("variant", "")
                if htype and htype != "none":
                    parts = [htype]
                    if hvar:
                        parts.append(hvar)
                    hyp_label = "_" + "_".join(parts)
            except Exception:
                pass

    ext = Path(image_path_str).suffix
    angle_part = f"_{base['concept_angle']}" if base.get("concept_angle") else ""
    aspect_part = ""
    if "/9_16/" in image_path_str.replace("\\", "/"):
        aspect_part = "_9_16"
    elif "/4_5/" in image_path_str.replace("\\", "/"):
        aspect_part = "_4_5"
    stem = f"{base['format']}_{base['persona']}_{base['lang']}{angle_part}{base.get('creative_suffix', '')}{hyp_label}{aspect_part}"
    base["stem"] = stem
    base["ext"] = ext
    base["aspect"] = aspect_part.lstrip("_") if aspect_part else ""
    return base

def _clean_metadata_for_download(meta: dict[str, Any], img_path: str, run_dir: Path | None) -> dict[str, Any]:
    """Strip excessive internal keys from image metadata and enrich with
    hypothesis info, persona name, and clean format labels for download ZIP."""
    from dashboard.backend.pipeline.copy_engine import _build_persona_name_map
    clean = dict(meta)

    # Strip internal plumbing
    for key in ("generated_image_src", "saved_ext", "output_dir", "metadata_file", "type"):
        clean.pop(key, None)

    # Normalise key names
    if "format" not in clean and "format_id" in clean:
        clean["format"] = clean.pop("format_id")
    if clean.get("format_id"):
        clean.pop("format_id", None)
    if "persona" not in clean and "persona_id" in clean:
        clean["persona"] = clean.pop("persona_id")
    if clean.get("persona_id"):
        clean.pop("persona_id", None)
    if "language" not in clean and "lang_id" in clean:
        clean["language"] = clean.pop("lang_id")
    if clean.get("lang_id"):
        clean.pop("lang_id", None)

    # Ensure hypothesis keys are always present
    hyp_type = clean.get("hypothesis_type") or ""
    hyp_var = clean.get("hypothesis_variant") or ""
    clean["hypothesis_type"] = hyp_type
    clean["hypothesis_variant"] = hyp_var

    # Enrich with persona name if we have a run_dir
    if run_dir is not None:
        persona_val = clean.get("persona", "")
        if persona_val:
            mapping = _build_persona_name_map(run_dir)
            if persona_val in mapping:
                clean["persona_name"] = mapping[persona_val]

    return clean

def api_download_single_image(run_id: str, image_file: str):
    """Return a zip containing the image file and its metadata JSON."""
    run_dir = RUNS_ROOT / run_id
    full_path = ROOT / image_file
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Image file not found")

    meta_path = full_path.with_suffix(".json")
    legacy_meta_path = full_path.with_suffix(full_path.suffix + ".json")

    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(full_path, full_path.name)
        meta_content = {"source": image_file}
        if meta_path.exists() or legacy_meta_path.exists():
            try:
                if not meta_path.exists() and legacy_meta_path.exists():
                    meta_path = legacy_meta_path
                meta_content = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        meta_content = _clean_metadata_for_download(meta_content, image_file, run_dir)
        meta_content["_download_name"] = full_path.stem
        zf.writestr(f"{full_path.stem}_metadata.json", json.dumps(meta_content, ensure_ascii=False, indent=2))

    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{full_path.stem}.zip"'})

def api_download_batch_images(run_id: str):
    """Return a zip of all images grouped by VN subfolders with metadata.
    Always scans the filesystem directly so newly generated images
    (e.g. 9:16 added after the manifest was saved) are included."""
    run_dir = RUNS_ROOT / run_id

    # Refresh cached thumbnail summary before scanning
    batch_label = run_id
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        batch_label = manifest.get("batch", run_id)

    # Always scan the filesystem — manifest may be stale
    image_files = scan_image_files_for_batch(batch_label) if batch_label != run_id else []

    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        vns: set[str] = set()
        for img_path in image_files:
            full_path = ROOT / img_path
            if not full_path.exists():
                continue

            vn = _extract_vn_from_image_path(img_path) or batch_label or "images"
            vns.add(vn)
            aspect = _extract_aspect_from_image_path(img_path)
            meta_path = full_path.with_suffix(".json")
            legacy_meta_path = full_path.with_suffix(full_path.suffix + ".json")

            folder = f"{vn}/{aspect}" if aspect else vn
            zf.write(full_path, f"{folder}/{full_path.name}")

            meta_content = {"source": img_path}
            if meta_path.exists() or legacy_meta_path.exists():
                try:
                    if not meta_path.exists() and legacy_meta_path.exists():
                        meta_path = legacy_meta_path
                    meta_content = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
            meta_content = _clean_metadata_for_download(meta_content, img_path, run_dir)
            meta_content["_download_name"] = full_path.stem
            zf.writestr(f"{folder}/{full_path.stem}_metadata.json",
                        json.dumps(meta_content, ensure_ascii=False, indent=2))

        if not image_files:
            zf.writestr("README.txt",
                        "No generated images found for this run.\n"
                        "Run image generation first, then try again.")

    buf.seek(0)
    label = "_".join(sorted(vns)) if vns else (batch_label if batch_label != run_id else run_id)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="batch_{label}.zip"'})

def api_download_batches(batch_names: list[str]):
    """Return a zip of all images for given batch names, grouped by VN folder."""
    image_files_by_vn: dict[str, list[str]] = {}
    for batch_name in batch_names:
        files = scan_image_files_for_batch(batch_name)
        if files:
            image_files_by_vn[batch_name] = files

    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for vn, files in image_files_by_vn.items():
            for img_path in files:
                full_path = ROOT / img_path
                if not full_path.exists():
                    continue
                aspect = _extract_aspect_from_image_path(img_path)
                meta_path = full_path.with_suffix(".json")
                legacy_meta_path = full_path.with_suffix(full_path.suffix + ".json")
                folder = f"{vn}/{aspect}" if aspect else vn
                zf.write(full_path, f"{folder}/{full_path.name}")
                meta_content = {"source": img_path}
                if meta_path.exists() or legacy_meta_path.exists():
                    try:
                        if not meta_path.exists() and legacy_meta_path.exists():
                            meta_path = legacy_meta_path
                        meta_content = json.loads(meta_path.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                meta_content = _clean_metadata_for_download(meta_content, img_path, None)
                meta_content["_download_name"] = full_path.stem
                zf.writestr(f"{folder}/{full_path.stem}_metadata.json",
                            json.dumps(meta_content, ensure_ascii=False, indent=2))

        if not image_files_by_vn:
            zf.writestr("README.txt",
                        "No generated images found for selected batch(es).\n"
                        "Run image generation first, then try again.")

    buf.seek(0)
    label = "_".join(batch_names) if batch_names else "batches"
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{label}.zip"'})
