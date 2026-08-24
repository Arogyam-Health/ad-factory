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

def enrich_manifest_for_dashboard(manifest: dict[str, Any]) -> dict[str, Any]:
    from dashboard.backend.pipeline.images import build_image_items_for_manifest, build_regeneration_queue_items_for_manifest
    enriched = dict(manifest)
    enriched["image_items"] = build_image_items_for_manifest(enriched)
    enriched["regeneration_queue_items"] = build_regeneration_queue_items_for_manifest(enriched)
    return enriched

def refresh_manifest_file_state(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    from dashboard.backend.pipeline.images import scan_image_files_for_batch, scan_prompt_files_for_batch, scan_regeneration_queue_files_for_batch
    batch_name = str(manifest.get("batch") or "").strip()
    if not batch_name:
        return manifest

    prompt_files = scan_prompt_files_for_batch(batch_name)
    image_files = scan_image_files_for_batch(batch_name)
    regeneration_queue_files = scan_regeneration_queue_files_for_batch(batch_name)
    image_generated = bool(image_files) or bool(manifest.get("image_generated", False))
    previous_prompt_files = list(manifest.get("prompt_files") or [])
    previous_image_files = list(manifest.get("image_files") or [])
    previous_queue_files = list(manifest.get("regeneration_queue_files") or [])
    if (
        previous_prompt_files == prompt_files
        and previous_image_files == image_files
        and previous_queue_files == regeneration_queue_files
        and bool(manifest.get("image_generated", False)) == image_generated
    ):
        return manifest

    newest_mtime = 0.0
    for rel in prompt_files + image_files:
        try:
            path = ROOT / rel
            if path.exists():
                newest_mtime = max(newest_mtime, path.stat().st_mtime)
        except Exception:
            pass
    updated_at = (
        datetime.fromtimestamp(newest_mtime, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if newest_mtime > 0
        else now_iso()
    )
    refreshed = {
        "run_id": run_dir.name,
        "batch": batch_name,
        "prompt_files": prompt_files,
        "image_files": image_files,
        "regeneration_queue_files": regeneration_queue_files,
        "image_generated": image_generated,
        "updated_at": updated_at,
    }
    merged = {**manifest, **refreshed}
    (run_dir / "manifest.json").write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    user_id = _get_run_owner(run_dir.name) or ""
    _persist_run_manifest_db(run_dir.name, user_id, merged, status=str(merged.get("status") or "completed"))
    if user_id:
        _store_output_mapping(run_dir.name, user_id, batch_name, merged)
    return merged

def load_run_language_mode(run_dir: Path) -> str:
    run_context_path = run_dir / "context" / "run_context.json"
    assembler_mode = "BOTH"
    if not run_context_path.exists():
        return assembler_mode
    try:
        run_context = json.loads(run_context_path.read_text(encoding="utf-8"))
        lang_mode = str(run_context.get("language_mode") or "ALL").upper()
        if lang_mode == "EN":
            return "EN"
        if lang_mode == "HI":
            return "HI"
    except Exception:
        return assembler_mode
    return assembler_mode

def merge_manifest(run_dir: Path, previous_manifest: dict[str, Any], refreshed: dict[str, Any]) -> dict[str, Any]:
    merged = {**previous_manifest, **refreshed}
    (run_dir / "manifest.json").write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    user_id = _get_run_owner(run_dir.name) or ""
    batch = str(merged.get("batch") or "").strip()
    _persist_run_manifest_db(run_dir.name, user_id, merged, status=str(merged.get("status") or "completed"))
    if batch and user_id:
        _store_output_mapping(run_dir.name, user_id, batch, merged)
    return merged

def _extract_backfill_batch(run_id: str) -> str | None:
    match = re.match(r"^batch_(v\d+)$", str(run_id or "").strip(), flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1)

def _build_backfill_manifest(run_id: str, batch: str) -> dict[str, Any]:
    from dashboard.backend.pipeline.images import scan_image_files_for_batch, scan_prompt_files_for_batch, scan_regeneration_queue_files_for_batch
    prompt_files = scan_prompt_files_for_batch(batch)
    if not prompt_files:
        raise HTTPException(status_code=404, detail=f"No prompt files found in output/{batch}")
    image_files = scan_image_files_for_batch(batch)
    batch_dir = ROOT / "output" / batch
    updated_at = now_iso()
    if batch_dir.exists():
        updated_at = datetime.fromtimestamp(batch_dir.stat().st_mtime, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    regeneration_queue_files = scan_regeneration_queue_files_for_batch(batch)
    return {
        "run_id": run_id,
        "batch": batch,
        "prompt_files": prompt_files,
        "image_files": image_files,
        "regeneration_queue_files": regeneration_queue_files,
        "image_generated": bool(image_files),
        "updated_at": updated_at,
        "source": "output_backfill",
    }

def load_manifest_for_run(run_id: str, user_id: str = "") -> tuple[Path | None, dict[str, Any], bool]:
    if user_id:
        _check_ownership(run_id, user_id)
    run_dir = RUNS_ROOT / run_id
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        refreshed = refresh_manifest_file_state(run_dir, manifest)
        return run_dir, refreshed, True

    try:
        from dashboard.backend.db.client import get_sync_db
        from dashboard.backend.db.collections import COLL_RUNS
        query = {"run_id": run_id}
        if user_id:
            query["user_id"] = user_id
        doc = get_sync_db()[COLL_RUNS].find_one(query, sort=[("updated_at", -1)])
        if doc and _mongo_run_has_dashboard_manifest(doc):
            return None, _mongo_run_to_manifest(doc), False
    except Exception:
        pass

    backfill_batch = _extract_backfill_batch(run_id)
    if backfill_batch:
        return None, _build_backfill_manifest(run_id, backfill_batch), False

    raise HTTPException(status_code=404, detail="Run not found")

def collect_backfill_result(run_id: str, batch: str) -> dict[str, Any]:
    manifest = _build_backfill_manifest(run_id, batch)
    manifest["generated_variant"] = "4:5"
    return manifest

def _ts_to_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def _mongo_run_to_manifest(doc: dict[str, Any]) -> dict[str, Any]:
    updated_at = doc.get("updated_at") or doc.get("created_at") or 0
    if isinstance(updated_at, (int, float)):
        updated_at_value = _ts_to_iso(float(updated_at))
    else:
        updated_at_value = str(updated_at or "")
    manifest = {
        "run_id": doc.get("run_id", ""),
        "batch": doc.get("batch", ""),
        "display_batch": doc.get("display_batch", ""),
        "run_number": int(doc.get("run_number") or 0),
        "status": doc.get("status", ""),
        "prompt_files": list(doc.get("prompt_files") or []),
        "image_files": list(doc.get("image_files") or []),
        "regeneration_queue_files": list(doc.get("regeneration_queue_files") or []),
        "image_generated": bool(doc.get("image_generated") or doc.get("image_files")),
        "flow_type": doc.get("flow_type", ""),
        "prompt_count": int(doc.get("prompt_count") or 0),
        "image_count": int(doc.get("image_count") or 0),
        "image_generation": dict(doc.get("image_generation") or {}),
        "device_id": doc.get("device_id", ""),
        "agent_id": doc.get("agent_id", ""),
        "reference_job_id": doc.get("reference_job_id", ""),
        "updated_at": updated_at_value,
        "source": "mongodb",
    }
    for key in _manifest_fields_for_db(doc):
        if key not in manifest:
            manifest[key] = doc.get(key)
    return manifest

def _mongo_run_has_dashboard_manifest(doc: dict[str, Any]) -> bool:
    return doc.get("flow_type") in {"structured", "reference"} or any(
        doc.get(key)
        for key in (
            "prompt_files",
            "image_files",
            "llm_mode",
            "copy_source",
            "local_artifacts",
            "image_generation",
        )
    )

def _dashboard_run_sort_key(run: dict[str, Any]) -> tuple[int, float]:
    batch = str(run.get("display_batch") or run.get("batch") or "").strip().lower()
    match = re.match(r"^v(\d+)(?:-|$)", batch)
    batch_num = int(run.get("run_number") or (match.group(1) if match else -1))
    updated = str(run.get("updated_at") or "")
    ts = 0.0
    if updated:
        try:
            ts = datetime.fromisoformat(updated.replace("Z", "+00:00")).timestamp()
        except Exception:
            ts = 0.0
    return (batch_num, ts)

def api_runs(user_id: str = "") -> dict[str, Any]:
    from dashboard.backend.pipeline.images import scan_image_files_for_batch, scan_prompt_files_for_batch, scan_regeneration_queue_files_for_batch
    ensure_dirs()
    runs: list[dict[str, Any]] = []
    seen_run_ids: set[str] = set()
    seen_batches: set[str] = set()
    if user_id:
        try:
            from dashboard.backend.db.client import get_sync_db
            from dashboard.backend.db.collections import COLL_RUNS
            docs = list(
                get_sync_db()[COLL_RUNS]
                .find({"user_id": user_id})
                .sort("updated_at", -1)
                .limit(50)
            )
        except Exception:
            docs = []
        runs = [enrich_manifest_for_dashboard(_mongo_run_to_manifest(doc)) for doc in docs]
        runs.sort(key=_dashboard_run_sort_key, reverse=True)
        return {"runs": runs}

    for run_dir in sorted(RUNS_ROOT.glob("run_*"), reverse=True):
        manifest = run_dir / "manifest.json"
        if manifest.exists():
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            refreshed = refresh_manifest_file_state(run_dir, payload)
            run_id = str(refreshed.get("run_id") or run_dir.name)
            batch = str(refreshed.get("batch") or "").strip()
            if run_id in seen_run_ids:
                continue
            seen_run_ids.add(run_id)
            if batch:
                seen_batches.add(batch)
            runs.append(enrich_manifest_for_dashboard(refreshed))

    # Backfill batches that exist on disk but have no run manifest
    # (e.g., older/generated output imported from another machine).
    output_root = ROOT / "output"
    if output_root.exists():
        for batch_dir in sorted(output_root.glob("v*"), reverse=True):
            if not batch_dir.is_dir():
                continue
            batch_name = batch_dir.name
            if batch_name in seen_batches:
                continue
            prompt_files = scan_prompt_files_for_batch(batch_name)
            if not prompt_files:
                continue
            image_files = scan_image_files_for_batch(batch_name)
            regeneration_queue_files = scan_regeneration_queue_files_for_batch(batch_name)
            updated_at = datetime.fromtimestamp(batch_dir.stat().st_mtime, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            runs.append(
                enrich_manifest_for_dashboard({
                    "run_id": f"batch_{batch_name}",
                    "batch": batch_name,
                    "prompt_files": prompt_files,
                    "image_files": image_files,
                    "regeneration_queue_files": regeneration_queue_files,
                    "image_generated": bool(image_files),
                    "updated_at": updated_at,
                    "source": "output_backfill",
                })
            )

    # MongoDB is the durable dashboard source; add rows not already found on disk
    # so redeploys and partial local storage do not hide completed runs.
    if user_id:
        try:
            from dashboard.backend.db.client import get_sync_db
            from dashboard.backend.db.collections import COLL_RUNS
            db = get_sync_db()
            for doc in db[COLL_RUNS].find(
                {"user_id": user_id},
                sort=[("updated_at", -1)],
                limit=50,
            ):
                run_id = str(doc.get("run_id") or "")
                mongo_manifest = enrich_manifest_for_dashboard(_mongo_run_to_manifest(doc))
                if run_id in seen_run_ids:
                    if not _mongo_run_has_dashboard_manifest(doc):
                        continue
                    for index, existing in enumerate(runs):
                        if str(existing.get("run_id") or "") == run_id:
                            runs[index] = mongo_manifest
                            break
                    continue
                seen_run_ids.add(run_id)
                runs.append(mongo_manifest)
        except Exception:
            pass

    runs.sort(key=_dashboard_run_sort_key, reverse=True)
    return {"runs": runs}

def api_run(run_id: str, user_id: str = "") -> dict[str, Any]:
    if user_id:
        try:
            from dashboard.backend.db.client import get_sync_db
            from dashboard.backend.db.collections import COLL_RUNS
            doc = get_sync_db()[COLL_RUNS].find_one(
                {"user_id": user_id, "run_id": run_id},
            )
            if doc and _mongo_run_has_dashboard_manifest(doc):
                return enrich_manifest_for_dashboard(_mongo_run_to_manifest(doc))
            if not doc:
                raise HTTPException(status_code=404, detail="Run not found")
        except HTTPException:
            raise
        except Exception:
            if app_settings.is_production:
                raise
    try:
        _run_dir, manifest, _has_storage_manifest = load_manifest_for_run(run_id, user_id=user_id)
        return enrich_manifest_for_dashboard(manifest)
    except HTTPException:
        if not user_id:
            raise
    try:
        from dashboard.backend.db.client import get_sync_db
        from dashboard.backend.db.collections import COLL_RUNS
        doc = get_sync_db()[COLL_RUNS].find_one(
            {"user_id": user_id, "run_id": run_id},
        )
        if doc and _mongo_run_has_dashboard_manifest(doc):
            return enrich_manifest_for_dashboard(_mongo_run_to_manifest(doc))
    except Exception:
        pass
    raise HTTPException(status_code=404, detail="Run not found")

def api_run_partial(run_id: str) -> dict[str, Any]:
    run_dir = RUNS_ROOT / run_id
    error_file = run_dir / "partial" / "error.txt"
    if error_file.exists():
        return {"ads": [], "progress": "0/0", "error": error_file.read_text(encoding="utf-8").strip()}
    partial_json = run_dir / "partial" / "ads.json"
    if not partial_json.exists():
        return {"ads": [], "progress": "0/0"}
    ads = json.loads(partial_json.read_text(encoding="utf-8"))
    progress_file = run_dir / "partial" / "progress.txt"
    progress = progress_file.read_text(encoding="utf-8").strip() if progress_file.exists() else "0/0"
    ads["progress"] = progress
    return ads

def api_delete_run(run_id: str, user_id: str = "") -> dict[str, Any]:
    if user_id:
        _check_ownership(run_id, user_id)
    run_dir = RUNS_ROOT / run_id
    mongo_run: dict[str, Any] | None = None
    if user_id:
        try:
            from dashboard.backend.db.client import get_sync_db
            from dashboard.backend.db.collections import COLL_RUNS
            mongo_run = get_sync_db()[COLL_RUNS].find_one({"user_id": user_id, "run_id": run_id})
        except Exception:
            mongo_run = None
    if not run_dir.exists() and not mongo_run:
        raise HTTPException(status_code=404, detail="Run not found")

    manifest_path = run_dir / "manifest.json"
    batch = mongo_run.get("batch") if mongo_run else None
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        batch = batch or manifest.get("batch")

    import shutil
    if run_dir.exists():
        shutil.rmtree(run_dir)

    mongo_deleted = False
    if user_id:
        try:
            from dashboard.backend.db.client import get_sync_db
            from dashboard.backend.db.collections import COLL_RUNS, COLL_PROMPTS, COLL_IMAGES, COLL_FILE_MAP, COLL_AGENT_JOBS
            db = get_sync_db()
            db[COLL_RUNS].delete_one({"user_id": user_id, "run_id": run_id})
            db[COLL_PROMPTS].delete_many({"user_id": user_id, "run_id": run_id})
            db[COLL_IMAGES].delete_many({"user_id": user_id, "run_id": run_id})
            db[COLL_FILE_MAP].delete_many({"user_id": user_id, "run_id": run_id})
            db[COLL_AGENT_JOBS].delete_many({"user_id": user_id, "run_id": run_id})
            mongo_deleted = True
        except Exception:
            mongo_deleted = False

    deleted_images = False
    deleted_prompts = False
    if batch:
        other_runs_with_same_batch = False
        if user_id:
            try:
                from dashboard.backend.db.client import get_sync_db
                from dashboard.backend.db.collections import COLL_RUNS
                other_runs_with_same_batch = bool(get_sync_db()[COLL_RUNS].find_one({"batch": batch, "run_id": {"$ne": run_id}}))
            except Exception:
                other_runs_with_same_batch = False
        for d in RUNS_ROOT.glob("run_*"):
            if d.name == run_id:
                continue
            mf = d / "manifest.json"
            if mf.exists():
                try:
                    m = json.loads(mf.read_text(encoding="utf-8"))
                    if m.get("batch") == batch:
                        other_runs_with_same_batch = True
                        break
                except (json.JSONDecodeError, OSError):
                    continue

        if not other_runs_with_same_batch:
            batch_images_dir = GENERATED_IMAGES_ROOT / batch
            if batch_images_dir.exists():
                shutil.rmtree(batch_images_dir)
                deleted_images = True

            batch_prompts_dir = ROOT / "output" / batch
            if batch_prompts_dir.exists():
                shutil.rmtree(batch_prompts_dir)
                deleted_prompts = True

    return {"status": "deleted", "run_id": run_id, "batch": batch, "deleted_images": deleted_images, "deleted_prompts": deleted_prompts, "mongo_deleted": mongo_deleted}

def api_delete_prompt(run_id: str, payload: dict[str, Any] = Body(...), user_id: str = "") -> dict[str, Any]:
    """Delete a prompt file and remove it from the run manifest."""
    if user_id:
        _check_ownership(run_id, user_id)
    run_dir = RUNS_ROOT / run_id
    manifest_path = run_dir / "manifest.json"
    prompt_path = payload.get("prompt_file", "")
    if not prompt_path:
        raise HTTPException(status_code=400, detail="prompt_file is required")

    if user_id:
        try:
            from dashboard.backend.db.client import get_sync_db
            from dashboard.backend.db.collections import COLL_RUNS, COLL_PROMPTS, COLL_FILE_MAP
            db = get_sync_db()
            result = db[COLL_PROMPTS].delete_many({"user_id": user_id, "run_id": run_id, "file_path": prompt_path})
            db[COLL_FILE_MAP].delete_many({"user_id": user_id, "run_id": run_id, "file_path": prompt_path})
            db[COLL_RUNS].update_one(
                {"user_id": user_id, "run_id": run_id},
                {"$pull": {"prompt_files": prompt_path}, "$set": {"updated_at": time.time()}},
            )
            if result.deleted_count == 0:
                raise HTTPException(status_code=404, detail="Prompt not found")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Could not delete prompt: {exc}") from exc

    full_path = ROOT / prompt_path
    if full_path.exists():
        full_path.unlink()

    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["prompt_files"] = [p for p in manifest.get("prompt_files", []) if p != prompt_path]
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {"status": "deleted", "prompt_file": prompt_path}

def _check_ownership(run_id: str, user_id: str) -> None:
    """Verify a user owns a run. In dev mode, allow if no owner recorded."""
    owner = _get_run_owner(run_id)
    if owner is None:
        if app_settings.is_production:
            raise HTTPException(status_code=403, detail="Run ownership unknown")
        return
    if owner != user_id:
        raise HTTPException(status_code=403, detail="Access denied: run belongs to another user")
