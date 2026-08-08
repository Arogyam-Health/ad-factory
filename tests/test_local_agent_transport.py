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


if __name__ == "__main__":
    unittest.main()
