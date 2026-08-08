from __future__ import annotations

import json
import hashlib
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import (
    COLL_AGENTS,
    COLL_AGENT_JOBS,
    COLL_AGENT_PAIRINGS,
    COLL_IMAGES,
    COLL_ORG_MEMBERS,
    COLL_RUNS,
)
from dashboard.backend.security.crypto import generate_token, hash_token
from dashboard.backend.services.run_storage import reserve_run_number
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


def _valid_device_id(device_id: str) -> bool:
    return bool(_DEVICE_ID_RE.fullmatch(str(device_id or "")))


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
    agent_id = "agent_" + generate_token(16)
    token = generate_token(48)
    token_hash = hash_token(token)
    now = time.time()
    doc = {
        "agent_id": agent_id,
        "user_id": user_id,
        "name": agent_name,
        "token_hash": token_hash,
        "device_id": device_id,
        "protocol_version": protocol_version,
        "supports_pairing": bool(supports_pairing and device_id and protocol_version == "v1"),
        "is_active": True,
        "last_heartbeat_at": now,
        "created_at": now,
    }
    get_sync_db()[COLL_AGENTS].insert_one(doc)
    return {
        "agent_id": agent_id,
        "token": token,
        "name": agent_name,
        "device_id": device_id,
        "protocol_version": protocol_version,
        "supports_pairing": doc["supports_pairing"],
    }


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

    run_number = reserve_run_number(owner_type, owner_id)
    run_id = "run_" + generate_token(16)
    now = time.time()
    doc = {
        "run_id": run_id,
        "user_id": user_id,
        "owner_type": owner_type,
        "owner_id": owner_id,
        "created_by_user_id": user_id,
        "agent_id": agent_id,
        "device_id": device_id,
        "run_number": run_number,
        "display_batch": f"v{run_number}",
        "flow_type": flow_type,
        "status": "allocated",
        "settings": bounded_settings,
        "created_at": now,
        "updated_at": now,
    }
    db[COLL_RUNS].insert_one(doc)
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
    expires_at = datetime.fromtimestamp(now + 120, tz=timezone.utc)
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
        error = "Agent disconnected before cancellation completed"
        result = db[COLL_AGENT_JOBS].update_one(
            {"job_id": job.get("job_id"), "status": previous_status},
            {"$set": {
                "status": "canceled",
                "progress": "agent disconnected",
                "error": error,
                "completed_at": now,
                "updated_at": now,
            }},
        )
        finalized += int(result.modified_count or 0)
    return finalized


def create_job(agent_id: str, user_id: str, job_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    job_id = "job_" + generate_token(16)
    now = time.time()
    doc = {
        "job_id": job_id,
        "agent_id": agent_id,
        "user_id": user_id,
        "job_type": job_type,
        "payload": payload,
        "status": "pending",
        "progress": "",
        "result": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "completed_at": None,
    }
    get_sync_db()[COLL_AGENT_JOBS].insert_one(doc)
    from dashboard.backend.agent.connections import agent_connections

    agent_connections.notify_from_thread(agent_id, {"type": "job_available", "job_id": job_id})
    return doc


def poll_jobs(agent_id: str) -> list[dict[str, Any]]:
    now = time.time()
    return list(
        get_sync_db()[COLL_AGENT_JOBS]
        .find({
            "agent_id": agent_id,
            "$or": [
                {"status": "pending"},
                {"status": "running", "lease_expires_at": {"$lte": now}},
            ],
        }, {"_id": 0})
        .sort("created_at", 1)
        .limit(5)
    )


def get_job_status_for_agent(job_id: str, agent_id: str) -> dict[str, Any] | None:
    job = get_sync_db()[COLL_AGENT_JOBS].find_one(
        {"job_id": job_id, "agent_id": agent_id},
        {"_id": 0, "status": 1, "progress": 1, "updated_at": 1},
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
        "error": "Canceled by user",
        "progress": "cancel requested",
        "updated_at": now,
        "cancel_requested_at": now,
    }
    if completed_at is not None:
        update["completed_at"] = completed_at
    get_sync_db()[COLL_AGENT_JOBS].update_one(
        {"user_id": user_id, "job_id": job_id},
        {"$set": update},
    )
    from dashboard.backend.agent.connections import agent_connections

    agent_connections.notify_from_thread(
        str(job.get("agent_id") or ""),
        {"type": "job_canceled", "job_id": job_id},
    )
    return {**job, **update}


def claim_job(job_id: str, agent_id: str, claim_id: str = "") -> Optional[dict[str, Any]]:
    from pymongo import ReturnDocument

    now = time.time()
    claim_id = claim_id or f"legacy-{job_id}"
    result = get_sync_db()[COLL_AGENT_JOBS].find_one_and_update(
        {
            "job_id": job_id,
            "agent_id": agent_id,
            "$or": [
                {"status": "pending"},
                {"status": "running", "claim_id": claim_id},
                {"status": "running", "lease_expires_at": {"$lte": now}},
            ],
        },
        {"$set": {
            "status": "running",
            "claim_id": claim_id,
            "lease_expires_at": now + 120,
            "started_at": now,
            "updated_at": now,
        }},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    return result


def update_job_progress(job_id: str, progress: str, agent_id: str, result: Any = None) -> None:
    now = time.time()
    db = get_sync_db()
    update: dict[str, Any] = {"progress": progress, "updated_at": now, "lease_expires_at": now + 120}
    if isinstance(result, dict):
        update["result"] = result
    db[COLL_AGENT_JOBS].update_one(
        {"job_id": job_id, "agent_id": agent_id, "status": "running"},
        {"$set": update},
    )
    if isinstance(result, dict):
        job = db[COLL_AGENT_JOBS].find_one({"job_id": job_id, "agent_id": agent_id}) or {}
        try:
            _persist_local_agent_result(db, job, result, now, completed=False)
        except Exception:
            pass


def complete_job(job_id: str, agent_id: str, result: Any = None) -> None:
    now = time.time()
    db = get_sync_db()
    job = db[COLL_AGENT_JOBS].find_one({"job_id": job_id, "agent_id": agent_id}) or {}
    if job.get("status") in {"cancel_requested", "canceled"}:
        db[COLL_AGENT_JOBS].update_one(
            {"job_id": job_id, "agent_id": agent_id},
            {"$set": {"status": "canceled", "error": "Canceled by user", "completed_at": now, "updated_at": now}},
        )
        return
    _persist_local_agent_result(db, job, result, now, completed=True)
    db[COLL_AGENT_JOBS].update_one(
        {"job_id": job_id, "agent_id": agent_id},
        {"$set": {
            "status": "completed",
            "result": result,
            "completed_at": now,
            "updated_at": now,
        }},
    )


def _persist_local_agent_result(db: Any, job: dict[str, Any], result: Any, now: float, *, completed: bool) -> None:
    if job.get("job_type") not in {"run_chatgpt_batch", "run_browser_batch"} or not isinstance(result, dict):
        return
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    user_id = str(job.get("user_id") or "")
    run_ids = [str(rid) for rid in (payload.get("run_ids") or []) if str(rid).strip()]
    images = [img for img in (result.get("images") or []) if isinstance(img, dict) and img.get("url")]
    if not user_id or not run_ids or not images:
        return

    batch_name = str(payload.get("batch_name") or "")
    for run_id in run_ids:
        run_images = [img for img in images if str(img.get("run_id") or "") == run_id]
        if not run_images and len(run_ids) == 1:
            run_images = images
        if not run_images:
            continue
        image_urls = [str(img.get("url")) for img in run_images]
        update = {
            "image_generated": True,
            "image_files": image_urls,
            "local_artifacts": run_images,
            "local_output_dir": str(result.get("local_output_dir") or ""),
            "artifact_base_url": str(result.get("artifact_base_url") or ""),
            "local_agent_warnings": result.get("warnings") or [],
            "updated_at": now,
            "manifest_summary": {"batch": batch_name, "image_count": len(image_urls)},
        }
        if completed:
            update["status"] = "completed"
        db[COLL_RUNS].update_one(
            {"user_id": user_id, "run_id": run_id},
            {"$set": {**update, "run_id": run_id, "user_id": user_id}, "$setOnInsert": {"created_at": now, "config": {}}},
            upsert=True,
        )
        for img in run_images:
            url = str(img.get("url") or "")
            image_id = str(img.get("artifact_id") or hashlib.sha256(f"{run_id}:{url}".encode()).hexdigest()[:16])
            db[COLL_IMAGES].update_one(
                {"user_id": user_id, "run_id": run_id, "image_id": image_id},
                {"$set": {
                    "user_id": user_id,
                    "run_id": run_id,
                    "image_id": image_id,
                    "batch": batch_name,
                    "file_path": url,
                    "local_path": url,
                    "url": url,
                    "filename": str(img.get("name") or "image.png"),
                    "prompt_id": str(img.get("prompt_id") or ""),
                    "item_id": str(img.get("item_id") or ""),
                    "aspect_ratio": str(img.get("aspect_ratio") or ""),
                    "bytes": int(img.get("bytes") or 0),
                    "status": "completed",
                    "storage_provider": "local_agent",
                    "created_at": now,
                    "updated_at": now,
                }},
                upsert=True,
            )


def fail_job(job_id: str, agent_id: str, error: str) -> None:
    now = time.time()
    db = get_sync_db()
    job = db[COLL_AGENT_JOBS].find_one({"job_id": job_id, "agent_id": agent_id}) or {}
    if job.get("status") in {"cancel_requested", "canceled"}:
        db[COLL_AGENT_JOBS].update_one(
            {"job_id": job_id, "agent_id": agent_id},
            {"$set": {"status": "canceled", "error": "Canceled by user", "completed_at": now, "updated_at": now}},
        )
        return
    get_sync_db()[COLL_AGENT_JOBS].update_one(
        {"job_id": job_id, "agent_id": agent_id},
        {"$set": {
            "status": "failed",
            "error": error,
            "completed_at": now,
            "updated_at": now,
        }},
    )
