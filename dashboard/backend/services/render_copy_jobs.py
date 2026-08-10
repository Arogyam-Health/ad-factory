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
from dashboard.backend.control_plane_policy import validate_metadata_document
from dashboard.backend.services.prompt_delivery import (
    decrypt_prompt_bundle,
    encrypt_prompt_bundle,
)
from dashboard.backend.services.llm_trace import record_recent_llm_trace
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
    run_number = reserve_run_number(owner_type, owner_id)
    now = time.time()
    doc = {
        "run_id": "run_" + uuid.uuid4().hex,
        "user_id": user_id,
        "owner_type": owner_type,
        "owner_id": owner_id,
        "created_by_user_id": user_id,
        "agent_id": "",
        "device_id": "",
        "run_number": run_number,
        "display_batch": f"v{run_number}",
        "flow_type": "structured",
        "status": "allocated",
        "created_at": now,
        "updated_at": now,
    }
    validate_metadata_document("runs", doc)
    db[COLL_RUNS].insert_one(doc)
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
        )
    }
    projection["delivery_id"] = delivery_id
    projection["delivery_status"] = "pending"
    db[COLL_PROMPTS].delete_many(
        {"run_id": job["run_id"], "user_id": job["user_id"]}
    )
    db[COLL_PROMPTS].insert_many(
        [
            {
                "prompt_id": str(prompt["prompt_id"]),
                "run_id": job["run_id"],
                "user_id": job["user_id"],
                "owner_type": job["owner_type"],
                "owner_id": job["owner_id"],
                "sha256": str(prompt["sha256"]),
                "format": str(prompt["format"]),
                "persona": str(prompt["persona_name"]),
                "language": str(prompt["language"]),
                "aspect_ratio": str(prompt["aspect_ratio"]),
                "status": "awaiting_local_delivery",
                "created_at": now,
                "updated_at": now,
            }
            for prompt in result["prompts"]
        ]
    )
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
) -> None:
    now = time.time()
    safe_error = {
        "error_code": error_code,
        "provider": provider,
        "model": model,
        "duration_ms": duration_ms,
        "http_status": http_status,
        "error_detail": str(error_detail or "")[:2000],
        "trace_persistence_error": str(trace_persistence_error or "")[:100],
    }
    get_sync_db()[COLL_RENDER_COPY_JOBS].update_one(
        {"copy_job_id": job["copy_job_id"]},
        {
            "$set": {
                "status": "failed",
                "progress_code": error_code,
                "error": safe_error,
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
    try:
        provider_config = get_materialized_provider_config(
            str(job["user_id"]),
            str(settings["provider"]),
        )
        if provider_config is None:
            raise ValueError("Provider configuration is unavailable")

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

        result = generate_structured_prompt_bundle(
            run_id=str(job["run_id"]),
            run_number=int(job["run_number"]),
            settings=settings,
            effective_config=resolve_effective_config(
                str(job["user_id"]),
                str(settings.get("org_id") or "") or None,
            ),
            provider_name=str(settings["provider"]),
            provider_model=str(settings["model"]),
            generate=provider_generate_callable(
                str(settings["provider"]),
                str(settings["model"]),
                provider_config,
                transport=relay_transport,
                trace_callback=lambda event: record_recent_llm_trace(
                    user_id=str(job["user_id"]),
                    run_id=str(job["run_id"]),
                    batch=f"v{int(job['run_number'])}",
                    event=event,
                ),
            ),
        )
        current = get_sync_db()[COLL_RENDER_COPY_JOBS].find_one(
            {"copy_job_id": job["copy_job_id"]},
            {"_id": 0, "status": 1},
        )
        if current and current.get("status") == "canceled":
            return True
        _complete_job(job, result)
    except ProviderCallError as exc:
        if exc.code in _TRANSIENT_RELAY_ERRORS:
            _defer_job_for_local_agent(job, exc.code)
            return True
        trace_error = exc.trace_persistence_error
        if not exc.trace_persisted:
            trace_error = _record_provider_failure_trace(
                job,
                exc,
                provider_config,
            )
        _fail_job(
            job,
            error_code=exc.code,
            provider=exc.provider,
            model=exc.model,
            duration_ms=exc.duration_ms,
            http_status=exc.http_status,
            error_detail=exc.error_detail,
            trace_persistence_error=trace_error,
        )
    except ValueError:
        _fail_job(
            job,
            error_code="copy_configuration_invalid",
            provider=str(settings.get("provider") or ""),
            model=str(settings.get("model") or ""),
        )
    except Exception:
        _fail_job(
            job,
            error_code="copy_generation_failed",
            provider=str(settings.get("provider") or ""),
            model=str(settings.get("model") or ""),
        )
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
            "status": {"$in": ["queued", "running"]},
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
