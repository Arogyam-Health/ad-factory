from __future__ import annotations

import unittest


class LocalAgentTransportTests(unittest.TestCase):
    def test_websocket_url_uses_secure_scheme(self) -> None:
        from local_agent_runtime.transport import websocket_url

        self.assertEqual(
            websocket_url("https://example.test/"),
            "wss://example.test/api/agent-runtime/ws",
        )
        self.assertEqual(
            websocket_url("http://localhost:4090"),
            "ws://localhost:4090/api/agent-runtime/ws",
        )

    def test_job_signal_tracks_notifications_and_cancellation(self) -> None:
        from local_agent_runtime.transport import JobSignal

        signal = JobSignal()
        signal.handle({"type": "job_available"})
        self.assertTrue(signal.wait(0))
        signal.handle({"type": "job_canceled", "job_id": "job-1"})
        self.assertTrue(signal.cancel_requested("job-1"))
        self.assertFalse(signal.cancel_requested("job-1"))

    def test_job_signal_queues_bounded_pairing_approvals(self) -> None:
        from local_agent_runtime.transport import JobSignal

        signal = JobSignal()
        approval = {
            "type": "pairing_approval",
            "challenge_id": "pch_1",
            "challenge_hash": "a" * 64,
            "agent_id": "agent-1",
            "device_id": "dev_" + "b" * 32,
            "owner_key": "user:user-1",
            "scopes": ["manifest:read"],
            "expires_at": 123.0,
        }
        signal.handle(approval)

        self.assertTrue(signal.wait(0))
        self.assertEqual(signal.drain_pairing_approvals(), [approval])
        self.assertEqual(signal.drain_pairing_approvals(), [])


if __name__ == "__main__":
    unittest.main()
