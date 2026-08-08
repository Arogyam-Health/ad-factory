from __future__ import annotations

import datetime
import json
import shutil
import ssl
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEVICE_ID = "dev_" + "e" * 32


class _RecordingServer(ThreadingHTTPServer):
    def __init__(self, server_address, handler_class):
        super().__init__(server_address, handler_class)
        self.requests: list[dict[str, object]] = []


class _DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/js/local-data-plane.js":
            body = (
                ROOT / "dashboard/frontend/js/local-data-plane.js"
            ).read_bytes()
            self._send(200, body, "text/javascript")
            return
        if self.path == "/":
            local_origin = self.server.local_origin
            body = f"""<!doctype html>
<script type="module">
import {{ LocalDataPlaneClient }} from "/js/local-data-plane.js";
const deviceId = {json.dumps(DEVICE_ID)};
sessionStorage.setItem(
  "ad_factory_local_session:" + deviceId,
  JSON.stringify({{
    access_token: "browser-e2e-session",
    expires_at: Date.now() / 1000 + 300,
    device_id: deviceId,
    agent_id: "agent-browser-e2e",
  }}),
);
try {{
  const client = new LocalDataPlaneClient({json.dumps(local_origin)});
  const envelope = await client.allocateRun({{
    agentId: "agent-browser-e2e",
    deviceId,
    ownerType: "user",
    ownerId: "user-browser-e2e",
    flowType: "structured",
    settings: {{ provider: "google", batch_size: 1 }},
  }});
  const uploaded = await client.uploadAssets(
    [new File([new Uint8Array([137,80,78,71,13,10,26,10,1,2,3])], "product.png", {{ type: "image/png" }})],
    {{ kind: "product_image", deviceId, operationId: "browser-e2e-upload" }},
  );
  const download = await client.downloadRun("run_browser_e2e", deviceId);
  const events = [];
  const controller = new AbortController();
  await client.streamEvents({{
    after: 0,
    deviceId,
    signal: controller.signal,
    reconnectDelay: 1,
    onEvent(event) {{
      events.push(event);
      if (events.length === 2) controller.abort();
    }},
  }});
  window.__result = {{ envelope, uploaded, downloadBytes: download.size, events }};
}} catch (error) {{
  window.__error = String(error?.stack || error);
}}
</script>""".encode()
            self._send(200, body, "text/html")
            return
        self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.requests.append(
            {"path": self.path, "headers": dict(self.headers), "body": body}
        )
        if self.path == "/api/runs/allocate":
            response = {
                "run_id": "run_browser_e2e",
                "run_number": 1,
                "display_batch": "v1",
                "agent_id": "agent-browser-e2e",
                "device_id": DEVICE_ID,
            }
            self._send(200, json.dumps(response).encode(), "application/json")
            return
        self._send(404, b"not found", "text/plain")

    def _send(self, status: int, body: bytes, media_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _LocalDataPlaneHandler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_OPTIONS(self) -> None:
        self.server.requests.append(
            {"path": self.path, "headers": dict(self.headers), "body": b""}
        )
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers", "Authorization, Idempotency-Key"
        )
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()

    def do_GET(self) -> None:
        self.server.requests.append(
            {"path": self.path, "headers": dict(self.headers), "body": b""}
        )
        if self.path == "/v1/runs/run_browser_e2e/download":
            body = b"PK\x03\x04browser-e2e-zip"
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/v1/events?after="):
            sequence = int(self.path.rsplit("=", 1)[-1]) + 1
            body = (
                f"id: {sequence}\n"
                f"data: {{\"sequence\":{sequence},\"operation\":\"updated\"}}\n\n"
            ).encode()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self._cors()
        self.end_headers()

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.requests.append(
            {"path": self.path, "headers": dict(self.headers), "body": body}
        )
        if self.path.startswith("/v1/assets?"):
            response = {
                "items": [
                    {
                        "resource_id": "res_browser_e2e",
                        "version": 1,
                        "sha256": "a" * 64,
                        "bytes": 11,
                        "kind": "product_image",
                    }
                ]
            }
            self.send_response(201)
            self._cors()
            encoded = json.dumps(response).encode()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            return
        self.send_response(404)
        self._cors()
        self.end_headers()

    def _cors(self) -> None:
        self.send_header(
            "Access-Control-Allow-Origin", self.server.dashboard_origin
        )
        self.send_header("Vary", "Origin")


def _certificate(directory: Path) -> tuple[Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "dashboard.test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("dashboard.test")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    key_path = directory / "dashboard.key"
    certificate_path = directory / "dashboard.crt"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return certificate_path, key_path


class BrowserLocalDataPlaneE2ETests(unittest.TestCase):
    def test_https_dashboard_sends_file_bytes_only_to_loopback_after_reload(
        self,
    ) -> None:
        chrome = next(
            (
                candidate
                for candidate in (
                    shutil.which("google-chrome"),
                    shutil.which("google-chrome-stable"),
                    shutil.which("chromium"),
                    shutil.which("chromium-browser"),
                )
                if candidate
            ),
            None,
        )
        if chrome is None:
            self.skipTest("Chrome or Chromium is required for browser boundary E2E")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dashboard = _RecordingServer(("127.0.0.1", 0), _DashboardHandler)
            local = _RecordingServer(("127.0.0.1", 0), _LocalDataPlaneHandler)
            dashboard_origin = f"https://dashboard.test:{dashboard.server_port}"
            local_origin = f"http://127.0.0.1:{local.server_port}"
            dashboard.local_origin = local_origin
            local.dashboard_origin = dashboard_origin

            certificate, key = _certificate(root)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certificate, key)
            dashboard.socket = context.wrap_socket(dashboard.socket, server_side=True)
            threads = [
                threading.Thread(target=server.serve_forever, daemon=True)
                for server in (dashboard, local)
            ]
            for thread in threads:
                thread.start()
            try:
                from playwright.sync_api import sync_playwright

                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(
                        executable_path=chrome,
                        headless=True,
                        args=[
                            "--no-sandbox",
                            "--ignore-certificate-errors",
                            "--host-resolver-rules=MAP dashboard.test 127.0.0.1",
                        ],
                    )
                    page = browser.new_page(ignore_https_errors=True)
                    for _attempt in range(2):
                        page.goto(dashboard_origin, wait_until="domcontentloaded")
                        page.wait_for_function(
                            "() => window.__result || window.__error", timeout=15_000
                        )
                        error = page.evaluate("window.__error || ''")
                        self.assertEqual(error, "")
                        self.assertEqual(
                            page.evaluate("window.__result.uploaded[0].resource_id"),
                            "res_browser_e2e",
                        )
                        self.assertGreater(
                            page.evaluate("window.__result.downloadBytes"), 4
                        )
                        self.assertEqual(
                            page.evaluate(
                                "window.__result.events.map((event) => event.sequence)"
                            ),
                            [1, 2],
                        )
                    browser.close()
            finally:
                dashboard.shutdown()
                local.shutdown()
                dashboard.server_close()
                local.server_close()

        render_posts = [
            request
            for request in dashboard.requests
            if request["path"] == "/api/runs/allocate"
        ]
        local_posts = [
            request
            for request in local.requests
            if str(request["path"]).startswith("/v1/assets?")
            and request["body"]
        ]
        self.assertEqual(len(render_posts), 2)
        self.assertEqual(len(local_posts), 2)
        self.assertEqual(
            len(
                [
                    request
                    for request in local.requests
                    if request["path"] == "/v1/runs/run_browser_e2e/download"
                    and any(
                        str(key).lower() == "authorization"
                        for key in request["headers"]
                    )
                ]
            ),
            2,
        )
        for request in render_posts:
            payload = json.loads(request["body"])
            serialized = json.dumps(payload).lower()
            self.assertNotIn("file", serialized)
            self.assertNotIn("base64", serialized)
            self.assertNotIn("png", serialized)
        for request in local_posts:
            self.assertIn(b"\x89PNG\r\n\x1a\n\x01\x02\x03", request["body"])
            headers = {
                str(key).lower(): str(value)
                for key, value in request["headers"].items()
            }
            self.assertTrue(
                headers.get("authorization", "").startswith("Bearer ")
            )
        preflights = [
            request
            for request in local.requests
            if request["path"] == "/v1/assets?kind=product_image"
            and not request["body"]
        ]
        if preflights:
            self.assertTrue(
                all(
                    {
                        str(key).lower(): str(value)
                        for key, value in request["headers"].items()
                    }.get("origin")
                    == dashboard_origin
                    for request in preflights
                )
            )


if __name__ == "__main__":
    unittest.main()
