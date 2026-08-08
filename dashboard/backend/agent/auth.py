from __future__ import annotations


_RUNTIME_EXACT_PATHS = {
    "/api/agents/heartbeat",
    "/api/agents/jobs/poll",
    "/api/agent-runtime/ws",
}


def is_agent_runtime_path(path: str) -> bool:
    if path in _RUNTIME_EXACT_PATHS:
        return True
    if not path.startswith("/api/agents/jobs/"):
        return False
    suffix = path.removeprefix("/api/agents/jobs/")
    if "/" not in suffix:
        return False
    _job_id, action = suffix.rsplit("/", 1)
    return action in {"status", "claim", "progress", "complete", "fail"}
