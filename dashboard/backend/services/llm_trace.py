from __future__ import annotations

import time
import uuid
from typing import Any

from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import COLL_LLM_TRACES, COLL_USERS


MAX_RECENT_TRACES_PER_USER = 5
MAX_RECENT_TRACES_PER_ORG = 5
MAX_TRACE_TEXT = 200_000

_TRACE_PROJECTION = {
    "_id": 0,
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
    "org_id": 1,
    "actor_email": 1,
    "display_name": 1,
}


def _actor_fields(user_id: str) -> dict[str, str]:
    try:
        user = get_sync_db()[COLL_USERS].find_one(
            {"user_id": user_id},
            {"_id": 0, "email": 1, "display_name": 1},
        ) or {}
    except Exception:
        user = {}
    return {
        "actor_email": str(user.get("email") or "")[:320],
        "display_name": str(user.get("display_name") or "")[:200],
    }


def _prune_stale(collection: Any, query: dict[str, Any], keep: int) -> None:
    recent = list(
        collection.find(
            query,
            {"_id": 0, "run_id": 1, "created_at": 1},
        ).sort("created_at", -1)
    )
    keep_run_ids: list[str] = []
    seen: set[str] = set()
    for item in recent:
        run_id = str(item.get("run_id") or "")
        if not run_id or run_id in seen:
            continue
        seen.add(run_id)
        keep_run_ids.append(run_id)
        if len(keep_run_ids) >= keep:
            break
    if not keep_run_ids:
        return
    prune_query = dict(query)
    prune_query["run_id"] = {"$nin": keep_run_ids}
    collection.delete_many(prune_query)


def record_recent_llm_trace(
    *,
    user_id: str,
    run_id: str,
    batch: str,
    event: dict[str, Any],
    org_id: str = "",
) -> dict[str, Any]:
    now = time.time()
    request = event.get("request")
    request = request if isinstance(request, dict) else {}
    response = event.get("response")
    response = response if isinstance(response, dict) else {}
    usage = response.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    clean_org_id = str(org_id or "")[:80]
    actor = _actor_fields(user_id)
    doc = {
        "trace_id": "trc_" + uuid.uuid4().hex,
        "user_id": user_id,
        "run_id": run_id,
        "org_id": clean_org_id,
        "actor_email": actor["actor_email"],
        "display_name": actor["display_name"],
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
            "prompt": str(request.get("prompt") or "")[:MAX_TRACE_TEXT],
        },
        "response": {
            "usage": {
                str(key)[:80]: value
                for key, value in usage.items()
                if isinstance(value, (int, float))
            },
            "content": str(response.get("content") or "")[:MAX_TRACE_TEXT],
        },
        "created_at": now,
    }
    collection = get_sync_db()[COLL_LLM_TRACES]
    collection.delete_many(
        {"user_id": user_id, "trace_id": {"$exists": False}}
    )
    collection.insert_one(doc)
    if clean_org_id:
        _prune_stale(
            collection,
            {"org_id": clean_org_id},
            MAX_RECENT_TRACES_PER_ORG,
        )
    else:
        _prune_stale(
            collection,
            {"user_id": user_id},
            MAX_RECENT_TRACES_PER_USER,
        )
    return {
        key: value
        for key, value in doc.items()
        if key != "user_id"
    }


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
        .find(query, _TRACE_PROJECTION)
        .sort("created_at", -1)
    )


def list_org_llm_traces(
    org_id: str,
    *,
    run_id: str = "",
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"org_id": org_id}
    if run_id:
        query["run_id"] = run_id
    return list(
        get_sync_db()[COLL_LLM_TRACES]
        .find(query, _TRACE_PROJECTION)
        .sort("created_at", -1)
    )


def list_traces_for_viewer(
    user_id: str,
    *,
    run_id: str = "",
) -> dict[str, Any]:
    from dashboard.backend.services.org_helper import get_user_default_org

    org = get_user_default_org(user_id) or {}
    if org.get("config_mode") == "shared_org_config" and org.get("org_id"):
        traces = list_org_llm_traces(str(org["org_id"]), run_id=run_id)
        return {
            "traces": traces,
            "scope": "org",
            "limit": MAX_RECENT_TRACES_PER_ORG,
        }
    traces = list_recent_llm_traces(user_id, run_id=run_id)
    return {
        "traces": traces,
        "scope": "personal",
        "limit": MAX_RECENT_TRACES_PER_USER,
    }


def delete_recent_llm_traces(
    user_id: str,
    *,
    trace_ids: list[str] | None = None,
) -> int:
    query: dict[str, Any] = {"user_id": user_id}
    if trace_ids is not None:
        query["trace_id"] = {"$in": trace_ids}
    return int(get_sync_db()[COLL_LLM_TRACES].delete_many(query).deleted_count)
