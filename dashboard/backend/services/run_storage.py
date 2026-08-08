from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Optional

from dashboard.backend.db.client import get_sync_db
from dashboard.backend.control_plane_policy import validate_metadata_document
from dashboard.backend.db.collections import COLL_RUNS, COLL_PROMPTS, COLL_IMAGES


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
    doc = {
        "user_id": user_id,
        "run_id": run_id,
        "owner_type": owner_type,
        "owner_id": owner_id,
        "run_number": run_number,
        "display_batch": display_batch,
        "status": run_data.get("status", "created"),
        "flow_type": str(run_data.get("flow_type") or "structured"),
        "agent_id": str(run_data.get("agent_id") or ""),
        "device_id": str(run_data.get("device_id") or ""),
        "local_workspace_id": str(run_data.get("local_workspace_id") or ""),
        "local_manifest_resource_id": str(
            run_data.get("local_manifest_resource_id") or ""
        ),
        "local_manifest_version": int(
            run_data.get("local_manifest_version") or 0
        ),
        "prompt_count": int(run_data.get("prompt_count") or 0),
        "image_count": int(run_data.get("image_count") or 0),
        "created_at": now,
        "updated_at": now,
    }
    validate_metadata_document("runs", doc)
    get_sync_db()[COLL_RUNS].insert_one(doc)
    return doc


def get_run(user_id: str, run_id: str) -> Optional[dict[str, Any]]:
    return get_sync_db()[COLL_RUNS].find_one(
        {"user_id": user_id, "run_id": run_id},
    )


def update_run(user_id: str, run_id: str, updates: dict[str, Any]) -> None:
    updates = {**updates, "updated_at": time.time()}
    validate_metadata_document("runs", updates)
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
        "resource_id": prompt_data.get("resource_id", ""),
        "resource_version": int(prompt_data.get("resource_version") or 0),
        "sha256": prompt_data.get("sha256", ""),
        "format": prompt_data.get("format", ""),
        "persona": prompt_data.get("persona", ""),
        "language": prompt_data.get("language", ""),
        "status": prompt_data.get("status", "pending"),
        "created_at": now,
        "updated_at": now,
    }
    validate_metadata_document("prompts", doc)
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
        "artifact_id": image_data.get(
            "artifact_id", image_data.get("image_id", f"art_{int(now)}")
        ),
        "prompt_id": image_data.get("prompt_id", ""),
        "resource_id": image_data.get("resource_id", ""),
        "resource_version": int(image_data.get("resource_version") or 0),
        "device_id": image_data.get("device_id", ""),
        "sha256": image_data.get("sha256", ""),
        "bytes": int(image_data.get("bytes") or 0),
        "width": int(image_data.get("width") or 0),
        "height": int(image_data.get("height") or 0),
        "aspect_ratio": image_data.get("aspect_ratio", ""),
        "status": image_data.get("status", "pending"),
        "created_at": now,
        "updated_at": now,
    }
    validate_metadata_document("images", doc)
    get_sync_db()[COLL_IMAGES].insert_one(doc)
    return doc


def list_images(user_id: str, run_id: str) -> list[dict[str, Any]]:
    return list(
        get_sync_db()[COLL_IMAGES]
        .find({"user_id": user_id, "run_id": run_id})
        .sort("created_at", 1)
    )


def save_llm_trace(user_id: str, trace_data: dict[str, Any]) -> dict[str, Any]:
    del user_id, trace_data
    raise ValueError("Provider traces are stored only on the localhost data plane")


def list_llm_traces(user_id: str, limit: int = 100, skip: int = 0) -> list[dict[str, Any]]:
    del user_id, limit, skip
    return []


def delete_llm_traces(user_id: str, trace_ids: Optional[list[str]] = None) -> int:
    del user_id, trace_ids
    return 0
