from __future__ import annotations

import contextlib
import importlib
import io
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = (
    ROOT / "scripts" / "migrate_user_configs_owner_schema.py",
    ROOT / "scripts" / "push_all_configs_to_vinay.py",
    ROOT / "scripts" / "seed_vinay_config.py",
)
SCRIPT_MODULES = (
    ("scripts.migrate_user_configs_owner_schema", ("script", "--dry-run")),
    ("scripts.push_all_configs_to_vinay", ("script",)),
    ("scripts.seed_vinay_config", ("script", "--dry-run")),
)
EMBEDDED_CREDENTIAL_URI = re.compile(
    r"""mongodb(?:\+srv)?://[^/\s"'@]+:[^/\s"'@]+@""",
    re.IGNORECASE,
)


class ScriptMongoSecurityTests(unittest.TestCase):
    def test_scripts_do_not_embed_credential_bearing_mongodb_uris(self) -> None:
        for script in SCRIPTS:
            with self.subTest(script=script.name):
                source = script.read_text(encoding="utf-8")
                self.assertFalse(
                    bool(EMBEDDED_CREDENTIAL_URI.search(source)),
                    f"{script.name} contains a credential-bearing MongoDB URI",
                )

    def test_missing_mongodb_uri_fails_before_connecting(self) -> None:
        for module_name, argv in SCRIPT_MODULES:
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    mock.patch.dict(os.environ, {}, clear=True),
                    mock.patch.object(sys, "argv", list(argv)),
                    mock.patch.object(module, "MongoClient") as mongo_client,
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    result = module.main()

                self.assertEqual(result, 1)
                mongo_client.assert_not_called()
                self.assertIn("MONGODB_URI is required", stderr.getvalue())

    def test_connection_failures_do_not_log_sensitive_settings(self) -> None:
        sensitive_marker = "value-that-must-stay-private"
        for module_name, argv in SCRIPT_MODULES:
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    mock.patch.dict(
                        os.environ,
                        {
                            "MONGODB_URI": sensitive_marker,
                            "MONGODB_DB_NAME": "ad_factory_test",
                        },
                        clear=True,
                    ),
                    mock.patch.object(sys, "argv", list(argv)),
                    mock.patch.object(
                        module,
                        "MongoClient",
                        side_effect=RuntimeError(sensitive_marker),
                    ),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    result = module.main()

                output = stdout.getvalue() + stderr.getvalue()
                self.assertEqual(result, 1)
                self.assertNotIn(sensitive_marker, output)
                self.assertIn("MongoDB operation failed", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
