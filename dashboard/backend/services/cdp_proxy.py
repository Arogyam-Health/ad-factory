"""CDP proxy that bridges Playwright's connect_over_cdp to the Chrome Extension bridge.

Playwright expects a standard CDP HTTP endpoint (/json/version, /json/list) and
WebSocket connections per target. This proxy translates those into extension
bridge commands so the automation script can control the user's real browser.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

app = FastAPI()

# Reference to the extension bridge singleton (set at import time)
_bridge = None
_user_id: str = ""
_host: str = "127.0.0.1"
_port: int = 0


def init_proxy(user_id: str, host: str = "127.0.0.1", port: int = 0) -> str:
    """Start the CDP proxy and return the base URL."""
    global _bridge, _user_id, _host, _port
    from dashboard.backend.services.extension_bridge import extension_bridge
    _bridge = extension_bridge
    _user_id = user_id
    _host = host
    _port = port

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to be ready
    time.sleep(0.3)
    actual_port = server.config.port
    url = f"http://{host}:{actual_port}"
    logger.info(f"[cdp-proxy] Started on {url}")
    return url


def _get_targets() -> list[dict[str, Any]]:
    """Get targets from the extension bridge, formatted as CDP /json/list."""
    if not _bridge:
        return []
    conn = _bridge.get_connection(_user_id)
    if not conn:
        return []
    targets = []
    for t in conn.targets:
        targets.append({
            "description": t.get("title", ""),
            "devtoolsFrontendUrl": "",
            "id": t.get("targetId", t.get("id", "")),
            "title": t.get("title", ""),
            "type": t.get("type", "page"),
            "url": t.get("url", ""),
            "webSocketDebuggerUrl": f"ws://{_host}:{_port}/devtools/page/{t.get('targetId', t.get('id', ''))}",
        })
    return targets


@app.get("/json/version")
async def json_version():
    return {
        "Browser": "Chrome Extension Bridge Proxy",
        "Protocol-Version": "1.3",
        "User-Agent": "AdFactoryCDPProxy/1.0",
        "V8-Version": "0.0",
        "WebKit-Version": "0.0",
        "webSocketDebuggerUrl": f"ws://{_host}:{_port}/devtools/browser",
    }


@app.get("/json/list")
async def json_list():
    return _get_targets()


@app.get("/json")
async def json_root():
    return _get_targets()


@app.websocket("/devtools/page/{target_id}")
async def devtools_page(websocket: WebSocket, target_id: str):
    """Relay CDP commands from Playwright through the extension bridge."""
    await websocket.accept()
    logger.info(f"[cdp-proxy] Playwright connected to target {target_id}")

    # Map from Playwright command IDs to extension bridge command IDs
    id_map: dict[str, str] = {}
    pending: dict[str, asyncio.Future] = {}

    async def relay_to_extension():
        """Read from Playwright WebSocket, send to extension bridge."""
        try:
            while True:
                data = await websocket.receive_text()
                msg = json.loads(data)
                method = msg.get("method", "")
                cmd_id = msg.get("id", "")

                if not method:
                    continue

                # Map Playwright ID to our own ID
                proxy_id = uuid.uuid4().hex[:12]
                id_map[proxy_id] = cmd_id

                try:
                    result = await _bridge.send_command(
                        _user_id, method, msg.get("params"), timeout=30.0, target_id=target_id
                    )
                    # Send response back to Playwright
                    resp = {"id": cmd_id, "result": result}
                    await websocket.send_text(json.dumps(resp))
                except Exception as e:
                    resp = {"id": cmd_id, "error": {"code": -32000, "message": str(e)}}
                    await websocket.send_text(json.dumps(resp))
        except WebSocketDisconnect:
            logger.info(f"[cdp-proxy] Playwright disconnected from target {target_id}")
        except Exception as e:
            logger.error(f"[cdp-proxy] Relay error: {e}")

    await relay_to_extension()


@app.websocket("/devtools/browser")
async def devtools_browser(websocket: WebSocket):
    """Handle browser-level CDP commands."""
    await websocket.accept()
    logger.info("[cdp-proxy] Playwright connected to browser")

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            method = msg.get("method", "")
            cmd_id = msg.get("id", "")

            if not method:
                continue

            try:
                result = await _bridge.send_command(
                    _user_id, method, msg.get("params"), timeout=30.0
                )
                resp = {"id": cmd_id, "result": result}
                await websocket.send_text(json.dumps(resp))
            except Exception as e:
                resp = {"id": cmd_id, "error": {"code": -32000, "message": str(e)}}
                await websocket.send_text(json.dumps(resp))
    except WebSocketDisconnect:
        logger.info("[cdp-proxy] Playwright disconnected from browser")
    except Exception as e:
        logger.error(f"[cdp-proxy] Browser relay error: {e}")
