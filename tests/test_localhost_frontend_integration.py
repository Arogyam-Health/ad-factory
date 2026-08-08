from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LocalhostFrontendPairingTests(unittest.TestCase):
    def test_dashboard_pairing_client_wires_complete_challenge_exchange(self) -> None:
        source = (ROOT / "dashboard/frontend/js/local-data-plane.js").read_text(
            encoding="utf-8"
        )
        for fragment in (
            "/v1/info",
            "/v1/pairing/challenges",
            "/api/agents/pairing/challenges",
            "/v1/pairing/sessions",
            "sessionStorage",
            "credentials: \"same-origin\"",
        ):
            self.assertIn(fragment, source)
        render_submission = source[
            source.index("/api/agents/pairing/challenges") :
        ]
        self.assertNotIn("localBaseUrl", render_submission[:1200])
        self.assertNotIn("localhostUrl", render_submission[:1200])

    def test_pairing_client_is_loaded_as_a_module(self) -> None:
        html = (ROOT / "dashboard/frontend/index.html").read_text(encoding="utf-8")
        self.assertIn(
            '<script type="module" src="/js/local-data-plane.js"></script>', html
        )


if __name__ == "__main__":
    unittest.main()
