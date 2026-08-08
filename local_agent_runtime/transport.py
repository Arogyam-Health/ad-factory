from __future__ import annotations

import asyncio
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

    def handle(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type") or "")
        if message_type == "job_available":
            self._available.set()
        elif message_type == "job_canceled":
            job_id = str(message.get("job_id") or "")
            if job_id:
                with self._lock:
                    self._canceled.add(job_id)

    def wait(self, timeout: float) -> bool:
        ready = self._available.wait(timeout)
        if ready:
            self._available.clear()
        return ready

    def cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            if job_id not in self._canceled:
                return False
            self._canceled.remove(job_id)
            return True


class AgentWebSocketClient:
    def __init__(
        self,
        api_base: str,
        token: str,
        signal: JobSignal,
        *,
        status_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.url = websocket_url(api_base)
        self.token = token
        self.signal = signal
        self.status_callback = status_callback or (lambda _status: None)
        self._stop = threading.Event()
        self._connected = threading.Event()
        self._thread: threading.Thread | None = None

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
                    max_size=1024 * 1024,
                ) as websocket:
                    self._connected.set()
                    self.status_callback("connected")
                    backoff = 1.0
                    while not self._stop.is_set():
                        try:
                            raw = await asyncio.wait_for(websocket.recv(), timeout=15)
                            message = json.loads(raw)
                            if isinstance(message, dict):
                                self.signal.handle(message)
                        except asyncio.TimeoutError:
                            await websocket.send(json.dumps({"type": "heartbeat", "at": time.time()}))
                        except json.JSONDecodeError:
                            continue
            except Exception as exc:
                self.status_callback(f"disconnected: {type(exc).__name__}")
            finally:
                self._connected.clear()
            await asyncio.sleep(backoff)
            backoff = min(30.0, backoff * 2)
