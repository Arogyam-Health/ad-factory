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

def api_product_doc() -> dict[str, Any]:
    info = default_product_doc_info()
    content = DEFAULT_PRODUCT_MASTER.read_text(encoding="utf-8", errors="ignore") if DEFAULT_PRODUCT_MASTER.exists() else ""
    return {**info, "content": content}

def api_save_product_doc(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    content = str(payload.get("content") or "")
    DEFAULT_PRODUCT_MASTER.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_PRODUCT_MASTER.write_text(content, encoding="utf-8")
    return {"status": "saved", **default_product_doc_info()}

def api_prompt_file_content(prompt_path: str = "") -> dict[str, Any]:
    """Return the full text of a prompt file."""
    if not prompt_path:
        raise HTTPException(status_code=400, detail="prompt_path is required")
    full_path = ROOT / prompt_path
    if not full_path.exists() or not full_path.is_file():
        raise HTTPException(status_code=404, detail=f"Prompt file not found: {prompt_path}")
    content = full_path.read_text(encoding="utf-8", errors="ignore")
    return {"content": content, "path": prompt_path}

def api_save_prompt_file_content(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Save full content of a prompt file."""
    prompt_path = str(payload.get("prompt_path") or "").strip()
    content = str(payload.get("content") or "")
    if not prompt_path:
        raise HTTPException(status_code=400, detail="prompt_path is required")
    full_path = ROOT / prompt_path
    if not full_path.exists():
        raise HTTPException(status_code=404, detail=f"Prompt file not found: {prompt_path}")
    full_path.write_text(content, encoding="utf-8")
    _invalidate_config_cache(full_path)
    return {"status": "saved", "path": prompt_path}

def api_input_prompt(prompt_type: str = "916_conversion") -> dict[str, Any]:
    """Return the content of an input prompt file."""
    from dashboard.backend.pipeline.personas import _resolve_starting_prompt_path
    path_map = {
        "916_conversion": CONVERT_916_TEMPLATE_PATH,
        "starting_prompt": _resolve_starting_prompt_path(),
    }
    p = path_map.get(prompt_type)
    if not p or not p.exists():
        raise HTTPException(status_code=404, detail=f"Input prompt not found: {prompt_type}")
    return {"content": p.read_text(encoding="utf-8"), "path": str(p.relative_to(ROOT))}

def api_save_input_prompt(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Save an input prompt file."""
    prompt_type = str(payload.get("prompt_type") or "").strip()
    content = str(payload.get("content") or "")
    path_map = {
        "916_conversion": CONVERT_916_TEMPLATE_PATH,
        "starting_prompt": STARTING_PROMPT_PATH,
    }
    p = path_map.get(prompt_type)
    if not p:
        raise HTTPException(status_code=400, detail="prompt_type must be '916_conversion' or 'starting_prompt'")
    p.write_text(content, encoding="utf-8")
    return {"status": "saved", "path": str(p.relative_to(ROOT))}

def save_upload(target: Path, upload: UploadFile | None) -> Path | None:
    if upload is None or not upload.filename:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    data = upload.file.read()
    target.write_bytes(data)
    return target

def coalesce_path(uploaded: Path | None, default_path: Path) -> Path:
    return uploaded if uploaded and uploaded.exists() else default_path

def resolve_safe_path(relative_path: str) -> Path:
    candidate = (ROOT / relative_path).resolve()
    if str(candidate).startswith(str(ROOT.resolve())):
        return candidate
    raise HTTPException(status_code=400, detail="Invalid path")

_opencode_catalog_cache: dict[str, Any] = {}

_opencode_catalog_lock = threading.Lock()

def _build_opencode_catalog_cached():
    global _opencode_catalog_cache
    try:
        catalog = build_opencode_catalog()
    except Exception:
        catalog = {
            "api_url": DEFAULT_OPENCODE_API_URL,
            "providers": [],
            "models_by_provider": {},
            "default_model": "",
        }
    with _opencode_catalog_lock:
        _opencode_catalog_cache = catalog

def _get_opencode_catalog():
    with _opencode_catalog_lock:
        return dict(_opencode_catalog_cache)

def api_defaults() -> dict[str, Any]:
    personas = parse_persona_library()
    opencode = _get_opencode_catalog()
    if not opencode.get("providers") and not opencode.get("models_by_provider"):
        try:
            opencode = build_opencode_catalog()
            with _opencode_catalog_lock:
                _opencode_catalog_cache.clear()
                _opencode_catalog_cache.update(opencode)
        except Exception:
            opencode = {
                "api_url": DEFAULT_OPENCODE_API_URL,
                "providers": [],
                "models_by_provider": {},
                "default_model": "",
            }
    return {
        "personas": personas,
        "formats": FORMATS,
        "format_patterns": load_format_visual_archetypes(),
        "image_sources": read_active_images(default_image_sources_file()),
        "input_images": list_input_images(),
        "product_doc": default_product_doc_info(),
        "default_files": {
            "product_info": str(DEFAULT_PRODUCT_MASTER.relative_to(ROOT)),

        },
        "opencode": opencode,
        "provider": {
            "current": (os.getenv("LLM_PROVIDER") or "opencode").strip().lower(),
            "google_api_key": bool(os.getenv("GOOGLE_API_KEY", "")),
            "opencode_api_url": os.getenv("OPENCODE_API_URL", "") or DEFAULT_OPENCODE_API_URL,
            "google_model": os.getenv("GOOGLE_MODEL", "") or DEFAULT_GOOGLE_MODEL,
            "google_models": api_google_models(),
        },
        "hypothesis": {
            "variables": HYPOTHESIS_VARIABLES,
            "default": {"type": "none", "variant": ""},
        },
        "batch_size": 10,
    }

def api_progress(batch_key: str) -> dict[str, Any]:
    from dashboard.backend.pipeline.images import generated_image_roots
    batch_key_clean = str(batch_key).strip()
    for root in generated_image_roots():
        log_path = root / batch_key_clean / "_headless_progress.json"
        if not log_path.exists():
            continue
        lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        entries = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        if not entries:
            continue
        latest = entries[-1]
        return {
            "batch_key": batch_key_clean,
            "step": latest.get("step", ""),
            "message": latest.get("message", ""),
            "time": latest.get("time", 0),
            "entries": entries,
        }
    raise HTTPException(status_code=404, detail=f"No progress found for batch: {batch_key_clean}")

def api_opencode_catalog() -> dict[str, Any]:
    try:
        catalog = build_opencode_catalog()
        with _opencode_catalog_lock:
            _opencode_catalog_cache.clear()
            _opencode_catalog_cache.update(catalog)
        return catalog
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to discover OpenCode catalog: {exc}") from exc

def api_save_provider_config(payload: dict[str, Any]) -> dict[str, Any]:
    # Read current env file
    current = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "=" in line:
                k, v = line.split("=", 1)
                current[k.strip()] = v.strip()

    # Override with payload fields
    def merge(key, payload_key=None):
        pk = payload_key or key.lower()
        if pk in payload:
            val = str(payload[pk]).strip()
            if val:
                current[key] = val
            else:
                current.pop(key, None)
        return current.get(key, "")

    merge("LLM_PROVIDER", "provider")
    merge("GOOGLE_API_KEY")
    merge("OPENCODE_API_URL")
    merge("OPENCODE_API_KEY")
    merge("GOOGLE_MODEL")

    try:
        ENV_PATH.write_text(
            "\n".join(f"{k}={v}" for k, v in current.items() if v) + "\n",
            encoding="utf-8",
        )
        for k, v in current.items():
            if v:
                os.environ[k] = v
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save config: {exc}")

    return {"status": "ok", "provider": current.get("LLM_PROVIDER", "opencode")}

def api_google_models(api_key: str = "") -> list[str]:
    key = api_key.strip() or os.getenv("GOOGLE_API_KEY", "").strip()
    if not key:
        return ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.5-pro"]
    url = f"{DEFAULT_GOOGLE_API_URL}/models?key={key}"
    try:
        resp = httpx.get(url, timeout=15)
        if resp.status_code != 200:
            return ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.5-pro"]
        data = resp.json()
        raw = data.get("models") or []
        models = []
        for m in raw:
            name = m.get("name", "")
            methods = m.get("supportedGenerationMethods") or []
            if "generateContent" in methods:
                models.append(name.replace("models/", "", 1))
        return sorted(models) if models else ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.5-pro"]
    except Exception:
        return ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.5-pro"]

def _try_parse_json(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass
    return {}

def api_llm_traces(user_id: str, limit: int = 50, offset: int = 0, run_id_filter: str | None = None) -> dict[str, Any]:
    from dashboard.backend.db.client import get_sync_db
    from dashboard.backend.db.collections import COLL_LLM_TRACES
    query = {"user_id": user_id}
    if run_id_filter:
        query["run_id"] = run_id_filter
    coll = get_sync_db()[COLL_LLM_TRACES]
    total = coll.count_documents(query)
    traces = list(
        coll.find(query, {"_id": 1, "run_id": 1, "batch": 1, "provider": 1, "model": 1, "prompt": 1, "response": 1, "duration_ms": 1, "status": 1, "created_at": 1})
        .sort("created_at", -1)
        .skip(offset)
        .limit(limit)
    )
    for t in traces:
        t["_id"] = str(t["_id"])
        t["label"] = t.pop("batch", "")
        t["request"] = _try_parse_json(t.pop("prompt", "{}"))
        t["response"] = _try_parse_json(t.get("response", "{}"))
        t["status_code"] = 200 if t.get("status") == "completed" else -1
        t["duration_s"] = round((t.pop("duration_ms", 0) or 0) / 1000, 2)
        ts = t.pop("created_at", 0)
        t["timestamp"] = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z") if ts else ""
        t.pop("error", None)
    return {"traces": traces, "total": total, "offset": offset, "limit": limit}

def api_delete_llm_traces(user_id: str, run_id_filter: str | None = None) -> dict[str, Any]:
    from dashboard.backend.db.client import get_sync_db
    from dashboard.backend.db.collections import COLL_LLM_TRACES
    query: dict[str, Any] = {"user_id": user_id}
    if run_id_filter:
        query["run_id"] = run_id_filter
    result = get_sync_db()[COLL_LLM_TRACES].delete_many(query)
    return {"deleted": result.deleted_count, "filter": run_id_filter}

def api_delete_llm_traces_by_files(user_id: str, trace_ids: list[str]) -> dict[str, Any]:
    from bson import ObjectId
    from dashboard.backend.db.client import get_sync_db
    from dashboard.backend.db.collections import COLL_LLM_TRACES
    query: dict[str, Any] = {"user_id": user_id, "_id": {"$in": [ObjectId(tid) for tid in trace_ids]}}
    result = get_sync_db()[COLL_LLM_TRACES].delete_many(query)
    return {"deleted": result.deleted_count, "trace_ids": trace_ids}

def api_run_prompts(user_id: str, run_id: str, limit: int = 200, offset: int = 0) -> dict[str, Any]:
    try:
        from dashboard.backend.db.client import get_sync_db
        from dashboard.backend.db.collections import COLL_PROMPTS
        docs = list(
            get_sync_db()[COLL_PROMPTS]
            .find({"user_id": user_id, "run_id": run_id})
            .sort("created_at", 1)
            .skip(offset)
            .limit(limit)
        )
        for d in docs:
            d.pop("_id", None)
            d.pop("content", None)
        return {"prompts": docs, "total": len(docs), "run_id": run_id}
    except Exception:
        return {"prompts": [], "total": 0, "run_id": run_id}

def _prompt_lookup_query(user_id: str, run_id: str, prompt_id: str) -> dict[str, Any]:
    query: dict[str, Any] = {"user_id": user_id, "run_id": run_id}
    try:
        from bson.objectid import ObjectId
        if ObjectId.is_valid(prompt_id):
            query["$or"] = [{"prompt_id": prompt_id}, {"_id": ObjectId(prompt_id)}]
            return query
    except Exception:
        pass
    query["prompt_id"] = prompt_id
    return query

def api_run_prompt_content(user_id: str, run_id: str, prompt_id: str) -> dict[str, Any]:
    try:
        from dashboard.backend.db.client import get_sync_db
        from dashboard.backend.db.collections import COLL_PROMPTS
        doc = get_sync_db()[COLL_PROMPTS].find_one(_prompt_lookup_query(user_id, run_id, prompt_id))
    except Exception:
        doc = None
    if not doc:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return {
        "content": doc.get("content", ""),
        "path": doc.get("file_path", ""),
        "prompt_id": doc.get("prompt_id", prompt_id),
        "filename": doc.get("filename", "prompt.txt"),
    }

def api_save_run_prompt_content(user_id: str, run_id: str, prompt_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    content = str(payload.get("content") or "")
    try:
        from dashboard.backend.db.client import get_sync_db
        from dashboard.backend.db.collections import COLL_PROMPTS
        db = get_sync_db()
        doc = db[COLL_PROMPTS].find_one(_prompt_lookup_query(user_id, run_id, prompt_id))
        if not doc:
            raise HTTPException(status_code=404, detail="Prompt not found")
        db[COLL_PROMPTS].update_one(
            {"_id": doc["_id"]},
            {"$set": {"content": content, "updated_at": time.time()}},
        )
        rel_path = str(doc.get("file_path") or "").strip()
        if rel_path:
            full_path = ROOT / rel_path
            if full_path.exists() and full_path.is_file():
                full_path.write_text(content, encoding="utf-8")
        return {"status": "saved", "path": rel_path, "prompt_id": doc.get("prompt_id", prompt_id)}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not save prompt: {exc}") from exc

def api_run_images(user_id: str, run_id: str, limit: int = 200, offset: int = 0) -> dict[str, Any]:
    try:
        from dashboard.backend.db.client import get_sync_db
        from dashboard.backend.db.collections import COLL_IMAGES
        docs = list(
            get_sync_db()[COLL_IMAGES]
            .find({"user_id": user_id, "run_id": run_id})
            .sort("created_at", 1)
            .skip(offset)
            .limit(limit)
        )
        for d in docs:
            d.pop("_id", None)
        return {"images": docs, "total": len(docs), "run_id": run_id}
    except Exception:
        return {"images": [], "total": 0, "run_id": run_id}

def _get_user_from_request(request: Request) -> dict[str, Any]:
    if app_settings.is_production:
        user = getattr(request.state, "user", None)
        if user is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return user
    return {"user_id": "dev_user", "is_admin": True}

def register_file_routes(app: FastAPI) -> None:
    """Register download endpoints.

    New DB-backed endpoints are registered first so they take priority over
    the legacy path-based endpoints (which use {param:path} catch-alls).
    """
    from dashboard.backend.pipeline.runs_db import _check_ownership
    from bson.objectid import ObjectId

    # ── New DB-backed endpoints (production-safe) ──────────────────────

    @app.get("/api/files/download/run/{run_id}/{file_id:path}")
    def download_run_file_new(
        run_id: str,
        file_id: str,
        request: Request,
    ):
        user = _get_user_from_request(request)
        _check_ownership(run_id, user["user_id"])
        known_files = {
            "manifest": "manifest.json",
            "run_context": "context/run_context.json",
            "copy_batch": "context/copy_batch.json",
            "hypothesis_config": "context/hypothesis_config.json",
            "visual_pattern_reuse": "context/visual_pattern_reuse.json",
            "background_reuse": "context/background_reuse.json",
        }
        relative = known_files.get(file_id, file_id)
        safe_path = resolve_safe_path(f"dashboard_storage/runs/{run_id}/{relative}")
        if not safe_path.exists():
            raise HTTPException(status_code=404, detail="File not found in run")
        target = safe_path.resolve()
        runs_root = RUNS_ROOT.resolve()
        run_dir_resolved = (RUNS_ROOT / run_id).resolve()
        if runs_root not in target.parents and target != runs_root:
            raise HTTPException(status_code=403, detail="Access denied")
        if run_dir_resolved not in target.parents and target != run_dir_resolved:
            raise HTTPException(status_code=403, detail="File outside run directory")
        if target.is_file():
            return FileResponse(target, filename=target.name)
        raise HTTPException(status_code=400, detail="Path is not a file")

    @app.get("/api/files/download/image/{image_id}")
    def download_image_by_id(
        image_id: str,
        request: Request,
    ):
        user = _get_user_from_request(request)
        try:
            from dashboard.backend.db.client import get_sync_db
            from dashboard.backend.db.collections import COLL_IMAGES
            doc = get_sync_db()[COLL_IMAGES].find_one({"image_id": image_id})
        except Exception:
            doc = None
        if not doc:
            raise HTTPException(status_code=404, detail="Image not found")
        if doc.get("user_id") != user["user_id"]:
            raise HTTPException(status_code=403, detail="Access denied")
        local_path = doc.get("local_path") or ""
        if local_path:
            img_path = Path(local_path) if Path(local_path).is_absolute() else ROOT / local_path
            if img_path.exists():
                return FileResponse(img_path, filename=doc.get("filename", "image.png"))
        file_path = doc.get("file_path") or ""
        if file_path:
            img_path = ROOT / file_path
            if img_path.exists():
                return FileResponse(img_path, filename=doc.get("filename", "image.png"))
        raise HTTPException(status_code=404, detail="Image file not available")

    @app.get("/api/files/download/prompt/{prompt_id}")
    def download_prompt_by_id(
        prompt_id: str,
        request: Request,
    ):
        user = _get_user_from_request(request)
        try:
            from dashboard.backend.db.client import get_sync_db
            from dashboard.backend.db.collections import COLL_PROMPTS
            query: dict[str, Any] = {"prompt_id": prompt_id}
            if ObjectId.is_valid(prompt_id):
                query = {"$or": [{"prompt_id": prompt_id}, {"_id": ObjectId(prompt_id)}]}
            doc = get_sync_db()[COLL_PROMPTS].find_one(query)
        except Exception:
            doc = None
        if not doc:
            raise HTTPException(status_code=404, detail="Prompt not found")
        if doc.get("user_id") != user["user_id"]:
            raise HTTPException(status_code=403, detail="Access denied")
        content = doc.get("content", "")
        filename = doc.get("filename", "prompt.txt")
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(content, headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    # ── Legacy path-based endpoints (dev-only in production) ───────────

    @app.get("/api/files/download/run/{run_id:path}")
    def download_run_file_legacy(
        run_id: str,
        request: Request,
    ):
        user = _get_user_from_request(request)
        _check_ownership(run_id, user["user_id"])
        if app_settings.is_production:
            raise HTTPException(status_code=403, detail="Use /api/files/download/run/{run_id}/{file_id} in production")
        safe_path = resolve_safe_path(f"dashboard_storage/runs/{run_id}")
        if not safe_path.exists():
            raise HTTPException(status_code=404, detail="Run not found")
        target = safe_path.resolve()
        runs_root = RUNS_ROOT.resolve()
        if runs_root not in target.parents and target != runs_root:
            raise HTTPException(status_code=403, detail="Access denied")
        if target.is_file():
            return FileResponse(target, filename=target.name)
        raise HTTPException(status_code=400, detail="Path is not a file")

    @app.get("/api/files/download/generated/{path:path}")
    def download_generated_file_legacy(
        path: str,
        request: Request,
    ):
        user = _get_user_from_request(request)
        if app_settings.is_production:
            raise HTTPException(status_code=403, detail="Use /api/files/download/image/{image_id} in production")
        run_id = _extract_run_id_from_generated_path(path)
        if run_id:
            _check_ownership(run_id, user["user_id"])
        elif app_settings.is_production:
            raise HTTPException(status_code=403, detail="Cannot determine run ownership from path")
        safe_path = resolve_safe_path(f"generated_images/{path}")
        if not safe_path.exists():
            raise HTTPException(status_code=404, detail="File not found")
        target = safe_path.resolve()
        images_root = GENERATED_IMAGES_ROOT.resolve()
        if images_root not in target.parents:
            raise HTTPException(status_code=403, detail="Access denied")
        if target.is_file():
            return FileResponse(target, filename=target.name)
        raise HTTPException(status_code=400, detail="Path is not a file")

    @app.get("/api/files/download/output/{path:path}")
    def download_output_file_legacy(
        path: str,
        request: Request,
    ):
        user = _get_user_from_request(request)
        if app_settings.is_production:
            raise HTTPException(status_code=403, detail="Use /api/files/download/run/{run_id}/copy_batch in production")
        run_id = _extract_run_id_from_output_path(path)
        if run_id:
            _check_ownership(run_id, user["user_id"])
        elif app_settings.is_production:
            raise HTTPException(status_code=403, detail="Cannot determine run ownership from output path")
        safe_path = resolve_safe_path(f"output/{path}")
        if not safe_path.exists():
            raise HTTPException(status_code=404, detail="File not found")
        target = safe_path.resolve()
        output_root = (ROOT / "output").resolve()
        if output_root not in target.parents:
            raise HTTPException(status_code=403, detail="Access denied")
        if target.is_file():
            return FileResponse(target, filename=target.name)
        raise HTTPException(status_code=400, detail="Path is not a file")

    app.add_api_route("/api/storage/info", storage_info, methods=["GET"])
    app.add_api_route("/api/seeds", list_seed_files, methods=["GET"])
    app.add_api_route("/api/seeds/download/{path:path}", download_seed_file, methods=["GET"])
    app.add_api_route("/api/files/input/{path:path}", download_input_file, methods=["GET"])


def storage_info(request: Request) -> dict[str, Any]:
    user = _get_user_from_request(request)
    return {
        "storage_provider": "local",
        "generated_images_dir": str(GENERATED_IMAGES_ROOT.resolve()),
        "output_dir": str((ROOT / "output").resolve()),
        "runs_dir": str(RUNS_ROOT.resolve()),
        "storage_root": str(STORAGE_ROOT.resolve()),
        "note": "All files are stored locally on the server filesystem.",
    }

def list_seed_files(request: Request) -> dict[str, list[dict[str, str]]]:
    """List available seed file names and paths (safe for production)."""
    files: list[dict[str, str]] = []
    if INPUT_ROOT.is_dir():
        for f in sorted(INPUT_ROOT.rglob("*")):
            if f.is_file() and not f.name.startswith("."):
                rel = f.relative_to(INPUT_ROOT)
                files.append({"name": f.name, "path": str(rel.as_posix())})
    return {"files": files}

def download_seed_file(path: str, request: Request) -> FileResponse:
    user = _get_user_from_request(request)
    safe_path = resolve_safe_path(str(INPUT_ROOT / path))
    if not safe_path.exists():
        raise HTTPException(status_code=404, detail="Seed file not found")
    target = safe_path.resolve()
    if INPUT_ROOT.resolve() not in target.parents:
        raise HTTPException(status_code=403, detail="Access denied")
    if not target.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")
    return FileResponse(target, filename=target.name)

def download_input_file(path: str, request: Request) -> FileResponse:
    user = _get_user_from_request(request)
    if app_settings.is_dev:
        safe_path = resolve_safe_path(str(INPUT_ROOT / path))
    else:
        safe_path = resolve_safe_path(str(INPUT_ROOT / path))
        if not safe_path.exists():
            raise HTTPException(status_code=404, detail="File not found")
    target = safe_path.resolve()
    if INPUT_ROOT.resolve() not in target.parents:
        raise HTTPException(status_code=403, detail="Access denied")
    if not target.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")
    return FileResponse(target, filename=target.name)
