from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EMBEDDED_CREDENTIAL_URI = re.compile(
    r"""mongodb(?:\+srv)?://[^/\s"'@]+:[^/\s"'@]+@""",
    re.IGNORECASE,
)


class ScriptMongoSecurityTests(unittest.TestCase):
    def test_scripts_do_not_embed_credential_bearing_mongodb_uris(self) -> None:
        scripts = sorted((ROOT / "scripts").glob("*.py"))
        self.assertTrue(scripts)
        for script in scripts:
            with self.subTest(script=script.name):
                source = script.read_text(encoding="utf-8")
                self.assertFalse(
                    bool(EMBEDDED_CREDENTIAL_URI.search(source)),
                    f"{script.name} contains a credential-bearing MongoDB URI",
                )


if __name__ == "__main__":
    unittest.main()
