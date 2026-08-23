#!/usr/bin/env python3
"""Local FastAPI factory. Pipeline logic lives in dashboard.backend.pipeline."""

from __future__ import annotations

import os
import sys
from typing import Any

from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from dashboard.backend.pipeline.paths import (
    DEFAULT_GOOGLE_API_URL,
    DEFAULT_GOOGLE_MODEL,
    DEFAULT_PRODUCT_MASTER,
    ENV_PATH,
    FORMATS,
    GENERATED_IMAGES_ROOT,
    INPUT_IMAGES_DIR,
    INPUT_ROOT,
    LLM_TRACES_DIR,
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
    cancel_event_for_run,
    signal_cancel_current_run,
    signal_cancel_run,
)
from dashboard.backend.pipeline.run_owners import (
    _extract_run_id_from_generated_path,
    _extract_run_id_from_output_path,
    _get_run_owner,
    _record_run_owner,
    _resolve_file_owner,
    _store_output_mapping,
)
from dashboard.backend.pipeline.copy_text import persona_slug
from dashboard.backend.pipeline.input_assets import store_uploaded_input_images
from dashboard.backend.pipeline.browser_env import dashboard_subprocess_env, wsl_chrome_cdp_url
from dashboard.backend.pipeline.images import (
    EXACT_COPY_BLOCK_RE,
    _IMAGE_PATH_SORT_RE,
    _aspect_key_for_image,
    _build_image_item,
    _clean_metadata_for_download,
    _collect_aspect_ratio_images,
    _extract_aspect_from_image_path,
    _extract_created_at_iso_from_file,
    _extract_vn_from_image_path,
    _find_45_prompt_for_regeneration,
    _find_prompt_for_group_bucket,
    _find_prompt_for_image,
    _find_prompt_from_image_name,
    _group_prompt_map_for_images,
    _image_path_sort_key,
    _image_sort_key,
    _mark_image_metadata_regenerated,
    _match_metadata_prompt,
    _original_path_for_queued_image,
    _parse_generated_image_name,
    _parse_image_naming,
    _parse_prompt_field,
    _prompt_excerpt,
    _prompt_matches_persona,
    _prompt_stem_for_image,
    _read_image_metadata,
    _regeneration_archive_path,
    _sorted_prompt_candidates,
    _unique_path,
    api_delete_image,
    api_delete_input_image,
    api_download_batch_images,
    api_download_batches,
    api_download_single_image,
    api_mark_images_to_regenerate,
    api_replace_image,
    api_restore_images_from_regeneration_queue,
    api_upload_input_images,
    apply_visual_locks,
    build_916_conversion_prompt_job,
    build_image_items_for_manifest,
    build_regeneration_queue_items_for_manifest,
    collect_45_reference_jobs_for_batch,
    collect_45_visual_locks,
    collect_run_result,
    ensure_916_conversion_template,
    extract_exact_on_image_copy_block,
    extract_on_image_copy_lines,
    force_aspect_ratio,
    generated_image_roots,
    image_static_route_for_path,
    load_batch_image_summary,
    parse_background_lock_from_prompt,
    parse_prompt_creative_index,
    parse_prompt_filename,
    parse_prompt_filename_full,
    resolve_916_conversion_template_text,
    scan_image_files_for_batch,
    scan_prompt_files_for_batch,
    scan_regeneration_queue_files_for_batch,
)
from dashboard.backend.pipeline.copy_engine import (
    EXACT_COPY_SHEET_COLUMNS,
    _append_audit_log,
    _build_copy_skeleton,
    _build_persona_name_map,
    _clean_bullets,
    _clean_str,
    _extract_prompt_row_metadata,
    _extract_vn_from_prompt_rel_path,
    _find_session_id,
    _get_architecture_definition,
    _load_run_prompt_files,
    _parse_exact_block_headline_value,
    _persona_name_from_candidate,
    _persona_number_from_candidate,
    _prompt_copy_records_from_mongo,
    _replace_exact_copy_block,
    api_edit_prompt,
    api_export_on_image_copy,
    api_file_content,
    api_import_on_image_copy,
    api_run_prompt_copies,
    api_run_update_prompt_copies,
    build_product_doc_bootstrap_prompt,
    concept_ids_from_requirements,
    ensure_testimonial_headline,
    extract_persona_input_block,
    normalize_generated_copy,
    parse_json_object_from_text,
    parse_opencode_json_output,
    parse_opencode_session_id,
    parse_uniqueness_collisions,
)
from dashboard.backend.pipeline.subprocesses import (
    _append_opencode_queue_log,
    _opencode_queue_slot,
    append_run_log,
    browser_automation_timeout_seconds,
    parse_json_stdout,
    run_cmd,
)
from dashboard.backend.pipeline.hypothesis import (
    _background_reuse_keys,
    apply_background_reuse_locks,
    apply_visual_pattern_reuse_to_plan,
    collect_background_reuse_locks,
    collect_visual_pattern_reuse_locks,
    expand_plan_with_hypothesis,
    resolve_format_plan,
)
from dashboard.backend.pipeline.runs_db import (
    _build_backfill_manifest,
    _check_ownership,
    _dashboard_run_sort_key,
    _extract_backfill_batch,
    _mongo_run_has_dashboard_manifest,
    _mongo_run_to_manifest,
    _ts_to_iso,
    api_delete_prompt,
    api_delete_run,
    api_run,
    api_run_partial,
    api_runs,
    collect_backfill_result,
    enrich_manifest_for_dashboard,
    load_manifest_for_run,
    load_run_language_mode,
    merge_manifest,
    refresh_manifest_file_state,
)
from dashboard.backend.pipeline.generation import (
    _build_expected_output_path,
    _build_output_stem_from_prompt,
    _bundle_916_prompt_files_for_batches,
    _bundle_binary_file,
    _bundle_input_images,
    _bundle_text_file,
    _find_45_parent_for_prompt,
    _find_prompt_by_name,
    _gemini_generate,
    _latest_online_agent_for_user,
    _list_output_batches,
    _list_user_mongo_batches,
    _load_prompt_text_for_generation,
    _local_prompt_filename,
    _local_prompt_item,
    _log_llm_trace,
    _queue_local_chatgpt_job,
    _reserve_batch_name,
    _resolve_916_generation_for_run,
    _run_pipeline_background,
    _write_generation_prompt,
    api_batch_generate_images_45,
    api_batch_generate_images_916,
    api_batch_generate_images_both,
    api_regenerate_queued_images,
    api_run_execute,
    api_run_generate_916,
    api_run_generate_916_selected,
    api_run_generate_images_45,
    api_run_generate_images_916_from_45,
    call_google_gemini,
    call_opencode_compatible,
    call_opencode_repair_copy,
    extract_selected_ad_keys_from_45_prompts,
    filter_copy_json_for_selected_ads,
    gemini_debugger_args,
    generate_916_for_run,
    map_45_to_96_prompts,
    rerender_prompts_for_run,
    resolve_gemini_debugger_address,
    run_916_conversion_from_45_for_batch,
    run_chatgpt_generation,
    run_gemini_generation,
    validate_selected_45_prompts,
)
from dashboard.backend.pipeline.files import (
    _build_opencode_catalog_cached,
    _get_opencode_catalog,
    _get_user_from_request,
    _opencode_catalog_cache,
    _opencode_catalog_lock,
    _prompt_lookup_query,
    register_file_routes,
    _try_parse_json,
    api_defaults,
    api_delete_llm_traces,
    api_delete_llm_traces_by_files,
    api_google_models,
    api_input_prompt,
    api_llm_traces,
    api_opencode_catalog,
    api_product_doc,
    api_progress,
    api_prompt_file_content,
    api_run_images,
    api_run_prompt_content,
    api_run_prompts,
    api_save_input_prompt,
    api_save_product_doc,
    api_save_prompt_file_content,
    api_save_provider_config,
    api_save_run_prompt_content,
    coalesce_path,
    download_input_file,
    download_seed_file,
    list_seed_files,
    resolve_safe_path,
    save_upload,
    storage_info,
)
from dashboard.backend.pipeline.chrome_ops import (
    _chrome_process,
    api_kill_chrome,
    api_launch_visible_browser,
    api_stop_generation,
)
from dashboard.backend.pipeline.personas import (
    _extract_persona_slug_from_prompt_filename,
    _resolve_starting_prompt_path,
    parse_persona_number_from_prompt,
)

from dashboard.backend.db.settings import settings as app_settings, validate_production_settings
from dashboard.backend.auth.service import get_current_user_from_cookie
from dashboard.backend.agent.auth import is_agent_runtime_path

app = FastAPI(title="Ad Dashboard API", version="1.0.0")


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"status": "ok", "service": "ad-factory"}


@app.get("/api/version")
def api_version() -> dict[str, Any]:
    return {
        "commit": str(os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_COMMIT") or "unknown"),
        "branch": str(os.getenv("RENDER_GIT_BRANCH") or os.getenv("GIT_BRANCH") or "unknown"),
        "agent_protocol": 1,
        "artifact_schema": 3,
    }


@app.get("/api/readyz")
def api_readyz() -> dict[str, Any]:
    from dashboard.backend.db.client import get_sync_db

    get_sync_db().command("ping")
    return {"status": "ready", "mongodb": True}


app.add_middleware(
    CORSMiddleware,
    allow_origins=app_settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


PUBLIC_API_PREFIXES = ("/api/auth/", "/api/generic-config", "/api/invites/", "/api/public/")


@app.middleware("http")
async def auth_middleware(request: Request, call_next) -> Response:
    from dashboard.backend.control_plane_policy import is_render_content_route

    path = request.url.path
    is_agent_bearer = is_agent_runtime_path(path) and request.headers.get(
        "Authorization", ""
    ).startswith("Bearer ")
    if path.startswith("/api/") and not path.startswith(PUBLIC_API_PREFIXES) and not is_agent_bearer:
        user = await run_in_threadpool(
            get_current_user_from_cookie, request.cookies.get("session")
        )
        if user is not None:
            request.state.user = user
        elif app_settings.is_production:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    if is_render_content_route(request.method, request.url.path):
        return JSONResponse(
            {
                "detail": (
                    "Content operations are available only through the paired "
                    "localhost data plane"
                )
            },
            status_code=410,
        )
    response = await call_next(request)
    return response


@app.on_event("startup")
def startup() -> None:
    load_env_file(ENV_PATH)
    validate_production_settings()
    try:
        from dashboard.backend.db.indexes import create_indexes
        idx_result = create_indexes()
        created = sum(v for v in idx_result.values() if v > 0)
        failed = sum(1 for v in idx_result.values() if v < 0)
        if created or failed:
            print(f"[startup] MongoDB indexes: {created} created, {failed} failed")
    except Exception as e:
        msg = str(e)
        if app_settings.is_production:
            print(f"[startup] FATAL: MongoDB connection failed in production: {msg}", file=sys.stderr)
            sys.exit(1)
        print(f"[startup] MongoDB index init skipped (dev): {e}")


from dashboard.backend.routes import defaults, progress, runs, generate, batch, export_import, execute, chrome, traces

app.include_router(defaults.router)
app.include_router(progress.router)
app.include_router(runs.router)
app.include_router(generate.router)
app.include_router(batch.router)
app.include_router(export_import.router)
app.include_router(execute.router)
app.include_router(chrome.router)
app.include_router(traces.router)


@app.get("/api/extension/status")
def retired_extension_status() -> dict[str, Any]:
    return {
        "connected": False,
        "active_connections": 0,
        "disabled": True,
        "reason": "local_agent_required",
    }


@app.websocket("/api/extension/ws")
async def retired_extension_websocket(websocket: WebSocket) -> None:
    await websocket.close(code=1008, reason="Use the paired local agent")


from dashboard.backend.auth.routes import router as auth_router
from dashboard.backend.services.provider_routes import router as provider_router
from dashboard.backend.services.blob_routes import router as blob_router
from dashboard.backend.services.user_config_routes import router as user_config_router
from dashboard.backend.agent.routes import router as agent_router
from dashboard.backend.services.org_routes import router as org_router
from dashboard.backend.services.invite_routes import router as invite_router
from dashboard.backend.services.config_routes import router as config_router
from dashboard.backend.admin.admin_routes import router as admin_router

app.include_router(auth_router)
app.include_router(provider_router)
app.include_router(blob_router)
app.include_router(user_config_router)
app.include_router(agent_router)
app.include_router(org_router)
app.include_router(invite_router)
app.include_router(config_router)
app.include_router(admin_router)


@app.get("/api/generic-config")
def get_generic_config_public() -> dict[str, Any]:
    from dashboard.backend.services.user_config import get_generic_config
    return get_generic_config()


@app.get("/api/generic-config/{key}")
def get_generic_config_key_public(key: str) -> dict[str, Any]:
    from dashboard.backend.services.user_config import get_generic_config, CONFIG_KEYS
    if key not in CONFIG_KEYS:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Unknown config key: {key}")
    cfg = get_generic_config()
    return {"key": key, "value": cfg.get(key, "")}


register_file_routes(app)

from dashboard.backend.spa_static import mount_react_spa

mount_react_spa(app, ROOT)
