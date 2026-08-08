from __future__ import annotations

import json
import io
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path


class LocalArtifactServerTests(unittest.TestCase):
    def setUp(self) -> None:
        from local_agent_runtime.artifact_server import ArtifactServer, ArtifactServerConfig
        from local_agent_runtime.storage import AgentPaths, AgentState, artifact_access_token

        self.temp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self.temp.name) / "agent")
        self.state = AgentState(self.paths)
        self.owner = "user-1"
        self.token = artifact_access_token(self.paths, self.owner)
        staging = self.paths.staging / "job-1" / "creative.png"
        staging.parent.mkdir(parents=True)
        staging.write_bytes(b"image-bytes")
        self.artifact = self.state.publish_artifact(
            source=staging,
            owner_key=self.owner,
            run_id="run-1",
            run_number=1,
            job_id="job-1",
            item_id="item-1",
            prompt_id="prompt-1",
            aspect_ratio="4:5",
            filename="creative.png",
        )
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

    def request(self, path: str, *, origin: str | None = None):
        separator = "&" if "?" in path else "?"
        capability = urllib.parse.urlencode({"owner": self.owner, "token": self.token})
        request = urllib.request.Request(self.server.url + path + separator + capability)
        if origin:
            request.add_header("Origin", origin)
        return urllib.request.urlopen(request, timeout=3)

    def post_json(self, path: str, payload: dict):
        capability = urllib.parse.urlencode({"owner": self.owner, "token": self.token})
        request = urllib.request.Request(
            self.server.url + path + "?" + capability,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Origin": "https://ad-factory.example"},
            method="POST",
        )
        return urllib.request.urlopen(request, timeout=3)

    def test_revision_preflight_allows_post(self) -> None:
        capability = urllib.parse.urlencode({"owner": self.owner, "token": self.token})
        request = urllib.request.Request(
            self.server.url + "/revisions?" + capability,
            headers={
                "Origin": "https://ad-factory.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Private-Network": "true",
            },
            method="OPTIONS",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            self.assertEqual(response.status, 204)
            self.assertIn("POST", response.headers.get("Access-Control-Allow-Methods", ""))
            self.assertEqual(response.headers.get("Access-Control-Allow-Private-Network"), "true")

    def test_health_identifies_the_exact_data_root(self) -> None:
        with self.request("/healthz") as response:
            payload = json.loads(response.read())
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["data_root"], str(self.paths.root))
        self.assertEqual(payload["schema_version"], 3)
        self.assertTrue(payload["instance_id"])

    def test_manifest_and_file_are_served_by_artifact_id(self) -> None:
        with self.request("/manifest") as response:
            manifest = json.loads(response.read())
        self.assertEqual(len(manifest["images"]), 1)
        self.assertEqual(manifest["images"][0]["artifact_id"], self.artifact.artifact_id)

        with self.request(f"/files/{self.artifact.artifact_id}") as response:
            self.assertEqual(response.read(), b"image-bytes")

    def test_cors_allows_only_the_configured_dashboard(self) -> None:
        with self.request("/manifest", origin="https://ad-factory.example") as response:
            self.assertEqual(
                response.headers.get("Access-Control-Allow-Origin"),
                "https://ad-factory.example",
            )

        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/manifest", origin="https://attacker.example")
        self.assertEqual(caught.exception.code, 403)

    def test_manifest_requires_an_owner_capability(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(self.server.url + "/manifest", timeout=3)
        self.assertEqual(caught.exception.code, 401)

    def test_owner_capability_cannot_read_another_owners_artifact(self) -> None:
        from local_agent_runtime.storage import artifact_access_token

        other_owner = "user-2"
        capability = urllib.parse.urlencode({
            "owner": other_owner,
            "token": artifact_access_token(self.paths, other_owner),
        })
        with urllib.request.urlopen(self.server.url + "/manifest?" + capability, timeout=3) as response:
            manifest = json.loads(response.read())
        self.assertEqual(manifest["images"], [])

        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(
                self.server.url + f"/files/{self.artifact.artifact_id}?" + capability,
                timeout=3,
            )
        self.assertEqual(caught.exception.code, 404)

    def test_revision_request_is_durably_queued(self) -> None:
        with self.post_json(
            "/revisions",
            {
                "image_file": f"{self.server.url}/files/{self.artifact.artifact_id}",
                "comment": "Make the headline larger",
                "engine": "chatgpt",
            },
        ) as response:
            queued = json.loads(response.read())
        self.assertEqual(response.status, 202)
        self.assertEqual(queued["status"], "queued")

        with self.request(f"/revisions/{queued['revision_id']}") as response:
            status = json.loads(response.read())
        self.assertEqual(status["status"], "queued")
        self.assertEqual(status["artifact_id"], self.artifact.artifact_id)

    def test_batch_download_streams_published_artifacts(self) -> None:
        with self.request("/download-batches?run_id=run-1") as response:
            body = response.read()
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            path = "v1-run-1/prompt-1/4_5/creative.png"
            self.assertEqual(archive.namelist(), [path])
            self.assertEqual(archive.read(path), b"image-bytes")


if __name__ == "__main__":
    unittest.main()
