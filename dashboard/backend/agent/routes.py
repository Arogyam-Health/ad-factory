from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query

from dashboard.backend.agent.service import (
    authenticate_agent,
    cancel_user_job,
    claim_job,
    complete_job,
    create_job,
    fail_job,
    heartbeat_agent,
    get_job_status_for_agent,
    list_user_agents,
    poll_jobs,
    register_agent,
    update_job_progress,
)
from dashboard.backend.auth.service import require_user_dependency

router = APIRouter()


def _get_agent_from_header(authorization: str = Header("")) -> dict[str, Any]:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing agent token")
    token = authorization[7:]
    agent = authenticate_agent(token)
    if agent is None:
        raise HTTPException(status_code=401, detail="Invalid agent token")
    heartbeat_agent(agent["agent_id"])
    return agent


@router.post("/api/agents/register")
def register_agent_endpoint(
    payload: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    return register_agent(
        user["user_id"],
        payload.get("name", "default-agent"),
        payload.get("description", ""),
    )


@router.get("/api/agents")
def list_agents(
    user: dict[str, Any] = Depends(require_user_dependency),
) -> list[dict[str, Any]]:
    return list_user_agents(user["user_id"])


@router.post("/api/agents/heartbeat")
def agent_heartbeat(
    agent: dict[str, Any] = Depends(_get_agent_from_header),
) -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/agents/jobs/poll")
def poll_for_jobs(
    agent: dict[str, Any] = Depends(_get_agent_from_header),
) -> list[dict[str, Any]]:
    return poll_jobs(agent["agent_id"])


@router.get("/api/agents/jobs/{job_id}/status")
def get_agent_job_status(
    job_id: str,
    agent: dict[str, Any] = Depends(_get_agent_from_header),
) -> dict[str, Any]:
    job = get_job_status_for_agent(job_id, agent["agent_id"])
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
    agent: dict[str, Any] = Depends(_get_agent_from_header),
) -> dict[str, Any]:
    job = claim_job(job_id, agent["agent_id"])
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found or already claimed")
    return job


@router.post("/api/agents/jobs/{job_id}/progress")
def report_progress(
    job_id: str,
    payload: dict[str, Any] = Body(...),
    agent: dict[str, Any] = Depends(_get_agent_from_header),
) -> dict[str, str]:
    update_job_progress(job_id, payload.get("progress", ""), agent["agent_id"])
    return {"status": "ok"}


@router.post("/api/agents/jobs/{job_id}/complete")
def complete_agent_job(
    job_id: str,
    payload: dict[str, Any] = Body(...),
    agent: dict[str, Any] = Depends(_get_agent_from_header),
) -> dict[str, str]:
    complete_job(job_id, agent["agent_id"], payload.get("result"))
    return {"status": "completed"}


@router.post("/api/agents/jobs/{job_id}/fail")
def fail_agent_job(
    job_id: str,
    payload: dict[str, Any] = Body(...),
    agent: dict[str, Any] = Depends(_get_agent_from_header),
) -> dict[str, str]:
    fail_job(job_id, agent["agent_id"], payload.get("error", "Unknown error"))
    return {"status": "failed"}
