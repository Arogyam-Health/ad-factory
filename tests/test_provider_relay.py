from __future__ import annotations

import json
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


class _Connections:
    def __init__(self) -> None:
        self.connection = SimpleNamespace(
            agent_id="agent-1",
            user_id="user-1",
            device_id="dev_" + "a" * 32,
            protocol_version="v1",
            supports_provider_relay=True,
        )
        self.sent: list[dict] = []
        self.sent_event = threading.Event()

    def for_user(
        self,
        user_id: str,
        protocol_version: str = "",
        supports_provider_relay: bool = False,
    ):
        if (
            user_id != self.connection.user_id
            or protocol_version != self.connection.protocol_version
            or (
                supports_provider_relay
                and not self.connection.supports_provider_relay
            )
        ):
            return None
        return self.connection

    def notify_from_thread(
        self,
        agent_id: str,
        payload: dict,
        *,
        device_id: str | None = None,
        wait: bool = False,
    ) -> bool:
        if (
            agent_id != self.connection.agent_id
            or device_id != self.connection.device_id
        ):
            return False
        self.sent.append(payload)
        self.sent_event.set()
        return True


class ProviderRelayTests(unittest.TestCase):
    def test_broker_is_memory_only_and_pins_result_to_user_agent_and_device(
        self,
    ) -> None:
        from dashboard.backend.services.provider_relay import ProviderRelayBroker

        broker = ProviderRelayBroker(ttl_seconds=2)
        connections = _Connections()
        result: dict = {}

        def invoke() -> None:
            result.update(
                broker.invoke(
                    user_id="user-1",
                    payload={
                        "provider": "opencode",
                        "endpoint": "https://opencode.ai/zen/v1/chat/completions",
                        "api_key": "secret-key",
                        "request_body": {"model": "deepseek-v4-flash-free"},
                    },
                    connections=connections,
                )
            )

        thread = threading.Thread(target=invoke)
        thread.start()
        self.assertTrue(connections.sent_event.wait(1))
        call = connections.sent[0]
        self.assertEqual(call["type"], "provider_call")
        self.assertFalse(
            broker.complete(
                call_id=call["call_id"],
                user_id="other-user",
                agent_id="agent-1",
                device_id="dev_" + "a" * 32,
                result={"http_status": 200, "body": "{}"},
            )
        )
        self.assertFalse(
            broker.complete(
                call_id=call["call_id"],
                user_id="user-1",
                agent_id="agent-1",
                device_id="dev_" + "b" * 32,
                result={"http_status": 200, "body": "{}"},
            )
        )
        self.assertTrue(
            broker.complete(
                call_id=call["call_id"],
                user_id="user-1",
                agent_id="agent-1",
                device_id="dev_" + "a" * 32,
                result={"http_status": 200, "body": '{"ok":true}'},
            )
        )
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result["http_status"], 200)
        self.assertEqual(broker.pending_count(), 0)

        source = (
            ROOT / "dashboard" / "backend" / "services" / "provider_relay.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("get_sync_db", source)
        self.assertNotIn("insert_one", source)
        self.assertNotIn("update_one", source)

    def test_broker_fails_pending_calls_when_pinned_agent_disconnects(self) -> None:
        from dashboard.backend.services.provider_relay import (
            ProviderRelayBroker,
            ProviderRelayError,
        )

        broker = ProviderRelayBroker(ttl_seconds=2)
        connections = _Connections()
        errors: list[Exception] = []

        def invoke() -> None:
            try:
                broker.invoke(
                    user_id="user-1",
                    payload={
                        "provider": "opencode",
                        "endpoint": "https://opencode.ai/zen/v1/chat/completions",
                        "api_key": "secret-key",
                        "request_body": {},
                    },
                    connections=connections,
                )
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=invoke)
        thread.start()
        self.assertTrue(connections.sent_event.wait(1))
        broker.fail_agent("agent-1", "dev_" + "a" * 32)
        thread.join(1)

        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ProviderRelayError)
        self.assertEqual(errors[0].code, "local_provider_agent_disconnected")
        self.assertEqual(broker.pending_count(), 0)

    def test_local_executor_allows_only_known_https_provider_endpoints(self) -> None:
        from local_agent_runtime.provider_relay import execute_provider_call

        response = Mock()
        response.status_code = 200
        response.headers = {"content-type": "application/json"}
        response.iter_content.return_value = [
            b'{"choices":[{"message":{"content":"{\\"ads\\":[]}"}}]}'
        ]
        post = Mock(return_value=response)
        result = execute_provider_call(
            {
                "provider": "opencode",
                "endpoint": "https://opencode.ai/zen/v1/chat/completions",
                "api_key": "secret-key",
                "request_body": {"model": "deepseek-v4-flash-free"},
            },
            post=post,
        )

        self.assertEqual(result["http_status"], 200)
        self.assertIn("choices", result["body"])
        self.assertIsNone(post.call_args.kwargs["timeout"])
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        self.assertNotIn("secret-key", json.dumps(result))

        forbidden = (
            "http://opencode.ai/zen/v1/chat/completions",
            "https://127.0.0.1/chat/completions",
            "https://example.com/chat/completions",
            "https://opencode.ai/other",
        )
        for endpoint in forbidden:
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValueError):
                    execute_provider_call(
                        {
                            "provider": "opencode",
                            "endpoint": endpoint,
                            "api_key": "secret-key",
                            "request_body": {},
                        },
                        post=post,
                    )

    def test_render_provider_callable_uses_relay_transport_not_render_http(
        self,
    ) -> None:
        from dashboard.backend.services.render_structured_copy import (
            provider_generate_callable,
        )

        transport = Mock(
            return_value={
                "http_status": 200,
                "content_type": "application/json",
                "body": '{"choices":[{"message":{"content":"{\\"ads\\":[]}"}}]}',
            }
        )
        with patch(
            "dashboard.backend.services.render_structured_copy.requests.post"
        ) as direct_post:
            generate = provider_generate_callable(
                "opencode",
                "opencode/deepseek-v4-flash-free",
                {
                    "api_url": "https://opencode.ai/zen/v1",
                    "api_key": "secret-key",
                },
                transport=transport,
            )
            self.assertEqual(generate({"task": "copy"}), {"ads": []})

        direct_post.assert_not_called()
        relayed = transport.call_args.args[0]
        self.assertEqual(relayed["provider"], "opencode")
        self.assertEqual(
            relayed["endpoint"],
            "https://opencode.ai/zen/v1/chat/completions",
        )
        self.assertEqual(relayed["api_key"], "secret-key")

    def test_agent_websocket_routes_results_and_never_logs_bodies(self) -> None:
        routes = (
            ROOT / "dashboard" / "backend" / "agent" / "routes.py"
        ).read_text(encoding="utf-8")
        transport = (
            ROOT / "local_agent_runtime" / "transport.py"
        ).read_text(encoding="utf-8")
        agent_service = (
            ROOT / "dashboard" / "backend" / "agent" / "service.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"provider_result"', routes)
        self.assertIn("provider_relay.complete", routes)
        self.assertIn("provider_handler", transport)
        self.assertIn('"provider_call"', transport)
        self.assertIn('"type": "capabilities"', transport)
        self.assertNotIn("supports_provider_relay", agent_service)
        self.assertNotIn("print(provider", transport)
        self.assertNotIn("logger.", transport)


if __name__ == "__main__":
    unittest.main()
