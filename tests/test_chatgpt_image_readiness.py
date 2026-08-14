from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from playwright.sync_api import TimeoutError as PWTimeoutError

from scripts import chatgpt_web_sutomation as automation


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(float(seconds), 0.1)


class ChatGPTImageReadinessTests(unittest.TestCase):
    def test_local_browser_streams_progress_and_propagates_cdp_url(self) -> None:
        root = Path(__file__).resolve().parents[1]
        browser_source = (
            root / "local_agent_runtime/structured_browser.py"
        ).read_text(encoding="utf-8")
        agent_source = (root / "scripts/local_agent.py").read_text(encoding="utf-8")
        self.assertNotIn("stdout=subprocess.PIPE", browser_source)
        self.assertNotIn("stderr=subprocess.STDOUT", browser_source)
        self.assertIn(
            'os.environ["AGENT_CDP_URL"] = AGENT_CDP_URL',
            agent_source,
        )

    def test_upload_wait_requires_ready_composer_attachments(self) -> None:
        clock = _Clock()
        with (
            patch.object(automation.time, "time", clock.time),
            patch.object(automation.time, "sleep", clock.sleep),
            patch.object(automation, "_visible_uploaded_image_count", return_value=1),
            patch.object(automation, "_composer_attachment_count", return_value=1),
            patch.object(automation, "_composer_attachment_ready_count", return_value=0),
            patch.object(automation, "upload_activity_present", return_value=False),
            patch.object(automation, "_attachment_spinner_count", return_value=0),
            patch.object(automation, "duplicate_upload_modal_present", return_value=False),
        ):
            with self.assertRaises(PWTimeoutError):
                automation.wait_for_uploads_to_settle(
                    object(),
                    set(),
                    expected_count=1,
                    timeout=3,
                )

    def test_per_file_upload_wait_rejects_unready_attachment_preview(self) -> None:
        clock = _Clock()
        with (
            patch.object(automation.time, "time", clock.time),
            patch.object(automation.time, "sleep", clock.sleep),
            patch.object(automation, "_visible_uploaded_image_count", return_value=1),
            patch.object(automation, "_composer_attachment_count", return_value=1),
            patch.object(automation, "_composer_attachment_ready_count", return_value=0),
            patch.object(automation, "upload_activity_present", return_value=False),
            patch.object(automation, "_attachment_spinner_count", return_value=0),
            patch.object(automation, "duplicate_upload_modal_present", return_value=False),
        ):
            with self.assertRaises(PWTimeoutError):
                automation._wait_for_uploaded_count_at_least(
                    object(),
                    set(),
                    target_count=1,
                    timeout=3,
                )

    def test_duplicate_upload_modal_does_not_bypass_settle_checks(self) -> None:
        clock = _Clock()
        with (
            patch.object(automation.time, "time", clock.time),
            patch.object(automation.time, "sleep", clock.sleep),
            patch.object(automation, "_visible_uploaded_image_count", return_value=1),
            patch.object(automation, "_composer_attachment_count", return_value=1),
            patch.object(automation, "_composer_attachment_ready_count", return_value=1),
            patch.object(automation, "upload_activity_present", return_value=True),
            patch.object(automation, "_attachment_spinner_count", return_value=1),
            patch.object(automation, "duplicate_upload_modal_present", return_value=True),
            patch.object(automation, "dismiss_duplicate_upload_modal", return_value=True),
        ):
            with self.assertRaises(PWTimeoutError):
                automation.wait_for_uploads_to_settle(
                    object(),
                    set(),
                    expected_count=1,
                    timeout=3,
                )

    def test_generation_wait_rejects_incomplete_or_unavailable_preview(self) -> None:
        clock = _Clock()
        candidate = {
            "src": "https://example.invalid/preview.webp",
            "width": 512,
            "height": 512,
            "naturalWidth": 1024,
            "naturalHeight": 1024,
            "assistantArea": True,
            "complete": True,
        }
        with (
            patch.object(automation.time, "time", clock.time),
            patch.object(automation.time, "sleep", clock.sleep),
            patch.object(automation, "_image_candidates", return_value=[candidate]),
            patch.object(automation, "generation_in_progress", return_value=False),
            patch.object(
                automation,
                "_generated_image_resource_ready",
                return_value=False,
            ),
            patch.object(automation, "_download_control_available", return_value=False),
        ):
            with self.assertRaises(PWTimeoutError):
                automation.wait_for_generated_image(
                    object(),
                    set(),
                    timeout=3,
                    quiet_seconds=1,
                )

    def test_generation_wait_requires_quiet_stable_downloadable_image(self) -> None:
        clock = _Clock()
        candidate = {
            "src": "https://example.invalid/generated.webp",
            "width": 512,
            "height": 512,
            "naturalWidth": 1024,
            "naturalHeight": 1024,
            "assistantArea": True,
            "complete": True,
        }
        with (
            patch.object(automation.time, "time", clock.time),
            patch.object(automation.time, "sleep", clock.sleep),
            patch.object(automation, "_image_candidates", return_value=[candidate]),
            patch.object(
                automation,
                "generation_in_progress",
                side_effect=[True] + [False] * 20,
            ),
            patch.object(
                automation,
                "_generated_image_resource_ready",
                return_value=True,
            ),
            patch.object(automation, "_download_control_available", return_value=False),
        ):
            result = automation.wait_for_generated_image(
                object(),
                set(),
                timeout=8,
                quiet_seconds=2,
            )
        self.assertEqual(result, candidate["src"])
        self.assertGreaterEqual(clock.now, 2)

    def test_generation_timeout_never_falls_back_to_unscoped_visible_image(self) -> None:
        clock = _Clock()
        with (
            patch.object(automation.time, "time", clock.time),
            patch.object(automation.time, "sleep", clock.sleep),
            patch.object(automation, "_image_candidates", return_value=[]),
            patch.object(automation, "mark_largest_generated_image", return_value="old-image"),
        ):
            with self.assertRaises(PWTimeoutError):
                automation.wait_for_generated_image(
                    object(),
                    set(),
                    timeout=1,
                    quiet_seconds=0,
                )

    def test_default_image_wait_is_thirty_minutes(self) -> None:
        root = Path(__file__).resolve().parents[1]
        chatgpt = (root / "scripts" / "chatgpt_web_sutomation.py").read_text(encoding="utf-8")
        gemini = (root / "scripts" / "gemini_web_automation.py").read_text(encoding="utf-8")
        agent = (root / "scripts" / "local_agent.py").read_text(encoding="utf-8")
        self.assertIn("default=1800", chatgpt)
        self.assertIn("default=1800", gemini)
        self.assertIn('payload.get("timeout") or 1800', agent)
        self.assertIn("timeout=1900", agent)
        self.assertNotIn("default=420", chatgpt)
        self.assertNotIn("default=420", gemini)


if __name__ == "__main__":
    unittest.main()
