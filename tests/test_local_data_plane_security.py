from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path


class LocalDataPlaneSecurityTests(unittest.TestCase):
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
                session_ttl_seconds=60,
                challenge_ttl_seconds=60,
            )
        )
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        self.temp.cleanup()

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        token: str = "",
        origin: str | None = ORIGIN,
        headers: dict[str, str] | None = None,
    ):
        body = None if payload is None else json.dumps(payload).encode()
        request_headers = dict(headers or {})
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
        if origin is not None:
            request_headers["Origin"] = origin
        if token:
            request_headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            self.server.url + path,
            data=body,
            headers=request_headers,
            method=method,
        )
        return urllib.request.urlopen(request, timeout=3)

    def pair(self, scopes: list[str]) -> str:
        with self.request("POST", "/v1/pairing/challenges", payload={}) as response:
            challenge = json.loads(response.read())
        self.server.approve_pairing_challenge(
            challenge["challenge_id"],
            challenge["challenge"],
            owner_key="user-1",
            scopes=scopes,
        )
        with self.request(
            "POST",
            "/v1/pairing/sessions",
            payload={
                "challenge_id": challenge["challenge_id"],
                "challenge": challenge["challenge"],
            },
        ) as response:
            return json.loads(response.read())["access_token"]

    def test_info_is_public_and_contains_no_local_or_owner_data(self) -> None:
        with self.request("GET", "/v1/info") as response:
            payload = json.loads(response.read())
        serialized = json.dumps(payload).lower()
        self.assertEqual(payload["protocol_versions"], ["v1"])
        self.assertIn("device_id", payload)
        for forbidden in (str(Path(self.temp.name)), "owner", "secret", "token", "pid", "content"):
            self.assertNotIn(forbidden.lower(), serialized)

    def test_exact_origin_null_origin_and_non_loopback_host_are_rejected(self) -> None:
        for origin in ("https://attacker.example", "null"):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request("GET", "/v1/info", origin=origin)
            self.assertEqual(caught.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("GET", "/v1/info", headers={"Host": "example.com"})
        self.assertEqual(caught.exception.code, 421)

    def test_private_network_preflight_is_exact_and_has_required_headers(self) -> None:
        with self.request(
            "OPTIONS",
            "/v1/assets",
            headers={
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization, content-type, idempotency-key",
                "Access-Control-Request-Private-Network": "true",
            },
        ) as response:
            self.assertEqual(response.status, 204)
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], self.ORIGIN)
            self.assertEqual(response.headers["Access-Control-Allow-Private-Network"], "true")
            self.assertIn("Authorization", response.headers["Access-Control-Allow-Headers"])
        with self.assertRaises(urllib.error.HTTPError):
            self.request(
                "OPTIONS",
                "/v1/assets",
                origin="null",
                headers={"Access-Control-Request-Private-Network": "true"},
            )

    def test_bearer_scope_enforcement_and_revocation(self) -> None:
        read_token = self.pair(["manifest:read"])
        with self.request("GET", "/v1/assets", token=read_token) as response:
            self.assertEqual(response.status, 200)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(
                "PUT",
                "/v1/documents/brief",
                payload={"content": "hello", "operation_id": "op-1", "expected_version": 0},
                token=read_token,
            )
        self.assertEqual(caught.exception.code, 403)
        with self.request("DELETE", "/v1/pairing/sessions/current", token=read_token) as response:
            self.assertEqual(response.status, 204)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("GET", "/v1/assets", token=read_token)
        self.assertEqual(caught.exception.code, 401)

    def test_pairing_challenge_is_one_time_and_expired_challenges_fail(self) -> None:
        with self.request("POST", "/v1/pairing/challenges", payload={}) as response:
            challenge = json.loads(response.read())
        self.server.approve_pairing_challenge(
            challenge["challenge_id"],
            challenge["challenge"],
            owner_key="user-1",
            scopes=["manifest:read"],
        )
        payload = {"challenge_id": challenge["challenge_id"], "challenge": challenge["challenge"]}
        with self.request("POST", "/v1/pairing/sessions", payload=payload):
            pass
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("POST", "/v1/pairing/sessions", payload=payload)
        self.assertEqual(caught.exception.code, 401)

        with self.request("POST", "/v1/pairing/challenges", payload={}) as response:
            expired = json.loads(response.read())
        self.server.approve_pairing_challenge(
            expired["challenge_id"],
            expired["challenge"],
            owner_key="user-1",
            scopes=["manifest:read"],
        )
        self.server.data_plane._challenges[expired["challenge_id"]].expires_at = 0
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(
                "POST",
                "/v1/pairing/sessions",
                payload={
                    "challenge_id": expired["challenge_id"],
                    "challenge": expired["challenge"],
                },
            )
        self.assertEqual(caught.exception.code, 401)

    def test_expired_bearer_session_is_rejected(self) -> None:
        token = self.pair(["manifest:read"])
        digest = self.server.data_plane._digest(token)
        self.server.data_plane._sessions[digest].expires_at = 0
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("GET", "/v1/assets", token=token)
        self.assertEqual(caught.exception.code, 401)

    def test_cross_owner_resources_are_hidden(self) -> None:
        first = self.pair(["manifest:read", "documents:write"])
        second = self.pair(["manifest:read"])
        with self.request(
            "PUT",
            "/v1/documents/private",
            payload={"content": "private", "operation_id": "owner-one", "expected_version": 0},
            token=first,
        ):
            pass
        # Pairing helper deliberately uses a second owner for this assertion.
        with self.request("POST", "/v1/pairing/challenges", payload={}) as response:
            challenge = json.loads(response.read())
        self.server.approve_pairing_challenge(
            challenge["challenge_id"],
            challenge["challenge"],
            owner_key="user-2",
            scopes=["manifest:read", "content:read"],
        )
        with self.request(
            "POST",
            "/v1/pairing/sessions",
            payload={"challenge_id": challenge["challenge_id"], "challenge": challenge["challenge"]},
        ) as response:
            second = json.loads(response.read())["access_token"]
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("GET", "/v1/documents/private", token=second)
        self.assertEqual(caught.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
