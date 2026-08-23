from __future__ import annotations

import asyncio
from collections import deque
import json
import threading
import time
from typing import Any, Callable
from urllib.parse import urlparse, urlunparse


def websocket_url(api_base: str) -> str:
    parsed = urlparse(api_base.rstrip("/"))
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse((scheme, parsed.netloc, "/api/agent-runtime/ws", "", "", ""))


class JobSignal:
    def __init__(self) -> None:
        self._available = threading.Event()
        self._lock = threading.Lock()
        self._canceled: set[str] = set()
        self._pairing_approvals: deque[dict[str, Any]] = deque(maxlen=32)

    def handle(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type") or "")
        if message_type == "job_available":
            self._available.set()
        elif message_type == "job_canceled":
            self.request_cancel(str(message.get("job_id") or ""))
        elif message_type == "pairing_approval":
            required = (
                "challenge_id",
                "challenge_hash",
                "agent_id",
                "device_id",
                "owner_key",
                "expires_at",
            )
            if (
                all(message.get(key) for key in required)
                and len(str(message.get("challenge_hash"))) == 64
                and isinstance(message.get("scopes"), list)
                and len(message["scopes"]) <= 16
                and len(json.dumps(message, separators=(",", ":"))) <= 8192
            ):
                with self._lock:
                    self._pairing_approvals.append(dict(message))
                self._available.set()

    def wait(self, timeout: float) -> bool:
        ready = self._available.wait(timeout)
        if ready:
            self._available.clear()
        return ready

    def request_cancel(self, job_id: str) -> None:
        job_id = str(job_id or "")
        if not job_id:
            return
        with self._lock:
            self._canceled.add(job_id)

    def cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._canceled

    def drain_pairing_approvals(self) -> list[dict[str, Any]]:
        with self._lock:
            approvals = list(self._pairing_approvals)
            self._pairing_approvals.clear()
        return approvals


class AgentWebSocketClient:
    def __init__(
        self,
        api_base: str,
        token: str,
        signal: JobSignal,
        *,
        status_callback: Callable[[str], None] | None = None,
        provider_handler: Callable[[dict[str, Any]], dict[str, Any]]
        | None = None,
    ) -> None:
        self.url = websocket_url(api_base)
        self.token = token
        self.signal = signal
        self.status_callback = status_callback or (lambda _status: None)
        self.provider_handler = provider_handler
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._thread: threading.Thread | None = None
        self._provider_tasks: set[asyncio.Task[Any]] = set()

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._thread_main, name="agent-websocket", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)

    def _thread_main(self) -> None:
        asyncio.run(self._run())

    async def _run(self) -> None:
        import websockets

        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self.url,
                    additional_headers={"Authorization": f"Bearer {self.token}"},
                    open_timeout=10,
                    close_timeout=2,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=2 * 1024 * 1024 + 65536,
                ) as websocket:
                    send_lock = asyncio.Lock()
                    async with send_lock:
                        await websocket.send(
                            json.dumps(
                                {
                                    "type": "authenticate",
                                    "token": self.token,
                                },
                                separators=(",", ":"),
                            )
                        )
                    while not self._stop.is_set():
                        try:
                            raw = await asyncio.wait_for(websocket.recv(), timeout=15)
                            message = json.loads(raw)
                            if isinstance(message, dict):
                                if (
                                    message.get("type") == "connected"
                                    and self.provider_handler is not None
                                ):
                                    async with send_lock:
                                        await websocket.send(
                                            json.dumps(
                                                {
                                                    "type": "capabilities",
                                                    "provider_relay": True,
                                                }
                                            )
                                        )
                                    self.signal.handle(message)
                                elif (
                                    message.get("type")
                                    == "capabilities_ack"
                                    and message.get("provider_relay") is True
                                ):
                                    self._connected.set()
                                    self.status_callback("connected")
                                    backoff = 1.0
                                elif message.get("type") == "connected":
                                    self._connected.set()
                                    self.status_callback("connected")
                                    backoff = 1.0
                                    self.signal.handle(message)
                                elif (
                                    message.get("type") == "provider_call"
                                    and self.provider_handler is not None
                                ):
                                    task = asyncio.create_task(
                                        self._handle_provider_call(
                                            websocket,
                                            message,
                                            send_lock,
                                        )
                                    )
                                    self._provider_tasks.add(task)
                                    task.add_done_callback(
                                        self._provider_tasks.discard
                                    )
                                else:
                                    self.signal.handle(message)
                        except asyncio.TimeoutError:
                            async with send_lock:
                                await websocket.send(
                                    json.dumps(
                                        {
                                            "type": "heartbeat",
                                            "at": time.time(),
                                        }
                                    )
                                )
                        except json.JSONDecodeError:
                            continue
            except Exception as exc:
                self.status_callback(f"disconnected: {type(exc).__name__}")
            finally:
                self._connected.clear()
            await asyncio.sleep(backoff)
            backoff = min(30.0, backoff * 2)

    async def _handle_provider_call(
        self,
        websocket: Any,
        message: dict[str, Any],
        send_lock: asyncio.Lock,
    ) -> None:
        call_id = str(message.get("call_id") or "")
        if not call_id.startswith("rly_") or len(call_id) > 80:
            return
        try:
            result = await asyncio.to_thread(
                self.provider_handler,
                {
                    key: value
                    for key, value in message.items()
                    if key not in {"type", "call_id"}
                },
            )
            if not isinstance(result, dict):
                raise ValueError("Provider result is invalid")
        except Exception as exc:
            result = {
                "http_status": 0,
                "content_type": "",
                "body": "",
                "transport_error": type(exc).__name__,
            }
        async with send_lock:
            await websocket.send(
                json.dumps(
                    {
                        "type": "provider_result",
                        "call_id": call_id,
                        "result": result,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
