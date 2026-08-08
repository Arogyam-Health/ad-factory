from __future__ import annotations

from typing import Any, Optional

import json
import re
import time

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
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

    run = get_sync_db()[COLL_RUNS].find_one(
        {"run_id": run_id, "user_id": str(user["user_id"])},
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
    operation_id = str((payload or {}).get("operation_id") or "")
    if not operation_id:
        raise HTTPException(status_code=400, detail="Operation ID is required")
    try:
        job = create_job(
            agent_id=str(run["agent_id"]),
            device_id=str(run["device_id"]),
            user_id=str(user["user_id"]),
            owner_type=str(run.get("owner_type") or "user"),
            owner_id=str(run.get("owner_id") or user["user_id"]),
            run_id=run_id,
            job_type="execute_run",
            command="generate_copy",
            parameters={},
            client_operation_id=operation_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
def record_structured_copy_projection(
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
        "error_code",
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
    for key in ("request_sha256", "response_sha256", "copy_sha256"):
        value = projection.get(key)
        if value is not None and not re.fullmatch(r"[a-f0-9]{64}", str(value)):
            raise HTTPException(status_code=400, detail="Projection hash is invalid")
    if projection.get("job_id") != job_id or projection.get("status") not in {
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
    db[COLL_RUNS].update_one(
        {"run_id": projection["run_id"]},
        {
            "$set": {
                "copy_generation": {
                    **projection,
                    "event_id": str(payload.get("event_id") or "")[:80],
                },
                "prompt_count": int(projection.get("prompt_count") or 0),
                "updated_at": time.time(),
            }
        },
    )
    return {"status": "accepted"}


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
