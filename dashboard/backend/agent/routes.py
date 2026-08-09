from __future__ import annotations

from typing import Any, Optional

import json
import re
import time

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from dashboard.backend.agent.service import (
    PAIRING_SCOPES,
    acknowledge_pairing_approval,
    allocate_run_envelope,
    authenticate_agent,
    bind_agent_device,
    cancel_user_job,
    claim_job,
    complete_job,
    create_job,
    fail_job,
    heartbeat_agent,
    get_job_status_for_agent,
    list_user_agents,
    poll_jobs,
    poll_pairing_approvals,
    register_agent,
    request_pairing_approval,
    update_job_progress,
)
from dashboard.backend.auth.service import require_user_dependency
from dashboard.backend.agent.connections import agent_connections

router = APIRouter()


def _get_agent_from_header(authorization: str = Header("")) -> dict[str, Any]:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing agent token")
    token = authorization[7:]
    agent = authenticate_agent(token)
    if agent is None:
        raise HTTPException(status_code=401, detail="Invalid agent token")
    return agent


@router.post("/api/agents/register")
def register_agent_endpoint(
    payload: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    try:
        device_id = str(payload.get("device_id") or "")
        protocol_version = str(payload.get("protocol_version") or "")
        if not device_id or not protocol_version:
            raise ValueError("Device ID and protocol version are required")
        return register_agent(
            user["user_id"],
            payload.get("name", "default-agent"),
            device_id=device_id,
            protocol_version=protocol_version,
            supports_pairing=payload.get("supports_pairing") is True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/agents")
def list_agents(
    user: dict[str, Any] = Depends(require_user_dependency),
) -> list[dict[str, Any]]:
    return list_user_agents(user["user_id"])


def _config_reference_owner(user_id: str, scope: str, owner_id: str) -> str:
    if scope == "personal":
        if owner_id and owner_id != user_id:
            raise HTTPException(status_code=403, detail="Personal config owner is invalid")
        return user_id
    if scope not in {"org_individual", "org_shared"} or not owner_id:
        raise HTTPException(status_code=400, detail="Config reference scope is invalid")
    from dashboard.backend.db.client import get_sync_db
    from dashboard.backend.db.collections import COLL_ORG_MEMBERS

    membership = get_sync_db()[COLL_ORG_MEMBERS].find_one(
        {"org_id": owner_id, "user_id": user_id, "status": "active"},
        {"_id": 1},
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="Organization access is required")
    return owner_id if scope == "org_shared" else f"{owner_id}:{user_id}"


@router.put("/api/local-config-references/{logical_key}")
def put_local_config_reference(
    logical_key: str,
    payload: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    from dashboard.backend.db.client import get_sync_db
    from dashboard.backend.db.collections import COLL_LOCAL_CONFIG_REFERENCES

    allowed = {
        "scope",
        "owner_id",
        "resource_id",
        "resource_version",
        "authority_device_id",
        "verified_replica_device_ids",
        "sha256",
    }
    if set(payload) - allowed:
        raise HTTPException(status_code=400, detail="Config references accept metadata only")
    scope = str(payload.get("scope") or "")
    owner_id = _config_reference_owner(
        str(user["user_id"]), scope, str(payload.get("owner_id") or "")
    )
    resource_id = str(payload.get("resource_id") or "")
    authority = str(payload.get("authority_device_id") or "")
    replicas = payload.get("verified_replica_device_ids") or []
    if (
        not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", logical_key)
        or not re.fullmatch(r"res_[A-Za-z0-9]{8,64}", resource_id)
        or not re.fullmatch(r"dev_[A-Za-z0-9]{8,64}", authority)
        or not isinstance(replicas, list)
        or len(replicas) > 32
        or any(not re.fullmatch(r"dev_[A-Za-z0-9]{8,64}", str(item)) for item in replicas)
    ):
        raise HTTPException(status_code=400, detail="Config reference metadata is invalid")
    document = {
        "scope": scope,
        "owner_id": owner_id,
        "logical_key": logical_key,
        "resource_id": resource_id,
        "resource_version": int(payload.get("resource_version") or 0),
        "sha256": str(payload.get("sha256") or "")[:64],
        "authority_device_id": authority,
        "verified_replica_device_ids": sorted(set(map(str, replicas))),
        "updated_at": time.time(),
    }
    get_sync_db()[COLL_LOCAL_CONFIG_REFERENCES].update_one(
        {"scope": scope, "owner_id": owner_id, "logical_key": logical_key},
        {"$set": document, "$setOnInsert": {"created_at": time.time()}},
        upsert=True,
    )
    return {**document, "status": "referenced"}


@router.get("/api/local-config-references")
def list_local_config_references(
    scope: str = Query(...),
    owner_id: str = Query(""),
    user: dict[str, Any] = Depends(require_user_dependency),
) -> list[dict[str, Any]]:
    from dashboard.backend.db.client import get_sync_db
    from dashboard.backend.db.collections import COLL_AGENTS, COLL_LOCAL_CONFIG_REFERENCES

    resolved_owner = _config_reference_owner(str(user["user_id"]), scope, owner_id)
    db = get_sync_db()
    rows = list(
        db[COLL_LOCAL_CONFIG_REFERENCES].find(
            {"scope": scope, "owner_id": resolved_owner}, {"_id": 0}
        )
    )
    devices = {
        str(device)
        for row in rows
        for device in [
            row.get("authority_device_id"),
            *(row.get("verified_replica_device_ids") or []),
        ]
        if device
    }
    online = {
        str(row["device_id"])
        for row in db[COLL_AGENTS].find(
            {
                "device_id": {"$in": sorted(devices)},
                "is_active": True,
                "last_heartbeat_at": {"$gte": time.time() - 180},
            },
            {"_id": 0, "device_id": 1},
        )
    }
    for row in rows:
        candidates = {
            str(row.get("authority_device_id") or ""),
            *map(str, row.get("verified_replica_device_ids") or []),
        }
        row["status"] = "available" if candidates & online else "unavailable"
        row["available_device_ids"] = sorted(candidates & online)
    return rows


@router.post("/api/runs/allocate")
def allocate_run(
    payload: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    try:
        settings = payload.get("settings")
        return allocate_run_envelope(
            user_id=str(user["user_id"]),
            owner_type=str(payload.get("owner_type") or "user"),
            owner_id=str(payload.get("owner_id") or user["user_id"]),
            agent_id=str(payload.get("agent_id") or ""),
            device_id=str(payload.get("device_id") or ""),
            flow_type=str(payload.get("flow_type") or ""),
            settings=settings if isinstance(settings, dict) else {},
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/runs/{run_id}/structured-copy")
def queue_structured_copy(
    run_id: str,
    payload: dict[str, Any] | None = Body(default=None),
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    from dashboard.backend.db.client import get_sync_db
    from dashboard.backend.db.collections import COLL_RUNS
    from dashboard.backend.services.render_copy_jobs import (
        enqueue_render_copy_job,
    )

    run = get_sync_db()[COLL_RUNS].find_one(
        {"run_id": run_id, "user_id": str(user["user_id"])},
        {
            "_id": 0,
            "agent_id": 1,
            "device_id": 1,
            "owner_type": 1,
            "owner_id": 1,
            "run_number": 1,
        },
    )
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    operation_id = str((payload or {}).get("operation_id") or "")
    settings = (payload or {}).get("settings")
    try:
        job = enqueue_render_copy_job(
            run={"run_id": run_id, **run},
            user_id=str(user["user_id"]),
            settings=settings if isinstance(settings, dict) else {},
            client_operation_id=operation_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "copy_job_id": job["copy_job_id"],
        "run_id": run_id,
        "status": job["status"],
        "progress_code": job["progress_code"],
    }


@router.post("/api/runs/allocate-copy")
def allocate_render_structured_copy_run(
    payload: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    from dashboard.backend.services.render_copy_jobs import (
        allocate_render_copy_run,
    )

    try:
        return allocate_render_copy_run(
            user_id=str(user["user_id"]),
            owner_type=str(payload.get("owner_type") or ""),
            owner_id=str(payload.get("owner_id") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/runs/{run_id}/structured-copy/{copy_job_id}")
def get_structured_copy_status(
    run_id: str,
    copy_job_id: str,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    from dashboard.backend.services.render_copy_jobs import copy_job_status

    status = copy_job_status(copy_job_id, str(user["user_id"]))
    if status is None or str(status.get("run_id") or "") != run_id:
        raise HTTPException(status_code=404, detail="Structured copy job not found")
    return status


@router.post("/api/runs/{run_id}/image-generation")
def queue_structured_image_generation(
    run_id: str,
    payload: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    from dashboard.backend.db.client import get_sync_db
    from dashboard.backend.db.collections import COLL_RUNS

    db = get_sync_db()
    run = db[COLL_RUNS].find_one(
        {
            "run_id": run_id,
            "user_id": str(user["user_id"]),
        },
        {
            "_id": 0,
            "agent_id": 1,
            "device_id": 1,
            "owner_type": 1,
            "owner_id": 1,
            "flow_type": 1,
        },
    )
    if (
        not run
        or run.get("flow_type") == "reference"
        or not run.get("agent_id")
        or not run.get("device_id")
    ):
        raise HTTPException(
            status_code=409, detail="Run has no authoritative local device"
        )
    operation_id = str(payload.get("operation_id") or "")
    engine = str(payload.get("engine") or "").lower()
    mode = str(payload.get("mode") or "").lower()
    if not operation_id:
        raise HTTPException(status_code=400, detail="Operation ID is required")
    if engine not in {"chatgpt", "gemini"} or mode not in {"45", "both", "916"}:
        raise HTTPException(
            status_code=400, detail="Image generation settings are invalid"
        )
    try:
        job = create_job(
            agent_id=str(run["agent_id"]),
            device_id=str(run["device_id"]),
            user_id=str(user["user_id"]),
            owner_type=str(run.get("owner_type") or "user"),
            owner_id=str(run.get("owner_id") or user["user_id"]),
            run_id=run_id,
            job_type="execute_run",
            command="generate_images",
            parameters={"engine": engine, "mode": mode},
            client_operation_id=operation_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db[COLL_RUNS].update_one(
        {"run_id": run_id, "user_id": str(user["user_id"])},
        {
            "$set": {
                "status": "queued",
                "updated_at": time.time(),
                "image_job_id": job["job_id"],
            }
        },
    )
    return {
        "job_id": job["job_id"],
        "run_id": run_id,
        "status": job["status"],
        "agent_id": job["agent_id"],
        "device_id": job["device_id"],
    }


@router.post("/api/runs/{run_id}/reference-generation")
def queue_reference_generation(
    run_id: str,
    payload: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    from dashboard.backend.db.client import get_sync_db
    from dashboard.backend.db.collections import COLL_RUNS

    run = get_sync_db()[COLL_RUNS].find_one(
        {
            "run_id": run_id,
            "user_id": str(user["user_id"]),
            "flow_type": "reference",
        },
        {
            "_id": 0,
            "agent_id": 1,
            "device_id": 1,
            "owner_type": 1,
            "owner_id": 1,
        },
    )
    if not run or not run.get("agent_id") or not run.get("device_id"):
        raise HTTPException(status_code=409, detail="Run has no authoritative local device")
    operation_id = str(payload.get("operation_id") or "")
    engine = str(payload.get("engine") or "").lower()
    mode = str(payload.get("mode") or "").lower()
    if not operation_id:
        raise HTTPException(status_code=400, detail="Operation ID is required")
    if engine not in {"chatgpt", "gemini"} or mode not in {"45", "both", "916"}:
        raise HTTPException(status_code=400, detail="Reference generation settings are invalid")
    try:
        job = create_job(
            agent_id=str(run["agent_id"]),
            device_id=str(run["device_id"]),
            user_id=str(user["user_id"]),
            owner_type=str(run.get("owner_type") or "user"),
            owner_id=str(run.get("owner_id") or user["user_id"]),
            run_id=run_id,
            job_type="execute_run",
            command="generate_reference",
            parameters={"engine": engine, "mode": mode},
            client_operation_id=operation_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    get_sync_db()[COLL_RUNS].update_one(
        {"run_id": run_id, "user_id": str(user["user_id"])},
        {
            "$set": {
                "status": "queued",
                "updated_at": time.time(),
                "reference_job_id": job["job_id"],
            }
        },
    )
    return {
        "job_id": job["job_id"],
        "run_id": run_id,
        "status": job["status"],
        "agent_id": job["agent_id"],
        "device_id": job["device_id"],
    }


@router.post("/api/agents/heartbeat")
def agent_heartbeat(
    agent: dict[str, Any] = Depends(_get_agent_from_header),
) -> dict[str, str]:
    heartbeat_agent(agent["agent_id"])
    return {"status": "ok"}


@router.post("/api/agents/device")
def register_agent_device(
    payload: dict[str, Any] = Body(...),
    agent: dict[str, Any] = Depends(_get_agent_from_header),
) -> dict[str, Any]:
    try:
        return bind_agent_device(
            str(agent["agent_id"]),
            str(payload.get("device_id") or ""),
            str(payload.get("protocol_version") or ""),
            payload.get("supports_pairing") is True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/agents/pairing/challenges")
def approve_browser_pairing(
    payload: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    scopes = payload.get("scopes")
    try:
        return request_pairing_approval(
            user_id=str(user["user_id"]),
            owner_type=str(payload.get("owner_type") or "user"),
            owner_id=str(payload.get("owner_id") or user["user_id"]),
            agent_id=str(payload.get("agent_id") or ""),
            device_id=str(payload.get("device_id") or ""),
            challenge_id=str(payload.get("challenge_id") or ""),
            challenge=str(payload.get("challenge") or ""),
            scopes=list(scopes) if isinstance(scopes, list) else sorted(PAIRING_SCOPES),
        )
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/api/agents/pairing/approvals")
def poll_browser_pairings(
    agent: dict[str, Any] = Depends(_get_agent_from_header),
) -> list[dict[str, Any]]:
    return poll_pairing_approvals(
        str(agent["agent_id"]), str(agent.get("device_id") or "")
    )


@router.post("/api/agents/pairing/approvals/{challenge_id}/ack")
def acknowledge_browser_pairing(
    challenge_id: str,
    agent: dict[str, Any] = Depends(_get_agent_from_header),
) -> dict[str, str]:
    if not acknowledge_pairing_approval(
        challenge_id, str(agent["agent_id"]), str(agent.get("device_id") or "")
    ):
        raise HTTPException(status_code=404, detail="Pairing approval not found")
    return {"challenge_id": challenge_id, "status": "delivered"}


@router.websocket("/api/agent-runtime/ws")
async def agent_runtime_websocket(websocket: WebSocket) -> None:
    authorization = str(websocket.headers.get("authorization") or "")
    if not authorization.startswith("Bearer "):
        await websocket.close(code=4001, reason="Missing agent token")
        return
    agent = await run_in_threadpool(authenticate_agent, authorization[7:])
    if agent is None:
        await websocket.close(code=4001, reason="Invalid agent token")
        return
    agent_id = str(agent["agent_id"])
    device_id = str(agent.get("device_id") or "")
    await websocket.accept()
    await agent_connections.register(
        agent_id, str(agent["user_id"]), websocket, device_id=device_id
    )
    await run_in_threadpool(heartbeat_agent, agent_id)
    await websocket.send_json({"type": "connected", "agent_id": agent_id, "heartbeat_seconds": 15})
    if await run_in_threadpool(poll_jobs, agent_id, device_id):
        await websocket.send_json({"type": "job_available"})
    for approval in await run_in_threadpool(
        poll_pairing_approvals, agent_id, device_id
    ):
        await websocket.send_json(approval)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "code": "invalid_json"})
                continue
            if message.get("type") in {"ping", "heartbeat"}:
                await run_in_threadpool(heartbeat_agent, agent_id)
                connection = agent_connections.get(agent_id)
                if connection is not None:
                    connection.last_seen_at = time.time()
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        await agent_connections.unregister(agent_id, websocket)


@router.get("/api/agents/jobs/poll")
def poll_for_jobs(
    agent: dict[str, Any] = Depends(_get_agent_from_header),
) -> list[dict[str, Any]]:
    return poll_jobs(str(agent["agent_id"]), str(agent.get("device_id") or ""))


@router.get("/api/agents/jobs/{job_id}/status")
def get_agent_job_status(
    job_id: str,
    agent: dict[str, Any] = Depends(_get_agent_from_header),
) -> dict[str, Any]:
    job = get_job_status_for_agent(
        job_id, str(agent["agent_id"]), str(agent.get("device_id") or "")
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/api/agents/jobs/{job_id}/cancel")
def cancel_agent_job(
    job_id: str,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    job = cancel_user_job(user["user_id"], job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": job.get("status", ""), "job_id": job_id}


@router.post("/api/agents/jobs/{job_id}/claim")
def claim_agent_job(
    job_id: str,
    payload: dict[str, Any] | None = Body(default=None),
    agent: dict[str, Any] = Depends(_get_agent_from_header),
) -> dict[str, Any]:
    job = claim_job(
        job_id,
        str(agent["agent_id"]),
        str(agent.get("device_id") or ""),
        str((payload or {}).get("claim_id") or ""),
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found or already claimed")
    return job


@router.post("/api/agents/jobs/{job_id}/progress")
def report_progress(
    job_id: str,
    payload: dict[str, Any] = Body(...),
    agent: dict[str, Any] = Depends(_get_agent_from_header),
) -> dict[str, str]:
    accepted = update_job_progress(
        job_id,
        str(agent["agent_id"]),
        str(agent.get("device_id") or ""),
        int(payload.get("fence") or 0),
        str(payload.get("progress_code") or ""),
    )
    if not accepted:
        raise HTTPException(status_code=409, detail="Stale or invalid job progress update")
    return {"status": "ok"}


@router.post("/api/agents/jobs/{job_id}/projection")
def record_local_generation_projection(
    job_id: str,
    payload: dict[str, Any] = Body(...),
    agent: dict[str, Any] = Depends(_get_agent_from_header),
) -> dict[str, str]:
    from dashboard.backend.db.client import get_sync_db
    from dashboard.backend.db.collections import COLL_AGENT_JOBS, COLL_RUNS

    projection = payload.get("projection")
    allowed = {
        "job_id",
        "run_id",
        "status",
        "provider",
        "model",
        "duration_ms",
        "input_tokens",
        "output_tokens",
        "request_sha256",
        "response_sha256",
        "copy_sha256",
        "copy_count",
        "prompt_count",
        "prompt_ids",
        "prompt_resource_ids",
        "asset_count",
        "repair_count",
        "copy_resource_id",
        "copy_resource_version",
        "trace_resource_id",
        "trace_resource_version",
        "settings_resource_id",
        "settings_resource_version",
        "product_document_resource_id",
        "product_document_version",
        "engine",
        "mode",
        "total_count",
        "completed_count",
        "output_count",
        "retry_count",
        "latest_output_id",
        "latest_output_version",
        "latest_output_sha256",
        "error_code",
        "flow_type",
        "reference_count",
        "persona_count",
    }
    if not isinstance(projection, dict) or set(projection) - allowed:
        raise HTTPException(status_code=400, detail="Projection contains unsupported fields")
    serialized = json.dumps(projection, separators=(",", ":"))
    forbidden = (
        "request_body",
        "response_body",
        "prompt_body",
        "config_body",
        "api_key",
        "client_secret",
        "localhost",
        "127.0.0.1",
        "file://",
    )
    if len(serialized) > 8192 or any(value in serialized.lower() for value in forbidden):
        raise HTTPException(status_code=400, detail="Projection is not bounded metadata")
    identifier = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")
    for key in ("prompt_ids", "prompt_resource_ids"):
        values = projection.get(key, [])
        if (
            not isinstance(values, list)
            or len(values) > 500
            or any(not isinstance(value, str) or not identifier.fullmatch(value) for value in values)
        ):
            raise HTTPException(status_code=400, detail="Projection IDs are invalid")
    for key in (
        "request_sha256",
        "response_sha256",
        "copy_sha256",
        "latest_output_sha256",
    ):
        value = projection.get(key)
        if value is not None and not re.fullmatch(r"[a-f0-9]{64}", str(value)):
            raise HTTPException(status_code=400, detail="Projection hash is invalid")
    if projection.get("job_id") != job_id or projection.get("status") not in {
        "running",
        "completed",
        "failed",
    }:
        raise HTTPException(status_code=400, detail="Projection identity is invalid")
    db = get_sync_db()
    job = db[COLL_AGENT_JOBS].find_one(
        {
            "job_id": job_id,
            "agent_id": str(agent["agent_id"]),
            "device_id": str(agent.get("device_id") or ""),
            "fence": int(payload.get("fence") or 0),
            "status": {"$in": ["running", "completed", "failed"]},
        },
        {"_id": 0, "run_id": 1},
    )
    if not job or str(job.get("run_id") or "") != str(projection.get("run_id") or ""):
        raise HTTPException(status_code=409, detail="Stale or invalid projection")
    projection_field = (
        "image_generation"
        if any(
            key in projection
            for key in ("output_count", "completed_count", "latest_output_id")
        )
        else "copy_generation"
    )
    updates: dict[str, Any] = {
        projection_field: {
            **projection,
            "event_id": str(payload.get("event_id") or "")[:80],
        },
        "updated_at": time.time(),
    }
    if projection_field == "image_generation":
        updates["image_count"] = int(projection.get("completed_count") or 0)
        if "prompt_count" in projection:
            updates["prompt_count"] = int(projection.get("prompt_count") or 0)
        if projection.get("flow_type") == "reference":
            updates["flow_type"] = "reference"
            updates["status"] = projection["status"]
    else:
        updates["prompt_count"] = int(projection.get("prompt_count") or 0)
    from dashboard.backend.control_plane_policy import validate_metadata_document

    validate_metadata_document("runs", updates)
    db[COLL_RUNS].update_one(
        {"run_id": projection["run_id"]},
        {"$set": updates},
    )
    return {"status": "accepted"}


@router.post("/api/agents/reconciliation/prompt-deleted")
def reconcile_deleted_prompt(
    payload: dict[str, Any] = Body(...),
    agent: dict[str, Any] = Depends(_get_agent_from_header),
) -> dict[str, Any]:
    from dashboard.backend.db.client import get_sync_db
    from dashboard.backend.db.collections import COLL_RUNS
    from dashboard.backend.routes.runs import delete_prompt_metadata_record

    run_id = str(payload.get("run_id") or "")
    prompt_id = str(payload.get("prompt_id") or "")
    resource_id = str(payload.get("resource_id") or "")
    if not run_id or not prompt_id:
        raise HTTPException(status_code=400, detail="Prompt identity is required")
    db = get_sync_db()
    run = db[COLL_RUNS].find_one(
        {
            "run_id": run_id,
            "user_id": str(agent["user_id"]),
            "agent_id": str(agent["agent_id"]),
            "device_id": str(agent.get("device_id") or ""),
        },
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
        user_id=str(agent["user_id"]),
        run_id=run_id,
        prompt_id=prompt_id,
        resource_id=resource_id,
    )
    return {
        "status": "accepted",
        "deleted": deleted,
        "prompt_count": prompt_count,
    }


@router.get("/api/agents/prompt-deliveries/poll")
def poll_agent_prompt_deliveries(
    response: Response,
    agent: dict[str, Any] = Depends(_get_agent_from_header),
) -> list[dict[str, Any]]:
    from dashboard.backend.services.render_copy_jobs import poll_prompt_deliveries

    response.headers["Cache-Control"] = "no-store"
    return poll_prompt_deliveries(agent)


@router.post("/api/agents/prompt-deliveries/{delivery_id}/ack")
def acknowledge_agent_prompt_delivery(
    delivery_id: str,
    payload: dict[str, Any] = Body(...),
    agent: dict[str, Any] = Depends(_get_agent_from_header),
) -> dict[str, Any]:
    from dashboard.backend.services.render_copy_jobs import (
        acknowledge_prompt_delivery,
    )

    prompt_ids = payload.get("prompt_ids")
    if (
        not isinstance(prompt_ids, list)
        or len(prompt_ids) > 500
        or any(not isinstance(value, str) or not value for value in prompt_ids)
    ):
        raise HTTPException(
            status_code=400,
            detail="Prompt delivery acknowledgement is invalid",
        )
    try:
        return acknowledge_prompt_delivery(
            delivery_id,
            agent,
            prompt_ids=prompt_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/agents/runs/{run_id}/image-context")
def materialize_agent_image_context(
    run_id: str,
    response: Response,
    agent: dict[str, Any] = Depends(_get_agent_from_header),
) -> dict[str, Any]:
    from dashboard.backend.db.client import get_sync_db
    from dashboard.backend.db.collections import COLL_RUNS
    from dashboard.backend.services.user_config import resolve_effective_config

    run = get_sync_db()[COLL_RUNS].find_one(
        {
            "run_id": run_id,
            "user_id": str(agent["user_id"]),
            "agent_id": str(agent["agent_id"]),
            "device_id": str(agent.get("device_id") or ""),
        },
        {"_id": 0, "owner_type": 1, "owner_id": 1},
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    config = resolve_effective_config(
        str(agent["user_id"]),
        str(run.get("owner_id") or "") if run.get("owner_type") == "org" else None,
    )
    conversion_prompt = str(config.get("conversion_916_prompt") or "").strip()
    response.headers["Cache-Control"] = "no-store"
    return {"run_id": run_id, "conversion_916_prompt": conversion_prompt}


@router.post("/api/agents/jobs/{job_id}/complete")
def complete_agent_job(
    job_id: str,
    payload: dict[str, Any] = Body(...),
    agent: dict[str, Any] = Depends(_get_agent_from_header),
) -> dict[str, str]:
    accepted = complete_job(
        job_id,
        str(agent["agent_id"]),
        str(agent.get("device_id") or ""),
        int(payload.get("fence") or 0),
        str(payload.get("event_id") or ""),
    )
    if not accepted:
        raise HTTPException(status_code=409, detail="Stale or invalid job completion")
    return {"status": "completed"}


@router.post("/api/agents/jobs/{job_id}/fail")
def fail_agent_job(
    job_id: str,
    payload: dict[str, Any] = Body(...),
    agent: dict[str, Any] = Depends(_get_agent_from_header),
) -> dict[str, str]:
    accepted = fail_job(
        job_id,
        str(agent["agent_id"]),
        str(agent.get("device_id") or ""),
        int(payload.get("fence") or 0),
        str(payload.get("event_id") or ""),
        str(payload.get("error_code") or "job_failed"),
        str(payload.get("error_message") or ""),
    )
    if not accepted:
        raise HTTPException(status_code=409, detail="Stale or invalid job failure")
    return {"status": "failed"}
