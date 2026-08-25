from __future__ import annotations

"""Render entry point: stateless metadata-only control plane."""

import os
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from dashboard.backend.agent.auth import is_agent_runtime_path
from dashboard.backend.auth.service import get_current_user_from_cookie
from dashboard.backend.control_plane_policy import is_render_content_route
from dashboard.backend.db.settings import (
    settings,
    validate_production_settings,
)


ROOT = Path(__file__).resolve().parents[2]
PUBLIC_API_PREFIXES = (
    "/api/auth/",
    "/api/invites/",
    "/api/public/",
    "/api/guide",
    "/api/docs/",
)

app = FastAPI(title="Ad Factory Control Plane", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.middleware("http")
async def control_plane_boundary(request: Request, call_next) -> Response:
    path = str(request.scope.get("path") or request.url.path or "").split("?", 1)[0]
    is_agent_bearer = is_agent_runtime_path(path) and request.headers.get(
        "Authorization", ""
    ).startswith("Bearer ")
    if path.startswith("/api/") and not path.startswith(PUBLIC_API_PREFIXES) and not is_agent_bearer:
        user = await run_in_threadpool(
            get_current_user_from_cookie, request.cookies.get("session")
        )
        if user is not None:
            request.state.user = user
        elif settings.is_production:
            return JSONResponse(
                {"detail": "Not authenticated"}, status_code=401
            )
    if is_render_content_route(request.method, path):
        return JSONResponse(
            {
                "detail": (
                    "Content operations are available only through the paired "
                    "localhost data plane"
                )
            },
            status_code=410,
            headers={"Cache-Control": "no-store"},
        )
    response = await call_next(request)
    if path.startswith("/api/") and not path.startswith("/api/public/"):
        response.headers["Cache-Control"] = "no-store"
    elif (
        path == "/"
        or path.endswith(".html")
        or path.startswith("/next")
        or path.startswith("/invite")
        or path in {"/config", "/organizations", "/traces", "/profile", "/admin"}
    ):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.on_event("startup")
def startup() -> None:
    validate_production_settings()
    try:
        from dashboard.backend.agent.migration import (
            cleanup_mongo_job_documents,
        )
        from dashboard.backend.db.client import get_sync_db
        from dashboard.backend.db.collections import COLL_AGENT_JOBS

        cleanup = cleanup_mongo_job_documents(
            get_sync_db()[COLL_AGENT_JOBS],
            apply=True,
        )
        if cleanup["mutated"]:
            print(
                "[startup] Sanitized legacy agent jobs: "
                f"updated={cleanup['changed']} deleted={cleanup['deleted']}",
                flush=True,
            )
        from dashboard.backend.db.indexes import create_indexes

        result = create_indexes()
        failed = sorted(name for name, value in result.items() if value < 0)
        if settings.is_production and failed:
            raise RuntimeError(
                "Required MongoDB indexes could not be created for: "
                + ", ".join(failed)
            )
        from dashboard.backend.services.user_config import ensure_generic_config

        ensure_generic_config()
        from dashboard.backend.services.render_copy_jobs import (
            start_render_copy_worker,
        )

        start_render_copy_worker()
    except Exception as exc:
        if settings.is_production:
            print(
                f"[startup] FATAL: control-plane database unavailable: {exc}",
                file=sys.stderr,
            )
            raise


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "ad-factory-control-plane"}


@app.get("/api/version")
def api_version() -> dict[str, Any]:
    return {
        "commit": str(
            os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_COMMIT") or "unknown"
        ),
        "branch": str(
            os.getenv("RENDER_GIT_BRANCH") or os.getenv("GIT_BRANCH") or "unknown"
        ),
        "agent_protocol": 1,
        "artifact_schema": 3,
        "content_plane": "localhost",
    }


@app.get("/api/readyz")
def readyz() -> dict[str, Any]:
    from dashboard.backend.db.client import get_sync_db

    get_sync_db().command("ping")
    return {"status": "ready", "mongodb": True, "content_storage": False}


@app.get("/api/extension/status")
def retired_extension_status() -> dict[str, Any]:
    """Keep stale dashboards quiet without re-enabling the Render CDP bridge."""
    return {
        "connected": False,
        "active_connections": 0,
        "disabled": True,
        "reason": "local_agent_required",
    }


@app.websocket("/api/extension/ws")
async def retired_extension_websocket(websocket: WebSocket) -> None:
    """Reject installed legacy extensions before they reach StaticFiles."""
    await websocket.close(code=1008, reason="Use the paired local agent")


from dashboard.backend.admin.admin_routes import router as admin_router
from dashboard.backend.agent.routes import router as agent_router
from dashboard.backend.auth.routes import router as auth_router
from dashboard.backend.routes import batch, defaults, execute, export_import, generate, runs, traces
from dashboard.backend.services.blob_routes import router as blob_router
from dashboard.backend.services.config_routes import router as config_router
from dashboard.backend.services.invite_routes import router as invite_router
from dashboard.backend.services.org_routes import router as org_router
from dashboard.backend.services.provider_routes import router as provider_router
from dashboard.backend.services.user_config_routes import router as user_config_router

app.include_router(auth_router)
app.include_router(agent_router)
app.include_router(runs.router)
app.include_router(traces.router)
app.include_router(batch.router)
app.include_router(defaults.router)
app.include_router(execute.router)
app.include_router(generate.router)
app.include_router(export_import.router)
app.include_router(provider_router)
app.include_router(blob_router)
app.include_router(user_config_router)
app.include_router(org_router)
app.include_router(invite_router)
app.include_router(config_router)
app.include_router(admin_router)

from dashboard.backend.spa_static import mount_react_spa

mount_react_spa(app, ROOT)
