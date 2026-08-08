import time
from typing import Any, Optional
from fastapi import APIRouter, Cookie, HTTPException, Query
from dashboard.backend.auth.service import get_current_user_from_cookie
from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import COLL_AGENT_JOBS
from dashboard.backend.agent.service import finalize_disconnected_agent_jobs

router = APIRouter()


def _local_only() -> None:
    raise HTTPException(
        status_code=410,
        detail="Batch generation is available only through the paired localhost data plane",
    )


for _suffix in ("45", "916", "both"):
    router.add_api_route(
        f"/api/batch/generate-images-{_suffix}", _local_only, methods=["POST"]
    )


@router.get("/api/batch/job-status")
def _batch_job_status(
    job_id: str = Query(""),
    session: Optional[str] = Cookie(None),
) -> dict[str, Any]:
    try:
        user = get_current_user_from_cookie(session)
        user_id = user.get("user_id", "") if user else ""
    except Exception:
        return {"active": False, "job": None}

    if not user_id:
        return {"active": False, "job": None}

    db = get_sync_db()
    finalize_disconnected_agent_jobs(user_id)
    if job_id:
        job = db[COLL_AGENT_JOBS].find_one(
            {"user_id": user_id, "job_id": job_id},
            {"_id": 0},
        )
    else:
        job = db[COLL_AGENT_JOBS].find_one(
            {"user_id": user_id, "status": {"$in": ["pending", "running", "cancel_requested"]}},
            {"_id": 0},
            sort=[("created_at", -1)],
        )
    if not job and not job_id:
        job = db[COLL_AGENT_JOBS].find_one(
            {
                "user_id": user_id,
                "status": {"$in": ["completed", "failed", "canceled"]},
                "updated_at": {"$gte": time.time() - 6 * 60 * 60},
            },
            {"_id": 0},
            sort=[("updated_at", -1)],
        )
    if not job:
        return {"active": False, "job": None}

    active = job.get("status") in {"pending", "running", "cancel_requested"}

    return {
        "active": active,
        "job": {
            "job_id": job.get("job_id", ""),
            "status": job.get("status", ""),
            "progress_code": job.get("progress_code", ""),
            "job_type": job.get("job_type", ""),
            "created_at": job.get("created_at", 0),
            "updated_at": job.get("updated_at", 0),
            "error_code": job.get("error_code"),
            "error_message": job.get("error_message"),
        },
    }
