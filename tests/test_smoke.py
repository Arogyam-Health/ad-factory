#!/usr/bin/env python3
from __future__ import annotations

"""
Smoke tests for cloud deployment layer — standalone (no pytest needed).

Tests: auth middleware, encryption, user isolation, agent tokens.
Run:  python tests/test_smoke.py

Skips tests that require MongoDB unless --db is passed.
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Force settings before importing app — must come before any project imports
os.environ["DEPLOYMENT_MODE"] = "production"
os.environ["APP_SECRET_KEY"] = "test-secret-key-not-for-production-use!"
os.environ["ENCRYPTION_KEY"] = "test-encryption-key-32-char-minimum!!"
os.environ["GOOGLE_CLIENT_ID"] = "test-client-id"
os.environ["GOOGLE_CLIENT_SECRET"] = "test-client-secret"
os.environ["CORS_ORIGINS"] = "http://localhost:4090"
os.environ["MONGODB_URI"] = "mongodb://localhost:27017/ad-factory-test"
os.environ["MONGODB_DB_NAME"] = "ad_factory_test"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from dashboard.backend.app import app
from dashboard.backend.db.settings import settings, DEPLOYMENT_PROD, validate_production_settings
from dashboard.backend.security.crypto import encrypt_value, decrypt_value, hash_token, mask_key, generate_token
from dashboard.backend.auth.models import generate_user_id, generate_session_token
from dashboard.backend.agent.service import register_agent, authenticate_agent


client = TestClient(app)


# ─── Helpers ────────────────────────────────────────────────────────────────


def db_available() -> bool:
    try:
        from dashboard.backend.db.client import ping
        return ping()
    except Exception:
        return False


def ok(condition: bool, message: str) -> int:
    """Assert a condition. Returns 0 for pass, 1 for fail."""
    if condition:
        print(f"  OK  {message}")
        return 0
    print(f"  FAIL {message}")
    return 1


# ─── Auth middleware tests ──────────────────────────────────────────────────


def test_auth_middleware() -> int:
    failed = 0
    print("\n[Auth middleware]")

    resp = client.get("/api/runs")
    failed += ok(resp.status_code == 401, "GET /api/runs without session returns 401")

    resp = client.get("/api/defaults")
    failed += ok(resp.status_code == 401, "GET /api/defaults without session returns 401")

    resp = client.get("/api/runs/test-id/export-on-image-copy")
    failed += ok(resp.status_code == 401, "GET /api/runs/.../export-on-image-copy without session returns 401")

    resp = client.get("/api/auth/status")
    failed += ok(resp.status_code == 200, "GET /api/auth/status without session returns 200 (public)")

    return failed


# ─── Encryption tests ──────────────────────────────────────────────────────


def test_encryption() -> int:
    failed = 0
    print("\n[Encryption]")

    original = "sk-test-api-key-12345"
    encrypted = encrypt_value(original)
    decrypted = decrypt_value(encrypted)
    failed += ok(encrypted != original, "encrypt_value produces different output")
    failed += ok(decrypted == original, "decrypt_value reverses encryption")

    failed += ok(encrypt_value("") == "", "encrypt_value empty string")
    failed += ok(decrypt_value("") == "", "decrypt_value empty string")

    failed += ok(mask_key("") == "", "mask_key empty string")
    failed += ok(mask_key("abc") == "****", "mask_key short string")
    result = mask_key("sk-test-api-key-12345")
    failed += ok(result == "sk-t****2345", f"mask_key long string: {result}")

    t1 = "test-token-abc"
    h1 = hash_token(t1)
    failed += ok(len(h1) == 64, "hash_token produces 64-char hex")
    failed += ok(h1 == hash_token(t1), "hash_token is deterministic")
    failed += ok(h1 != hash_token("test-token-xyz"), "hash_token different for different inputs")

    t = generate_token(32)
    failed += ok(len(t) > 32, "generate_token produces token > 32 chars")
    failed += ok(isinstance(t, str), "generate_token returns string")

    return failed


# ─── Agent lifecycle tests ──────────────────────────────────────────────────


def test_agent_lifecycle() -> int:
    failed = 0
    print("\n[Agent lifecycle]")

    if not db_available():
        print("  SKIP (MongoDB not available)")
        return failed

    result = register_agent("test-user", "test-agent", "test description")
    failed += ok("agent_id" in result, "register_agent returns agent_id")
    failed += ok("token" in result, "register_agent returns token")
    failed += ok(result["name"] == "test-agent", "register_agent returns correct name")

    agent = authenticate_agent(result["token"])
    failed += ok(agent is not None, "authenticate_agent returns agent for valid token")
    if agent:
        failed += ok(agent["agent_id"] == result["agent_id"], "authenticate_agent returns correct agent")

    bad = authenticate_agent("nonexistent-token")
    failed += ok(bad is None, "authenticate_agent returns None for bad token")

    return failed


# ─── User isolation tests ──────────────────────────────────────────────────


def test_user_isolation() -> int:
    failed = 0
    print("\n[User isolation]")

    u1 = generate_user_id()
    u2 = generate_user_id()
    failed += ok(u1 != u2, "generate_user_id produces unique IDs")
    failed += ok(u1.startswith("usr_"), "generate_user_id starts with usr_")

    s1 = generate_session_token()
    s2 = generate_session_token()
    failed += ok(s1 != s2, "generate_session_token produces unique tokens")

    return failed


# ─── Settings validation tests ─────────────────────────────────────────────


def test_settings_validation() -> int:
    failed = 0
    print("\n[Settings validation]")

    failed += ok(settings.is_production, "settings.is_production is True in test mode")

    old_key = settings.app_secret_key
    old_enc = settings.encryption_key
    try:
        settings.app_secret_key = "change-me-in-production"
        settings.encryption_key = "change-me"
        try:
            validate_production_settings()
            failed += ok(False, "validate_production_settings should exit with bad defaults")
        except SystemExit:
            failed += ok(True, "validate_production_settings exits with bad defaults")
    finally:
        settings.app_secret_key = old_key
        settings.encryption_key = old_enc

    return failed


# ─── Provider config isolation tests ───────────────────────────────────────


def test_provider_config_isolation() -> int:
    failed = 0
    print("\n[Provider config isolation]")

    resp = client.get("/api/user/provider-config/opencode")
    failed += ok(resp.status_code == 401, "GET provider-config without session returns 401")

    resp = client.put("/api/user/provider-config/opencode", json={"config": {}})
    failed += ok(resp.status_code == 401, "PUT provider-config without session returns 401")

    resp = client.delete("/api/user/provider-config/opencode")
    failed += ok(resp.status_code == 401, "DELETE provider-config without session returns 401")

    return failed


# ─── main ──────────────────────────────────────────────────────────────────


def main() -> int:
    print(f"Python: {sys.version}")
    print(f"Settings mode: {settings.deployment_mode}")
    print(f"Is production: {settings.is_production}")
    print(f"MongoDB available: {db_available()}")

    total = 0
    total += test_auth_middleware()
    total += test_encryption()
    total += test_agent_lifecycle()
    total += test_user_isolation()
    total += test_settings_validation()
    total += test_provider_config_isolation()

    print(f"\n{'='*50}")
    if total == 0:
        print("ALL TESTS PASSED")
    else:
        print(f"{total} TEST(S) FAILED")
    return total


if __name__ == "__main__":
    sys.exit(main())
