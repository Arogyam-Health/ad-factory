from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class LocalDataPlaneAssetTests(unittest.TestCase):
    ORIGIN = "https://ad-factory-3rn5.onrender.com"

    def setUp(self) -> None:
        from local_agent_runtime.artifact_server import ArtifactServer, ArtifactServerConfig
        from local_agent_runtime.storage import AgentPaths

        self.temp = tempfile.TemporaryDirectory()
        self.server = ArtifactServer(
            ArtifactServerConfig(
                paths=AgentPaths(Path(self.temp.name) / "agent"),
                port=0,
                allowed_origins=(self.ORIGIN,),
                max_upload_bytes=128,
                max_request_bytes=512,
            )
        )
        self.server.start()
        self.token = self.pair(
            [
                "manifest:read",
                "content:read",
                "assets:write",
                "documents:write",
                "prompts:write",
                "runs:execute",
                "outputs:write",
                "revisions:write",
                "delete",
            ]
        )

    def tearDown(self) -> None:
        self.server.stop()
        self.temp.cleanup()

    def open(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        payload: dict | None = None,
        headers: dict[str, str] | None = None,
        token: str | None = None,
    ):
        if payload is not None:
            body = json.dumps(payload).encode()
        request_headers = {"Origin": self.ORIGIN, **(headers or {})}
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
        bearer = self.token if token is None and hasattr(self, "token") else token
        if bearer:
            request_headers["Authorization"] = f"Bearer {bearer}"
        request = urllib.request.Request(
            self.server.url + path,
            data=body,
            headers=request_headers,
            method=method,
        )
        return urllib.request.urlopen(request, timeout=3)

    def pair(self, scopes: list[str]) -> str:
        with self.open("POST", "/v1/pairing/challenges", payload={}, token="") as response:
            challenge = json.loads(response.read())
        self.server.approve_pairing_challenge(
            challenge["challenge_id"],
            challenge["challenge"],
            owner_key="user-1",
            scopes=scopes,
        )
        with self.open(
            "POST",
            "/v1/pairing/sessions",
            payload={"challenge_id": challenge["challenge_id"], "challenge": challenge["challenge"]},
            token="",
        ) as response:
            return json.loads(response.read())["access_token"]

    def upload(self, operation_id: str = "upload-1", body: bytes = PNG, filename: str = "hero.png"):
        return self.open(
            "POST",
            "/v1/assets?kind=product_image",
            body=body,
            headers={
                "Content-Type": "application/octet-stream",
                "X-Filename": filename,
                "Idempotency-Key": operation_id,
            },
        )

    def test_streaming_upload_is_hashed_idempotent_and_metadata_is_redacted(self) -> None:
        with self.upload() as response:
            created = json.loads(response.read())
        with self.upload() as response:
            retry = json.loads(response.read())
        self.assertEqual(created, retry)
        self.assertEqual(created["sha256"], __import__("hashlib").sha256(PNG).hexdigest())
        self.assertNotIn(str(Path(self.temp.name)), json.dumps(created))
        with self.open("GET", "/v1/assets") as response:
            assets = json.loads(response.read())["items"]
        self.assertEqual(len(assets), 1)
        self.assertNotIn("path", json.dumps(assets).lower())

    def test_asset_metadata_content_head_range_and_delete(self) -> None:
        with self.upload() as response:
            resource_id = json.loads(response.read())["resource_id"]
        with self.open("GET", f"/v1/assets/{resource_id}") as response:
            metadata = json.loads(response.read())
            self.assertEqual(response.headers["ETag"], '"1"')
        self.assertEqual(metadata["bytes"], len(PNG))
        with self.open("HEAD", f"/v1/assets/{resource_id}/content") as response:
            self.assertEqual(int(response.headers["Content-Length"]), len(PNG))
            self.assertEqual(response.read(), b"")
        with self.open(
            "GET",
            f"/v1/assets/{resource_id}/content",
            headers={"Range": "bytes=2-7"},
        ) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.read(), PNG[2:8])
            self.assertEqual(response.headers["Content-Range"], f"bytes 2-7/{len(PNG)}")
        with self.open("DELETE", f"/v1/assets/{resource_id}", headers={"Idempotency-Key": "delete-1"}):
            pass
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.open("GET", f"/v1/assets/{resource_id}")
        self.assertEqual(caught.exception.code, 404)

    def test_missing_asset_delete_is_idempotent_after_local_reset(self) -> None:
        for operation_id in ("delete-missing-1", "delete-missing-2"):
            with self.open(
                "DELETE",
                "/v1/assets/res_missing_after_reset",
                headers={"Idempotency-Key": operation_id},
            ) as response:
                payload = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(payload["status"], "deleted")

    def test_trace_list_read_and_delete_remain_on_localhost(self) -> None:
        source = self.server.config.paths.staging / "trace.json"
        source.write_text(
            json.dumps(
                {
                    "provider": "fake",
                    "model": "test-model",
                    "status": "completed",
                    "request": {"task": "local only"},
                    "response": {"ok": True},
                }
            ),
            encoding="utf-8",
        )
        version = self.server.state.put_resource(
            source=source,
            owner_key="user-1",
            kind="trace",
            logical_key="run-1:job-1:trace",
            operation_id="trace-create",
            metadata={"run_id": "run-1", "job_id": "job-1", "status": "completed"},
            media_type="application/json",
        )

        with self.open("GET", "/v1/traces") as response:
            listed = json.loads(response.read())
        self.assertEqual(listed["items"][0]["trace_id"], version.resource_id)
        self.assertNotIn("local only", json.dumps(listed))
        with self.open(
            "GET", f"/v1/traces/{version.resource_id}/content"
        ) as response:
            self.assertEqual(json.loads(response.read())["request"]["task"], "local only")
        with self.open(
            "DELETE",
            f"/v1/traces/{version.resource_id}",
            headers={"Idempotency-Key": "trace-delete"},
        ) as response:
            self.assertEqual(json.loads(response.read())["status"], "deleted")

    def test_upload_rejects_traversal_mime_mismatch_and_limits(self) -> None:
        cases = [
            ("../hero.png", PNG, 400),
            ("hero.jpg", PNG, 415),
            ("hero.png", b"x" * 129, 413),
        ]
        for index, (filename, body, status) in enumerate(cases):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.upload(f"bad-{index}", body, filename)
            self.assertEqual(caught.exception.code, status)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.upload("aggregate-too-large", b"x" * 513, "hero.png")
        self.assertEqual(caught.exception.code, 413)

    def test_multipart_upload_supports_multiple_streamed_files(self) -> None:
        boundary = "adfactory-boundary"
        parts = []
        for filename in ("one.png", "two.png"):
            parts.extend(
                [
                    f"--{boundary}\r\n".encode(),
                    (
                        f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'
                        "Content-Type: image/png\r\n\r\n"
                    ).encode(),
                    PNG,
                    b"\r\n",
                ]
            )
        body = b"".join(parts) + f"--{boundary}--\r\n".encode()
        with self.open(
            "POST",
            "/v1/assets?kind=reference_image",
            body=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Idempotency-Key": "multipart-assets",
            },
        ) as response:
            items = json.loads(response.read())["items"]
        self.assertEqual([item["filename"] for item in items], ["one.png", "two.png"])
        self.assertEqual(len({item["resource_id"] for item in items}), 2)

    def test_documents_and_configs_use_etags_and_conflict_on_stale_write(self) -> None:
        for collection, scope in (("documents", "documents:write"), ("configs", "documents:write")):
            with self.open(
                "PUT",
                f"/v1/{collection}/main",
                payload={"content": "version one", "operation_id": f"{collection}-1", "expected_version": 0},
            ) as response:
                first = json.loads(response.read())
                self.assertEqual(response.headers["ETag"], '"1"')
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.open(
                    "PUT",
                    f"/v1/{collection}/main",
                    payload={"content": "stale", "operation_id": f"{collection}-stale", "expected_version": 0},
                )
            self.assertEqual(caught.exception.code, 409)
            with self.open("GET", f"/v1/{collection}/main/versions") as response:
                self.assertEqual(len(json.loads(response.read())["items"]), 1)
            self.assertEqual(first["version"], 1)

    def test_run_prompt_generation_output_lifecycle_and_download_contracts(self) -> None:
        with self.open(
            "POST",
            "/v1/runs",
            payload={
                "run_id": "run-1",
                "workspace_id": "workspace-1",
                "run_number": 1,
                "flow_type": "structured",
                "operation_id": "run-create",
            },
        ) as response:
            self.assertEqual(response.status, 201)
        with self.open(
            "PUT",
            "/v1/prompts/prompt-1",
            payload={"run_id": "run-1", "content": "make an ad", "operation_id": "prompt-1"},
        ):
            pass
        with self.open("GET", "/v1/runs/run-1/prompts") as response:
            prompts = json.loads(response.read())["items"]
        self.assertEqual(prompts[0]["prompt_id"], "prompt-1")
        self.assertNotIn("make an ad", json.dumps(prompts))
        with self.open("GET", "/v1/prompts/prompt-1/content") as response:
            self.assertEqual(response.read(), b"make an ad")
        with self.open(
            "POST",
            "/v1/runs/run-1/execute",
            payload={"command": "assemble_prompts", "operation_id": "execute-1"},
        ) as response:
            queued = json.loads(response.read())
        self.assertEqual(queued["status"], "queued")
        with self.open(
            "POST",
            "/v1/runs/run-1/generations",
            payload={"engine": "chatgpt", "mode": "45", "operation_id": "generation-1"},
        ) as response:
            self.assertEqual(json.loads(response.read())["status"], "queued")
        with self.open("GET", "/v1/runs/run-1/download") as response:
            archive = zipfile.ZipFile(io.BytesIO(response.read()))
        self.assertIn("prompts/prompt-1.txt", archive.namelist())
        self.assertEqual(archive.read("prompts/prompt-1.txt"), b"make an ad")
        with self.open(
            "DELETE",
            "/v1/prompts/prompt-1",
            headers={"Idempotency-Key": "delete-prompt-1"},
        ) as response:
            self.assertEqual(json.loads(response.read())["status"], "deleted")
        with self.open(
            "DELETE",
            "/v1/prompts/prompt-1",
            headers={"Idempotency-Key": "delete-prompt-2"},
        ) as response:
            self.assertEqual(json.loads(response.read())["status"], "already_deleted")
        prompt_events = [
            event
            for event in self.server.state.pending_outbox()
            if event["event_type"] == "prompt_deleted"
        ]
        self.assertEqual(len(prompt_events), 1)
        self.assertEqual(prompt_events[0]["payload"]["run_id"], "run-1")
        self.assertEqual(prompt_events[0]["payload"]["prompt_id"], "prompt-1")

    def test_changes_and_events_resume_after_sequence(self) -> None:
        before = self.server.state.change_sequence()
        with self.upload(operation_id="change-upload"):
            pass
        with self.open("GET", f"/v1/changes?after={before}&limit=10") as response:
            changes = json.loads(response.read())["items"]
        self.assertEqual(len(changes), 1)
        sequence = changes[0]["sequence"]
        with self.open("GET", f"/v1/events?after={before}") as response:
            body = response.read().decode()
        self.assertIn(f"id: {sequence}", body)
        self.assertNotIn(str(Path(self.temp.name)), body)

    def test_prompt_import_export_and_run_contracts(self) -> None:
        with self.open(
            "POST",
            "/v1/runs",
            payload={
                "run_id": "run-import",
                "workspace_id": "workspace-import",
                "run_number": 2,
                "flow_type": "structured",
                "operation_id": "run-import-create",
            },
        ):
            pass
        with self.open(
            "POST",
            "/v1/runs/run-import/prompt-imports",
            payload={
                "operation_id": "prompt-import",
                "items": [{"prompt_id": "imported-1", "content": "imported prompt"}],
            },
        ) as response:
            self.assertEqual(json.loads(response.read())["items"][0]["prompt_id"], "imported-1")
        with self.open("GET", "/v1/runs/run-import/manifest") as response:
            manifest = json.loads(response.read())
        self.assertNotIn("owner_key", manifest)
        self.assertNotIn("metadata_json", json.dumps(manifest))
        with self.open("GET", "/v1/runs/run-import/prompt-export") as response:
            archive = zipfile.ZipFile(io.BytesIO(response.read()))
        self.assertEqual(archive.read("prompts/imported-1.txt"), b"imported prompt")
        with self.open("GET", "/v1/runs") as response:
            self.assertIn("run-import", [item["run_id"] for item in json.loads(response.read())["items"]])
        with self.open("GET", "/v1/runs/run-import") as response:
            self.assertEqual(json.loads(response.read())["run_id"], "run-import")
        with self.open(
            "DELETE",
            "/v1/runs/run-import",
            headers={"Idempotency-Key": "run-import-delete"},
        ) as response:
            self.assertEqual(json.loads(response.read())["status"], "deleted")

    def test_output_content_versions_and_lifecycle_contracts(self) -> None:
        state = self.server.state
        state.create_run(
            run_id="run-output",
            owner_key="user-1",
            device_id=self.server.data_plane.device_id,
            workspace_id="workspace-output",
            run_number=3,
            flow_type="structured",
            operation_id="seed-run-output",
        )
        source = self.server.config.paths.staging / "output.png"
        source.write_bytes(PNG)
        resource = state.put_resource(
            source=source,
            owner_key="user-1",
            kind="output_image",
            logical_key="outputs/one",
            operation_id="seed-output-resource",
            media_type="image/png",
        )
        state.create_output(
            output_id="output-1",
            run_id="run-output",
            prompt_id="prompt-1",
            item_id="item-1",
            aspect_ratio="4:5",
            resource_id=resource.resource_id,
            resource_version=resource.version,
            operation_id="seed-output",
        )
        with self.open("GET", "/v1/outputs/output-1") as response:
            self.assertEqual(json.loads(response.read())["current_version"], 1)
        with self.open("GET", "/v1/outputs/output-1/content") as response:
            self.assertEqual(response.read(), PNG)
        with self.open("GET", "/v1/outputs/output-1/versions") as response:
            self.assertEqual(len(json.loads(response.read())["items"]), 1)
        for action, status in (("archive", "archived"), ("restore", "available")):
            with self.open(
                "POST",
                f"/v1/outputs/output-1/{action}",
                payload={"operation_id": f"output-{action}"},
            ) as response:
                self.assertEqual(json.loads(response.read())["status"], status)
        for action in ("replacements", "revisions"):
            with self.open(
                "POST",
                f"/v1/outputs/output-1/{action}",
                payload={
                    "operation_id": f"output-{action}",
                    **({"comment": "Increase headline contrast"} if action == "revisions" else {}),
                },
            ) as response:
                self.assertEqual(response.status, 202)
                self.assertEqual(json.loads(response.read())["status"], "queued")
        with state._connect() as conn:
            revision_job = conn.execute(
                "SELECT payload_json FROM jobs WHERE job_id LIKE 'rev_%'"
            ).fetchone()[0]
        self.assertNotIn("Increase headline contrast", revision_job)
        with self.open(
            "POST",
            "/v1/outputs/output-1/versions/1/activate",
            payload={"operation_id": "output-activate"},
        ) as response:
            self.assertEqual(json.loads(response.read())["version"], 1)
        with self.open(
            "DELETE",
            "/v1/outputs/output-1",
            headers={"Idempotency-Key": "output-delete"},
        ) as response:
            self.assertEqual(json.loads(response.read())["status"], "deleted")


if __name__ == "__main__":
    unittest.main()
