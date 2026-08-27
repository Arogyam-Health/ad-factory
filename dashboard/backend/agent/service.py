from __future__ import annotations

import json
import hashlib
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from dashboard.backend.db.client import get_sync_db
from dashboard.backend.control_plane_policy import validate_metadata_document
from dashboard.backend.db.collections import (
    COLL_AGENTS,
    COLL_AGENT_JOBS,
    COLL_AGENT_PAIRINGS,
    COLL_ORG_MEMBERS,
    COLL_RUNS,
)
from dashboard.backend.security.crypto import generate_token, hash_token
from dashboard.backend.services.run_storage import (
    display_batch_label,
    numbering_scope,
    reserve_run_number,
)
from pymongo.errors import DuplicateKeyError


PAIRING_SCOPES = frozenset(
    {
        "manifest:read",
        "content:read",
        "assets:write",
        "documents:write",
        "prompts:write",
        "runs:execute",
        "outputs:write",
        "revisions:write",
        "delete",
    }
)
_DEVICE_ID_RE = re.compile(r"^dev_[a-f0-9]{32}$")
_CHALLENGE_ID_RE = re.compile(r"^pch_[A-Za-z0-9_-]{16,80}$")
_PAIRING_CHALLENGE_TTL_SECONDS = 600
_RUN_SETTING_KEYS = frozenset(
    {
        "ad_multiplier",
        "batch_size",
        "engine",
        "generate_916",
        "global_formats",
        "headless",
        "hypothesis_type",
        "hypothesis_variant",
        "language_mode",
        "model",
        "provider",
        "selected_personas",
        "server_type",
        "share_background_across_personas",
    }
)
_JOB_PARAMETER_KEYS = frozenset(
    {
        "engine",
        "mode",
        "count",
        "manifest_version",
        "config_version_id",
        "prompt_version_id",
        "resource_version",
        "upload_set_version",
        "output_version",
        "product_asset_ids",
    }
)
_PRODUCT_ASSET_ID_LIMIT = 48
_JOB_TOP_LEVEL_KEYS = frozenset(
    {
        "job_id",
        "agent_id",
        "device_id",
        "user_id",
        "owner_type",
        "owner_id",
        "run_id",
        "job_type",
        "command",
        "parameters",
        "client_operation_id",
        "status",
        "progress_code",
        "error_code",
        "error_message",
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
        "lease_expires_at",
        "claim_id",
        "fence",
        "terminal_event_id",
        "purge_at",
        "cancel_requested_at",
    }
)
_JOB_STATUSES = frozenset(
    {"pending", "running", "cancel_requested", "completed", "failed", "canceled"}
)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")
_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_TERMINAL_RETENTION_DAYS = 7


def _valid_device_id(device_id: str) -> bool:
    return bool(_DEVICE_ID_RE.fullmatch(str(device_id or "")))


def _bounded_identifier(value: Any, field: str, *, required: bool = True) -> str:
    text = str(value or "")
    if (required and not text) or (text and not _ID_RE.fullmatch(text)):
        raise ValueError(f"Invalid {field}")
    return text


def _safe_parameter_string(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError("Job parameter string is out of bounds")
    lowered_value = value.lower()
    if (
        "://" in lowered_value
        or lowered_value.startswith(("data:", "file:", "/", "\\\\"))
        or _WINDOWS_ABSOLUTE_RE.match(value)
        or "\n" in value
        or "\r" in value
    ):
        raise ValueError("Job parameters contain prohibited values")
    return value


def _safe_parameter_value(key: str, value: Any) -> Any:
    if key == "product_asset_ids":
        if not isinstance(value, list) or not (1 <= len(value) <= _PRODUCT_ASSET_ID_LIMIT):
            raise ValueError("Job parameters must be bounded metadata")
        return [_safe_parameter_string(item) for item in value]
    if isinstance(value, bool) or value is None:
        raise ValueError("Job parameters must be bounded metadata")
    if isinstance(value, int):
        if not 0 <= value <= 10_000:
            raise ValueError("Job parameter integer is out of bounds")
        return value
    return _safe_parameter_string(value)


def validate_job_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    """Return a bounded metadata-only job document or reject it."""
    if not isinstance(envelope, dict) or set(envelope) - _JOB_TOP_LEVEL_KEYS:
        raise ValueError("Job envelope contains unsupported fields")
    required = {
        "job_id",
        "agent_id",
        "device_id",
        "user_id",
        "owner_type",
        "owner_id",
        "run_id",
        "job_type",
        "command",
        "parameters",
        "client_operation_id",
        "status",
        "progress_code",
        "created_at",
        "updated_at",
        "fence",
    }
    if required - set(envelope):
        raise ValueError("Job envelope is incomplete")
    clean = dict(envelope)
    for field in (
        "job_id",
        "agent_id",
        "user_id",
        "owner_id",
        "run_id",
        "job_type",
        "command",
        "client_operation_id",
    ):
        clean[field] = _bounded_identifier(clean.get(field), field)
    if not _valid_device_id(str(clean.get("device_id") or "")):
        raise ValueError("Invalid device_id")
    if clean.get("owner_type") not in {"user", "org"}:
        raise ValueError("Invalid owner_type")
    if clean.get("status") not in _JOB_STATUSES:
        raise ValueError("Invalid job status")
    for field in ("progress_code", "error_code"):
        value = clean.get(field)
        if value is not None and (not isinstance(value, str) or not _CODE_RE.fullmatch(value)):
            raise ValueError(f"Invalid {field}")
    error_message = clean.get("error_message")
    if error_message is not None and (
        not isinstance(error_message, str)
        or len(error_message) > 512
        or "\n" in error_message
        or "\r" in error_message
        or "://" in error_message
        or error_message.startswith(("/", "\\\\", "data:", "file:"))
        or bool(_WINDOWS_ABSOLUTE_RE.match(error_message))
    ):
        raise ValueError("Invalid error_message")
    parameters = clean.get("parameters")
    if not isinstance(parameters, dict) or set(parameters) - _JOB_PARAMETER_KEYS:
        raise ValueError("Job parameters contain unsupported fields")
    clean["parameters"] = {
        key: _safe_parameter_value(key, value) for key, value in parameters.items()
    }
    if not isinstance(clean.get("fence"), int) or int(clean["fence"]) < 0:
        raise ValueError("Invalid fence")
    if len(json.dumps(clean, default=str, separators=(",", ":"))) > 8192:
        raise ValueError("Job envelope is too large")
    return clean


def register_agent(
    user_id: str,
    agent_name: str,
    description: str = "",
    *,
    device_id: str = "",
    protocol_version: str = "",
    supports_pairing: bool = False,
) -> dict[str, Any]:
    agent_name = str(agent_name or "").strip()
    if not agent_name or len(agent_name) > 100:
        raise ValueError("Agent name must be between 1 and 100 characters")
    if device_id and not _valid_device_id(device_id):
        raise ValueError("Invalid device ID")
    if protocol_version and protocol_version != "v1":
        raise ValueError("Unsupported agent protocol")
    pairing_capable = bool(supports_pairing and device_id and protocol_version == "v1")
    token = generate_token(48)
    token_hash = hash_token(token)
    now = time.time()
    db = get_sync_db()
    collection = db[COLL_AGENTS]
    existing = None
    if pairing_capable:
        existing = collection.find_one(
            {
                "user_id": user_id,
                "device_id": device_id,
                "is_active": True,
                "supports_pairing": True,
            }
        )
    if existing:
        agent_id = str(existing["agent_id"])
        collection.update_one(
            {"agent_id": agent_id},
            {
                "$set": {
                    "name": agent_name,
                    "token_hash": token_hash,
                    "protocol_version": protocol_version,
                    "supports_pairing": True,
                    "is_active": True,
                    "last_heartbeat_at": now,
                    "updated_at": now,
                }
            },
        )
        _deactivate_sibling_agents(db, user_id, device_id, keep_agent_id=agent_id)
        return {
            "agent_id": agent_id,
            "token": token,
            "name": agent_name,
            "device_id": device_id,
            "protocol_version": protocol_version,
            "supports_pairing": True,
        }
    agent_id = "agent_" + generate_token(16)
    doc = {
        "agent_id": agent_id,
        "user_id": user_id,
        "name": agent_name,
        "token_hash": token_hash,
        "device_id": device_id,
        "protocol_version": protocol_version,
        "supports_pairing": pairing_capable,
        "is_active": True,
        "last_heartbeat_at": now,
        "created_at": now,
    }
    collection.insert_one(doc)
    if pairing_capable:
        _deactivate_sibling_agents(db, user_id, device_id, keep_agent_id=agent_id)
    return {
        "agent_id": agent_id,
        "token": token,
        "name": agent_name,
        "device_id": device_id,
        "protocol_version": protocol_version,
        "supports_pairing": pairing_capable,
    }


def _deactivate_sibling_agents(
    db: Any,
    user_id: str,
    device_id: str,
    *,
    keep_agent_id: str,
) -> None:
    if not device_id:
        return
    now = time.time()
    siblings = list(
        db[COLL_AGENTS].find(
            {
                "user_id": user_id,
                "device_id": device_id,
                "is_active": True,
                "agent_id": {"$ne": keep_agent_id},
            },
            {"_id": 0, "agent_id": 1},
        )
    )
    sibling_ids = [str(doc["agent_id"]) for doc in siblings if doc.get("agent_id")]
    if not sibling_ids:
        return
    db[COLL_AGENTS].update_many(
        {"agent_id": {"$in": sibling_ids}},
        {"$set": {"is_active": False, "updated_at": now}},
    )
    db[COLL_AGENT_PAIRINGS].delete_many({"agent_id": {"$in": sibling_ids}})
    db[COLL_AGENT_JOBS].delete_many({"agent_id": {"$in": sibling_ids}})


def authenticate_agent(token: str) -> Optional[dict[str, Any]]:
    token_hash = hash_token(token)
    return get_sync_db()[COLL_AGENTS].find_one({"token_hash": token_hash, "is_active": True})


def heartbeat_agent(agent_id: str) -> None:
    get_sync_db()[COLL_AGENTS].update_one(
        {"agent_id": agent_id},
        {"$set": {"last_heartbeat_at": time.time()}},
    )


def bind_agent_device(
    agent_id: str,
    device_id: str,
    protocol_version: str,
    supports_pairing: bool,
) -> dict[str, Any]:
    if not _valid_device_id(device_id) or protocol_version != "v1":
        raise ValueError("Invalid device protocol registration")
    collection = get_sync_db()[COLL_AGENTS]
    current = collection.find_one({"agent_id": agent_id, "is_active": True})
    if current is None:
        raise ValueError("Agent not found")
    existing_device = str(current.get("device_id") or "")
    if existing_device and existing_device != device_id:
        raise ValueError("Agent credential is already bound to another device")
    collection.update_one(
        {"agent_id": agent_id, "is_active": True},
        {
            "$set": {
                "device_id": device_id,
                "protocol_version": protocol_version,
                "supports_pairing": bool(supports_pairing),
                "updated_at": time.time(),
            }
        },
    )
    return {
        "agent_id": agent_id,
        "device_id": device_id,
        "protocol_version": protocol_version,
        "supports_pairing": bool(supports_pairing),
    }


def deactivate_agent(agent_id: str) -> None:
    get_sync_db()[COLL_AGENTS].update_one(
        {"agent_id": agent_id},
        {"$set": {"is_active": False}},
    )


def list_user_agents(user_id: str) -> list[dict[str, Any]]:
    docs = get_sync_db()[COLL_AGENTS].find(
        {"user_id": user_id},
        {"token_hash": 0},
    ).sort("created_at", -1)
    return [
        {
            "agent_id": d["agent_id"],
            "name": d.get("name", ""),
            "is_active": d.get("is_active", False),
            "last_heartbeat_at": d.get("last_heartbeat_at", 0),
            "created_at": d.get("created_at", 0),
            "device_id": d.get("device_id", ""),
            "protocol_version": d.get("protocol_version", ""),
            "supports_pairing": bool(d.get("supports_pairing", False)),
        }
        for d in docs
    ]


def _bounded_run_settings(settings: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(settings, dict) or set(settings) - _RUN_SETTING_KEYS:
        raise ValueError("Run settings contain unsupported fields")
    bounded: dict[str, Any] = {}
    for key, value in settings.items():
        if isinstance(value, bool) or (
            isinstance(value, int) and not isinstance(value, bool) and -1000 <= value <= 1000
        ):
            bounded[key] = value
        elif isinstance(value, str) and len(value) <= 200:
            bounded[key] = value
        elif (
            isinstance(value, list)
            and len(value) <= 100
            and all(
                (isinstance(item, str) and len(item) <= 100)
                or (isinstance(item, int) and not isinstance(item, bool) and -1000 <= item <= 1000)
                for item in value
            )
        ):
            bounded[key] = list(value)
        else:
            raise ValueError("Run settings must be bounded scalar metadata")
    return bounded


def allocate_run_envelope(
    *,
    user_id: str,
    owner_type: str,
    owner_id: str,
    agent_id: str,
    device_id: str,
    flow_type: str,
    settings: dict[str, Any],
) -> dict[str, Any]:
    if (
        owner_type not in {"user", "org"}
        or not owner_id
        or len(owner_id) > 200
        or flow_type not in {"structured", "reference"}
        or not agent_id
        or len(agent_id) > 200
        or not _valid_device_id(device_id)
    ):
        raise ValueError("Invalid run allocation")
    bounded_settings = _bounded_run_settings(settings)
    db = get_sync_db()
    agent = db[COLL_AGENTS].find_one(
        {
            "agent_id": agent_id,
            "user_id": user_id,
            "device_id": device_id,
            "is_active": True,
            "protocol_version": "v1",
            "supports_pairing": True,
        }
    )
    if agent is None:
        raise ValueError("Agent device does not belong to this user")
    if owner_type == "user":
        if owner_id != user_id:
            raise ValueError("User owner does not match the authenticated user")
    elif db[COLL_ORG_MEMBERS].find_one(
        {"org_id": owner_id, "user_id": user_id, "status": "active"}
    ) is None:
        raise ValueError("Authenticated user is not an active organization member")

    run_id = "run_" + generate_token(16)
    now = time.time()
    doc = None
    for _ in range(8):
        run_number = reserve_run_number(
            owner_type, owner_id, flow_type, user_id=user_id
        )
        candidate = {
            "run_id": run_id,
            "user_id": user_id,
            "owner_type": owner_type,
            "owner_id": owner_id,
            "created_by_user_id": user_id,
            "agent_id": agent_id,
            "device_id": device_id,
            "run_number": run_number,
            "display_batch": display_batch_label(flow_type, run_number),
            "flow_type": flow_type,
            "flow_family": numbering_scope(flow_type),
            "status": "allocated",
            "settings": bounded_settings,
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
            "agent_id",
            "device_id",
            "run_number",
            "display_batch",
            "flow_type",
            "status",
        )
    }


def _safe_pairing_approval(doc: dict[str, Any]) -> dict[str, Any]:
    expiry = doc["expires_at"]
    expires_at = expiry.timestamp() if isinstance(expiry, datetime) else float(expiry)
    return {
        "type": "pairing_approval",
        "challenge_id": str(doc["challenge_id"]),
        "challenge_hash": str(doc["challenge_hash"]),
        "agent_id": str(doc["agent_id"]),
        "device_id": str(doc["device_id"]),
        "owner_key": str(doc["owner_key"]),
        "scopes": list(doc["scopes"]),
        "expires_at": expires_at,
    }


def request_pairing_approval(
    *,
    user_id: str,
    owner_type: str,
    owner_id: str,
    agent_id: str,
    device_id: str,
    challenge_id: str,
    challenge: str,
    scopes: list[str],
) -> dict[str, Any]:
    if (
        owner_type not in {"user", "org"}
        or not owner_id
        or len(owner_id) > 200
        or not agent_id
        or len(agent_id) > 200
        or not _valid_device_id(device_id)
        or not _CHALLENGE_ID_RE.fullmatch(challenge_id)
        or not 32 <= len(challenge) <= 128
    ):
        raise ValueError("Invalid pairing request")
    requested_scopes = frozenset(str(scope) for scope in scopes)
    if (
        not requested_scopes
        or any(len(scope) > 64 for scope in requested_scopes)
        or not requested_scopes.issubset(PAIRING_SCOPES)
    ):
        raise ValueError("Invalid pairing scopes")
    db = get_sync_db()
    agent = db[COLL_AGENTS].find_one(
        {
            "agent_id": agent_id,
            "user_id": user_id,
            "device_id": device_id,
            "is_active": True,
            "protocol_version": "v1",
            "supports_pairing": True,
        }
    )
    if agent is None:
        raise ValueError("Agent device does not belong to this user")
    if owner_type == "user":
        if owner_id != user_id:
            raise ValueError("User owner does not match the authenticated user")
    elif db[COLL_ORG_MEMBERS].find_one(
        {"org_id": owner_id, "user_id": user_id, "status": "active"}
    ) is None:
        raise ValueError("Authenticated user is not an active organization member")

    digest = hashlib.sha256(challenge.encode("utf-8")).hexdigest()
    pairings = db[COLL_AGENT_PAIRINGS]
    if pairings.find_one({"challenge_id": challenge_id}) or pairings.find_one(
        {"challenge_hash": digest}
    ):
        raise ValueError("Pairing challenge was already submitted")
    now = time.time()
    expires_at = datetime.fromtimestamp(now + _PAIRING_CHALLENGE_TTL_SECONDS, tz=timezone.utc)
    doc = {
        "challenge_id": challenge_id,
        "challenge_hash": digest,
        "user_id": user_id,
        "owner_type": owner_type,
        "owner_id": owner_id,
        "owner_key": f"{owner_type}:{owner_id}",
        "agent_id": agent_id,
        "device_id": device_id,
        "scopes": sorted(requested_scopes),
        "status": "pending",
        "created_at": now,
        "expires_at": expires_at,
    }
    try:
        pairings.insert_one(doc)
    except DuplicateKeyError as exc:
        raise ValueError("Pairing challenge was already submitted") from exc
    from dashboard.backend.agent.connections import agent_connections

    agent_connections.notify_from_thread(
        agent_id, _safe_pairing_approval(doc), device_id=device_id
    )
    return {
        "challenge_id": challenge_id,
        "agent_id": agent_id,
        "device_id": device_id,
        "status": "pending",
        "expires_at": expires_at.timestamp(),
    }


def poll_pairing_approvals(agent_id: str, device_id: str) -> list[dict[str, Any]]:
    if not _valid_device_id(device_id):
        return []
    docs = (
        get_sync_db()[COLL_AGENT_PAIRINGS]
        .find(
            {
                "agent_id": agent_id,
                "device_id": device_id,
                "status": "pending",
                "expires_at": {"$gt": datetime.now(timezone.utc)},
            },
            {"_id": 0},
        )
        .sort("created_at", 1)
        .limit(16)
    )
    return [_safe_pairing_approval(doc) for doc in docs]


def acknowledge_pairing_approval(
    challenge_id: str, agent_id: str, device_id: str
) -> bool:
    result = get_sync_db()[COLL_AGENT_PAIRINGS].update_one(
        {
            "challenge_id": challenge_id,
            "agent_id": agent_id,
            "device_id": device_id,
            "status": "pending",
            "expires_at": {"$gt": datetime.now(timezone.utc)},
        },
        {"$set": {"status": "delivered", "delivered_at": time.time()}},
    )
    return bool(result.modified_count)


def get_recent_active_agent(user_id: str, max_age_seconds: int = 90) -> Optional[dict[str, Any]]:
    return get_sync_db()[COLL_AGENTS].find_one(
        {
            "user_id": user_id,
            "is_active": True,
            "last_heartbeat_at": {"$gte": time.time() - max_age_seconds},
        },
        {"token_hash": 0},
        sort=[("last_heartbeat_at", -1)],
    )


def finalize_disconnected_agent_jobs(user_id: str, max_age_seconds: int = 90) -> int:
    db = get_sync_db()
    now = time.time()
    active_jobs = list(db[COLL_AGENT_JOBS].find(
        {"user_id": user_id, "status": {"$in": ["pending", "running", "cancel_requested"]}},
        {"job_id": 1, "agent_id": 1, "status": 1},
    ))
    finalized = 0
    for job in active_jobs:
        agent = db[COLL_AGENTS].find_one(
            {
                "agent_id": job.get("agent_id"),
                "is_active": True,
                "last_heartbeat_at": {"$gte": now - max_age_seconds},
            },
            {"_id": 1},
        )
        if agent:
            continue
        previous_status = str(job.get("status") or "")
        if previous_status != "cancel_requested":
            continue
        result = db[COLL_AGENT_JOBS].update_one(
            {"job_id": job.get("job_id"), "status": previous_status},
            {"$set": {
                "status": "canceled",
                "progress_code": "canceled",
                "error_code": "agent_disconnected",
                "error_message": "Agent disconnected before cancellation completed",
                "completed_at": now,
                "purge_at": datetime.now(timezone.utc)
                + timedelta(days=_TERMINAL_RETENTION_DAYS),
                "updated_at": now,
            }},
        )
        finalized += int(result.modified_count or 0)
    return finalized


def create_job(
    agent_id: str,
    user_id: str,
    job_type: str,
    payload: dict[str, Any] | None = None,
    *,
    device_id: str = "",
    owner_type: str = "user",
    owner_id: str = "",
    run_id: str = "",
    command: str = "",
    parameters: dict[str, Any] | None = None,
    client_operation_id: str = "",
    allow_inactive_agent: bool = False,
) -> dict[str, Any]:
    """Create a metadata-only job pinned to one authorized agent device.

    The positional payload form remains temporarily accepted for old callers,
    but only allowlisted scalar metadata is retained.
    """
    db = get_sync_db()
    agent_query: dict[str, Any] = {"agent_id": agent_id, "user_id": user_id}
    if not allow_inactive_agent:
        agent_query["is_active"] = True
    agent = db[COLL_AGENTS].find_one(agent_query)
    if agent is None:
        raise ValueError("Agent does not belong to this user")
    resolved_device = device_id or str(agent.get("device_id") or "")
    if not _valid_device_id(resolved_device) or resolved_device != str(
        agent.get("device_id") or ""
    ):
        raise ValueError("Job device does not match the agent device")

    legacy = payload if isinstance(payload, dict) else {}
    resolved_owner_id = owner_id or user_id
    resolved_run_id = run_id or str(legacy.get("run_id") or "")
    if not resolved_run_id:
        legacy_run_ids = legacy.get("run_ids")
        if isinstance(legacy_run_ids, list) and len(legacy_run_ids) == 1:
            resolved_run_id = str(legacy_run_ids[0] or "")
    # Old diagnostic jobs had no run. Keep them metadata-only during the
    # compatibility window without granting access to another run.
    resolved_run_id = resolved_run_id or f"control:{job_type}"
    resolved_command = command or {
        "run_browser_batch": "generate_images",
        "run_chatgpt_batch": "generate_images",
        "run_chatgpt": "generate_images",
        "run_gemini": "generate_images",
        "run_916_conversion": "convert_images",
        "check_cdp": "check_browser",
    }.get(job_type, job_type)
    resolved_parameters = dict(parameters or {})
    if not parameters:
        for key in _JOB_PARAMETER_KEYS:
            if key in legacy:
                resolved_parameters[key] = legacy[key]

    if resolved_run_id.startswith("run_") or resolved_run_id.startswith("run-"):
        run = db[COLL_RUNS].find_one(
            {
                "run_id": resolved_run_id,
                "user_id": user_id,
                "owner_type": owner_type,
                "owner_id": resolved_owner_id,
                "agent_id": agent_id,
                "device_id": resolved_device,
            }
        )
        if run is None and not (job_type == "purge_run" and allow_inactive_agent):
            raise ValueError("Run is not authorized for this agent device")
    if owner_type == "user":
        if resolved_owner_id != user_id:
            raise ValueError("Job owner does not match the authenticated user")
    elif owner_type == "org":
        if db[COLL_ORG_MEMBERS].find_one(
            {"org_id": resolved_owner_id, "user_id": user_id, "status": "active"}
        ) is None:
            raise ValueError("Authenticated user is not an active organization member")
    else:
        raise ValueError("Invalid job owner")

    operation_id = client_operation_id or str(
        legacy.get("client_operation_id") or f"legacy:{generate_token(16)}"
    )
    existing = db[COLL_AGENT_JOBS].find_one(
        {
            "owner_type": owner_type,
            "owner_id": resolved_owner_id,
            "client_operation_id": operation_id,
        },
        {"_id": 0},
    )
    if existing is not None:
        return existing
    job_id = "job_" + generate_token(16)
    now = time.time()
    doc = validate_job_envelope({
        "job_id": job_id,
        "agent_id": agent_id,
        "device_id": resolved_device,
        "user_id": user_id,
        "owner_type": owner_type,
        "owner_id": resolved_owner_id,
        "run_id": resolved_run_id,
        "job_type": job_type,
        "command": resolved_command,
        "parameters": resolved_parameters,
        "client_operation_id": operation_id,
        "status": "pending",
        "progress_code": "queued",
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "completed_at": None,
        "lease_expires_at": None,
        "fence": 0,
        "purge_at": None,
    })
    try:
        db[COLL_AGENT_JOBS].insert_one(doc)
    except DuplicateKeyError:
        existing = db[COLL_AGENT_JOBS].find_one(
            {
                "owner_type": owner_type,
                "owner_id": resolved_owner_id,
                "client_operation_id": operation_id,
            },
            {"_id": 0},
        )
        if existing is None:
            raise
        return existing
    from dashboard.backend.agent.connections import agent_connections

    agent_connections.notify_from_thread(
        agent_id,
        {"type": "job_available", "job_id": job_id},
        device_id=resolved_device,
    )
    return doc


def poll_jobs(agent_id: str, device_id: str = "") -> list[dict[str, Any]]:
    now = time.time()
    if not device_id:
        agent = get_sync_db()[COLL_AGENTS].find_one({"agent_id": agent_id}) or {}
        device_id = str(agent.get("device_id") or "")
    if not _valid_device_id(device_id):
        return []
    return list(
        get_sync_db()[COLL_AGENT_JOBS]
        .find({
            "agent_id": agent_id,
            "device_id": device_id,
            "$or": [
                {"status": "pending"},
                {"status": "running", "lease_expires_at": {"$lte": now}},
            ],
        }, {"_id": 0})
        .sort("created_at", 1)
        .limit(5)
    )


def get_job_status_for_agent(
    job_id: str, agent_id: str, device_id: str = ""
) -> dict[str, Any] | None:
    query: dict[str, Any] = {"job_id": job_id, "agent_id": agent_id}
    if device_id:
        query["device_id"] = device_id
    job = get_sync_db()[COLL_AGENT_JOBS].find_one(
        query,
        {
            "_id": 0,
            "status": 1,
            "progress_code": 1,
            "error_code": 1,
            "error_message": 1,
            "updated_at": 1,
        },
    )
    if not job:
        return None
    job["cancel_requested"] = job.get("status") in {"cancel_requested", "canceled"}
    return job


def cancel_user_job(user_id: str, job_id: str) -> dict[str, Any] | None:
    now = time.time()
    job = get_sync_db()[COLL_AGENT_JOBS].find_one({"user_id": user_id, "job_id": job_id}, {"_id": 0})
    if not job:
        return None
    status = job.get("status")
    if status == "pending":
        new_status = "canceled"
        completed_at = now
    elif status == "running":
        new_status = "cancel_requested"
        completed_at = None
    elif status == "cancel_requested":
        new_status = "cancel_requested"
        completed_at = None
    else:
        return job

    update = {
        "status": new_status,
        "error_code": "user_canceled",
        "error_message": "Canceled by user",
        "progress_code": "cancel_requested",
        "updated_at": now,
        "cancel_requested_at": now,
    }
    if completed_at is not None:
        update["completed_at"] = completed_at
        update["purge_at"] = datetime.now(timezone.utc) + timedelta(
            days=_TERMINAL_RETENTION_DAYS
        )
    get_sync_db()[COLL_AGENT_JOBS].update_one(
        {"user_id": user_id, "job_id": job_id},
        {"$set": update},
    )
    from dashboard.backend.agent.connections import agent_connections

    agent_connections.notify_from_thread(
        str(job.get("agent_id") or ""),
        {"type": "job_canceled", "job_id": job_id},
        device_id=str(job.get("device_id") or ""),
    )
    return {**job, **update}


def claim_job(
    job_id: str,
    agent_id: str,
    device_id: str = "",
    claim_id: str = "",
) -> Optional[dict[str, Any]]:
    from pymongo import ReturnDocument

    now = time.time()
    if device_id and not _valid_device_id(device_id):
        return None
    if not device_id:
        agent = get_sync_db()[COLL_AGENTS].find_one({"agent_id": agent_id}) or {}
        device_id = str(agent.get("device_id") or "")
    if not _valid_device_id(device_id):
        return None
    claim_id = claim_id or f"legacy-{job_id}"
    existing = get_sync_db()[COLL_AGENT_JOBS].find_one(
        {
            "job_id": job_id,
            "agent_id": agent_id,
            "device_id": device_id,
            "status": "running",
            "claim_id": claim_id,
        },
        {"_id": 0},
    )
    if existing is not None:
        return existing
    result = get_sync_db()[COLL_AGENT_JOBS].find_one_and_update(
        {
            "job_id": job_id,
            "agent_id": agent_id,
            "device_id": device_id,
            "$or": [
                {"status": "pending"},
                {"status": "running", "lease_expires_at": {"$lte": now}},
            ],
        },
        {
            "$set": {
                "status": "running",
                "progress_code": "claimed",
                "claim_id": claim_id,
                "lease_expires_at": now + 120,
                "started_at": now,
                "updated_at": now,
            },
            "$inc": {"fence": 1},
        },
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    return result


def update_job_progress(
    job_id: str,
    agent_id: str,
    device_id: str,
    fence: int,
    progress_code: str,
) -> bool:
    if not _valid_device_id(device_id) or not _CODE_RE.fullmatch(
        str(progress_code or "")
    ):
        return False
    now = time.time()
    result = get_sync_db()[COLL_AGENT_JOBS].update_one(
        {
            "job_id": job_id,
            "agent_id": agent_id,
            "device_id": device_id,
            "status": "running",
            "fence": int(fence),
        },
        {
            "$set": {
                "progress_code": progress_code,
                "updated_at": now,
                "lease_expires_at": now + 120,
            }
        },
    )
    return bool(result.modified_count)


def _terminal_job_update(
    *,
    job_id: str,
    agent_id: str,
    device_id: str,
    fence: int,
    event_id: str,
    status: str,
    progress_code: str,
    error_code: str | None = None,
    error_message: str | None = None,
) -> bool:
    if (
        not _valid_device_id(device_id)
        or not _ID_RE.fullmatch(str(event_id or ""))
        or not _CODE_RE.fullmatch(progress_code)
        or (error_code is not None and not _CODE_RE.fullmatch(error_code))
        or (
            error_message is not None
            and (
                len(error_message) > 512
                or "\n" in error_message
                or "\r" in error_message
                or "://" in error_message
                or error_message.startswith(("/", "\\\\", "data:", "file:"))
                or bool(_WINDOWS_ABSOLUTE_RE.match(error_message))
            )
        )
    ):
        return False
    collection = get_sync_db()[COLL_AGENT_JOBS]
    replay = collection.find_one(
        {
            "job_id": job_id,
            "agent_id": agent_id,
            "device_id": device_id,
            "fence": int(fence),
            "terminal_event_id": event_id,
        }
    )
    if replay is not None:
        return True
    current = collection.find_one(
        {
            "job_id": job_id,
            "agent_id": agent_id,
            "device_id": device_id,
            "fence": int(fence),
            "status": {"$in": ["running", "cancel_requested"]},
        }
    )
    if current is None:
        return False
    if current.get("status") == "cancel_requested":
        status = "canceled"
        progress_code = "canceled"
        error_code = "user_canceled"
        error_message = "Canceled by user"
    now = time.time()
    update: dict[str, Any] = {
        "status": status,
        "progress_code": progress_code,
        "terminal_event_id": event_id,
        "completed_at": now,
        "updated_at": now,
        "lease_expires_at": None,
        "purge_at": datetime.now(timezone.utc)
        + timedelta(days=_TERMINAL_RETENTION_DAYS),
    }
    if error_code:
        update["error_code"] = error_code
    if error_message:
        update["error_message"] = error_message
    result = collection.update_one(
        {
            "job_id": job_id,
            "agent_id": agent_id,
            "device_id": device_id,
            "fence": int(fence),
            "status": {"$in": ["running", "cancel_requested"]},
        },
        {"$set": update},
    )
    return bool(result.modified_count)


def complete_job(
    job_id: str,
    agent_id: str,
    device_id: str,
    fence: int,
    event_id: str,
) -> bool:
    completed = _terminal_job_update(
        job_id=job_id,
        agent_id=agent_id,
        device_id=device_id,
        fence=fence,
        event_id=event_id,
        status="completed",
        progress_code="completed",
    )
    if completed:
        db = get_sync_db()
        job = db[COLL_AGENT_JOBS].find_one(
            {"job_id": job_id, "agent_id": agent_id, "device_id": device_id},
            {"_id": 0, "job_type": 1, "run_id": 1, "client_operation_id": 1, "user_id": 1},
        )
        if job and job.get("job_type") == "purge_run":
            from dashboard.backend.services.run_storage import purge_run_metadata

            purge_run_metadata(
                db,
                user_id=str(job.get("user_id") or ""),
                run_id=str(job.get("run_id") or ""),
            )
    return completed


def fail_job(
    job_id: str,
    agent_id: str,
    device_id: str,
    fence: int,
    event_id: str,
    error_code: str,
    error_message: str = "",
) -> bool:
    failed = _terminal_job_update(
        job_id=job_id,
        agent_id=agent_id,
        device_id=device_id,
        fence=fence,
        event_id=event_id,
        status="failed",
        progress_code="failed",
        error_code=error_code,
        error_message=error_message or None,
    )
    if failed:
        db = get_sync_db()
        job = db[COLL_AGENT_JOBS].find_one(
            {"job_id": job_id, "agent_id": agent_id, "device_id": device_id},
            {"_id": 0, "job_type": 1, "run_id": 1, "client_operation_id": 1},
        )
        if job and job.get("job_type") == "purge_run":
            db[COLL_RUNS].update_one(
                {
                    "run_id": job.get("run_id"),
                    "deletion_tombstone.operation_id": job.get("client_operation_id"),
                },
                {
                    "$set": {
                        "status": "purge_failed",
                        "deletion_tombstone.error_code": error_code or "purge_failed",
                        "updated_at": time.time(),
                    }
                },
            )
        elif job and job.get("job_type") == "execute_run" and job.get("run_id"):
            now = time.time()
            last_error = str(error_message or error_code or "Local browser automation failed")
            db[COLL_RUNS].update_one(
                {"run_id": job.get("run_id")},
                {
                    "$set": {
                        "status": "failed",
                        "updated_at": now,
                        "image_generation": {
                            "status": "failed",
                            "error_code": error_code or "local_execution_failed",
                            "last_error": last_error[:512],
                            "job_id": job_id,
                        },
                    }
                },
            )
    return failed
