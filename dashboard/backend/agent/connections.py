from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket


@dataclass
class AgentConnection:
    agent_id: str
    user_id: str
    device_id: str
    websocket: WebSocket
    protocol_version: str = ""
    supports_provider_relay: bool = False
    connected_at: float = field(default_factory=time.time)
    last_seen_at: float = field(default_factory=time.time)


class AgentConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, AgentConnection] = {}
        self._lock = asyncio.Lock()
        self._sync_lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def get(self, agent_id: str, device_id: str | None = None) -> AgentConnection | None:
        with self._sync_lock:
            connection = self._connections.get(agent_id)
        if connection is not None and device_id is not None and connection.device_id != device_id:
            return None
        return connection

    def for_user(
        self,
        user_id: str,
        protocol_version: str = "",
        supports_provider_relay: bool = False,
    ) -> AgentConnection | None:
        with self._sync_lock:
            matches = [
                connection
                for connection in self._connections.values()
                if connection.user_id == user_id
                and (
                    not protocol_version
                    or connection.protocol_version == protocol_version
                )
                and (
                    not supports_provider_relay
                    or connection.supports_provider_relay
                )
            ]
        return max(matches, key=lambda item: item.last_seen_at) if matches else None

    def set_provider_relay_capability(
        self,
        agent_id: str,
        device_id: str,
        enabled: bool,
    ) -> bool:
        with self._sync_lock:
            connection = self._connections.get(agent_id)
            if (
                connection is None
                or connection.device_id != device_id
            ):
                return False
            connection.supports_provider_relay = bool(enabled)
            return connection.supports_provider_relay

    async def register(
        self,
        agent_id: str,
        user_id: str,
        websocket: WebSocket,
        device_id: str = "",
        protocol_version: str = "",
    ) -> AgentConnection:
        self._loop = asyncio.get_running_loop()
        async with self._lock:
            with self._sync_lock:
                existing = self._connections.get(agent_id)
            if existing is not None:
                try:
                    await existing.websocket.close(code=4001, reason="Agent reconnected")
                except Exception:
                    pass
            connection = AgentConnection(
                agent_id=agent_id,
                user_id=user_id,
                device_id=device_id,
                websocket=websocket,
                protocol_version=protocol_version,
            )
            with self._sync_lock:
                self._connections[agent_id] = connection
            return connection

    async def unregister(self, agent_id: str, websocket: WebSocket | None = None) -> None:
        async with self._lock:
            with self._sync_lock:
                existing = self._connections.get(agent_id)
            if existing is not None and (websocket is None or existing.websocket is websocket):
                with self._sync_lock:
                    self._connections.pop(agent_id, None)

    async def notify(
        self,
        agent_id: str,
        payload: dict[str, Any],
        *,
        device_id: str | None = None,
    ) -> bool:
        connection = self.get(agent_id, device_id)
        if connection is None:
            return False
        try:
            await connection.websocket.send_json(payload)
            return True
        except Exception:
            await self.unregister(agent_id, connection.websocket)
            return False

    def notify_from_thread(
        self,
        agent_id: str,
        payload: dict[str, Any],
        *,
        device_id: str | None = None,
        wait: bool = False,
    ) -> bool:
        loop = self._loop
        if loop is None or loop.is_closed():
            return False
        future = asyncio.run_coroutine_threadsafe(
            self.notify(agent_id, payload, device_id=device_id), loop
        )
        if not wait:
            return True
        try:
            return bool(future.result(timeout=10))
        except Exception:
            return False


agent_connections = AgentConnectionManager()
