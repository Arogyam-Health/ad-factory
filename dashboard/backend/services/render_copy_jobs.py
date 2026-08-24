from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from dashboard.backend.agent.connections import agent_connections
from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import (
    COLL_PROMPT_DELIVERIES,
    COLL_PROMPTS,
    COLL_RENDER_COPY_JOBS,
    COLL_RUNS,
    COLL_ORG_MEMBERS,
)
from dashboard.backend.agent.service import reserve_run_number
from dashboard.backend.services.run_storage import display_batch_label
from dashboard.backend.control_plane_policy import validate_metadata_document
from dashboard.backend.services.prompt_delivery import (
    decrypt_prompt_bundle,
    encrypt_prompt_bundle,
)
from dashboard.backend.services.llm_trace import record_recent_llm_trace
from dashboard.backend.services.opencode_catalog import next_free_opencode_model
from dashboard.backend.services.provider_config import (
    get_materialized_provider_config,
)
from dashboard.backend.services.provider_relay import (
    ProviderRelayError,
    provider_relay,
)
from dashboard.backend.services.render_structured_copy import (
    ProviderCallError,
    generate_structured_prompt_bundle,
    provider_generate_callable,
)
from dashboard.backend.services.user_config import resolve_effective_config


_COPY_LEASE_SECONDS = 300
_DELIVERY_TTL_HOURS = 24
_TERMINAL_RETENTION_DAYS = 7
_LOCAL_PROVIDER_RETRY_SECONDS = 5
_TRANSIENT_RELAY_ERRORS = frozenset(
    {
        "local_provider_agent_offline",
        "local_provider_agent_disconnected",
        "provider_relay_expired",
    }
)
_worker_event = threading.Event()
_worker_lock = threading.Lock()
_worker_started = False


def allocate_render_copy_run(
    *,
    user_id: str,
    owner_type: str,
    owner_id: str,
) -> dict[str, Any]:
    if owner_type not in {"user", "org"} or not owner_id or len(owner_id) > 200:
        raise ValueError("Invalid Render copy run owner")
    db = get_sync_db()
    if owner_type == "user":
        if owner_id != user_id:
            raise ValueError("User owner does not match the authenticated user")
    elif db[COLL_ORG_MEMBERS].find_one(
        {"org_id": owner_id, "user_id": user_id, "status": "active"}
    ) is None:
        raise ValueError("Authenticated user is not an active organization member")
    now = time.time()
    doc = None
    for _ in range(8):
        run_number = reserve_run_number(
            owner_type, owner_id, "structured", user_id=user_id
        )
        candidate = {
            "run_id": "run_" + uuid.uuid4().hex,
            "user_id": user_id,
            "owner_type": owner_type,
            "owner_id": owner_id,
            "created_by_user_id": user_id,
            "agent_id": "",
            "device_id": "",
            "run_number": run_number,
            "display_batch": display_batch_label("structured", run_number),
            "flow_type": "structured",
            "flow_family": "structured",
            "status": "allocated",
            "created_at": now,
            "updated_at": now,
        }
        validate_metadata_document("runs", candidate)
        try:
            db[COLL_RUNS].insert_one(candidate)
            doc = candidate
            break
        except DuplicateKeyError:
            continue
    if doc is None:
        raise ValueError("Could not allocate a unique run number")
    return {
        key: doc[key]
        for key in (
            "run_id",
            "owner_type",
            "owner_id",
            "run_number",
            "display_batch",
            "flow_type",
            "status",
        )
    }


def validate_copy_settings(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Structured copy settings must be an object")
    allowed = {
        "selected_personas",
        "global_formats",
        "formats_by_persona",
        "multiplier",
        "language_mode",
        "provider",
        "model",
        "org_id",
        "batch_size",
        "share_background_across_personas",
        "reuse_backgrounds_from_run_id",
        "reuse_visual_patterns_from_run_id",
        "hypothesis",
        "selected_concept",
        "visual_archetypes_by_format",
    }
    if set(raw) - allowed:
        raise ValueError("Structured copy settings contain unsupported fields")
    personas = raw.get("selected_personas")
    formats = raw.get("global_formats")
    if (
        not isinstance(personas, list)
        or not personas
        or len(personas) > 50
        or any(not isinstance(value, int) or value < 1 for value in personas)
    ):
        raise ValueError("Structured copy personas are invalid")
    if (
        not isinstance(formats, list)
        or not formats
        or len(formats) > 5
        or any(str(value).upper() not in {"HERO", "BA", "TEST", "FEAT", "UGC"} for value in formats)
    ):
        raise ValueError("Structured copy formats are invalid")
    provider = str(raw.get("provider") or "").lower()
    if provider == "google":
        provider = "google_gemini"
    if provider not in {"opencode", "google_gemini"}:
        raise ValueError("Structured copy provider is invalid")
    model = str(raw.get("model") or "").strip()
    if not model or len(model) > 256:
        raise ValueError("Structured copy model is invalid")
    language_mode = str(raw.get("language_mode") or "EN").upper()
    if language_mode not in {"EN", "HI", "HINGLISH", "ALL", "BOTH"}:
        raise ValueError("Structured copy language mode is invalid")
    multiplier = int(raw.get("multiplier") or 1)
    if multiplier < 1 or multiplier > 20:
        raise ValueError("Structured copy multiplier is invalid")
    formats_by_persona = raw.get("formats_by_persona")
    if formats_by_persona is not None and not isinstance(formats_by_persona, dict):
        raise ValueError("Per-persona formats are invalid")
    formats_by_persona = formats_by_persona or {}
    if len(formats_by_persona) > 50:
        raise ValueError("Per-persona formats are invalid")
    for persona_key, persona_formats in formats_by_persona.items():
        if (
            not str(persona_key).isdigit()
            or not isinstance(persona_formats, list)
            or len(persona_formats) > 5
            or any(
                str(value).upper()
                not in {"HERO", "BA", "TEST", "FEAT", "UGC"}
                for value in persona_formats
            )
        ):
            raise ValueError("Per-persona formats are invalid")
    batch_size = int(raw.get("batch_size") or 10)
    if batch_size < 1 or batch_size > 500:
        raise ValueError("Structured copy batch size is invalid")
    share_background = raw.get("share_background_across_personas")
    if share_background is None:
        share_background = False
    if not isinstance(share_background, bool):
        raise ValueError("share_background_across_personas must be a boolean")
    reuse_backgrounds = str(raw.get("reuse_backgrounds_from_run_id") or "").strip()
    reuse_patterns = str(raw.get("reuse_visual_patterns_from_run_id") or "").strip()
    if len(reuse_backgrounds) > 80 or len(reuse_patterns) > 80:
        raise ValueError("Reuse run id is invalid")
    hypothesis = raw.get("hypothesis")
    if hypothesis is None:
        hypothesis = {"type": "none", "variant": ""}
    if not isinstance(hypothesis, dict):
        raise ValueError("Hypothesis settings are invalid")
    hyp_type = str(hypothesis.get("type") or "none").strip().lower()[:64]
    hyp_variant = str(hypothesis.get("variant") or "").strip()[:64]
    if not hyp_type:
        hyp_type = "none"
    selected_concept = str(raw.get("selected_concept") or "").strip()[:160]
    archetypes = raw.get("visual_archetypes_by_format")
    if archetypes is None:
        archetypes = {}
    if not isinstance(archetypes, dict) or len(archetypes) > 5:
        raise ValueError("Visual archetypes by format are invalid")
    visual_archetypes_by_format = {}
    for fmt, archetype_id in archetypes.items():
        normalized = str(fmt).upper()
        if normalized not in {"HERO", "BA", "TEST", "FEAT", "UGC"}:
            raise ValueError("Visual archetypes by format are invalid")
        ident = str(archetype_id or "").strip()
        if len(ident) > 80:
            raise ValueError("Visual archetypes by format are invalid")
        if ident:
            visual_archetypes_by_format[normalized] = ident
    return {
        "selected_personas": personas,
        "global_formats": [str(value).upper() for value in formats],
        "formats_by_persona": {
            str(key): [str(value).upper() for value in values]
            for key, values in formats_by_persona.items()
        },
        "multiplier": multiplier,
        "language_mode": language_mode,
        "provider": provider,
        "model": model,
        "org_id": str(raw.get("org_id") or ""),
        "batch_size": batch_size,
        "share_background_across_personas": share_background,
        "reuse_backgrounds_from_run_id": reuse_backgrounds,
        "reuse_visual_patterns_from_run_id": reuse_patterns,
        "hypothesis": {"type": hyp_type, "variant": hyp_variant},
        "selected_concept": selected_concept,
        "visual_archetypes_by_format": visual_archetypes_by_format,
    }


def enqueue_render_copy_job(
    *,
    run: dict[str, Any],
    user_id: str,
    settings: dict[str, Any],
    client_operation_id: str,
) -> dict[str, Any]:
    if not client_operation_id or len(client_operation_id) > 256:
        raise ValueError("Operation ID is required")
    clean = validate_copy_settings(settings)
    now = time.time()
    job = {
        "copy_job_id": "copy_" + uuid.uuid4().hex,
        "run_id": str(run["run_id"]),
        "user_id": user_id,
        "owner_type": str(run.get("owner_type") or "user"),
        "owner_id": str(run.get("owner_id") or user_id),
        "agent_id": str(run.get("agent_id") or ""),
        "device_id": str(run.get("device_id") or ""),
        "run_number": int(run.get("run_number") or 0),
        "settings": clean,
        "client_operation_id": client_operation_id,
        "status": "queued",
        "progress_code": "queued_on_render",
        "created_at": now,
        "updated_at": now,
        "lease_expires_at": None,
        "attempts": 0,
    }
    collection = get_sync_db()[COLL_RENDER_COPY_JOBS]
    try:
        collection.insert_one(job)
    except DuplicateKeyError:
        existing = collection.find_one(
            {
                "owner_type": job["owner_type"],
                "owner_id": job["owner_id"],
                "client_operation_id": client_operation_id,
            },
            {"_id": 0},
        )
        if existing is None:
            raise
        return existing
    get_sync_db()[COLL_RUNS].update_one(
        {"run_id": job["run_id"], "user_id": user_id},
        {
            "$set": {
                "status": "copy_queued",
                "copy_job_id": job["copy_job_id"],
                "updated_at": now,
            }
        },
    )
    wake_render_copy_worker()
    return job


def _claim_next_job() -> dict[str, Any] | None:
    now = time.time()
    return get_sync_db()[COLL_RENDER_COPY_JOBS].find_one_and_update(
        {
            "$or": [
                {
                    "status": "queued",
                    "$or": [
                        {"next_attempt_at": {"$exists": False}},
                        {"next_attempt_at": {"$lte": now}},
                    ],
                },
                {
                    "status": "running",
                    "lease_expires_at": {"$lte": now},
                },
            ]
        },
        {
            "$set": {
                "status": "running",
                "progress_code": "waiting_for_local_provider",
                "updated_at": now,
                "lease_expires_at": now + _COPY_LEASE_SECONDS,
            },
            "$inc": {"attempts": 1},
        },
        sort=[("created_at", 1)],
        return_document=ReturnDocument.AFTER,
    )


def _reuse_lock_keys(
    fmt: str,
    persona_no: int | None,
    visual_archetype: str,
    share_across_personas: bool,
) -> list[str]:
    fmt = str(fmt or "").strip().upper()
    persona = f"P{int(persona_no):02d}" if isinstance(persona_no, int) else ""
    arch = str(visual_archetype or "").strip()
    if share_across_personas:
        return [key for key in [f"{fmt}::{arch}" if arch else "", fmt] if key]
    return [
        key
        for key in [
            f"{fmt}::{persona}::{arch}" if persona and arch else "",
            f"{fmt}::{persona}" if persona else "",
        ]
        if key
    ]


def collect_copy_reuse_locks(user_id: str, settings: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Read previous-run prompt metadata from Mongo for background/pattern reuse."""
    background_run = str(settings.get("reuse_backgrounds_from_run_id") or "").strip()
    visual_run = str(settings.get("reuse_visual_patterns_from_run_id") or "").strip()
    if not background_run and not visual_run:
        return {"background": {}, "visual": {}}
    db = get_sync_db()
    background_locks: dict[str, dict[str, Any]] = {}
    visual_locks: dict[str, dict[str, Any]] = {}

    def prompts_for(run_id: str) -> list[dict[str, Any]]:
        ident = str(run_id or "").strip()
        if not ident:
            return []
        return list(
            db[COLL_PROMPTS].find(
                {"run_id": ident, "user_id": user_id},
                {"_id": 0},
            )
        )

    for prompt in prompts_for(str(settings.get("reuse_backgrounds_from_run_id") or "")):
        fmt = str(prompt.get("format") or "").strip().upper()
        persona_no = prompt.get("persona_number")
        persona_no = int(persona_no) if isinstance(persona_no, int) else None
        slot = str(prompt.get("background_id") or "").strip()
        seed = prompt.get("background_seed")
        if not fmt or not slot or not isinstance(seed, int):
            continue
        lock = {
            "background_slot": slot,
            "background_seed": seed,
            "background_reused_from_run_id": str(
                settings.get("reuse_backgrounds_from_run_id") or ""
            ),
        }
        archetype = str(prompt.get("visual_archetype") or "").strip()
        for key in _reuse_lock_keys(fmt, persona_no, archetype, False):
            background_locks.setdefault(key, lock)
        for key in _reuse_lock_keys(fmt, persona_no, archetype, True):
            background_locks.setdefault(key, lock)

    for prompt in prompts_for(str(settings.get("reuse_visual_patterns_from_run_id") or "")):
        fmt = str(prompt.get("format") or "").strip().upper()
        persona_no = prompt.get("persona_number")
        persona_no = int(persona_no) if isinstance(persona_no, int) else None
        archetype = str(prompt.get("visual_archetype") or "").strip()
        if not fmt or not archetype:
            continue
        lock = {
            "visual_archetype": archetype,
            "visual_pattern_reused_from_run_id": str(
                settings.get("reuse_visual_patterns_from_run_id") or ""
            ),
        }
        for key in _reuse_lock_keys(fmt, persona_no, archetype, False):
            visual_locks.setdefault(key, lock)
        for key in _reuse_lock_keys(fmt, persona_no, archetype, True):
            visual_locks.setdefault(key, lock)

    return {"background": background_locks, "visual": visual_locks}


def _complete_job(job: dict[str, Any], result: dict[str, Any]) -> None:
    db = get_sync_db()
    now = time.time()
    delivery_id = "dlv_" + uuid.uuid4().hex
    encrypted = encrypt_prompt_bundle(
        {
            "delivery_id": delivery_id,
            "run_id": job["run_id"],
            "run_number": job["run_number"],
            "owner_type": job["owner_type"],
            "owner_id": job["owner_id"],
            "prompts": result["prompts"],
        }
    )
    db[COLL_PROMPT_DELIVERIES].update_one(
        {"run_id": job["run_id"]},
        {
            "$set": {
                "delivery_id": delivery_id,
                "run_id": job["run_id"],
                "user_id": job["user_id"],
                "owner_type": job["owner_type"],
                "owner_id": job["owner_id"],
                "agent_id": job["agent_id"],
                "device_id": job["device_id"],
                **encrypted,
                "status": "pending",
                "created_at": now,
                "updated_at": now,
                "expires_at": datetime.now(timezone.utc)
                + timedelta(hours=_DELIVERY_TTL_HOURS),
            }
        },
        upsert=True,
    )
    projection = {
        key: result[key]
        for key in (
            "provider",
            "model",
            "duration_ms",
            "request_sha256",
            "response_sha256",
            "copy_sha256",
            "copy_count",
            "prompt_count",
            "prompt_ids",
            "repair_count",
            "batch_size",
        )
        if key in result
    }
    projection["delivery_id"] = delivery_id
    projection["delivery_status"] = "pending"
    settings = job.get("settings") if isinstance(job.get("settings"), dict) else {}
    for key in (
        "share_background_across_personas",
        "reuse_backgrounds_from_run_id",
        "reuse_visual_patterns_from_run_id",
        "hypothesis",
        "visual_archetypes_by_format",
    ):
        if key in settings:
            projection[key] = settings[key]
    if "batch_size" not in projection and "batch_size" in settings:
        projection["batch_size"] = settings["batch_size"]
    last_error = str(job.get("last_error") or "")
    if last_error:
        projection["last_error"] = last_error[:2000]
        projection["fallback_model"] = str(job.get("fallback_model") or "")[:256]
    db[COLL_PROMPTS].delete_many(
        {"run_id": job["run_id"], "user_id": job["user_id"]}
    )
    prompt_docs = [
        {
            "prompt_id": str(prompt["prompt_id"]),
            "run_id": job["run_id"],
            "user_id": job["user_id"],
            "owner_type": job["owner_type"],
            "owner_id": job["owner_id"],
            "sha256": str(prompt["sha256"]),
            "format": str(prompt["format"]),
            "persona": str(prompt["persona_name"]),
            "persona_number": int(prompt.get("persona_number") or 0) or None,
            "language": str(prompt["language"]),
            "aspect_ratio": str(prompt["aspect_ratio"]),
            "visual_archetype": str(prompt.get("visual_archetype") or ""),
            "background_id": str(prompt.get("background_id") or ""),
            "background_seed": prompt.get("background_seed"),
            "status": "awaiting_local_delivery",
            "created_at": now,
            "updated_at": now,
        }
        for prompt in (result.get("prompts") or [])
    ]
    if not prompt_docs:
        raise RuntimeError("Copy generation produced no prompts")
    db[COLL_PROMPTS].insert_many(prompt_docs)
    db[COLL_RUNS].update_one(
        {"run_id": job["run_id"], "user_id": job["user_id"]},
        {
            "$set": {
                "status": "copy_ready",
                "copy_generation": projection,
                "prompt_count": int(result["prompt_count"]),
                "updated_at": now,
            }
        },
    )
    db[COLL_RENDER_COPY_JOBS].update_one(
        {"copy_job_id": job["copy_job_id"]},
        {
            "$set": {
                "status": "completed",
                "progress_code": "awaiting_local_delivery",
                "completed_at": now,
                "updated_at": now,
                "lease_expires_at": None,
                "purge_at": datetime.now(timezone.utc)
                + timedelta(days=_TERMINAL_RETENTION_DAYS),
            }
        },
    )


def _fail_job(
    job: dict[str, Any],
    *,
    error_code: str,
    provider: str = "",
    model: str = "",
    duration_ms: int = 0,
    http_status: int | None = None,
    error_detail: str = "",
    trace_persistence_error: str = "",
    last_error: str = "",
) -> None:
    now = time.time()
    sticky = str(last_error or error_detail or error_code)[:2000]
    safe_error = {
        "error_code": error_code,
        "provider": provider,
        "model": model,
        "duration_ms": duration_ms,
        "http_status": http_status,
        "error_detail": str(error_detail or "")[:2000],
        "trace_persistence_error": str(trace_persistence_error or "")[:100],
        "last_error": sticky,
    }
    get_sync_db()[COLL_RENDER_COPY_JOBS].update_one(
        {"copy_job_id": job["copy_job_id"]},
        {
            "$set": {
                "status": "failed",
                "progress_code": error_code,
                "error": safe_error,
                "last_error": sticky,
                "completed_at": now,
                "updated_at": now,
                "lease_expires_at": None,
                "purge_at": datetime.now(timezone.utc)
                + timedelta(days=_TERMINAL_RETENTION_DAYS),
            }
        },
    )
    get_sync_db()[COLL_RUNS].update_one(
        {"run_id": job["run_id"], "user_id": job["user_id"]},
        {
            "$set": {
                "status": "failed",
                "copy_generation": {"status": "failed", **safe_error},
                "updated_at": now,
            }
        },
    )
    _record_job_failure_trace(
        job,
        error_code=error_code,
        provider=provider,
        model=model,
        duration_ms=duration_ms,
        http_status=http_status,
        error_detail=sticky,
    )


def _persist_copy_last_error(
    job: dict[str, Any],
    last_error: str,
    fallback_model: str = "",
) -> None:
    now = time.time()
    sticky = str(last_error or "")[:2000]
    fallback = str(fallback_model or "")[:256]
    get_sync_db()[COLL_RENDER_COPY_JOBS].update_one(
        {"copy_job_id": job["copy_job_id"]},
        {
            "$set": {
                "last_error": sticky,
                "fallback_model": fallback,
                "fallback_attempted": True,
                "progress_code": "retrying_free_model" if fallback else "provider_error",
                "updated_at": now,
            }
        },
    )
    get_sync_db()[COLL_RUNS].update_one(
        {"run_id": job["run_id"], "user_id": job["user_id"]},
        {
            "$set": {
                "copy_generation.last_error": sticky,
                "copy_generation.fallback_model": fallback,
                "updated_at": now,
            }
        },
    )


def _defer_job_for_local_agent(
    job: dict[str, Any],
    error_code: str,
) -> None:
    now = time.time()
    get_sync_db()[COLL_RENDER_COPY_JOBS].update_one(
        {
            "copy_job_id": job["copy_job_id"],
            "status": "running",
        },
        {
            "$set": {
                "status": "queued",
                "progress_code": "waiting_for_local_provider",
                "last_relay_error": str(error_code)[:100],
                "next_attempt_at": now + _LOCAL_PROVIDER_RETRY_SECONDS,
                "lease_expires_at": None,
                "updated_at": now,
            }
        },
    )
    get_sync_db()[COLL_RUNS].update_one(
        {"run_id": job["run_id"], "user_id": job["user_id"]},
        {
            "$set": {
                "status": "copy_queued",
                "updated_at": now,
            }
        },
    )


def resume_user_provider_jobs(user_id: str) -> int:
    db = get_sync_db()
    jobs = list(
        db[COLL_RENDER_COPY_JOBS].find(
            {
                "user_id": user_id,
                "status": "failed",
                "progress_code": {"$in": sorted(_TRANSIENT_RELAY_ERRORS)},
            },
            {"_id": 0, "copy_job_id": 1, "run_id": 1},
        )
    )
    now = time.time()
    resumed = 0
    for job in jobs:
        result = db[COLL_RENDER_COPY_JOBS].update_one(
            {
                "copy_job_id": str(job.get("copy_job_id") or ""),
                "user_id": user_id,
                "status": "failed",
                "progress_code": {
                    "$in": sorted(_TRANSIENT_RELAY_ERRORS)
                },
            },
            {
                "$set": {
                    "status": "queued",
                    "progress_code": "waiting_for_local_provider",
                    "next_attempt_at": now,
                    "lease_expires_at": None,
                    "updated_at": now,
                },
                "$unset": {
                    "error": "",
                    "completed_at": "",
                    "purge_at": "",
                },
            },
        )
        if not result.modified_count:
            continue
        resumed += 1
        db[COLL_RUNS].update_one(
            {"run_id": str(job.get("run_id") or ""), "user_id": user_id},
            {
                "$set": {
                    "status": "copy_queued",
                    "updated_at": now,
                },
                "$unset": {"copy_generation": ""},
            },
        )
    if resumed:
        wake_render_copy_worker()
    return resumed


def _copy_trace_org_id(job: dict[str, Any]) -> str:
    if str(job.get("owner_type") or "") == "org":
        return str(job.get("owner_id") or "")
    settings = job.get("settings") if isinstance(job.get("settings"), dict) else {}
    return str(settings.get("org_id") or "")


def _record_job_failure_trace(
    job: dict[str, Any],
    *,
    error_code: str,
    provider: str = "",
    model: str = "",
    duration_ms: int = 0,
    http_status: int | None = None,
    error_detail: str = "",
) -> None:
    try:
        record_recent_llm_trace(
            user_id=str(job["user_id"]),
            run_id=str(job["run_id"]),
            batch=f"v{int(job.get('run_number') or 0)}",
            org_id=_copy_trace_org_id(job),
            event={
                "provider": provider,
                "model": model,
                "api_model": (
                    model.removeprefix("opencode/")
                    if str(provider or "") == "opencode"
                    else model
                ),
                "endpoint": "",
                "label": "copy",
                "status": "failed",
                "http_status": http_status,
                "duration_ms": duration_ms,
                "error_code": error_code,
                "error_detail": error_detail,
                "request": {
                    "task": "Generate structured advertising copy as JSON",
                    "planned_ad_count": 0,
                    "languages": [],
                    "request_sha256": "",
                },
                "response": {"usage": {}},
            },
        )
    except Exception:
        return


def _record_provider_failure_trace(
    job: dict[str, Any],
    exc: ProviderCallError,
    provider_config: dict[str, str],
) -> str:
    api_url = str(provider_config.get("api_url") or "").rstrip("/")
    endpoint = (
        f"{api_url}/chat/completions"
        if exc.provider == "opencode" and api_url
        else ""
    )
    try:
        record_recent_llm_trace(
            user_id=str(job["user_id"]),
            run_id=str(job["run_id"]),
            batch=f"v{int(job['run_number'])}",
            org_id=_copy_trace_org_id(job),
            event={
                "provider": exc.provider,
                "model": exc.model,
                "api_model": (
                    exc.model.removeprefix("opencode/")
                    if exc.provider == "opencode"
                    else exc.model
                ),
                "endpoint": endpoint,
                "label": "copy",
                "status": "failed",
                "http_status": exc.http_status,
                "duration_ms": exc.duration_ms,
                "error_code": exc.code,
                "error_detail": exc.error_detail,
                "request": {
                    "task": "Generate structured advertising copy as JSON",
                    "planned_ad_count": 0,
                    "languages": [],
                    "request_sha256": "",
                },
                "response": {"usage": {}},
            },
        )
        return ""
    except Exception as trace_exc:
        return type(trace_exc).__name__


def process_next_render_copy_job() -> bool:
    job = _claim_next_job()
    if job is None:
        return False
    settings = job["settings"]
    provider_config: dict[str, str] | None = None

    def relay_transport(payload: dict[str, Any]) -> dict[str, Any]:
        get_sync_db()[COLL_RENDER_COPY_JOBS].update_one(
            {"copy_job_id": job["copy_job_id"]},
            {
                "$set": {
                    "progress_code": "calling_provider_local",
                    "updated_at": time.time(),
                }
            },
        )
        try:
            return provider_relay.invoke(
                user_id=str(job["user_id"]),
                payload=payload,
                connections=agent_connections,
            )
        except ProviderRelayError as relay_exc:
            raise ProviderCallError(
                code=relay_exc.code,
                provider=str(settings["provider"]),
                model=str(settings["model"]),
                duration_ms=0,
                error_detail=relay_exc.code,
            ) from relay_exc

    def invoke(provider_name: str, provider_model: str, config: dict[str, str]) -> dict[str, Any]:
        return generate_structured_prompt_bundle(
            run_id=str(job["run_id"]),
            run_number=int(job["run_number"]),
            settings=settings,
            effective_config=resolve_effective_config(
                str(job["user_id"]),
                str(settings.get("org_id") or "") or None,
            ),
            provider_name=provider_name,
            provider_model=provider_model,
            reuse_locks=collect_copy_reuse_locks(str(job["user_id"]), settings),
            generate=provider_generate_callable(
                provider_name,
                provider_model,
                config,
                transport=relay_transport,
                trace_callback=lambda event: record_recent_llm_trace(
                    user_id=str(job["user_id"]),
                    run_id=str(job["run_id"]),
                    batch=f"v{int(job['run_number'])}",
                    org_id=_copy_trace_org_id(job),
                    event=event,
                ),
            ),
        )

    def finish(result: dict[str, Any]) -> bool:
        current = get_sync_db()[COLL_RENDER_COPY_JOBS].find_one(
            {"copy_job_id": job["copy_job_id"]},
            {"_id": 0, "status": 1},
        )
        if current and current.get("status") == "canceled":
            return True
        _complete_job(job, result)
        return True

    try:
        provider_config = get_materialized_provider_config(
            str(job["user_id"]),
            str(settings["provider"]),
        )
        if provider_config is None:
            raise ValueError("Provider configuration is unavailable")
        return finish(
            invoke(
                str(settings["provider"]),
                str(settings["model"]),
                provider_config,
            )
        )
    except ProviderCallError as exc:
        if exc.code in _TRANSIENT_RELAY_ERRORS:
            _defer_job_for_local_agent(job, exc.code)
            return True
        trace_error = exc.trace_persistence_error
        if not exc.trace_persisted:
            trace_error = _record_provider_failure_trace(
                job,
                exc,
                provider_config or {},
            )
        fallback_model = next_free_opencode_model(
            str(exc.model or settings.get("model") or "")
        )
        if job.get("fallback_attempted") or not fallback_model:
            _fail_job(
                job,
                error_code=exc.code,
                provider=exc.provider,
                model=exc.model,
                duration_ms=exc.duration_ms,
                http_status=exc.http_status,
                error_detail=exc.error_detail,
                trace_persistence_error=trace_error,
                last_error=str(exc.error_detail or exc.code),
            )
            return True
        last_error = (
            f"Provider error: {exc.error_detail or exc.code}. "
            f"Falling back to {fallback_model}."
        )
        job["last_error"] = last_error
        job["fallback_model"] = fallback_model
        job["fallback_attempted"] = True
        _persist_copy_last_error(job, last_error, fallback_model)
        fallback_config = get_materialized_provider_config(
            str(job["user_id"]),
            "opencode",
        ) or (provider_config if str(settings.get("provider") or "") == "opencode" else None)
        if fallback_config is None:
            _fail_job(
                job,
                error_code=exc.code,
                provider=exc.provider,
                model=exc.model,
                duration_ms=exc.duration_ms,
                http_status=exc.http_status,
                error_detail=f"{last_error} No OpenCode credentials for fallback.",
                trace_persistence_error=trace_error,
                last_error=f"{last_error} No OpenCode credentials for fallback.",
            )
            return True
        try:
            return finish(invoke("opencode", fallback_model, fallback_config))
        except ProviderCallError as fallback_exc:
            if fallback_exc.code in _TRANSIENT_RELAY_ERRORS:
                _defer_job_for_local_agent(job, fallback_exc.code)
                return True
            fallback_trace = fallback_exc.trace_persistence_error
            if not fallback_exc.trace_persisted:
                fallback_trace = _record_provider_failure_trace(
                    job,
                    fallback_exc,
                    fallback_config,
                )
            combined = (
                f"{last_error} Fallback also failed: "
                f"{fallback_exc.error_detail or fallback_exc.code}."
            )
            _fail_job(
                job,
                error_code=fallback_exc.code,
                provider=fallback_exc.provider,
                model=fallback_exc.model,
                duration_ms=fallback_exc.duration_ms,
                http_status=fallback_exc.http_status,
                error_detail=combined,
                trace_persistence_error=fallback_trace or trace_error,
                last_error=combined,
            )
            return True
    except ValueError as exc:
        _fail_job(
            job,
            error_code="copy_configuration_invalid",
            provider=str(settings.get("provider") or ""),
            model=str(settings.get("model") or ""),
            error_detail=str(exc),
            last_error=str(exc),
        )
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}".strip()[:2000]
        if "No background variants found" in detail or "Background id not found" in detail:
            _fail_job(
                job,
                error_code="copy_configuration_invalid",
                provider=str(settings.get("provider") or ""),
                model=str(settings.get("model") or ""),
                error_detail=detail,
                last_error=detail,
            )
            return True
        fallback_model = next_free_opencode_model(
            str(settings.get("model") or "")
        )
        if job.get("fallback_attempted") or not fallback_model:
            _fail_job(
                job,
                error_code="copy_generation_failed",
                provider=str(settings.get("provider") or ""),
                model=str(settings.get("model") or ""),
                error_detail=detail,
                last_error=detail,
            )
            return True
        last_error = (
            f"Provider error: {detail}. Falling back to {fallback_model}."
        )
        job["last_error"] = last_error
        job["fallback_model"] = fallback_model
        job["fallback_attempted"] = True
        _persist_copy_last_error(job, last_error, fallback_model)
        fallback_config = get_materialized_provider_config(
            str(job["user_id"]),
            "opencode",
        ) or (provider_config if str(settings.get("provider") or "") == "opencode" else None)
        if fallback_config is None:
            _fail_job(
                job,
                error_code="copy_generation_failed",
                provider=str(settings.get("provider") or ""),
                model=str(settings.get("model") or ""),
                error_detail=f"{last_error} No OpenCode credentials for fallback.",
                last_error=f"{last_error} No OpenCode credentials for fallback.",
            )
            return True
        try:
            return finish(invoke("opencode", fallback_model, fallback_config))
        except ProviderCallError as fallback_exc:
            if fallback_exc.code in _TRANSIENT_RELAY_ERRORS:
                _defer_job_for_local_agent(job, fallback_exc.code)
                return True
            combined = (
                f"{last_error} Fallback also failed: "
                f"{fallback_exc.error_detail or fallback_exc.code}."
            )
            _fail_job(
                job,
                error_code=fallback_exc.code,
                provider=fallback_exc.provider,
                model=fallback_exc.model,
                duration_ms=fallback_exc.duration_ms,
                http_status=fallback_exc.http_status,
                error_detail=combined,
                last_error=combined,
            )
            return True
        except Exception as fallback_exc:
            combined = (
                f"{last_error} Fallback also failed: "
                f"{type(fallback_exc).__name__}: {fallback_exc}."
            )
            _fail_job(
                job,
                error_code="copy_generation_failed",
                provider="opencode",
                model=fallback_model,
                error_detail=combined,
                last_error=combined,
            )
            return True
    return True


def _worker_loop() -> None:
    while True:
        processed = False
        try:
            while process_next_render_copy_job():
                processed = True
        except Exception:
            pass
        _worker_event.wait(1 if processed else 5)
        _worker_event.clear()


def start_render_copy_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        threading.Thread(
            target=_worker_loop,
            name="render-copy-worker",
            daemon=True,
        ).start()
        _worker_started = True


def wake_render_copy_worker() -> None:
    start_render_copy_worker()
    _worker_event.set()


def copy_job_status(copy_job_id: str, user_id: str) -> dict[str, Any] | None:
    return get_sync_db()[COLL_RENDER_COPY_JOBS].find_one(
        {"copy_job_id": copy_job_id, "user_id": user_id},
        {
            "_id": 0,
            "copy_job_id": 1,
            "run_id": 1,
            "status": 1,
            "progress_code": 1,
            "error": 1,
            "last_error": 1,
            "fallback_model": 1,
            "created_at": 1,
            "updated_at": 1,
            "completed_at": 1,
        },
    )


def cancel_render_copy_run(run_id: str, user_id: str) -> dict[str, Any] | None:
    now = time.time()
    job = get_sync_db()[COLL_RENDER_COPY_JOBS].find_one(
        {
            "run_id": run_id,
            "user_id": user_id,
            "status": {"$in": ["queued", "running", "copy_queued"]},
        },
        {"_id": 0, "copy_job_id": 1},
        sort=[("created_at", -1)],
    )
    if job is None:
        return None
    get_sync_db()[COLL_RENDER_COPY_JOBS].update_one(
        {"copy_job_id": job["copy_job_id"], "user_id": user_id},
        {
            "$set": {
                "status": "canceled",
                "progress_code": "user_canceled",
                "completed_at": now,
                "updated_at": now,
                "lease_expires_at": None,
                "purge_at": datetime.now(timezone.utc)
                + timedelta(days=_TERMINAL_RETENTION_DAYS),
            }
        },
    )
    get_sync_db()[COLL_RUNS].update_one(
        {"run_id": run_id, "user_id": user_id},
        {"$set": {"status": "canceled", "updated_at": now}},
    )
    return {"status": "canceled", "run_id": run_id}


def poll_prompt_deliveries(agent: dict[str, Any]) -> list[dict[str, Any]]:
    db = get_sync_db()
    for _ in range(3):
        claimed = db[COLL_PROMPT_DELIVERIES].find_one_and_update(
            {
                "user_id": str(agent["user_id"]),
                "status": "pending",
                "agent_id": "",
                "device_id": "",
            },
            {
                "$set": {
                    "agent_id": str(agent["agent_id"]),
                    "device_id": str(agent.get("device_id") or ""),
                    "updated_at": time.time(),
                }
            },
            sort=[("created_at", 1)],
            return_document=ReturnDocument.AFTER,
        )
        if claimed is None:
            break
        db[COLL_RUNS].update_one(
            {
                "run_id": str(claimed["run_id"]),
                "user_id": str(agent["user_id"]),
                "agent_id": "",
                "device_id": "",
            },
            {
                "$set": {
                    "agent_id": str(agent["agent_id"]),
                    "device_id": str(agent.get("device_id") or ""),
                    "updated_at": time.time(),
                }
            },
        )
        db[COLL_RENDER_COPY_JOBS].update_one(
            {"run_id": str(claimed["run_id"]), "user_id": str(agent["user_id"])},
            {
                "$set": {
                    "agent_id": str(agent["agent_id"]),
                    "device_id": str(agent.get("device_id") or ""),
                }
            },
        )
    docs = db[COLL_PROMPT_DELIVERIES].find(
        {
            "user_id": str(agent["user_id"]),
            "agent_id": str(agent["agent_id"]),
            "device_id": str(agent.get("device_id") or ""),
            "status": "pending",
        }
    ).sort("created_at", 1).limit(3)
    deliveries = []
    for doc in docs:
        bundle = decrypt_prompt_bundle(doc)
        deliveries.append(
            {
                "delivery_id": str(doc["delivery_id"]),
                "run_id": str(doc["run_id"]),
                "plaintext_sha256": str(doc["plaintext_sha256"]),
                "bundle": bundle,
            }
        )
    return deliveries


def acknowledge_prompt_delivery(
    delivery_id: str,
    agent: dict[str, Any],
    *,
    prompt_ids: list[str],
) -> dict[str, Any]:
    db = get_sync_db()
    scope = {
        "delivery_id": delivery_id,
        "user_id": str(agent["user_id"]),
        "agent_id": str(agent["agent_id"]),
        "device_id": str(agent.get("device_id") or ""),
    }
    delivery = db[COLL_PROMPT_DELIVERIES].find_one(scope)
    if delivery is None:
        run = db[COLL_RUNS].find_one(
            {
                "user_id": str(agent["user_id"]),
                "copy_generation.acknowledged_delivery_id": delivery_id,
            },
            {"_id": 0, "run_id": 1},
        )
        if run:
            return {"status": "already_acknowledged", "run_id": run["run_id"]}
        raise ValueError("Prompt delivery was not found")
    expected = decrypt_prompt_bundle(delivery)
    expected_ids = [
        str(item.get("prompt_id") or "")
        for item in expected.get("prompts", [])
        if isinstance(item, dict)
    ]
    if prompt_ids != expected_ids:
        raise ValueError("Prompt delivery acknowledgement does not match")
    now = time.time()
    db[COLL_RUNS].update_one(
        {
            "run_id": str(delivery["run_id"]),
            "user_id": str(agent["user_id"]),
        },
        {
            "$set": {
                "agent_id": str(agent["agent_id"]),
                "device_id": str(agent.get("device_id") or ""),
                "status": "copy_completed",
                "copy_generation.delivery_status": "delivered",
                "copy_generation.acknowledged_delivery_id": delivery_id,
                "copy_generation.delivered_at": now,
                "updated_at": now,
            }
        },
    )
    db[COLL_PROMPT_DELIVERIES].delete_one(scope)
    db[COLL_PROMPTS].update_many(
        {
            "run_id": str(delivery["run_id"]),
            "user_id": str(agent["user_id"]),
        },
        {"$set": {"status": "available_local", "updated_at": now}},
    )
    return {
        "status": "acknowledged",
        "run_id": str(delivery["run_id"]),
    }
