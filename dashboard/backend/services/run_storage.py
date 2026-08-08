from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Optional

from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import COLL_RUNS, COLL_PROMPTS, COLL_IMAGES, COLL_LLM_TRACES


def reserve_run_number(
    owner_type: str,
    owner_id: str,
    *,
    collection: Any | None = None,
) -> int:
    """Atomically reserve the next display number within an owner scope."""
    if not owner_type or not owner_id:
        raise ValueError("owner_type and owner_id are required")
    if collection is None:
        from pymongo import ReturnDocument
        from dashboard.backend.db.collections import COLL_RUN_COUNTERS

        db = get_sync_db()
        collection = db[COLL_RUN_COUNTERS]
        scope = {"owner_type": owner_type, "owner_id": owner_id}
        if collection.find_one(scope, {"_id": 1}) is None:
            latest = db[COLL_RUNS].find_one(
                {**scope, "run_number": {"$exists": True}},
                {"run_number": 1},
                sort=[("run_number", -1)],
            )
            highest = int((latest or {}).get("run_number") or 0)
            if owner_type == "user":
                legacy_runs = db[COLL_RUNS].find(
                    {"user_id": owner_id, "batch": {"$regex": r"^v\d+$"}},
                    {"batch": 1},
                )
                for legacy in legacy_runs:
                    match = re.fullmatch(r"v(\d+)", str(legacy.get("batch") or ""), flags=re.IGNORECASE)
                    if match:
                        highest = max(highest, int(match.group(1)))
            collection.update_one(
                scope,
                {"$setOnInsert": {
                    **scope,
                    "value": highest,
                    "created_at": time.time(),
                }},
                upsert=True,
            )
        return_document = ReturnDocument.AFTER
    else:
        return_document = True
    doc = collection.find_one_and_update(
        {"owner_type": owner_type, "owner_id": owner_id},
        {
            "$inc": {"value": 1},
            "$setOnInsert": {"created_at": time.time()},
            "$set": {"updated_at": time.time()},
        },
        upsert=True,
        return_document=return_document,
    )
    return int(doc["value"])


def build_storage_batch(run_number: int, run_id: str) -> str:
    """Return a globally unique directory key while keeping vN as the display label."""
    suffix = hashlib.sha256(run_id.encode()).hexdigest()[:12]
    return f"v{int(run_number)}-{suffix}"


def create_run(user_id: str, run_id: str, run_data: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    owner_type = str(run_data.get("owner_type") or "user")
    owner_id = str(run_data.get("owner_id") or user_id)
    run_number = int(run_data.get("run_number") or reserve_run_number(owner_type, owner_id))
    display_batch = str(run_data.get("display_batch") or f"v{run_number}")
    batch = str(run_data.get("batch") or build_storage_batch(run_number, run_id))
    doc = {
        "user_id": user_id,
        "run_id": run_id,
        "owner_type": owner_type,
        "owner_id": owner_id,
        "run_number": run_number,
        "display_batch": display_batch,
        "status": run_data.get("status", "created"),
        "batch": batch,
        "config": run_data.get("config", {}),
        "created_at": now,
        "updated_at": now,
    }
    get_sync_db()[COLL_RUNS].insert_one(doc)
    return doc


def get_run(user_id: str, run_id: str) -> Optional[dict[str, Any]]:
    return get_sync_db()[COLL_RUNS].find_one(
        {"user_id": user_id, "run_id": run_id},
    )


def update_run(user_id: str, run_id: str, updates: dict[str, Any]) -> None:
    updates["updated_at"] = time.time()
    get_sync_db()[COLL_RUNS].update_one(
        {"user_id": user_id, "run_id": run_id},
        {"$set": updates},
    )


def list_runs(user_id: str, limit: int = 50, skip: int = 0) -> list[dict[str, Any]]:
    return list(
        get_sync_db()[COLL_RUNS]
        .find({"user_id": user_id})
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
    )


def delete_run(user_id: str, run_id: str) -> None:
    get_sync_db()[COLL_RUNS].delete_one({"user_id": user_id, "run_id": run_id})
    get_sync_db()[COLL_PROMPTS].delete_many({"user_id": user_id, "run_id": run_id})
    get_sync_db()[COLL_IMAGES].delete_many({"user_id": user_id, "run_id": run_id})


def save_prompt(user_id: str, run_id: str, prompt_data: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    doc = {
        "user_id": user_id,
        "run_id": run_id,
        "prompt_id": prompt_data.get("prompt_id", f"p_{int(now)}"),
        "batch": prompt_data.get("batch", ""),
        "format": prompt_data.get("format", ""),
        "persona": prompt_data.get("persona", ""),
        "language": prompt_data.get("language", ""),
        "content": prompt_data.get("content", ""),
        "filename": prompt_data.get("filename", ""),
        "status": prompt_data.get("status", "pending"),
        "created_at": now,
        "updated_at": now,
    }
    get_sync_db()[COLL_PROMPTS].insert_one(doc)
    return doc


def list_prompts(user_id: str, run_id: str) -> list[dict[str, Any]]:
    return list(
        get_sync_db()[COLL_PROMPTS]
        .find({"user_id": user_id, "run_id": run_id})
        .sort("created_at", 1)
    )


def save_image_metadata(user_id: str, run_id: str, image_data: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    doc = {
        "user_id": user_id,
        "run_id": run_id,
        "image_id": image_data.get("image_id", f"img_{int(now)}"),
        "batch": image_data.get("batch", ""),
        "format": image_data.get("format", ""),
        "filename": image_data.get("filename", ""),
        "storage_url": image_data.get("storage_url", ""),
        "local_path": image_data.get("local_path", ""),
        "status": image_data.get("status", "pending"),
        "metadata": image_data.get("metadata", {}),
        "created_at": now,
        "updated_at": now,
    }
    get_sync_db()[COLL_IMAGES].insert_one(doc)
    return doc


def list_images(user_id: str, run_id: str) -> list[dict[str, Any]]:
    return list(
        get_sync_db()[COLL_IMAGES]
        .find({"user_id": user_id, "run_id": run_id})
        .sort("created_at", 1)
    )


MAX_TRACE_RUNS = 5


def _enforce_trace_retention(user_id: str) -> None:
    """Delete traces from runs older than the most recent MAX_TRACE_RUNS per user."""
    coll = get_sync_db()[COLL_LLM_TRACES]
    pipeline = [
        {"$match": {"user_id": user_id}},
        {"$group": {"_id": "$run_id", "latest": {"$max": "$created_at"}}},
        {"$sort": {"latest": -1}},
        {"$skip": MAX_TRACE_RUNS},
    ]
    old_run_ids = [doc["_id"] for doc in coll.aggregate(pipeline)]
    if old_run_ids:
        coll.delete_many({"user_id": user_id, "run_id": {"$in": old_run_ids}})


def save_llm_trace(user_id: str, trace_data: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    doc = {
        "user_id": user_id,
        "run_id": trace_data.get("run_id", ""),
        "batch": trace_data.get("batch", ""),
        "provider": trace_data.get("provider", ""),
        "model": trace_data.get("model", ""),
        "prompt": trace_data.get("prompt", ""),
        "response": trace_data.get("response", ""),
        "duration_ms": trace_data.get("duration_ms", 0),
        "token_count": trace_data.get("token_count", 0),
        "status": trace_data.get("status", "completed"),
        "created_at": now,
    }
    get_sync_db()[COLL_LLM_TRACES].insert_one(doc)
    _enforce_trace_retention(user_id)
    return doc


def list_llm_traces(user_id: str, limit: int = 100, skip: int = 0) -> list[dict[str, Any]]:
    return list(
        get_sync_db()[COLL_LLM_TRACES]
        .find({"user_id": user_id})
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
    )


def delete_llm_traces(user_id: str, trace_ids: Optional[list[str]] = None) -> int:
    query: dict[str, Any] = {"user_id": user_id}
    if trace_ids:
        query["_id"] = {"$in": trace_ids}
    result = get_sync_db()[COLL_LLM_TRACES].delete_many(query)
    return result.deleted_count
