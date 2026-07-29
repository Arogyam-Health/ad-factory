"""CDP proxy bridging Playwright's connect_over_cdp to the Chrome Extension bridge.

Playwright's Node.js driver connects to a standard CDP endpoint:
  1. GET /json/version → gets browser WebSocket URL
  2. Connects WebSocket to /devtools/browser
  3. Sends browser-level commands (Target.*, Browser.*) over that socket
  4. Page-level commands use a sessionId from Target.attachToTarget

This proxy intercepts browser-level commands locally and forwards
page-level commands through the extension bridge to the user's real browser.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import threading
import time
import uuid
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

_bridge = None
_user_id: str = ""
_host: str = "127.0.0.1"
_port: int = 0
_main_loop: asyncio.AbstractEventLoop | None = None

_sessions: dict[str, str] = {}
_browser_ws: WebSocket | None = None
_last_targets: list[dict[str, Any]] = []
_poll_task: asyncio.Task | None = None

_cached_targets: list[dict[str, Any]] = []
_cached_targets_lock = threading.Lock()


def _sync_get_targets() -> list[dict[str, Any]]:
    """Thread-safe way to read cached targets."""
    with _cached_targets_lock:
        return list(_cached_targets)


def _sync_update_targets():
    """Update the cached target list (called from main event loop)."""
    global _cached_targets
    if not _bridge:
        return
    conn = _bridge.get_connection(_user_id)
    if not conn:
        return
    targets = []
    for t in conn.targets:
        targets.append({
            "id": t.get("targetId", t.get("id", "")),
            "type": t.get("type", "page"),
            "title": t.get("title", ""),
            "url": t.get("url", ""),
            "attached": False,
            "browserContextId": "",
        })
    with _cached_targets_lock:
        _cached_targets = targets


async def _background_target_refresh():
    """Background task that periodically refreshes target cache from main loop."""
    while True:
        try:
            _sync_update_targets()
        except Exception:
            pass
        await asyncio.sleep(1)


def _ensure_target_refresh():
    """Start background target refresh if not running."""
    global _poll_task
    if _poll_task is None or _poll_task.done():
        _poll_task = asyncio.create_task(_background_target_refresh())


def _run_async(coro) -> Any:
    """Run an async coroutine from the proxy thread in the main event loop."""
    global _main_loop
    if _main_loop is None or not _main_loop.is_running():
        raise RuntimeError("Main event loop not available")
    future = asyncio.run_coroutine_threadsafe(coro, _main_loop)
    return future.result(timeout=30)


app = FastAPI()


def init_proxy(user_id: str, host: str = "127.0.0.1", port: int = 0) -> str:
    """Start the CDP proxy and return the base URL."""
    global _bridge, _user_id, _host, _port, _main_loop
    from dashboard.backend.services.extension_bridge import extension_bridge
    _bridge = extension_bridge
    _user_id = user_id
    _host = host

    _main_loop = asyncio.get_event_loop()
    _ensure_target_refresh()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((host, port if port else 0))
    actual_port = sock.getsockname()[1]
    _port = actual_port

    config = uvicorn.Config(app, host=host, port=actual_port, log_level="warning", sock=sock)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    time.sleep(0.3)
    url = f"http://{host}:{actual_port}"
    logger.info(f"[cdp-proxy] Started on {url}")
    return url


def _build_target_list() -> list[dict[str, Any]]:
    """Build CDP-formatted target list for /json/list."""
    targets = _sync_get_targets()
    result = []
    for t in targets:
        tid = t.get("id", "")
        result.append({
            "description": t.get("title", ""),
            "devtoolsFrontendUrl": f"devtools://devtools/bundled/inspector.html?ws={_host}:{_port}/devtools/page/{tid}",
            "id": tid,
            "title": t.get("title", ""),
            "type": t.get("type", "page"),
            "url": t.get("url", ""),
            "webSocketDebuggerUrl": f"ws://{_host}:{_port}/devtools/page/{tid}",
        })
    return result


@app.get("/json/version")
async def json_version():
    return {
        "Browser": "Chrome/120.0.0.0",
        "Protocol-Version": "1.3",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0",
        "V8-Version": "12.0.0",
        "WebKit-Version": "537.36",
        "webSocketDebuggerUrl": f"ws://{_host}:{_port}/devtools/browser",
    }


@app.get("/json/list")
async def json_list():
    return _build_target_list()


@app.get("/json")
async def json_root():
    return _build_target_list()


@app.websocket("/devtools/page/{target_id}")
async def devtools_page(websocket: WebSocket, target_id: str):
    """Page-level CDP session. Forward commands to extension bridge."""
    await websocket.accept()
    logger.info(f"[cdp-proxy] Page session for target {target_id}")

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            method = msg.get("method", "")
            cmd_id = msg.get("id", "")

            if not method:
                continue

            try:
                result = _run_async(
                    _bridge.send_command(_user_id, method, msg.get("params"), timeout=30.0, target_id=target_id)
                )
                await websocket.send_text(json.dumps({"id": cmd_id, "result": result}))
            except Exception as e:
                await websocket.send_text(json.dumps({"id": cmd_id, "error": {"code": -32000, "message": str(e).splitlines()[0]}}))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


@app.websocket("/devtools/browser")
async def devtools_browser(websocket: WebSocket):
    """Browser-level CDP session. Intercept browser commands locally,
    forward page commands (with sessionId) to extension bridge."""
    global _browser_ws
    await websocket.accept()
    _browser_ws = websocket
    logger.info("[cdp-proxy] Playwright connected to browser")

    poll_stop = threading.Event()

    def _poll_targets_in_main():
        """Poll target changes on main loop and send events to browser WS."""
        nonlocal poll_stop
        global _last_targets
        last_seen: set[str] = set()
        while not poll_stop.is_set():
            try:
                current = _sync_get_targets()
                current_ids = {t["id"] for t in current}

                new_ids = current_ids - last_seen
                removed_ids = last_seen - current_ids

                if new_ids or removed_ids:
                    for tid in new_ids:
                        info = next((t for t in current if t["id"] == tid), None)
                        if info and _browser_ws:
                            _send_event_to_browser("Target.targetCreated", {"targetInfo": info})
                    for tid in removed_ids:
                        if _browser_ws:
                            _send_event_to_browser("Target.targetDestroyed", {"targetId": tid})
                    last_seen = current_ids
                    _last_targets = current

                time.sleep(1)
            except Exception:
                time.sleep(1)

    def _send_event_to_browser(method: str, params: dict):
        """Schedule sending a CDP event to the browser WebSocket."""
        try:
            msg = json.dumps({"method": method, "params": params})
            if _main_loop and _main_loop.is_running():
                async def _send():
                    try:
                        if _browser_ws:
                            await _browser_ws.send_text(msg)
                    except Exception:
                        pass
                asyncio.run_coroutine_threadsafe(_send(), _main_loop)
        except Exception:
            pass

    poll_thread = threading.Thread(target=_poll_targets_in_main, daemon=True)
    poll_thread.start()

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            method = msg.get("method", "")
            cmd_id = msg.get("id", "")
            session_id = msg.get("sessionId", "")

            if not method:
                continue

            if session_id:
                target_id = _sessions.get(session_id)
                if not target_id:
                    await websocket.send_text(json.dumps({
                        "id": cmd_id, "error": {"code": -32000, "message": f"Unknown session: {session_id}"}
                    }))
                    continue

                try:
                    result = _run_async(
                        _bridge.send_command(_user_id, method, msg.get("params"), timeout=30.0, target_id=target_id)
                    )
                    resp = {"id": cmd_id, "sessionId": session_id, "result": result}
                    await websocket.send_text(json.dumps(resp))
                except Exception as e:
                    await websocket.send_text(json.dumps({
                        "id": cmd_id, "sessionId": session_id,
                        "error": {"code": -32000, "message": str(e).splitlines()[0]}
                    }))
                continue

            if method == "Target.setDiscoverTargets":
                await websocket.send_text(json.dumps({"id": cmd_id, "result": {}}))
            elif method == "Target.setAutoAttach":
                await websocket.send_text(json.dumps({"id": cmd_id, "result": {}}))
            elif method == "Target.getTargets":
                targets = _sync_get_targets()
                await websocket.send_text(json.dumps({"id": cmd_id, "result": {"targetInfos": targets}}))
            elif method == "Target.attachToTarget":
                target_id = msg.get("params", {}).get("targetId", "")
                flatten = msg.get("params", {}).get("flatten", True)
                session_id = uuid.uuid4().hex[:8]
                _sessions[session_id] = target_id
                await websocket.send_text(json.dumps({
                    "id": cmd_id, "result": {"sessionId": session_id}
                }))
            elif method == "Target.detachFromTarget":
                sid = msg.get("params", {}).get("sessionId", "")
                _sessions.pop(sid, None)
                await websocket.send_text(json.dumps({"id": cmd_id, "result": {}}))
            elif method == "Target.closeTarget":
                await websocket.send_text(json.dumps({"id": cmd_id, "result": {"success": True}}))
            elif method == "Target.createTarget":
                fake_id = uuid.uuid4().hex[:12]
                await websocket.send_text(json.dumps({
                    "id": cmd_id, "result": {"targetId": fake_id}
                }))
            elif method == "Browser.getVersion":
                await websocket.send_text(json.dumps({
                    "id": cmd_id, "result": {
                        "protocolVersion": "1.3",
                        "product": "Chrome/120.0.0.0",
                        "revision": "@000000000000",
                        "userAgent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                        "jsVersion": "12.0.0.0",
                    }
                }))
            elif method == "Target.activateTarget":
                await websocket.send_text(json.dumps({"id": cmd_id, "result": {}}))
            else:
                await websocket.send_text(json.dumps({"id": cmd_id, "result": {}}))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"[cdp-proxy] Browser WS error: {e}")
    finally:
        _browser_ws = None
        poll_stop.set()
        poll_thread.join(timeout=2)
        _sessions.clear()
