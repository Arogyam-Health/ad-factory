from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RevisionPromptTests(unittest.TestCase):
    def setUp(self) -> None:
        from local_agent_runtime.revision_prompt import build_output_revision_prompt

        self.build = build_output_revision_prompt
        self.templates = json.loads(
            (ROOT / "dashboard" / "backend" / "copy_system" / "prompt_assembler_templates.json").read_text(
                encoding="utf-8"
            )
        )
        self.conversion = (
            ROOT / "dashboard" / "backend" / "defaults" / "conversion_916_prompt.txt"
        ).read_text(encoding="utf-8")

    def test_45_revision_keeps_original_prompt_and_editable_safezone(self) -> None:
        prompt = self.build(
            comment="Make the CTA larger",
            aspect_ratio="4:5",
            original_prompt="Canvas: 1080 x 1350. Original 4:5 generation prompt.",
            assembler_templates=self.templates,
            conversion_916_prompt=self.conversion,
        )
        self.assertIn("Make the CTA larger", prompt)
        self.assertIn("Original 4:5 generation prompt", prompt)
        self.assertIn(self.templates["safezone_45"], prompt)
        self.assertIn("1080(length) x 1350(height)", prompt)
        self.assertNotIn(self.templates["safezone_916"], prompt)
        self.assertNotIn(self.conversion.strip(), prompt)

    def test_916_revision_uses_conversion_and_916_safezone_not_45_prompt(self) -> None:
        prompt = self.build(
            comment="Move the headline up",
            aspect_ratio="9:16",
            original_prompt="Canvas: 1080 x 1350. Original 4:5 generation prompt.",
            assembler_templates=self.templates,
            conversion_916_prompt=self.conversion,
        )
        self.assertIn("Move the headline up", prompt)
        self.assertIn(self.conversion.strip(), prompt)
        self.assertIn(self.templates["safezone_916"], prompt)
        self.assertIn("1080(length) x 1920(height)", prompt)
        self.assertNotIn("Original 4:5 generation prompt", prompt)
        self.assertNotIn(self.templates["safezone_45"], prompt)
        self.assertIn("already 9:16", prompt)


if __name__ == "__main__":
    unittest.main()
