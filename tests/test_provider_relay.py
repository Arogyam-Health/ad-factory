from __future__ import annotations

import json
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


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
        self.assertFalse(
            broker.complete(
                call_id=call["call_id"],
                user_id="user-1",
                agent_id="agent-1",
                device_id="dev_" + "a" * 32,
                result={"http_status": 200, "body": '{"duplicate":true}'},
            )
        )
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result["http_status"], 200)
        self.assertEqual(broker.pending_count(), 0)

    def test_broker_rejects_incompatible_agents_and_oversized_payloads(
        self,
    ) -> None:
        from dashboard.backend.services.provider_relay import (
            MAX_RELAY_REQUEST_BYTES,
            ProviderRelayBroker,
            ProviderRelayError,
        )

        broker = ProviderRelayBroker(ttl_seconds=2)
        connections = _Connections()
        connections.connection.supports_provider_relay = False
        with self.assertRaises(ProviderRelayError) as offline:
            broker.invoke(
                user_id="user-1",
                payload={
                    "provider": "opencode",
                    "endpoint": (
                        "https://opencode.ai/zen/v1/chat/completions"
                    ),
                    "api_key": "secret-key",
                    "request_body": {},
                },
                connections=connections,
            )
        self.assertEqual(
            offline.exception.code,
            "local_provider_agent_offline",
        )

        connections.connection.supports_provider_relay = True
        with self.assertRaises(ProviderRelayError) as oversized:
            broker.invoke(
                user_id="user-1",
                payload={
                    "provider": "opencode",
                    "endpoint": (
                        "https://opencode.ai/zen/v1/chat/completions"
                    ),
                    "api_key": "secret-key",
                    "request_body": {
                        "content": "x" * MAX_RELAY_REQUEST_BYTES
                    },
                },
                connections=connections,
            )
        self.assertEqual(
            oversized.exception.code,
            "provider_relay_request_too_large",
        )

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

    def test_broker_bounds_provider_response_before_returning_to_render(
        self,
    ) -> None:
        from dashboard.backend.services.provider_relay import (
            MAX_RELAY_RESPONSE_BYTES,
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
                        "endpoint": (
                            "https://opencode.ai/zen/v1/chat/completions"
                        ),
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
        call_id = connections.sent[0]["call_id"]
        self.assertTrue(
            broker.complete(
                call_id=call_id,
                user_id="user-1",
                agent_id="agent-1",
                device_id="dev_" + "a" * 32,
                result={
                    "http_status": 200,
                    "body": "x" * (MAX_RELAY_RESPONSE_BYTES + 1),
                },
            )
        )
        thread.join(1)

        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ProviderRelayError)
        self.assertEqual(
            errors[0].code,
            "provider_relay_response_too_large",
        )

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

    def test_agent_websocket_authenticates_after_upgrade(self) -> None:
        from dashboard.backend.agent import routes

        app = FastAPI()
        app.include_router(routes.router)
        agent = {
            "agent_id": "agent-1",
            "user_id": "user-1",
            "device_id": "dev_" + "a" * 32,
            "protocol_version": "v1",
        }
        with (
            patch.object(
                routes,
                "authenticate_agent",
                return_value=agent,
            ) as authenticate,
            patch.object(routes, "heartbeat_agent"),
            patch.object(routes, "poll_jobs", return_value=[]),
            patch.object(
                routes,
                "poll_pairing_approvals",
                return_value=[],
            ),
            patch(
                "dashboard.backend.services.render_copy_jobs."
                "resume_user_provider_jobs",
                return_value=0,
            ),
            TestClient(app) as client,
        ):
            with client.websocket_connect(
                "/api/agent-runtime/ws"
            ) as websocket:
                websocket.send_json(
                    {
                        "type": "authenticate",
                        "token": "agent-secret",
                    }
                )
                connected = websocket.receive_json()
                self.assertEqual(connected["type"], "connected")
                websocket.send_json(
                    {
                        "type": "capabilities",
                        "provider_relay": True,
                    }
                )
                capability = websocket.receive_json()
                self.assertTrue(capability["provider_relay"])

        authenticate.assert_called_once_with("agent-secret")

    def test_offline_local_provider_is_requeued_instead_of_failed(
        self,
    ) -> None:
        from dashboard.backend.services import render_copy_jobs
        from dashboard.backend.services.render_structured_copy import (
            ProviderCallError,
        )

        job = {
            "copy_job_id": "copy-1",
            "run_id": "run-1",
            "run_number": 1,
            "user_id": "user-1",
            "settings": {
                "provider": "opencode",
                "model": "opencode/big-pickle",
            },
        }
        offline = ProviderCallError(
            code="local_provider_agent_offline",
            provider="opencode",
            model="opencode/big-pickle",
            duration_ms=0,
        )
        with (
            patch.object(
                render_copy_jobs,
                "_claim_next_job",
                return_value=job,
            ),
            patch.object(
                render_copy_jobs,
                "get_materialized_provider_config",
                return_value={"api_key": "secret"},
            ),
            patch.object(
                render_copy_jobs,
                "generate_structured_prompt_bundle",
                side_effect=offline,
            ),
            patch.object(
                render_copy_jobs,
                "resolve_effective_config",
                return_value={},
            ),
            patch.object(
                render_copy_jobs,
                "_defer_job_for_local_agent",
            ) as defer,
            patch.object(render_copy_jobs, "_fail_job") as fail,
        ):
            self.assertTrue(
                render_copy_jobs.process_next_render_copy_job()
            )

        defer.assert_called_once_with(
            job,
            "local_provider_agent_offline",
        )
        fail.assert_not_called()

    def test_provider_failure_retries_once_with_free_opencode_model(self) -> None:
        from dashboard.backend.services import render_copy_jobs
        from dashboard.backend.services.opencode_catalog import next_free_opencode_model
        from dashboard.backend.services.render_structured_copy import (
            ProviderCallError,
        )

        self.assertEqual(
            next_free_opencode_model("opencode/big-pickle"),
            "opencode/mimo-v2.5-free",
        )
        self.assertEqual(
            next_free_opencode_model("opencode/mimo-v2.5-free"),
            "opencode/north-mini-code-free",
        )

        job = {
            "copy_job_id": "copy-1",
            "run_id": "run-1",
            "run_number": 1,
            "user_id": "user-1",
            "settings": {
                "provider": "opencode",
                "model": "opencode/big-pickle",
            },
        }
        first = ProviderCallError(
            code="provider_http_error",
            provider="opencode",
            model="opencode/big-pickle",
            duration_ms=11,
            http_status=429,
            error_detail="rate limited",
        )
        success = {
            "prompts": [],
            "prompt_count": 0,
            "provider": "opencode",
            "model": "opencode/mimo-v2.5-free",
        }
        with (
            patch.object(render_copy_jobs, "_claim_next_job", return_value=job),
            patch.object(
                render_copy_jobs,
                "get_materialized_provider_config",
                return_value={"api_key": "secret"},
            ),
            patch.object(
                render_copy_jobs,
                "generate_structured_prompt_bundle",
                side_effect=[first, success],
            ) as generate,
            patch.object(render_copy_jobs, "resolve_effective_config", return_value={}),
            patch.object(render_copy_jobs, "collect_copy_reuse_locks", return_value={}),
            patch.object(
                render_copy_jobs,
                "provider_generate_callable",
                return_value=lambda *args, **kwargs: {},
            ),
            patch.object(render_copy_jobs, "_record_provider_failure_trace", return_value=""),
            patch.object(render_copy_jobs, "_persist_copy_last_error") as persist,
            patch.object(render_copy_jobs, "get_sync_db") as get_db,
            patch.object(render_copy_jobs, "_complete_job") as complete,
            patch.object(render_copy_jobs, "_fail_job") as fail,
        ):
            get_db.return_value.__getitem__.return_value.find_one.return_value = {
                "status": "running"
            }
            self.assertTrue(render_copy_jobs.process_next_render_copy_job())

        self.assertEqual(generate.call_count, 2)
        self.assertEqual(generate.call_args.kwargs["provider_model"], "opencode/mimo-v2.5-free")
        persist.assert_called_once()
        self.assertIn("Falling back to opencode/mimo-v2.5-free", persist.call_args.args[1])
        complete.assert_called_once()
        fail.assert_not_called()

    def test_fallback_failure_keeps_both_provider_errors(self) -> None:
        from dashboard.backend.services import render_copy_jobs
        from dashboard.backend.services.render_structured_copy import (
            ProviderCallError,
        )

        job = {
            "copy_job_id": "copy-1",
            "run_id": "run-1",
            "run_number": 1,
            "user_id": "user-1",
            "settings": {
                "provider": "opencode",
                "model": "opencode/big-pickle",
            },
        }
        first = ProviderCallError(
            code="provider_http_error",
            provider="opencode",
            model="opencode/big-pickle",
            duration_ms=11,
            error_detail="rate limited",
        )
        second = ProviderCallError(
            code="provider_http_error",
            provider="opencode",
            model="opencode/mimo-v2.5-free",
            duration_ms=8,
            error_detail="also down",
        )
        with (
            patch.object(render_copy_jobs, "_claim_next_job", return_value=job),
            patch.object(
                render_copy_jobs,
                "get_materialized_provider_config",
                return_value={"api_key": "secret"},
            ),
            patch.object(
                render_copy_jobs,
                "generate_structured_prompt_bundle",
                side_effect=[first, second],
            ),
            patch.object(render_copy_jobs, "resolve_effective_config", return_value={}),
            patch.object(render_copy_jobs, "collect_copy_reuse_locks", return_value={}),
            patch.object(
                render_copy_jobs,
                "provider_generate_callable",
                return_value=lambda *args, **kwargs: {},
            ),
            patch.object(render_copy_jobs, "_record_provider_failure_trace", return_value=""),
            patch.object(render_copy_jobs, "_persist_copy_last_error"),
            patch.object(render_copy_jobs, "_fail_job") as fail,
        ):
            self.assertTrue(render_copy_jobs.process_next_render_copy_job())

        fail.assert_called_once()
        sticky = fail.call_args.kwargs["last_error"]
        self.assertIn("Falling back to opencode/mimo-v2.5-free", sticky)
        self.assertIn("Fallback also failed", sticky)
        self.assertIn("also down", sticky)

    def test_unexpected_copy_error_is_surfaced_and_retries_free_model(self) -> None:
        from dashboard.backend.services import render_copy_jobs

        job = {
            "copy_job_id": "copy-1",
            "run_id": "run-1",
            "run_number": 1,
            "user_id": "user-1",
            "settings": {
                "provider": "opencode",
                "model": "opencode/big-pickle",
            },
        }
        success = {
            "prompts": [{"prompt_id": "p1"}],
            "prompt_count": 1,
            "provider": "opencode",
            "model": "opencode/mimo-v2.5-free",
        }
        with (
            patch.object(render_copy_jobs, "_claim_next_job", return_value=job),
            patch.object(
                render_copy_jobs,
                "get_materialized_provider_config",
                return_value={"api_key": "secret"},
            ),
            patch.object(
                render_copy_jobs,
                "generate_structured_prompt_bundle",
                side_effect=[RuntimeError("relay exploded"), success],
            ) as generate,
            patch.object(render_copy_jobs, "resolve_effective_config", return_value={}),
            patch.object(render_copy_jobs, "collect_copy_reuse_locks", return_value={}),
            patch.object(
                render_copy_jobs,
                "provider_generate_callable",
                return_value=lambda *args, **kwargs: {},
            ),
            patch.object(render_copy_jobs, "_persist_copy_last_error") as persist,
            patch.object(render_copy_jobs, "get_sync_db") as get_db,
            patch.object(render_copy_jobs, "_complete_job") as complete,
            patch.object(render_copy_jobs, "_fail_job") as fail,
        ):
            get_db.return_value.__getitem__.return_value.find_one.return_value = {
                "status": "running"
            }
            self.assertTrue(render_copy_jobs.process_next_render_copy_job())

        self.assertEqual(generate.call_count, 2)
        persist.assert_called_once()
        self.assertIn("RuntimeError: relay exploded", persist.call_args.args[1])
        complete.assert_called_once()
        fail.assert_not_called()

    def test_missing_backgrounds_does_not_retry_another_model(self) -> None:
        from dashboard.backend.services import render_copy_jobs

        job = {
            "copy_job_id": "copy-1",
            "run_id": "run-1",
            "run_number": 1,
            "user_id": "user-1",
            "settings": {
                "provider": "opencode",
                "model": "opencode/big-pickle",
            },
        }
        with (
            patch.object(render_copy_jobs, "_claim_next_job", return_value=job),
            patch.object(
                render_copy_jobs,
                "get_materialized_provider_config",
                return_value={"api_key": "secret"},
            ),
            patch.object(
                render_copy_jobs,
                "generate_structured_prompt_bundle",
                side_effect=RuntimeError("No background variants found for format HERO"),
            ) as generate,
            patch.object(render_copy_jobs, "resolve_effective_config", return_value={}),
            patch.object(render_copy_jobs, "collect_copy_reuse_locks", return_value={}),
            patch.object(
                render_copy_jobs,
                "provider_generate_callable",
                return_value=lambda *args, **kwargs: {},
            ),
            patch.object(render_copy_jobs, "_persist_copy_last_error") as persist,
            patch.object(render_copy_jobs, "get_sync_db") as get_db,
            patch.object(render_copy_jobs, "_complete_job") as complete,
            patch.object(render_copy_jobs, "_fail_job") as fail,
        ):
            get_db.return_value.__getitem__.return_value.find_one.return_value = {
                "status": "running"
            }
            self.assertTrue(render_copy_jobs.process_next_render_copy_job())

        self.assertEqual(generate.call_count, 1)
        persist.assert_not_called()
        complete.assert_not_called()
        fail.assert_called_once()
        self.assertIn("No background variants found", fail.call_args.kwargs["error_detail"])

    def test_browser_provider_failure_does_not_fallback_to_opencode(self) -> None:
        from dashboard.backend.services import render_copy_jobs
        from dashboard.backend.services.render_structured_copy import (
            ProviderCallError,
        )

        job = {
            "copy_job_id": "copy-1",
            "run_id": "run-1",
            "run_number": 1,
            "user_id": "user-1",
            "settings": {
                "provider": "browser",
                "model": "chatgpt",
            },
        }
        failed = ProviderCallError(
            code="provider_invalid_output",
            provider="browser",
            model="chatgpt",
            duration_ms=11,
            http_status=200,
            error_detail="headline_missing",
        )
        with (
            patch.object(render_copy_jobs, "_claim_next_job", return_value=job),
            patch.object(
                render_copy_jobs,
                "get_materialized_provider_config",
            ) as materialize,
            patch.object(
                render_copy_jobs,
                "generate_browser_structured_prompt_bundle",
                side_effect=failed,
            ),
            patch.object(
                render_copy_jobs,
                "next_free_opencode_model",
            ) as fallback,
            patch.object(render_copy_jobs, "resolve_effective_config", return_value={}),
            patch.object(render_copy_jobs, "collect_copy_reuse_locks", return_value={}),
            patch.object(render_copy_jobs, "_record_provider_failure_trace", return_value=""),
            patch.object(render_copy_jobs, "_fail_job") as fail,
            patch.object(render_copy_jobs, "_complete_job") as complete,
        ):
            self.assertTrue(render_copy_jobs.process_next_render_copy_job())

        materialize.assert_not_called()
        fallback.assert_not_called()
        complete.assert_not_called()
        fail.assert_called_once()
        self.assertEqual(fail.call_args.kwargs["provider"], "browser")
        self.assertEqual(fail.call_args.kwargs["error_code"], "provider_invalid_output")


if __name__ == "__main__":
    unittest.main()
