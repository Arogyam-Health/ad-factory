from __future__ import annotations

from dataclasses import dataclass, field
import json
import threading
import time
import uuid
from typing import Any


MAX_RELAY_REQUEST_BYTES = 2 * 1024 * 1024
MAX_RELAY_RESPONSE_BYTES = 4 * 1024 * 1024
RELAY_PROTOCOL_VERSION = "v1"


class ProviderRelayError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class _PendingCall:
    call_id: str
    user_id: str
    agent_id: str
    device_id: str
    expires_at: float
    event: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None
    error_code: str = ""


class ProviderRelayBroker:
    def __init__(self, *, ttl_seconds: float = 30 * 60) -> None:
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self._pending: dict[str, _PendingCall] = {}
        self._lock = threading.Lock()

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def invoke(
        self,
        *,
        user_id: str,
        payload: dict[str, Any],
        connections: Any,
    ) -> dict[str, Any]:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(serialized) > MAX_RELAY_REQUEST_BYTES:
            raise ProviderRelayError("provider_relay_request_too_large")
        connection = connections.for_user(
            user_id,
            protocol_version=RELAY_PROTOCOL_VERSION,
            supports_provider_relay=True,
        )
        if connection is None:
            raise ProviderRelayError("local_provider_agent_offline")

        call_id = "rly_" + uuid.uuid4().hex
        pending = _PendingCall(
            call_id=call_id,
            user_id=user_id,
            agent_id=str(connection.agent_id),
            device_id=str(connection.device_id),
            expires_at=time.monotonic() + self.ttl_seconds,
        )
        with self._lock:
            self._pending[call_id] = pending
        delivered = connections.notify_from_thread(
            pending.agent_id,
            {"type": "provider_call", "call_id": call_id, **payload},
            device_id=pending.device_id,
            wait=True,
        )
        if not delivered:
            self._remove(call_id)
            raise ProviderRelayError("local_provider_agent_disconnected")

        if not pending.event.wait(self.ttl_seconds):
            self._remove(call_id)
            raise ProviderRelayError("provider_relay_expired")
        self._remove(call_id)
        if pending.error_code:
            raise ProviderRelayError(pending.error_code)
        if pending.result is None:
            raise ProviderRelayError("provider_relay_invalid_result")
        return pending.result

    def complete(
        self,
        *,
        call_id: str,
        user_id: str,
        agent_id: str,
        device_id: str,
        result: dict[str, Any],
    ) -> bool:
        with self._lock:
            pending = self._pending.get(call_id)
            if (
                pending is None
                or pending.user_id != user_id
                or pending.agent_id != agent_id
                or pending.device_id != device_id
                or pending.expires_at <= time.monotonic()
                or pending.event.is_set()
            ):
                return False
            if not isinstance(result, dict):
                return False
            body = result.get("body")
            if not isinstance(body, str):
                return False
            if len(body.encode("utf-8")) > MAX_RELAY_RESPONSE_BYTES:
                pending.error_code = "provider_relay_response_too_large"
            else:
                pending.result = {
                    "http_status": int(result.get("http_status") or 0),
                    "content_type": str(
                        result.get("content_type") or ""
                    )[:200],
                    "body": body,
                    "transport_error": str(
                        result.get("transport_error") or ""
                    )[:100],
                }
            pending.event.set()
            return True

    def fail_agent(self, agent_id: str, device_id: str) -> int:
        failed = 0
        with self._lock:
            for pending in self._pending.values():
                if (
                    pending.agent_id == agent_id
                    and pending.device_id == device_id
                    and not pending.event.is_set()
                ):
                    pending.error_code = (
                        "local_provider_agent_disconnected"
                    )
                    pending.event.set()
                    failed += 1
        return failed

    def _remove(self, call_id: str) -> None:
        with self._lock:
            self._pending.pop(call_id, None)


provider_relay = ProviderRelayBroker()
