from __future__ import annotations

import json
import hmac
import mimetypes
import os
import shutil
import tempfile
import threading
import time
import urllib.parse
import uuid
import zipfile
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .data_plane import LocalDataPlane
from .storage import AgentPaths, AgentState, artifact_access_token


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
    challenge_ttl_seconds: int = 120
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

    def approve_pairing_challenge(
        self,
        challenge_id: str,
        challenge: str,
        *,
        owner_key: str,
        scopes: list[str] | tuple[str, ...],
    ) -> None:
        """Accept an approval delivered by the authenticated agent channel."""
        self.data_plane.approve_challenge(
            challenge_id,
            challenge,
            owner_key=owner_key,
            scopes=scopes,
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
        self._server.serve_forever()

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._thread = None
        self._server = None

    def owner_manifest(self, owner: str) -> dict[str, Any]:
        manifest = self.state.manifest(self.url, owner_key=owner)
        capability = urllib.parse.urlencode({
            "owner": owner,
            "token": artifact_access_token(self.config.paths, owner),
        })
        for image in manifest["images"]:
            image["url"] = f"{self.url}/files/{image['artifact_id']}?{capability}"
        return manifest

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

            def _authorized_owner(self) -> str | None:
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                owner = str((query.get("owner") or [""])[0])
                token = str((query.get("token") or [""])[0])
                if not owner or not token:
                    return None
                expected = artifact_access_token(service.config.paths, owner)
                return owner if hmac.compare_digest(token, expected) else None

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
                owner = self._authorized_owner()
                if owner is None:
                    self._error(401, "invalid_capability", "A valid artifact capability is required")
                    return
                if request_path in {"/artifacts", "/manifest"}:
                    self._json(200, service.owner_manifest(owner))
                    return
                if request_path == "/events":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Connection", "keep-alive")
                    self._cors_headers()
                    self.end_headers()
                    last_sequence = -1
                    last_heartbeat = 0.0
                    try:
                        while True:
                            sequence = service.state.change_sequence()
                            now = time.time()
                            if sequence != last_sequence:
                                self.wfile.write(f"event: artifacts\ndata: {sequence}\n\n".encode("utf-8"))
                                self.wfile.flush()
                                last_sequence = sequence
                            elif now - last_heartbeat >= 15:
                                self.wfile.write(b": heartbeat\n\n")
                                self.wfile.flush()
                                last_heartbeat = now
                            time.sleep(0.25)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return
                if request_path == "/download-batches":
                    query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    requested_run_ids = {value for value in query.get("run_id", []) if value}
                    if not requested_run_ids:
                        self._error(400, "run_ids_required", "At least one run_id is required")
                        return
                    images = [
                        item for item in service.owner_manifest(owner)["images"]
                        if str(item.get("run_id") or "") in requested_run_ids
                    ]
                    if not images:
                        self._error(404, "artifacts_not_found", "No local files found for selected runs")
                        return
                    temporary = tempfile.NamedTemporaryFile(prefix="ad-factory-", suffix=".zip", delete=False)
                    temporary_path = Path(temporary.name)
                    temporary.close()
                    try:
                        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                            for item in images:
                                path = service.config.paths.root / str(item["path"])
                                if path.is_file():
                                    archive.write(
                                        path,
                                        arcname=(
                                            f"v{item['run_number']}-{item['run_id']}/"
                                            f"{item['prompt_id']}/{str(item['aspect_ratio']).replace(':', '_')}/"
                                            f"{item['filename']}"
                                        ),
                                    )
                        filename_part = "_".join(sorted(requested_run_ids))
                        filename_part = "".join(ch for ch in filename_part if ch.isalnum() or ch in "-_") or "runs"
                        self.send_response(200)
                        self.send_header("Content-Type", "application/zip")
                        self.send_header("Content-Disposition", f'attachment; filename="ad_factory_{filename_part}.zip"')
                        self.send_header("Content-Length", str(temporary_path.stat().st_size))
                        self._cors_headers()
                        self.end_headers()
                        with temporary_path.open("rb") as source:
                            shutil.copyfileobj(source, self.wfile, length=1024 * 1024)
                    finally:
                        temporary_path.unlink(missing_ok=True)
                    return
                if request_path.startswith("/revisions/"):
                    revision_id = urllib.parse.unquote(request_path.removeprefix("/revisions/"))
                    revision = service.state.revision(revision_id)
                    if revision is None:
                        self._error(404, "revision_not_found", "Revision not found")
                        return
                    artifact = service.state.artifact_record(str(revision.get("artifact_id") or ""))
                    if artifact is None or artifact.get("owner_key") != owner:
                        self._error(404, "revision_not_found", "Revision not found")
                        return
                    capability = urllib.parse.urlencode({
                        "owner": owner,
                        "token": artifact_access_token(service.config.paths, owner),
                    })
                    revision["status_url"] = f"{service.url}/revisions/{revision_id}?{capability}"
                    self._json(200, revision)
                    return
                if request_path.startswith("/files/"):
                    artifact_id = urllib.parse.unquote(request_path.removeprefix("/files/"))
                    if not artifact_id or "/" in artifact_id or ".." in artifact_id:
                        self._error(400, "invalid_artifact_id", "Invalid artifact ID")
                        return
                    path = service.state.artifact_path(artifact_id)
                    record = service.state.artifact_record(artifact_id)
                    if record is None or record.get("owner_key") != owner or path is None or not path.is_file():
                        self._error(404, "artifact_not_found", "Artifact not found")
                        return
                    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(path.stat().st_size))
                    self._cors_headers()
                    self.end_headers()
                    with path.open("rb") as source:
                        shutil.copyfileobj(source, self.wfile, length=1024 * 1024)
                    return
                self._error(404, "not_found", "Endpoint not found")

            def do_DELETE(self) -> None:
                if service.data_plane.dispatch(self):
                    return
                self._error(
                    405,
                    "legacy_read_only",
                    "Legacy artifact endpoints are read-only; use the scoped /v1 API",
                )
                return
                if not self._origin_allowed():
                    self._error(403, "origin_forbidden", "Origin is not allowed")
                    return
                owner = self._authorized_owner()
                if owner is None:
                    self._error(401, "invalid_capability", "A valid artifact capability is required")
                    return
                request_path = urllib.parse.urlparse(self.path).path
                if not request_path.startswith("/files/"):
                    self._error(404, "not_found", "Endpoint not found")
                    return
                artifact_id = urllib.parse.unquote(request_path.removeprefix("/files/"))
                record = service.state.artifact_record(artifact_id)
                if record is None or record.get("owner_key") != owner:
                    self._error(404, "artifact_not_found", "Artifact not found")
                    return
                deleted = service.state.delete_artifact(artifact_id)
                if not deleted:
                    self._error(404, "artifact_not_found", "Artifact not found")
                    return
                self._json(200, {"status": "deleted", "artifact_id": artifact_id})

            def do_POST(self) -> None:
                if service.data_plane.dispatch(self):
                    return
                self._error(
                    405,
                    "legacy_read_only",
                    "Legacy artifact endpoints are read-only; use the scoped /v1 API",
                )
                return
                if not self._origin_allowed():
                    self._error(403, "origin_forbidden", "Origin is not allowed")
                    return
                owner = self._authorized_owner()
                if owner is None:
                    self._error(401, "invalid_capability", "A valid artifact capability is required")
                    return
                request_path = urllib.parse.urlparse(self.path).path
                if request_path != "/revisions":
                    self._error(404, "not_found", "Endpoint not found")
                    return
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = 0
                if length <= 0 or length > 16 * 1024:
                    self._error(413, "invalid_body_size", "Revision request must be between 1 byte and 16 KiB")
                    return
                try:
                    payload = json.loads(self.rfile.read(length))
                    image_path = urllib.parse.urlparse(str(payload.get("image_file") or "")).path
                    artifact_id = urllib.parse.unquote(image_path.removeprefix("/files/"))
                    record = service.state.artifact_record(artifact_id)
                    if record is None or record.get("owner_key") != owner:
                        raise ValueError("Artifact not found")
                    revision = service.state.queue_revision(
                        artifact_id,
                        str(payload.get("comment") or ""),
                        str(payload.get("engine") or "chatgpt").lower(),
                    )
                except (json.JSONDecodeError, ValueError) as exc:
                    self._error(400, "invalid_revision", str(exc))
                    return
                capability = urllib.parse.urlencode({
                    "owner": owner,
                    "token": artifact_access_token(service.config.paths, owner),
                })
                revision["status_url"] = f"{service.url}/revisions/{revision['revision_id']}?{capability}"
                self._json(202, revision)

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
