from __future__ import annotations

import time
from typing import Any, Optional

from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import COLL_RUNS, COLL_PROMPTS, COLL_IMAGES, COLL_LLM_TRACES


def create_run(user_id: str, run_id: str, run_data: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    doc = {
        "user_id": user_id,
        "run_id": run_id,
        "status": run_data.get("status", "created"),
        "batch": run_data.get("batch", ""),
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
