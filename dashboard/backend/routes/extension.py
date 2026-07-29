"""FastAPI routes for the Chrome Extension CDP Bridge.

Provides:
- WebSocket endpoint for extension connections
- REST endpoints for connection status, command execution, and target management
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from dashboard.backend.auth.service import get_current_user_from_cookie
from dashboard.backend.services.extension_bridge import extension_bridge

logger = logging.getLogger(__name__)

router = APIRouter()

# Rate limiting: max commands per second per user
_RATE_LIMIT = 10
_rate_limits: dict[str, list[float]] = {}

# Whitelisted CDP domains (only these can be invoked via REST API)
_ALLOWED_CDP_DOMAINS = {"Page", "Runtime", "DOM", "Input", "Target", "Browser", "Network"}


def _check_rate_limit(user_id: str) -> bool:
    now = time.time()
    timestamps = _rate_limits.setdefault(user_id, [])
    # Prune old entries
    timestamps[:] = [t for t in timestamps if now - t < 1.0]
    if len(timestamps) >= _RATE_LIMIT:
        return False
    timestamps.append(now)
    return True


def _validate_cdp_method(method: str) -> bool:
    domain = method.split(".")[0]
    return domain in _ALLOWED_CDP_DOMAINS


# ─── WebSocket endpoint ───


@router.websocket("/api/extension/ws")
async def extension_websocket(ws: WebSocket, session: str = Query("")):
    """WebSocket endpoint for Chrome Extension connections.

    Query params:
        session: The user's session cookie value for authentication.
    """
    # Authenticate
    if not session:
        await ws.close(code=4001, reason="Missing session token")
        return

    user = get_current_user_from_cookie(session)
    if not user:
        await ws.close(code=4001, reason="Invalid session")
        return

    user_id = user["user_id"]
    logger.info(f"[extension-ws] User {user_id} connecting")

    await ws.accept()
    conn = await extension_bridge.register(user_id, ws)
    logger.info(f"[extension-ws] User {user_id} registered (active: {extension_bridge.active_connections})")

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"error": "Invalid JSON"})
                continue

            await extension_bridge.handle_message(user_id, msg)
    except WebSocketDisconnect:
        logger.info(f"[extension-ws] User {user_id} disconnected")
    except Exception as exc:
        logger.error(f"[extension-ws] User {user_id} error: {exc}")
    finally:
        await extension_bridge.unregister(user_id)


# ─── REST endpoints ───


@router.get("/api/extension/status")
def extension_status(user: dict[str, Any] | None = None) -> dict[str, Any]:
    """Check extension connection status for the current user."""
    uid = user["user_id"] if user else ""
    conn = extension_bridge.get_connection(uid) if uid else None
    return {
        "connected": conn is not None,
        "user_id": uid,
        "active_connections": extension_bridge.active_connections,
    }


@router.get("/api/extension/status-all")
def extension_status_all() -> dict[str, Any]:
    """Admin: list all active extension connections."""
    import asyncio as _aio
    loop = _aio.get_event_loop()
    health = loop.run_until_complete(extension_bridge.health_check())
    return health


@router.get("/api/extension/targets")
async def extension_targets(user: dict[str, Any]) -> dict[str, Any]:
    """Get browser targets (open tabs) from the extension."""
    targets = await extension_bridge.get_targets(user["user_id"])
    return {"targets": targets}


@router.post("/api/extension/command")
async def extension_command(
    user: dict[str, Any],
    method: str = Query(...),
    timeout: float = Query(30.0),
    target_id: str = Query(""),
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a CDP command via the extension bridge.

    Only whitelisted CDP domains are allowed.
    """
    if not _validate_cdp_method(method):
        raise HTTPException(
            status_code=400,
            detail=f"CDP method '{method}' not allowed. Allowed domains: {sorted(_ALLOWED_CDP_DOMAINS)}",
        )

    if not _check_rate_limit(user["user_id"]):
        raise HTTPException(status_code=429, detail="Rate limit exceeded (max 10 commands/second)")

    try:
        result = await extension_bridge.send_command(
            user_id=user["user_id"],
            method=method,
            params=body or {},
            timeout=timeout,
            target_id=target_id or None,
        )
        return {"result": result}
    except ConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/extension/navigate")
async def extension_navigate(
    user: dict[str, Any],
    url: str = Query(...),
    target_id: str = Query(""),
) -> dict[str, Any]:
    """Navigate a tab to a URL via the extension."""
    params = {"url": url}
    try:
        result = await extension_bridge.send_command(
            user_id=user["user_id"],
            method="Page.navigate",
            params=params,
            timeout=30.0,
            target_id=target_id or None,
        )
        return {"result": result}
    except ConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/extension/screenshot")
async def extension_screenshot(
    user: dict[str, Any],
    target_id: str = Query(""),
) -> dict[str, Any]:
    """Capture a screenshot via the extension."""
    try:
        result = await extension_bridge.send_command(
            user_id=user["user_id"],
            method="Page.captureScreenshot",
            params={},
            timeout=10.0,
            target_id=target_id or None,
        )
        return {"result": result}
    except ConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
