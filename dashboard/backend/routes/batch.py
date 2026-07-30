from typing import Any, Optional
from fastapi import APIRouter, Body, Cookie, Request

from dashboard.backend.app import (
    api_batch_generate_images_45,
    api_batch_generate_images_916,
    api_batch_generate_images_both,
)
from dashboard.backend.auth.service import get_current_user_from_cookie
from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import COLL_AGENT_JOBS
import dashboard.backend.services.cdp_proxy  # noqa: F401 — eager import to capture main event loop

router = APIRouter()


def _resolve_user_id(request: Request, session: Optional[str]) -> str:
    try:
        user = get_current_user_from_cookie(session)
        return user.get("user_id", "") if user else ""
    except Exception:
        return ""


@router.post("/api/batch/generate-images-45")
def _batch_generate_45(request: Request, payload: dict[str, Any] = Body(...), session: Optional[str] = Cookie(None)) -> dict[str, Any]:
    return api_batch_generate_images_45(payload, user_id=_resolve_user_id(request, session))


@router.post("/api/batch/generate-images-916")
def _batch_generate_916(request: Request, payload: dict[str, Any] = Body(...), session: Optional[str] = Cookie(None)) -> dict[str, Any]:
    return api_batch_generate_images_916(payload, user_id=_resolve_user_id(request, session))


@router.post("/api/batch/generate-images-both")
def _batch_generate_both(request: Request, payload: dict[str, Any] = Body(...), session: Optional[str] = Cookie(None)) -> dict[str, Any]:
    return api_batch_generate_images_both(payload, user_id=_resolve_user_id(request, session))


@router.get("/api/batch/job-status")
def _batch_job_status(session: Optional[str] = Cookie(None)) -> dict[str, Any]:
    try:
        user = get_current_user_from_cookie(session)
        user_id = user.get("user_id", "") if user else ""
    except Exception:
        return {"active": False, "job": None}

    if not user_id:
        return {"active": False, "job": None}

    job = get_sync_db()[COLL_AGENT_JOBS].find_one(
        {"user_id": user_id, "status": {"$in": ["pending", "running"]}},
        {"_id": 0, "result": 0},
        sort=[("created_at", -1)],
    )
    if not job:
        return {"active": False, "job": None}

    return {
        "active": True,
        "job": {
            "job_id": job.get("job_id", ""),
            "status": job.get("status", ""),
            "progress": job.get("progress", ""),
            "job_type": job.get("job_type", ""),
            "created_at": job.get("created_at", 0),
        },
    }
