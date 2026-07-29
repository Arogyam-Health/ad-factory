"""WebSocket connection manager for the Chrome Extension CDP Bridge.

Manages extension WebSocket connections, command dispatch, and response matching.
The extension connects via WebSocket and the server sends CDP commands through it.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from fastapi import WebSocket, WebSocketDisconnect


@dataclass
class ExtensionConnection:
    """A single extension WebSocket connection."""
    user_id: str
    ws: WebSocket
    connected_at: float = field(default_factory=time.time)
    last_pong: float = field(default_factory=time.time)
    pending_commands: dict[str, asyncio.Future] = field(default_factory=dict)
    targets: list[dict[str, Any]] = field(default_factory=list)
    attached_sessions: set[str] = field(default_factory=set)


class ExtensionBridge:
    """Manages all extension WebSocket connections and CDP command routing."""

    def __init__(self) -> None:
        self._connections: dict[str, ExtensionConnection] = {}  # user_id → connection
        self._lock = asyncio.Lock()

    @property
    def active_connections(self) -> int:
        return len(self._connections)

    def get_connection(self, user_id: str) -> Optional[ExtensionConnection]:
        return self._connections.get(user_id)

    async def register(self, user_id: str, ws: WebSocket) -> ExtensionConnection:
        """Register a new extension connection. Disconnects any existing connection for this user."""
        async with self._lock:
            # Disconnect existing connection for this user
            if user_id in self._connections:
                old = self._connections[user_id]
                try:
                    await old.ws.close(code=4001, reason="New connection from same user")
                except Exception:
                    pass
                # Cancel pending commands
                for future in old.pending_commands.values():
                    if not future.done():
                        future.cancel()

            conn = ExtensionConnection(user_id=user_id, ws=ws)
            self._connections[user_id] = conn
            return conn

    async def unregister(self, user_id: str) -> None:
        async with self._lock:
            conn = self._connections.pop(user_id, None)
            if conn:
                for future in conn.pending_commands.values():
                    if not future.done():
                        future.cancel()

    async def send_command(
        self,
        user_id: str,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,
        target_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a CDP command to the extension and wait for the response.

        Args:
            user_id: The user whose extension connection to use.
            method: CDP method name (e.g., "Page.navigate").
            params: CDP method parameters.
            timeout: Seconds to wait for response.
            target_id: Optional target ID to inject into params.

        Returns:
            The CDP response result dict.

        Raises:
            ConnectionError: If no active connection for this user.
            TimeoutError: If command times out.
            RuntimeError: If the extension returns an error.
        """
        conn = self._connections.get(user_id)
        if not conn:
            raise ConnectionError(f"No active extension connection for user {user_id}")

        cmd_id = uuid.uuid4().hex[:12]
        cmd_params = dict(params or {})
        if target_id:
            cmd_params["_tabId"] = target_id

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        conn.pending_commands[cmd_id] = future

        try:
            await conn.ws.send_json({
                "id": cmd_id,
                "method": method,
                "params": cmd_params,
            })

            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            conn.pending_commands.pop(cmd_id, None)
            raise TimeoutError(f"CDP command {method} timed out after {timeout}s")
        except Exception:
            conn.pending_commands.pop(cmd_id, None)
            raise

    async def handle_message(self, user_id: str, msg: dict[str, Any]) -> None:
        """Handle an incoming message from the extension WebSocket."""
        conn = self._connections.get(user_id)
        if not conn:
            return

        # Response to a command we sent
        msg_id = msg.get("id")
        if msg_id and msg_id in conn.pending_commands:
            future = conn.pending_commands.pop(msg_id)
            if not future.done():
                if msg.get("error"):
                    future.set_exception(RuntimeError(msg["error"].get("message", "CDP error")))
                else:
                    future.set_result(msg.get("result", {}))
            return

        # Target list update
        method = msg.get("method", "")
        if method == "Target.targetListChanged":
            conn.targets = msg.get("params", {}).get("targetInfos", [])
            return

        # Pong
        if msg.get("type") == "pong":
            conn.last_pong = time.time()
            return

        # Ping from extension
        if msg.get("type") == "ping":
            try:
                await conn.ws.send_json({"type": "pong"})
            except Exception:
                pass
            return

    async def get_targets(self, user_id: str) -> list[dict[str, Any]]:
        """Get the list of browser targets from the extension."""
        conn = self._connections.get(user_id)
        if not conn:
            return []
        return list(conn.targets)

    async def list_connections(self) -> list[dict[str, Any]]:
        """List all active connections."""
        result = []
        for uid, conn in self._connections.items():
            result.append({
                "user_id": uid,
                "connected_at": conn.connected_at,
                "last_pong": conn.last_pong,
                "targets": len(conn.targets),
                "pending_commands": len(conn.pending_commands),
            })
        return result

    async def health_check(self) -> dict[str, Any]:
        """Check health of all connections."""
        now = time.time()
        stale_threshold = 60  # seconds
        connections = []
        for uid, conn in self._connections.items():
            stale = (now - conn.last_pong) > stale_threshold
            connections.append({
                "user_id": uid,
                "stale": stale,
                "seconds_since_pong": round(now - conn.last_pong, 1),
            })
        return {
            "total_connections": len(self._connections),
            "connections": connections,
        }


# Singleton instance
extension_bridge = ExtensionBridge()
