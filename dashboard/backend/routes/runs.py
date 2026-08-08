from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from dashboard.backend.agent.service import cancel_user_job, create_job
from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import (
    COLL_AGENT_JOBS,
    COLL_IMAGES,
    COLL_PROMPTS,
    COLL_RUNS,
)

router = APIRouter()

_RUN_PROJECTION = {
    "_id": 0,
    "run_id": 1,
    "owner_type": 1,
    "owner_id": 1,
    "created_by_user_id": 1,
    "agent_id": 1,
    "device_id": 1,
    "run_number": 1,
    "display_batch": 1,
    "flow_type": 1,
    "status": 1,
    "local_workspace_id": 1,
    "local_manifest_resource_id": 1,
    "local_manifest_version": 1,
    "prompt_count": 1,
    "image_count": 1,
    "copy_generation": 1,
    "image_generation": 1,
    "deletion_tombstone": 1,
    "created_at": 1,
    "updated_at": 1,
}


def _user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    user_id = str((user or {}).get("user_id") or "")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user_id


@router.get("/api/runs")
def list_runs(request: Request) -> dict[str, Any]:
    user_id = _user_id(request)
    rows = list(
        get_sync_db()[COLL_RUNS]
        .find({"user_id": user_id}, _RUN_PROJECTION)
        .sort("created_at", -1)
        .limit(200)
    )
    return {"runs": rows, "total": len(rows)}


@router.get("/api/runs/{run_id}")
def get_run(run_id: str, request: Request) -> dict[str, Any]:
    user_id = _user_id(request)
    row = get_sync_db()[COLL_RUNS].find_one(
        {"run_id": run_id, "user_id": user_id}, _RUN_PROJECTION
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return row


@router.get("/api/runs/{run_id}/partial")
def get_partial_run(run_id: str, request: Request) -> dict[str, Any]:
    return get_run(run_id, request)


@router.get("/api/runs/{run_id}/prompts")
def list_run_prompts(run_id: str, request: Request) -> dict[str, Any]:
    user_id = _user_id(request)
    rows = list(
        get_sync_db()[COLL_PROMPTS]
        .find(
            {"run_id": run_id, "user_id": user_id},
            {
                "_id": 0,
                "prompt_id": 1,
                "run_id": 1,
                "resource_id": 1,
                "resource_version": 1,
                "sha256": 1,
                "format": 1,
                "persona": 1,
                "language": 1,
                "status": 1,
            },
        )
        .sort("created_at", 1)
        .limit(500)
    )
    return {"run_id": run_id, "prompts": rows, "total": len(rows)}


@router.get("/api/runs/{run_id}/images")
def list_run_images(run_id: str, request: Request) -> dict[str, Any]:
    user_id = _user_id(request)
    rows = list(
        get_sync_db()[COLL_IMAGES]
        .find(
            {"run_id": run_id, "user_id": user_id},
            {
                "_id": 0,
                "artifact_id": 1,
                "run_id": 1,
                "prompt_id": 1,
                "resource_id": 1,
                "resource_version": 1,
                "device_id": 1,
                "sha256": 1,
                "bytes": 1,
                "width": 1,
                "height": 1,
                "aspect_ratio": 1,
                "status": 1,
            },
        )
        .sort("created_at", 1)
        .limit(500)
    )
    return {"run_id": run_id, "images": rows, "total": len(rows)}


@router.delete("/api/runs/{run_id}")
def delete_run(run_id: str, request: Request) -> dict[str, Any]:
    user_id = _user_id(request)
    db = get_sync_db()
    run = db[COLL_RUNS].find_one(
        {"run_id": run_id, "user_id": user_id},
        {
            "_id": 0,
            "agent_id": 1,
            "device_id": 1,
            "owner_type": 1,
            "owner_id": 1,
        },
    )
    if not run or not run.get("agent_id") or not run.get("device_id"):
        raise HTTPException(
            status_code=409, detail="Run has no authoritative local device"
        )
    operation_id = "purge_" + uuid.uuid4().hex
    now = time.time()
    db[COLL_RUNS].update_one(
        {"run_id": run_id, "user_id": user_id},
        {
            "$set": {
                "status": "deleting",
                "deletion_tombstone": {
                    "operation_id": operation_id,
                    "device_id": run["device_id"],
                    "created_at": now,
                    "acknowledged_at": None,
                },
                "updated_at": now,
            }
        },
    )
    db[COLL_PROMPTS].delete_many({"user_id": user_id, "run_id": run_id})
    db[COLL_IMAGES].delete_many({"user_id": user_id, "run_id": run_id})
    job = create_job(
        agent_id=str(run["agent_id"]),
        device_id=str(run["device_id"]),
        user_id=user_id,
        owner_type=str(run.get("owner_type") or "user"),
        owner_id=str(run.get("owner_id") or user_id),
        run_id=run_id,
        job_type="purge_run",
        command="purge_run",
        parameters={},
        client_operation_id=operation_id,
    )
    return {
        "status": "deleting",
        "run_id": run_id,
        "tombstone_operation_id": operation_id,
        "purge_job_id": job["job_id"],
    }


@router.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str, request: Request) -> dict[str, Any]:
    user_id = _user_id(request)
    job = get_sync_db()[COLL_AGENT_JOBS].find_one(
        {
            "run_id": run_id,
            "user_id": user_id,
            "status": {"$in": ["pending", "running", "cancel_requested"]},
        },
        {"_id": 0, "job_id": 1},
        sort=[("created_at", -1)],
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Active run job not found")
    canceled = cancel_user_job(user_id, str(job["job_id"]))
    return {"status": str((canceled or {}).get("status") or ""), "run_id": run_id}


def _local_only(run_id: str) -> None:
    del run_id
    raise HTTPException(
        status_code=410,
        detail="Content lifecycle operations are available only through localhost",
    )


for _suffix, _methods in (
    ("prompt-copies", ["GET", "POST"]),
    ("edit-prompt", ["POST"]),
    ("delete-prompt", ["POST"]),
    ("delete-image", ["POST"]),
    ("revise-image", ["POST"]),
    ("revisions/{revision_id}", ["GET"]),
    ("mark-images-to-regenerate", ["POST"]),
    ("restore-images-from-queue", ["POST"]),
    ("regenerate-queued-images", ["POST"]),
    ("replace-image", ["POST"]),
    ("download-image", ["GET"]),
    ("download-batch", ["GET"]),
):
    router.add_api_route(
        f"/api/runs/{{run_id}}/{_suffix}", _local_only, methods=_methods
    )
