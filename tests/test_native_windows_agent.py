from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from local_agent_runtime.structured_browser import image_upload_suffix


class NativeWindowsAgentTests(unittest.TestCase):
    def test_webp_keeps_webp_suffix_when_windows_mimetypes_are_empty(self) -> None:
        with patch(
            "local_agent_runtime.structured_browser.mimetypes.guess_extension",
            return_value=None,
        ):
            self.assertEqual(
                image_upload_suffix("image/webp", Path("/cas/objects/ab.blob")),
                ".webp",
            )
            self.assertEqual(image_upload_suffix("image/jpeg", Path("x.bin")), ".jpg")
            self.assertEqual(image_upload_suffix("application/octet-stream", Path("shot.png")), ".png")

    def test_native_windows_uploads_use_original_paths(self) -> None:
        from scripts.chatgpt_web_sutomation import copy_to_windows_temp

        with TemporaryDirectory() as tmp:
            image = Path(tmp) / "product.webp"
            image.write_bytes(b"webp")
            with patch("scripts.chatgpt_web_sutomation.sys.platform", "win32"):
                paths = copy_to_windows_temp([image])
            self.assertEqual(paths, [str(image.resolve())])

    def test_device_id_is_created_when_hardlink_is_unsupported(self) -> None:
        from local_agent_runtime.data_plane import load_or_create_device_id
        from local_agent_runtime.storage import AgentPaths

        with TemporaryDirectory() as tmp:
            paths = AgentPaths(Path(tmp) / "agent")
            paths.ensure()
            with patch(
                "local_agent_runtime.data_plane.os.link",
                side_effect=OSError("hard links are not supported"),
            ):
                first = load_or_create_device_id(paths)
            second = load_or_create_device_id(paths)
            self.assertTrue(first.startswith("dev_"))
            self.assertEqual(first, second)
            self.assertTrue((paths.config / "device-id").is_file())


if __name__ == "__main__":
    unittest.main()
