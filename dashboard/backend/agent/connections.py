from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket


@dataclass
class AgentConnection:
    agent_id: str
    user_id: str
    websocket: WebSocket
    connected_at: float = field(default_factory=time.time)
    last_seen_at: float = field(default_factory=time.time)


class AgentConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, AgentConnection] = {}
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def get(self, agent_id: str) -> AgentConnection | None:
        return self._connections.get(agent_id)

    async def register(self, agent_id: str, user_id: str, websocket: WebSocket) -> AgentConnection:
        self._loop = asyncio.get_running_loop()
        async with self._lock:
            existing = self._connections.get(agent_id)
            if existing is not None:
                try:
                    await existing.websocket.close(code=4001, reason="Agent reconnected")
                except Exception:
                    pass
            connection = AgentConnection(agent_id=agent_id, user_id=user_id, websocket=websocket)
            self._connections[agent_id] = connection
            return connection

    async def unregister(self, agent_id: str, websocket: WebSocket | None = None) -> None:
        async with self._lock:
            existing = self._connections.get(agent_id)
            if existing is not None and (websocket is None or existing.websocket is websocket):
                self._connections.pop(agent_id, None)

    async def notify(self, agent_id: str, payload: dict[str, Any]) -> bool:
        connection = self._connections.get(agent_id)
        if connection is None:
            return False
        try:
            await connection.websocket.send_json(payload)
            return True
        except Exception:
            await self.unregister(agent_id, connection.websocket)
            return False

    def notify_from_thread(self, agent_id: str, payload: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self.notify(agent_id, payload), loop)


agent_connections = AgentConnectionManager()
