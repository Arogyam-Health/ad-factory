from __future__ import annotations

import hashlib
import time
from typing import Any, Optional

from dashboard.backend.db.client import get_sync_db
from dashboard.backend.control_plane_policy import validate_metadata_document
from dashboard.backend.db.collections import (
    COLL_AGENT_JOBS,
    COLL_FILE_MAP,
    COLL_IMAGES,
    COLL_LLM_TRACES,
    COLL_PROMPT_DELIVERIES,
    COLL_PROMPTS,
    COLL_RENDER_COPY_JOBS,
    COLL_RUNS,
)

REFERENCE_FLOW_TYPES = frozenset({"reference", "reference_image"})
_DEAD_RUN_STATUSES = frozenset({"deleted", "deleting", "purge_failed"})


def numbering_scope(flow_type: str | None) -> str:
    """Structured and Reference keep independent vN sequences."""
    return "reference" if str(flow_type or "").strip().lower() in REFERENCE_FLOW_TYPES else "structured"


def _flow_numbering_query(flow_type: str | None) -> dict[str, Any]:
    family = numbering_scope(flow_type)
    if family == "reference":
        return {
            "$or": [
                {"flow_family": "reference"},
                {"flow_type": {"$in": sorted(REFERENCE_FLOW_TYPES)}},
            ]
        }
    return {
        "$and": [
            {"flow_family": {"$ne": "reference"}},
            {"flow_type": {"$nin": sorted(REFERENCE_FLOW_TYPES)}},
        ]
    }


def display_batch_label(flow_type: str | None, run_number: int) -> str:
    if numbering_scope(flow_type) == "reference":
        return f"ref_v{int(run_number)}"
    return f"v{int(run_number)}"


def _live_run_query(account_id: str, flow_type: str | None) -> dict[str, Any]:
    return {
        "user_id": account_id,
        "status": {"$nin": sorted(_DEAD_RUN_STATUSES)},
        "run_number": {"$exists": True},
        **_flow_numbering_query(flow_type),
    }


def _used_run_numbers(db: Any, account_id: str, flow_type: str | None) -> set[int]:
    used: set[int] = set()
    for doc in db[COLL_RUNS].find(_live_run_query(account_id, flow_type), {"run_number": 1}):
        try:
            number = int(doc.get("run_number") or 0)
        except (TypeError, ValueError):
            continue
        if number > 0:
            used.add(number)
    return used


def next_available_run_number(
    account_id: str,
    flow_type: str = "structured",
    *,
    db: Any | None = None,
) -> int:
    """Lowest unused vN for this dashboard account and flow."""
    used = _used_run_numbers(db or get_sync_db(), account_id, flow_type)
    number = 1
    while number in used:
        number += 1
    return number


def reserve_run_number(
    owner_type: str,
    owner_id: str,
    flow_type: str = "structured",
    *,
    collection: Any | None = None,
    user_id: str | None = None,
    db: Any | None = None,
) -> int:
    """Reserve the lowest free display number for this account and flow.

    Names belong to the signed-in dashboard user, not the org and not another
    account on the same machine. Structured and Reference keep separate
    sequences. Deleting v3 makes the next structured plate v3 again.
    """
    account_id = str(user_id or owner_id or "")
    if not account_id:
        raise ValueError("owner_type and owner_id are required")
    if collection is not None:
        family = numbering_scope(flow_type)
        doc = collection.find_one_and_update(
            {
                "owner_type": owner_type,
                "owner_id": owner_id,
                "flow_family": family,
            },
            {
                "$inc": {"value": 1},
                "$setOnInsert": {"created_at": time.time()},
                "$set": {"updated_at": time.time()},
            },
            upsert=True,
            return_document=True,
        )
        return int(doc["value"])
    return next_available_run_number(account_id, flow_type, db=db)


def rewind_run_counter(
    owner_type: str,
    owner_id: str,
    flow_type: str = "structured",
    *,
    db: Any | None = None,
) -> int:
    """Point this flow's counter at the highest remaining run (0 if none)."""
    if not owner_type or not owner_id:
        return 0
    from dashboard.backend.db.collections import COLL_RUN_COUNTERS

    db = db or get_sync_db()
    family = numbering_scope(flow_type)
    used = _used_run_numbers(db, str(owner_id), flow_type)
    highest = max(used) if used else 0
    now = time.time()
    db[COLL_RUN_COUNTERS].update_one(
        {
            "owner_type": owner_type,
            "owner_id": owner_id,
            "flow_family": family,
        },
        {
            "$set": {
                "owner_type": owner_type,
                "owner_id": owner_id,
                "flow_family": family,
                "value": highest,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    return highest


def build_storage_batch(run_number: int, run_id: str) -> str:
    """Return a globally unique directory key while keeping vN as the display label."""
    suffix = hashlib.sha256(run_id.encode()).hexdigest()[:12]
    return f"v{int(run_number)}-{suffix}"


def create_run(user_id: str, run_id: str, run_data: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    owner_type = str(run_data.get("owner_type") or "user")
    owner_id = str(run_data.get("owner_id") or user_id)
    flow_type = str(run_data.get("flow_type") or "structured")
    run_number = int(
        run_data.get("run_number")
        or reserve_run_number(
            owner_type,
            owner_id,
            flow_type,
            user_id=user_id,
        )
    )
    display_batch = str(run_data.get("display_batch") or display_batch_label(flow_type, run_number))
    doc = {
        "user_id": user_id,
        "run_id": run_id,
        "owner_type": owner_type,
        "owner_id": owner_id,
        "run_number": run_number,
        "display_batch": display_batch,
        "status": run_data.get("status", "created"),
        "flow_type": flow_type,
        "flow_family": numbering_scope(flow_type),
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


def purge_run_metadata(db: Any, *, user_id: str, run_id: str) -> None:
    """Drop every control-plane document for one run. Local bytes are separate."""
    scope = {"run_id": run_id, "user_id": user_id}
    run = db[COLL_RUNS].find_one(
        scope, {"_id": 0, "owner_type": 1, "owner_id": 1, "user_id": 1, "flow_type": 1}
    )
    db[COLL_PROMPT_DELIVERIES].delete_many(scope)
    db[COLL_RENDER_COPY_JOBS].delete_many(scope)
    db[COLL_PROMPTS].delete_many(scope)
    db[COLL_IMAGES].delete_many(scope)
    db[COLL_AGENT_JOBS].delete_many(scope)
    db[COLL_LLM_TRACES].delete_many(scope)
    db[COLL_FILE_MAP].delete_many({"run_id": run_id})
    db[COLL_RUNS].delete_one(scope)
    if run:
        rewind_run_counter(
            str(run.get("owner_type") or "user"),
            str(run.get("user_id") or user_id),
            str(run.get("flow_type") or "structured"),
            db=db,
        )


def delete_run(user_id: str, run_id: str) -> None:
    purge_run_metadata(get_sync_db(), user_id=user_id, run_id=run_id)


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
