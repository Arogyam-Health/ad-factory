from __future__ import annotations

import json
import hashlib
import time
from typing import Any, Optional

from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import COLL_AGENTS, COLL_AGENT_JOBS, COLL_RUNS, COLL_IMAGES
from dashboard.backend.security.crypto import generate_token, hash_token


def register_agent(user_id: str, agent_name: str, description: str = "") -> dict[str, Any]:
    agent_id = "agent_" + generate_token(16)
    token = generate_token(48)
    token_hash = hash_token(token)
    now = time.time()
    doc = {
        "agent_id": agent_id,
        "user_id": user_id,
        "name": agent_name,
        "description": description,
        "token_hash": token_hash,
        "is_active": True,
        "last_heartbeat_at": now,
        "created_at": now,
    }
    get_sync_db()[COLL_AGENTS].insert_one(doc)
    return {
        "agent_id": agent_id,
        "token": token,
        "name": agent_name,
    }


def authenticate_agent(token: str) -> Optional[dict[str, Any]]:
    token_hash = hash_token(token)
    return get_sync_db()[COLL_AGENTS].find_one({"token_hash": token_hash, "is_active": True})


def heartbeat_agent(agent_id: str) -> None:
    get_sync_db()[COLL_AGENTS].update_one(
        {"agent_id": agent_id},
        {"$set": {"last_heartbeat_at": time.time()}},
    )


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
        }
        for d in docs
    ]


def get_recent_active_agent(user_id: str, max_age_seconds: int = 60) -> Optional[dict[str, Any]]:
    return get_sync_db()[COLL_AGENTS].find_one(
        {
            "user_id": user_id,
            "is_active": True,
            "last_heartbeat_at": {"$gte": time.time() - max_age_seconds},
        },
        {"token_hash": 0},
        sort=[("last_heartbeat_at", -1)],
    )


def finalize_disconnected_agent_jobs(user_id: str, max_age_seconds: int = 30) -> int:
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
        error = "Agent disconnected before cancellation completed" if previous_status == "cancel_requested" else "Agent disconnected"
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
    return doc


def poll_jobs(agent_id: str) -> list[dict[str, Any]]:
    return list(
        get_sync_db()[COLL_AGENT_JOBS]
        .find({"agent_id": agent_id, "status": "pending"}, {"_id": 0})
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
    return {**job, **update}


def claim_job(job_id: str, agent_id: str) -> Optional[dict[str, Any]]:
    now = time.time()
    result = get_sync_db()[COLL_AGENT_JOBS].find_one_and_update(
        {"job_id": job_id, "agent_id": agent_id, "status": "pending"},
        {"$set": {"status": "running", "started_at": now, "updated_at": now}},
        projection={"_id": 0},
    )
    return result


def update_job_progress(job_id: str, progress: str, agent_id: str, result: Any = None) -> None:
    now = time.time()
    db = get_sync_db()
    update: dict[str, Any] = {"progress": progress, "updated_at": now}
    if isinstance(result, dict):
        update["result"] = result
    db[COLL_AGENT_JOBS].update_one(
        {"job_id": job_id, "agent_id": agent_id},
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
    get_sync_db()[COLL_AGENT_JOBS].update_one(
        {"job_id": job_id, "agent_id": agent_id},
        {"$set": {
            "status": "completed",
            "result": result,
            "completed_at": now,
            "updated_at": now,
        }},
    )
    try:
        _persist_local_agent_result(db, job, result, now, completed=True)
    except Exception:
        pass


def _persist_local_agent_result(db: Any, job: dict[str, Any], result: Any, now: float, *, completed: bool) -> None:
    if job.get("job_type") != "run_chatgpt_batch" or not isinstance(result, dict):
        return
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    user_id = str(job.get("user_id") or "")
    run_ids = [str(rid) for rid in (payload.get("run_ids") or []) if str(rid).strip()]
    images = [img for img in (result.get("images") or []) if isinstance(img, dict) and img.get("url")]
    if not user_id or not run_ids or not images:
        return

    batch_name = str(payload.get("batch_name") or "")
    image_urls = [str(img.get("url")) for img in images]
    update = {
        "image_generated": True,
        "image_files": image_urls,
        "local_artifacts": images,
        "local_output_dir": str(result.get("local_output_dir") or ""),
        "artifact_base_url": str(result.get("artifact_base_url") or ""),
        "local_agent_warnings": result.get("warnings") or [],
        "updated_at": now,
    }
    if completed:
        update["status"] = "completed"
    if batch_name:
        update["batch"] = batch_name
    update["manifest_summary"] = {
        "batch": batch_name,
        "image_count": len(image_urls),
    }

    for run_id in run_ids:
        db[COLL_RUNS].update_one(
            {"user_id": user_id, "run_id": run_id},
            {"$set": {**update, "run_id": run_id, "user_id": user_id}, "$setOnInsert": {"created_at": now, "config": {}}},
            upsert=True,
        )
        for img in images:
            url = str(img.get("url") or "")
            image_id = hashlib.sha256(f"{run_id}:{url}".encode()).hexdigest()[:16]
            db[COLL_IMAGES].update_one(
                {"image_id": image_id},
                {"$set": {
                    "user_id": user_id,
                    "run_id": run_id,
                    "image_id": image_id,
                    "batch": batch_name,
                    "file_path": url,
                    "local_path": url,
                    "url": url,
                    "filename": str(img.get("name") or "image.png"),
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
