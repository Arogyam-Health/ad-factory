from __future__ import annotations

import json
import hmac
import os
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .data_plane import LocalDataPlane, load_or_create_internal_token
from .storage import AgentPaths, AgentState


@dataclass(frozen=True)
class ArtifactServerConfig:
    paths: AgentPaths
    host: str = "127.0.0.1"
    port: int = 8765
    allowed_origins: tuple[str, ...] = (
        "https://ad-factory-3rn5.onrender.com",
    )
    max_upload_bytes: int = 25 * 1024 * 1024
    max_request_bytes: int = 100 * 1024 * 1024
    challenge_ttl_seconds: int = 600
    session_ttl_seconds: int = 15 * 60


class ArtifactServer:
    def __init__(self, config: ArtifactServerConfig) -> None:
        self.config = config
        self.state = AgentState(config.paths)
        self.instance_id = uuid.uuid4().hex
        self.started_at = time.time()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.data_plane = LocalDataPlane(self)
        self._runtime_token = load_or_create_internal_token(config.paths)

    def approve_pairing_challenge(
        self,
        challenge_id: str,
        challenge: str | None = None,
        *,
        challenge_digest: str | None = None,
        owner_key: str,
        scopes: list[str] | tuple[str, ...],
        agent_id: str = "",
        device_id: str = "",
    ) -> None:
        """Accept an approval delivered by the authenticated agent channel."""
        self.data_plane.approve_challenge(
            challenge_id,
            challenge,
            challenge_digest=challenge_digest,
            owner_key=owner_key,
            scopes=scopes,
            agent_id=agent_id,
            device_id=device_id,
        )

    @property
    def url(self) -> str:
        if self._server is None:
            return f"http://{self.config.host}:{self.config.port}"
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> None:
        if self._server is not None:
            return
        handler = self._handler_class()
        self._server = ThreadingHTTPServer((self.config.host, self.config.port), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="artifact-server", daemon=True)
        self._thread.start()

    def serve_forever(self) -> None:
        if self._server is None:
            handler = self._handler_class()
            self._server = ThreadingHTTPServer((self.config.host, self.config.port), handler)
            self._server.daemon_threads = True
        try:
            self._server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self._server.server_close()
            self._server = None

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._thread = None
        self._server = None

    def _handler_class(self):
        service = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "AdFactoryArtifact/3"
            protocol_version = "HTTP/1.1"

            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def _origin(self) -> str:
                return str(self.headers.get("Origin") or "").rstrip("/")

            def _origin_allowed(self) -> bool:
                origin = self._origin()
                return not origin or origin in service.config.allowed_origins

            def _cors_headers(self) -> None:
                origin = self._origin()
                if origin in service.config.allowed_origins:
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Expose-Headers", "Content-Disposition, ETag")
                self.send_header("Access-Control-Allow-Private-Network", "true")
                self.send_header("Cache-Control", "no-store")

            def _json(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self._cors_headers()
                self.end_headers()
                self.wfile.write(body)

            def _error(self, status: int, code: str, message: str) -> None:
                self._json(status, {"error": {"code": code, "message": message}})

            def do_OPTIONS(self) -> None:
                if service.data_plane.dispatch(self):
                    return
                if not self._origin_allowed():
                    self._error(403, "origin_forbidden", "Origin is not allowed")
                    return
                self.send_response(204)
                self._cors_headers()
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self) -> None:
                if service.data_plane.dispatch(self):
                    return
                if not self._origin_allowed():
                    self._error(403, "origin_forbidden", "Origin is not allowed")
                    return
                request_path = urllib.parse.urlparse(self.path).path
                if request_path in {"/health", "/healthz"}:
                    self._json(
                        200,
                        {
                            "status": "ok",
                            "instance_id": service.instance_id,
                            "pid": os.getpid(),
                            "data_root": str(service.config.paths.root),
                            "schema_version": 3,
                            "started_at": service.started_at,
                        },
                    )
                    return
                self._error(
                    410,
                    "legacy_artifact_plane_removed",
                    "Artifact content is served only by the scoped /v1 API",
                )

            def do_DELETE(self) -> None:
                if service.data_plane.dispatch(self):
                    return
                self._error(
                    410,
                    "legacy_artifact_plane_removed",
                    "Artifact content is served only by the scoped /v1 API",
                )

            def do_POST(self) -> None:
                if urllib.parse.urlparse(self.path).path == "/_agent/pairing/approvals":
                    try:
                        service.data_plane._validate_request_boundary(self)
                        authorization = str(self.headers.get("Authorization") or "")
                        supplied = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
                        if not supplied or not hmac.compare_digest(supplied, service._runtime_token):
                            self._error(401, "authentication_required", "Runtime authentication required")
                            return
                        try:
                            length = int(self.headers.get("Content-Length") or "0")
                        except ValueError:
                            length = 0
                        if length <= 0 or length > 8192:
                            self._error(413, "invalid_body_size", "Approval body is invalid")
                            return
                        payload = json.loads(self.rfile.read(length))
                        service.approve_pairing_challenge(
                            str(payload.get("challenge_id") or ""),
                            challenge_digest=str(payload.get("challenge_hash") or ""),
                            owner_key=str(payload.get("owner_key") or ""),
                            scopes=list(payload.get("scopes") or []),
                            agent_id=str(payload.get("agent_id") or ""),
                            device_id=str(payload.get("device_id") or ""),
                        )
                    except (json.JSONDecodeError, ValueError):
                        self._error(400, "invalid_approval", "Pairing approval is invalid")
                        return
                    self._json(200, {"status": "approved"})
                    return
                if service.data_plane.dispatch(self):
                    return
                self._error(
                    410,
                    "legacy_artifact_plane_removed",
                    "Artifact content is served only by the scoped /v1 API",
                )

            def do_HEAD(self) -> None:
                if service.data_plane.dispatch(self):
                    return
                self._error(404, "not_found", "Endpoint not found")

            def do_PUT(self) -> None:
                if service.data_plane.dispatch(self):
                    return
                self._error(404, "not_found", "Endpoint not found")

        return Handler


def run_artifact_server(
    data_root: str,
    port: int = 8765,
    allowed_origins: tuple[str, ...] | None = None,
) -> None:
    config = ArtifactServerConfig(
        paths=AgentPaths(Path(data_root).expanduser().resolve()),
        port=port,
        allowed_origins=allowed_origins or ArtifactServerConfig.allowed_origins,
    )
    ArtifactServer(config).serve_forever()
