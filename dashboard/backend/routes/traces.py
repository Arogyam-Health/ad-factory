from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Response

from dashboard.backend.auth.service import require_user_dependency
from dashboard.backend.services.llm_trace import (
    delete_recent_llm_traces,
    list_traces_for_viewer,
)


router = APIRouter()


@router.get("/api/llm-traces")
def get_llm_traces(
    response: Response,
    run_id: str = "",
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    listed = list_traces_for_viewer(
        str(user["user_id"]),
        run_id=str(run_id or "")[:200],
    )
    personal = listed.get("personal") or listed.get("traces") or []
    org_traces = listed.get("org") or []
    response.headers["Cache-Control"] = "no-store"
    return {
        "personal": personal,
        "org": org_traces,
        "org_name": listed.get("org_name") or "",
        "traces": personal,
        "total": len(personal) + len(org_traces),
        "offset": 0,
        "limit": listed["limit"],
        "scope": listed["scope"],
    }


@router.delete("/api/llm-traces")
def delete_all_llm_traces(
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, int]:
    return {"deleted": delete_recent_llm_traces(str(user["user_id"]))}


@router.delete("/api/llm-traces/{trace_id}")
def delete_llm_trace(
    trace_id: str,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, int]:
    if not trace_id.startswith("trc_") or len(trace_id) > 80:
        raise HTTPException(status_code=400, detail="Trace ID is invalid")
    return {
        "deleted": delete_recent_llm_traces(
            str(user["user_id"]),
            trace_ids=[trace_id],
        )
    }


@router.post("/api/llm-traces/delete-batch")
def delete_llm_trace_batch(
    payload: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, int]:
    trace_ids = payload.get("trace_ids")
    if (
        not isinstance(trace_ids, list)
        or len(trace_ids) > 10
        or any(
            not isinstance(value, str)
            or not value.startswith("trc_")
            or len(value) > 80
            for value in trace_ids
        )
    ):
        raise HTTPException(status_code=400, detail="Trace IDs are invalid")
    return {
        "deleted": delete_recent_llm_traces(
            str(user["user_id"]),
            trace_ids=trace_ids,
        )
    }
