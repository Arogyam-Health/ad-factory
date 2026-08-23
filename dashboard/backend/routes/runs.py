from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request

from dashboard.backend.agent.service import cancel_user_job, create_job
from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import (
    COLL_AGENT_JOBS,
    COLL_AGENTS,
    COLL_IMAGES,
    COLL_LLM_TRACES,
    COLL_PROMPT_DELIVERIES,
    COLL_PROMPTS,
    COLL_RENDER_COPY_JOBS,
    COLL_RUN_COUNTERS,
    COLL_RUNS,
)
from dashboard.backend.services.run_storage import rewind_run_counter

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
    "copy_job_id": 1,
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
        from dashboard.backend.auth.service import get_current_user_from_cookie

        cookies = getattr(request, "cookies", None) or {}
        session = cookies.get("session") if hasattr(cookies, "get") else None
        cookie_user = get_current_user_from_cookie(session)
        user_id = str((cookie_user or {}).get("user_id") or "")
        if cookie_user is not None:
            request.state.user = cookie_user
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user_id


def delete_prompt_metadata_record(
    db: Any,
    *,
    run: dict[str, Any],
    user_id: str,
    run_id: str,
    prompt_id: str,
    resource_id: str = "",
) -> tuple[int, int]:
    deleted_ids = {
        str(value)
        for value in run.get("deleted_prompt_ids", [])
        if isinstance(value, str)
    }
    result = db[COLL_PROMPTS].delete_one(
        {
            "user_id": user_id,
            "run_id": run_id,
            "prompt_id": prompt_id,
        }
    )
    if prompt_id in deleted_ids:
        return int(result.deleted_count), int(run.get("prompt_count") or 0)

    copy_generation = run.get("copy_generation")
    projected_ids = (
        copy_generation.get("prompt_ids", [])
        if isinstance(copy_generation, dict)
        else []
    )
    if isinstance(projected_ids, list) and projected_ids:
        prompt_count = sum(
            1 for value in projected_ids if str(value) != prompt_id
        )
    else:
        prompt_count = max(0, int(run.get("prompt_count") or 0) - 1)
    pull: dict[str, Any] = {"copy_generation.prompt_ids": prompt_id}
    if resource_id:
        pull["copy_generation.prompt_resource_ids"] = resource_id
    db[COLL_RUNS].update_one(
        {"user_id": user_id, "run_id": run_id},
        {
            "$set": {"prompt_count": prompt_count, "updated_at": time.time()},
            "$addToSet": {"deleted_prompt_ids": prompt_id},
            "$pull": pull,
        },
    )
    return int(result.deleted_count), prompt_count


@router.get("/api/runs")
def list_runs(request: Request) -> dict[str, Any]:
    user_id = _user_id(request)
    query: dict[str, Any] = {
        "user_id": user_id,
        "status": {"$nin": ["deleted"]},
    }
    flow = str(request.query_params.get("flow") or "").strip().lower()
    if flow == "reference":
        query["flow_type"] = {"$in": ["reference", "reference_image"]}
    elif flow == "structured":
        query["flow_type"] = {"$nin": ["reference", "reference_image"]}
    rows = list(
        get_sync_db()[COLL_RUNS]
        .find(
            query,
            _RUN_PROJECTION,
        )
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


@router.delete("/api/runs/{run_id}/prompts/{prompt_id}")
def delete_run_prompt_metadata(
    run_id: str, prompt_id: str, request: Request
) -> dict[str, Any]:
    user_id = _user_id(request)
    db = get_sync_db()
    run = db[COLL_RUNS].find_one(
        {"user_id": user_id, "run_id": run_id},
        {
            "_id": 0,
            "run_id": 1,
            "prompt_count": 1,
            "copy_generation": 1,
            "deleted_prompt_ids": 1,
        },
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    deleted, prompt_count = delete_prompt_metadata_record(
        db,
        run=run,
        user_id=user_id,
        run_id=run_id,
        prompt_id=prompt_id,
    )
    return {
        "run_id": run_id,
        "prompt_id": prompt_id,
        "deleted": deleted,
        "prompt_count": prompt_count,
    }


@router.patch("/api/runs/{run_id}/prompts/{prompt_id}")
def update_run_prompt_metadata(
    run_id: str,
    prompt_id: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    user_id = _user_id(request)
    db = get_sync_db()
    run = db[COLL_RUNS].find_one({"user_id": user_id, "run_id": run_id}, {"_id": 1})
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    updates: dict[str, Any] = {"updated_at": time.time()}
    if isinstance(payload.get("sha256"), str) and payload["sha256"]:
        updates["sha256"] = payload["sha256"][:64]
    if isinstance(payload.get("resource_version"), int):
        updates["resource_version"] = payload["resource_version"]
    result = db[COLL_PROMPTS].update_one(
        {"user_id": user_id, "run_id": run_id, "prompt_id": prompt_id},
        {"$set": updates},
    )
    if not result.matched_count:
        raise HTTPException(status_code=404, detail="Prompt metadata not found")
    return {"run_id": run_id, "prompt_id": prompt_id, "status": "updated"}


@router.delete("/api/runs/{run_id}/images/{output_id}")
def delete_run_image_metadata(
    run_id: str, output_id: str, request: Request
) -> dict[str, Any]:
    user_id = _user_id(request)
    db = get_sync_db()
    run = db[COLL_RUNS].find_one(
        {"user_id": user_id, "run_id": run_id},
        {"_id": 0, "image_count": 1},
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    result = db[COLL_IMAGES].delete_one(
        {
            "user_id": user_id,
            "run_id": run_id,
            "$or": [
                {"output_id": output_id},
                {"artifact_id": output_id},
                {"image_id": output_id},
            ],
        }
    )
    image_count = max(0, int(run.get("image_count") or 0) - int(result.deleted_count))
    db[COLL_RUNS].update_one(
        {"user_id": user_id, "run_id": run_id},
        {"$set": {"image_count": image_count, "updated_at": time.time()}},
    )
    return {
        "run_id": run_id,
        "output_id": output_id,
        "deleted": int(result.deleted_count),
        "image_count": image_count,
    }


@router.post("/api/runs/reconcile-local")
def reconcile_local_runs(
    request: Request, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    user_id = _user_id(request)
    agent_id = str(payload.get("agent_id") or "")
    device_id = str(payload.get("device_id") or "")
    owner_type = str(payload.get("owner_type") or "")
    owner_id = str(payload.get("owner_id") or "")
    local_run_ids = payload.get("local_run_ids")
    if (
        not agent_id
        or not device_id
        or owner_type not in {"user", "org"}
        or not owner_id
        or not isinstance(local_run_ids, list)
        or len(local_run_ids) > 500
        or any(not isinstance(run_id, str) or not run_id for run_id in local_run_ids)
    ):
        raise HTTPException(status_code=400, detail="Invalid local run inventory")

    db = get_sync_db()
    agent = db[COLL_AGENTS].find_one(
        {
            "user_id": user_id,
            "agent_id": agent_id,
            "device_id": device_id,
        },
        {"_id": 0, "agent_id": 1, "device_id": 1},
    )
    if agent is None:
        raise HTTPException(status_code=403, detail="Local device is not authorized")
    if owner_type == "user" and owner_id != user_id:
        raise HTTPException(status_code=403, detail="Run owner is not authorized")

    candidates = list(
        db[COLL_RUNS].find(
            {
                "user_id": user_id,
                "agent_id": agent_id,
                "device_id": device_id,
                "owner_type": owner_type,
                "owner_id": owner_id,
                            "copy_job_id": {"$exists": False},
                "created_at": {"$lt": time.time() - 120},
                "status": {"$nin": ["queued", "running", "deleting", "purge_failed"]},
            },
            {"_id": 0, "run_id": 1},
        )
    )
    local_ids = set(local_run_ids)
    stale_ids = sorted(
        str(row["run_id"])
        for row in candidates
        if str(row.get("run_id") or "") not in local_ids
    )
    confirm = bool(payload.get("confirm"))
    if not stale_ids or not confirm:
        return {"removed": 0, "run_ids": stale_ids, "pending": stale_ids}

    content_scope = {
        "user_id": user_id,
        "run_id": {"$in": stale_ids},
    }
    db[COLL_PROMPTS].delete_many(content_scope)
    db[COLL_IMAGES].delete_many(content_scope)
    db[COLL_AGENT_JOBS].delete_many(content_scope)
    db[COLL_LLM_TRACES].delete_many(content_scope)
    db[COLL_RUNS].delete_many(
        {
            **content_scope,
            "device_id": device_id,
        }
    )
    return {"removed": len(stale_ids), "run_ids": stale_ids}


def purge_run_metadata(db: Any, *, user_id: str, run_id: str) -> None:
    """Drop every metadata document Render holds for a single run."""
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
    db[COLL_RUNS].delete_one(scope)
    if run:
        rewind_run_counter(
            str(run.get("owner_type") or "user"),
            str(run.get("owner_id") or user_id),
            str(run.get("flow_type") or "structured"),
            db=db,
        )


def delete_run_for_user(db: Any, *, user_id: str, run_id: str) -> dict[str, Any]:
    """Queue a local purge for device-bound runs; hard-delete everything else.

    A run with no authoritative device has no local content to reclaim, so it
    would otherwise be undeletable from the dashboard.
    """
    run = db[COLL_RUNS].find_one(
        {"run_id": run_id, "user_id": user_id},
        {
            "_id": 0,
            "agent_id": 1,
            "device_id": 1,
            "owner_type": 1,
            "owner_id": 1,
            "copy_job_id": 1,
            "deletion_tombstone": 1,
            "status": 1,
        },
    )
    if not run or not run.get("agent_id") or not run.get("device_id"):
        purge_run_metadata(db, user_id=user_id, run_id=run_id)
        return {"status": "deleted", "run_id": run_id}
    tombstone = run.get("deletion_tombstone") or {}
    operation_id = str(tombstone.get("operation_id") or "")
    if str(run.get("status") or "") == "purge_failed":
        operation_id = "purge_" + uuid.uuid4().hex
    else:
        operation_id = operation_id or "purge_" + uuid.uuid4().hex
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
        allow_inactive_agent=True,
    )
    return {
        "status": "deleting",
        "run_id": run_id,
        "tombstone_operation_id": operation_id,
        "purge_job_id": job["job_id"],
    }


@router.delete("/api/runs/{run_id}")
def delete_run(run_id: str, request: Request) -> dict[str, Any]:
    return delete_run_for_user(
        get_sync_db(), user_id=_user_id(request), run_id=run_id
    )


@router.post("/api/runs/bulk-delete")
def bulk_delete_runs(
    request: Request, payload: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    user_id = _user_id(request)
    run_ids = payload.get("run_ids")
    if (
        not isinstance(run_ids, list)
        or not run_ids
        or len(run_ids) > 500
        or any(not isinstance(run_id, str) or not run_id.strip() for run_id in run_ids)
    ):
        raise HTTPException(status_code=400, detail="Provide 1-500 run ids to delete")
    db = get_sync_db()
    results: list[dict[str, Any]] = []
    for run_id in dict.fromkeys(str(value).strip() for value in run_ids):
        try:
            results.append(delete_run_for_user(db, user_id=user_id, run_id=run_id))
        except HTTPException as exc:
            results.append(
                {"status": "error", "run_id": run_id, "detail": str(exc.detail)}
            )
    return {
        "results": results,
        "deleted": sum(1 for item in results if item["status"] == "deleted"),
        "deleting": sum(1 for item in results if item["status"] == "deleting"),
        "failed": sum(1 for item in results if item["status"] == "error"),
    }


@router.post("/api/runs/purge-all")
def purge_all_user_runs(
    request: Request, payload: dict[str, Any] = Body(default={})
) -> dict[str, Any]:
    user_id = _user_id(request)
    if str(payload.get("confirm") or "").strip().upper() != "PURGE":
        raise HTTPException(
            status_code=400,
            detail="Type PURGE to confirm deleting every run owned by this account",
        )
    db = get_sync_db()
    scope = {"user_id": user_id}
    prompts = db[COLL_PROMPTS].delete_many(scope).deleted_count
    images = db[COLL_IMAGES].delete_many(scope).deleted_count
    deliveries = db[COLL_PROMPT_DELIVERIES].delete_many(scope).deleted_count
    copy_jobs = db[COLL_RENDER_COPY_JOBS].delete_many(scope).deleted_count
    jobs = db[COLL_AGENT_JOBS].delete_many(scope).deleted_count
    traces = db[COLL_LLM_TRACES].delete_many(scope).deleted_count
    runs = db[COLL_RUNS].delete_many(scope).deleted_count
    counters = db[COLL_RUN_COUNTERS].delete_many({"owner_id": user_id}).deleted_count
    return {
        "status": "purged",
        "runs": runs,
        "prompts": prompts,
        "images": images,
        "prompt_deliveries": deliveries,
        "render_copy_jobs": copy_jobs,
        "agent_jobs": jobs,
        "llm_traces": traces,
        "run_counters": counters,
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
        from dashboard.backend.services.render_copy_jobs import (
            cancel_render_copy_run,
        )

        canceled_copy = cancel_render_copy_run(run_id, user_id)
        if canceled_copy is None:
            raise HTTPException(status_code=404, detail="Active run job not found")
        return canceled_copy
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
