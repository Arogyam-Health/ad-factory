from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class LocalArtifactServerTests(unittest.TestCase):
    """The local service must expose health plus the scoped /v1 plane only.

    The legacy capability-token artifact plane was a second, competing image
    source for the dashboard; every one of its routes must now be gone.
    """

    def setUp(self) -> None:
        from local_agent_runtime.artifact_server import ArtifactServer, ArtifactServerConfig
        from local_agent_runtime.storage import AgentPaths, AgentState

        self.temp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self.temp.name) / "agent")
        self.state = AgentState(self.paths)
        self.server = ArtifactServer(
            ArtifactServerConfig(
                paths=self.paths,
                host="127.0.0.1",
                port=0,
                allowed_origins=("https://ad-factory.example",),
            )
        )
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        self.temp.cleanup()

    def get(self, path: str, *, origin: str | None = None):
        request = urllib.request.Request(self.server.url + path)
        if origin:
            request.add_header("Origin", origin)
        return urllib.request.urlopen(request, timeout=3)

    def assert_status(self, path: str, expected: int, method: str = "GET") -> None:
        request = urllib.request.Request(self.server.url + path, method=method)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=3)
        self.assertEqual(caught.exception.code, expected, path)

    def test_health_identifies_the_exact_data_root(self) -> None:
        with self.get("/healthz") as response:
            payload = json.loads(response.read())
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["data_root"], str(self.paths.root))
        self.assertEqual(payload["schema_version"], 3)
        self.assertTrue(payload["instance_id"])

    def test_every_legacy_artifact_route_is_retired(self) -> None:
        for path in (
            "/manifest",
            "/artifacts",
            "/events",
            "/download-batches?run_id=run-1",
            "/files/art_example",
            "/revisions/rev_example",
        ):
            self.assert_status(path, 410)
        self.assert_status("/files/art_example", 410, method="DELETE")
        self.assert_status("/revisions", 410, method="POST")

    def test_capability_tokens_are_no_longer_minted(self) -> None:
        import local_agent_runtime.storage as storage

        self.assertFalse(hasattr(storage, "artifact_access_token"))
        self.assertFalse(hasattr(self.state, "publish_artifact"))
        self.assertFalse(hasattr(self.state, "manifest"))
        self.assertFalse((self.paths.config / "artifact-secret").exists())

    def test_cors_still_guards_the_remaining_surface(self) -> None:
        with self.get("/healthz", origin="https://ad-factory.example") as response:
            self.assertEqual(
                response.headers.get("Access-Control-Allow-Origin"),
                "https://ad-factory.example",
            )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/healthz", origin="https://attacker.example")
        self.assertEqual(caught.exception.code, 403)

    def test_preflight_still_succeeds_for_the_v1_plane(self) -> None:
        request = urllib.request.Request(
            self.server.url + "/v1/info",
            headers={
                "Origin": "https://ad-factory.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Private-Network": "true",
            },
            method="OPTIONS",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            self.assertEqual(response.status, 204)
            self.assertEqual(
                response.headers.get("Access-Control-Allow-Private-Network"), "true"
            )


if __name__ == "__main__":
    unittest.main()
