from __future__ import annotations

import time
import uuid
from typing import Any

from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import COLL_LLM_TRACES


MAX_RECENT_TRACES_PER_USER = 5


def record_recent_llm_trace(
    *,
    user_id: str,
    run_id: str,
    batch: str,
    event: dict[str, Any],
) -> dict[str, Any]:
    now = time.time()
    request = event.get("request")
    request = request if isinstance(request, dict) else {}
    response = event.get("response")
    response = response if isinstance(response, dict) else {}
    usage = response.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    doc = {
        "trace_id": "trc_" + uuid.uuid4().hex,
        "user_id": user_id,
        "run_id": run_id,
        "batch": str(batch or "")[:100],
        "provider": str(event.get("provider") or "")[:50],
        "model": str(event.get("model") or "")[:256],
        "api_model": str(event.get("api_model") or "")[:256],
        "endpoint": str(event.get("endpoint") or "")[:2048],
        "label": str(event.get("label") or "copy")[:50],
        "status": (
            "completed"
            if str(event.get("status") or "") == "completed"
            else "failed"
        ),
        "http_status": (
            int(event["http_status"])
            if isinstance(event.get("http_status"), int)
            else None
        ),
        "duration_ms": max(0, int(event.get("duration_ms") or 0)),
        "error_code": str(event.get("error_code") or "")[:100],
        "error_detail": str(event.get("error_detail") or "")[:2000],
        "request": {
            "task": str(request.get("task") or "")[:160],
            "planned_ad_count": max(
                0, int(request.get("planned_ad_count") or 0)
            ),
            "languages": [
                str(value)[:20]
                for value in (
                    request.get("languages")
                    if isinstance(request.get("languages"), list)
                    else []
                )
            ][:10],
            "request_sha256": str(
                request.get("request_sha256") or ""
            )[:64],
        },
        "response": {
            "usage": {
                str(key)[:80]: value
                for key, value in usage.items()
                if isinstance(value, (int, float))
            }
        },
        "created_at": now,
    }
    collection = get_sync_db()[COLL_LLM_TRACES]
    collection.delete_many(
        {"user_id": user_id, "trace_id": {"$exists": False}}
    )
    collection.insert_one(doc)
    stale = list(
        collection.find(
            {"user_id": user_id},
            {"_id": 0, "trace_id": 1, "created_at": 1},
        )
        .sort("created_at", -1)
        .skip(MAX_RECENT_TRACES_PER_USER)
    )
    stale_ids = [
        str(item.get("trace_id") or "")
        for item in stale
        if str(item.get("trace_id") or "")
    ]
    if stale_ids:
        collection.delete_many(
            {"user_id": user_id, "trace_id": {"$in": stale_ids}}
        )
    return {key: value for key, value in doc.items() if key != "user_id"}


def list_recent_llm_traces(
    user_id: str,
    *,
    run_id: str = "",
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"user_id": user_id}
    if run_id:
        query["run_id"] = run_id
    return list(
        get_sync_db()[COLL_LLM_TRACES]
        .find(
            query,
            {
                "_id": 0,
                "user_id": 0,
                "trace_id": 1,
                "run_id": 1,
                "batch": 1,
                "provider": 1,
                "model": 1,
                "api_model": 1,
                "endpoint": 1,
                "label": 1,
                "status": 1,
                "http_status": 1,
                "duration_ms": 1,
                "error_code": 1,
                "error_detail": 1,
                "request": 1,
                "response": 1,
                "created_at": 1,
            },
        )
        .sort("created_at", -1)
        .limit(MAX_RECENT_TRACES_PER_USER)
    )


def delete_recent_llm_traces(
    user_id: str,
    *,
    trace_ids: list[str] | None = None,
) -> int:
    query: dict[str, Any] = {"user_id": user_id}
    if trace_ids is not None:
        query["trace_id"] = {"$in": trace_ids}
    return int(get_sync_db()[COLL_LLM_TRACES].delete_many(query).deleted_count)
