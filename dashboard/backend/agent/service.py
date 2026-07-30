from __future__ import annotations

import json
import time
from typing import Any, Optional

from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import COLL_AGENTS, COLL_AGENT_JOBS
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


def claim_job(job_id: str, agent_id: str) -> Optional[dict[str, Any]]:
    now = time.time()
    result = get_sync_db()[COLL_AGENT_JOBS].find_one_and_update(
        {"job_id": job_id, "agent_id": agent_id, "status": "pending"},
        {"$set": {"status": "running", "started_at": now, "updated_at": now}},
        projection={"_id": 0},
    )
    return result


def update_job_progress(job_id: str, progress: str, agent_id: str) -> None:
    get_sync_db()[COLL_AGENT_JOBS].update_one(
        {"job_id": job_id, "agent_id": agent_id},
        {"$set": {"progress": progress, "updated_at": time.time()}},
    )


def complete_job(job_id: str, agent_id: str, result: Any = None) -> None:
    now = time.time()
    get_sync_db()[COLL_AGENT_JOBS].update_one(
        {"job_id": job_id, "agent_id": agent_id},
        {"$set": {
            "status": "completed",
            "result": result,
            "completed_at": now,
            "updated_at": now,
        }},
    )


def fail_job(job_id: str, agent_id: str, error: str) -> None:
    now = time.time()
    get_sync_db()[COLL_AGENT_JOBS].update_one(
        {"job_id": job_id, "agent_id": agent_id},
        {"$set": {
            "status": "failed",
            "error": error,
            "completed_at": now,
            "updated_at": now,
        }},
    )
