from __future__ import annotations

import asyncio
import unittest


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages = []
        self.closed = False

    async def send_json(self, payload) -> None:
        self.messages.append(payload)

    async def close(self, **_kwargs) -> None:
        self.closed = True


class AgentConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_notification_reaches_connected_agent(self) -> None:
        from dashboard.backend.agent.connections import AgentConnectionManager

        manager = AgentConnectionManager()
        websocket = FakeWebSocket()
        await manager.register("agent-1", "user-1", websocket)
        delivered = await manager.notify("agent-1", {"type": "job_available", "job_id": "job-1"})

        self.assertTrue(delivered)
        self.assertEqual(websocket.messages[-1]["job_id"], "job-1")

    async def test_reconnect_replaces_old_socket(self) -> None:
        from dashboard.backend.agent.connections import AgentConnectionManager

        manager = AgentConnectionManager()
        old = FakeWebSocket()
        new = FakeWebSocket()
        await manager.register("agent-1", "user-1", old)
        await manager.register("agent-1", "user-1", new)

        self.assertTrue(old.closed)
        self.assertIs(manager.get("agent-1").websocket, new)


if __name__ == "__main__":
    unittest.main()
