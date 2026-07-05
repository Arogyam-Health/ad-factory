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
os.environ["MONGODB_URI"] = "mongodb+srv://test:test@cluster0.example.com/ad-factory-test?retryWrites=true&w=majority"
os.environ["MONGODB_DB_NAME"] = "ad_factory_test"
os.environ["GOOGLE_REDIRECT_URI"] = "https://example.com/api/auth/google/callback"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from dashboard.backend.app import app
from dashboard.backend.app import (
    _record_run_owner,
    _get_run_owner,
    _check_ownership,
    _resolve_file_owner,
    _store_output_mapping,
    _extract_run_id_from_output_path,
    _extract_run_id_from_generated_path,
    api_run_prompts,
    api_run_images,
    RUNS_ROOT,
)
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


# ─── Ownership isolation tests ──────────────────────────────────────────────


def _login_as(user_id: str) -> dict[str, str]:
    """Return cookies dict simulating a logged-in user."""
    from dashboard.backend.auth.service import create_session
    token = create_session(user_id)
    return {"session": token}


def test_ownership_isolation() -> int:
    failed = 0
    print("\n[Ownership isolation]")

    _record_run_owner("test-run-aaa", "user_A")
    _record_run_owner("test-run-bbb", "user_B")
    failed += ok(_get_run_owner("test-run-aaa") == "user_A", "_record_run_owner / _get_run_owner user_A")
    failed += ok(_get_run_owner("test-run-bbb") == "user_B", "_record_run_owner / _get_run_owner user_B")
    failed += ok(_get_run_owner("test-run-nonexistent") is None, "_get_run_owner returns None for unknown run")

    # _check_ownership: owner passes, non-owner raises
    _check_ownership("test-run-aaa", "user_A")
    failed += ok(True, "_check_ownership passes for owner")
    try:
        _check_ownership("test-run-aaa", "user_B")
        failed += ok(False, "_check_ownership should raise for non-owner")
    except Exception:
        failed += ok(True, "_check_ownership raises 403 for non-owner")

    try:
        _check_ownership("test-run-nonexistent", "user_A")
        failed += ok(False, "_check_ownership should raise for unknown run in production")
    except Exception:
        failed += ok(True, "_check_ownership raises 403 for unknown run in production")

    if db_available():
        # Create a minimal run dir and test HTTP endpoints
        run_id = "test-ownership-run"
        run_dir = RUNS_ROOT / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "manifest.json").write_text('{"test": true}', encoding="utf-8")
        (run_dir / ".owner").write_text("user_A", encoding="utf-8")
        _record_run_owner(run_id, "user_A")

        cookies_a = _login_as("user_A")
        resp = client.get(f"/api/files/download/run/{run_id}/manifest.json", cookies=cookies_a)
        failed += ok(resp.status_code == 200, "User A can download own run file")

        cookies_b = _login_as("user_B")
        resp = client.get(f"/api/files/download/run/{run_id}/manifest.json", cookies=cookies_b)
        failed += ok(resp.status_code == 403, "User B gets 403 for User A's run file")

        resp = client.get("/api/files/download/run/nonexistent-run-id/manifest.json", cookies=cookies_a)
        failed += ok(resp.status_code == 404, "Unknown run returns 404")

        orphan_run_id = "test-orphan-run"
        orphan_dir = RUNS_ROOT / orphan_run_id
        orphan_dir.mkdir(parents=True, exist_ok=True)
        (orphan_dir / "manifest.json").write_text('{"orphan": true}', encoding="utf-8")
        resp = client.get(f"/api/files/download/run/{orphan_run_id}/manifest.json", cookies=cookies_a)
        failed += ok(resp.status_code == 403, "Orphan run returns 403 in production (no owner)")

        img_run_id = "run_test_owned"
        _record_run_owner(img_run_id, "user_A")
        resp = client.get(f"/api/files/download/generated/{img_run_id}/some/img.png", cookies=cookies_b)
        failed += ok(resp.status_code == 403, "User B gets 403 for User A's generated image")

        import shutil
        shutil.rmtree(run_dir, ignore_errors=True)
        shutil.rmtree(orphan_dir, ignore_errors=True)
    else:
        print("  SKIP HTTP ownership tests (MongoDB not available)")
        failed += ok(True, "HTTP ownership tests skipped")

    return failed


# ─── File mapping / output path ownership tests ────────────────────────────


def test_file_mapping() -> int:
    failed = 0
    print("\n[File mapping]")

    from dashboard.backend.db.settings import settings as app_settings

    if not db_available():
        print("  SKIP (MongoDB not available)")
        return failed

    run_id = "test-file-map-run"
    user_id = "user_map_test"
    batch = "v999"
    manifest = {
        "prompt_files": ["output/v999/45/TEST_slug_EN_pain_point.txt"],
        "image_files": ["generated_images/v999/4_5/generated images/test_img.png"],
        "results": [{"format": "TEST"}],
    }

    # Without stored mapping, lookup returns None
    owner = _resolve_file_owner("output/v999/45/TEST_slug_EN_pain_point.txt")
    failed += ok(owner is None, "_resolve_file_owner returns None before mapping is stored")

    # Store mapping
    _store_output_mapping(run_id, user_id, batch, manifest)

    # Now lookup should find the owner
    owner = _resolve_file_owner("output/v999/45/TEST_slug_EN_pain_point.txt")
    failed += ok(owner is not None and owner["run_id"] == run_id and owner["user_id"] == user_id, "Stored prompt file resolves to correct owner")

    owner = _resolve_file_owner("generated_images/v999/4_5/generated images/test_img.png")
    failed += ok(owner is not None and owner["user_id"] == user_id, "Stored image file resolves to correct owner")

    # User B should get a different result for their run
    _store_output_mapping("run-b-999", "user_B", "v999", {"prompt_files": ["output/v999/45/BA_other_EN_pain.txt"], "image_files": ["generated_images/v999/4_5/other.png"]})
    owner_b = _resolve_file_owner("output/v999/45/BA_other_EN_pain.txt")
    failed += ok(owner_b is not None and owner_b["user_id"] == "user_B", "User B files map to User B")

    # User A cannot access User B's file via _check_ownership
    owner_a = _resolve_file_owner("output/v999/45/BA_other_EN_pain.txt")
    failed += ok(owner_a is not None and owner_a["user_id"] == "user_B", "Same file still maps to User B regardless of who looks up")
    try:
        _check_ownership(owner_a["run_id"], "user_A")
        failed += ok(False, "_check_ownership should block User A from User B's run")
    except Exception:
        failed += ok(True, "_check_ownership blocks User A from User B's run")

    # _extract_run_id_from_output_path (uses MongoDB file_map)
    rid = _extract_run_id_from_output_path("output/v999/45/TEST_slug_EN_pain_point.txt")
    failed += ok(rid == run_id, "_extract_run_id_from_output_path resolves via file_map")

    # Generated path lookup via file_map (not path-parsing)
    rid = _extract_run_id_from_generated_path("generated_images/v999/4_5/generated images/test_img.png")
    failed += ok(rid == run_id, "_extract_run_id_from_generated_path resolves via file_map")

    # Legacy generated download returns production-guard message when authenticated
    if app_settings.is_production:
        from dashboard.backend.auth.service import create_session
        tok = create_session("user_map_test")
        resp = client.get("/api/files/download/generated/v999/4_5/generated images/test_img.png", cookies={"session": tok})
        failed += ok(resp.status_code == 403, "Legacy generated download returns 403 in production with auth")
        failed += ok("Use /api/files/download/image/{image_id}" in resp.text, "Legacy endpoint tells frontend to migrate")

    # Verify COLL_PROMPTS and COLL_IMAGES were populated
    from dashboard.backend.db.client import get_sync_db
    from dashboard.backend.db.collections import COLL_PROMPTS, COLL_IMAGES
    db = get_sync_db()
    prompt_docs = list(db[COLL_PROMPTS].find({"run_id": run_id}))
    failed += ok(len(prompt_docs) >= 1, "COLL_PROMPTS has at least 1 document after _store_output_mapping")
    if prompt_docs:
        d = prompt_docs[0]
        failed += ok(d.get("user_id") == user_id, "Prompt doc has correct user_id")
        failed += ok(d.get("run_id") == run_id, "Prompt doc has correct run_id")
        failed += ok(bool(d.get("prompt_id")), "Prompt doc has non-empty prompt_id")
        failed += ok(d.get("storage_provider") == "local", "Prompt doc has storage_provider = local")
        failed += ok(d.get("status") == "completed", "Prompt doc has status = completed")

    image_docs = list(db[COLL_IMAGES].find({"run_id": run_id}))
    failed += ok(len(image_docs) >= 1, "COLL_IMAGES has at least 1 document after _store_output_mapping")
    if image_docs:
        d = image_docs[0]
        failed += ok(d.get("user_id") == user_id, "Image doc has correct user_id")
        failed += ok(d.get("run_id") == run_id, "Image doc has correct run_id")
        failed += ok(bool(d.get("image_id")), "Image doc has non-empty image_id")
        failed += ok(d.get("storage_provider") == "local", "Image doc has storage_provider = local")

    # Test list endpoints
    prompts_resp = api_run_prompts(user_id, run_id)
    failed += ok(prompts_resp["total"] >= 1, "api_run_prompts returns >= 1 prompt")
    if prompts_resp["prompts"]:
        p = prompts_resp["prompts"][0]
        failed += ok("prompt_id" in p, "api_run_prompts returns prompt_id")
        failed += ok("content" not in p, "api_run_prompts excludes content body from list")

    images_resp = api_run_images(user_id, run_id)
    failed += ok(images_resp["total"] >= 1, "api_run_images returns >= 1 image")
    if images_resp["images"]:
        im = images_resp["images"][0]
        failed += ok("image_id" in im, "api_run_images returns image_id")
        failed += ok("file_path" in im, "api_run_images returns file_path")

    # User B listing User A's run should return 0 results
    resp_b = api_run_prompts("user_B", run_id)
    failed += ok(resp_b["total"] == 0, "api_run_prompts filters by user_id (User B sees 0)")

    # Clean up
    try:
        from dashboard.backend.db.collections import COLL_FILE_MAP
        db[COLL_FILE_MAP].delete_many({"run_id": {"$in": [run_id, "run-b-999"]}})
        db[COLL_PROMPTS].delete_many({"run_id": run_id})
        db[COLL_IMAGES].delete_many({"run_id": run_id})
        db[COLL_PROMPTS].delete_many({"run_id": "run-b-999"})
        db[COLL_IMAGES].delete_many({"run_id": "run-b-999"})
    except Exception:
        pass

    return failed


# ─── Storage / Cloudinary tests ───────────────────────────────────────────


def test_storage_backend() -> int:
    failed = 0
    print("\n[Storage backend]")

    from dashboard.backend.services.storage.service import reset_storage_backend, get_storage_backend, image_metadata_for_db
    from dashboard.backend.services.storage.local import LocalStorageBackend

    reset_storage_backend()
    backend = get_storage_backend()
    failed += ok(isinstance(backend, LocalStorageBackend), "Default backend is LocalStorageBackend")
    failed += ok(backend.provider_name == "local", "Local backend provider_name is 'local'")

    # image_metadata_for_db returns local provider with no cloudinary creds
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.write(b"fake-png-data")
    tmp.close()
    tmp_path = Path(tmp.name)

    doc = image_metadata_for_db(tmp_path, run_id="test-run", user_id="test-user", batch="v999")
    failed += ok(doc["storage_provider"] == "local", "image_metadata_for_db returns local provider")
    failed += ok(doc["run_id"] == "test-run", "Returns correct run_id")
    failed += ok(doc["user_id"] == "test-user", "Returns correct user_id")
    failed += ok(doc["batch"] == "v999", "Returns correct batch")
    failed += ok(bool(doc.get("image_id")), "Returns image_id")
    failed += ok(doc.get("format") == "png", f"Returns format ('png'): {doc.get('format')}")
    failed += ok(doc.get("bytes", 0) > 0, "Returns bytes > 0")
    failed += ok(doc.get("filename") == tmp_path.name, "Returns filename")

    # image download endpoint requires auth
    resp = client.get(f"/api/files/download/image/{doc['image_id']}")
    failed += ok(resp.status_code == 401, "Download image without auth returns 401")

    tmp_path.unlink()
    return failed


def test_cloudinary_upload() -> int:
    failed = 0
    print("\n[Cloudinary upload]")

    try:
        import cloudinary  # noqa: F401
    except ImportError:
        print("  SKIP (cloudinary package not installed)")
        return failed

    from dashboard.backend.services.storage.service import reset_storage_backend

    import tempfile
    try:
        from unittest.mock import patch
    except ImportError:
        print("  SKIP (unittest.mock not available)")
        return failed

    # Set env vars so backend reports available
    import os
    os.environ["CLOUDINARY_CLOUD_NAME"] = "test-cloud"
    os.environ["CLOUDINARY_API_KEY"] = "test-key"
    os.environ["CLOUDINARY_API_SECRET"] = "test-secret"
    reset_storage_backend()

    mock_result = {
        "public_id": "ad-factory/test_image",
        "secure_url": "https://res.cloudinary.com/test-cloud/image/upload/v1/ad-factory/test_image.png",
        "width": 1024,
        "height": 768,
        "format": "png",
        "bytes": 12345,
        "etag": "abc123",
        "version": "1",
        "signature": "sig123",
        "original_filename": "test_image",
    }

    with patch("cloudinary.uploader.upload", return_value=mock_result) as mock_upload:
        from dashboard.backend.services.storage.service import image_metadata_for_db
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.write(b"fake-png-data")
        tmp.close()
        tmp_path = Path(tmp.name)

        doc = image_metadata_for_db(tmp_path, run_id="test-cld-run", user_id="test-cld-user", batch="v999")
        mock_upload.assert_called_once()
        failed += ok(True, "cloudinary.uploader.upload was called")

        failed += ok(doc["storage_provider"] == "cloudinary", f"storage_provider is cloudinary: {doc['storage_provider']}")
        failed += ok(doc["secure_url"] == mock_result["secure_url"], "secure_url matches Cloudinary response")
        failed += ok(doc["cloudinary_public_id"] == mock_result["public_id"], "cloudinary_public_id matches")
        failed += ok(doc["width"] == 1024, "width matches")
        failed += ok(doc["height"] == 768, "height matches")
        failed += ok(doc["format"] == "png", "format matches")
        failed += ok(doc["bytes"] == 12345, "bytes matches")
        failed += ok("metadata" in doc, "metadata present")
        failed += ok(doc["metadata"]["etag"] == "abc123", "cloudinary metadata present")

        # Download endpoint should redirect to secure_url (requires MongoDB)
        from dashboard.backend.db.client import get_sync_db
        from dashboard.backend.db.collections import COLL_IMAGES
        from dashboard.backend.db.client import db_available

        if db_available():
            db = get_sync_db()
            db[COLL_IMAGES].update_one(
                {"image_id": doc["image_id"]},
                {"$set": doc},
                upsert=True,
            )

            from dashboard.backend.auth.service import create_session
            tok = create_session("test-cld-user")
            resp = client.get(f"/api/files/download/image/{doc['image_id']}", cookies={"session": tok})
            failed += ok(resp.status_code in (200, 307, 302), f"Download returns redirect or success: {resp.status_code}")
            if resp.status_code in (302, 307):
                failed += ok(mock_result["secure_url"] in str(resp.headers.get("location", "")), "Redirects to Cloudinary secure_url")

            # Wrong user gets 403
            tok_b = create_session("user_B")
            resp_b = client.get(f"/api/files/download/image/{doc['image_id']}", cookies={"session": tok_b})
            failed += ok(resp_b.status_code == 403, "Wrong user gets 403 for cloudinary image")

            db[COLL_IMAGES].delete_many({"run_id": "test-cld-run"})
        else:
            from dashboard.backend.auth.service import create_session
            resp = client.get(f"/api/files/download/image/{doc['image_id']}")
            failed += ok(resp.status_code == 401, "Download image without auth returns 401 (not found)")
            print("  SKIP download/ownership HTTP tests (MongoDB not available)")

        tmp_path.unlink()

    # Clean env
    del os.environ["CLOUDINARY_CLOUD_NAME"]
    del os.environ["CLOUDINARY_API_KEY"]
    del os.environ["CLOUDINARY_API_SECRET"]
    reset_storage_backend()

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


# ─── Config system tests ────────────────────────────────────────────────────


def test_config_system() -> int:
    failed = 0
    print("\n[Config system]")

    from dashboard.backend.services.user_config import (
        CONFIG_KEYS,
        get_generic_config,
        get_user_config,
        set_user_config,
        delete_user_config,
        has_custom_config,
        get_config_doc,
        _extract_flat_from_new_schema,
        _normalize_doc_to_new_schema,
        _EMPTY_BY_KEY,
    )

    # 1. CONFIG_KEYS has exactly the 8 keys
    expected_keys = [
        "product_master_doc", "starting_prompt", "copy_prompt_templates",
        "persona_seeds", "copy_architecture", "background_variant",
        "prompt_assembler_templates", "conversion_916_prompt",
    ]
    failed += ok(sorted(CONFIG_KEYS) == sorted(expected_keys), "CONFIG_KEYS has exactly 8 expected keys")

    # 2. get_generic_config returns all 8 keys
    generic = get_generic_config()
    failed += ok(sorted(generic.keys()) == sorted(expected_keys), "get_generic_config returns all 8 keys")
    failed += ok(all(isinstance(generic[k], str) for k in CONFIG_KEYS), "All generic config values are strings")

    # 3. get_user_config returns generic when no custom config exists
    test_user = generate_user_id()
    config = get_user_config(test_user)
    failed += ok(config == generic, "get_user_config returns generic when no custom config")

    # 4. set_user_config writes new owner schema
    if db_available():
        custom_config = {"product_master_doc": "Custom product doc for test", "starting_prompt": "Custom starting prompt"}
        result = set_user_config(test_user, custom_config)
        failed += ok(result["product_master_doc"] == "Custom product doc for test", "set_user_config stores product_master_doc")
        failed += ok(result["starting_prompt"] == "Custom starting prompt", "set_user_config stores starting_prompt")
        # Other keys should fall back to generic
        failed += ok(result["copy_prompt_templates"] == generic["copy_prompt_templates"], "Missing keys fall back to generic")

        # 5. set_user_config returns merged effective config
        failed += ok(len(result) == 8, "set_user_config returns all 8 keys (merged)")

        # 6. Missing custom keys fallback to generic
        partial_config = {"copy_architecture": '{"test": true}'}
        result2 = set_user_config(test_user, partial_config)
        failed += ok(result2["copy_architecture"] == '{"test": true}', "Updated key present")
        failed += ok(result2["product_master_doc"] == "Custom product doc for test", "Previously set key preserved")
        failed += ok(result2["copy_prompt_templates"] == generic["copy_prompt_templates"], "Unset key still falls back to generic")

        # 7. has_custom_config works with new schema
        failed += ok(has_custom_config(test_user), "has_custom_config returns True after set")
        test_user_no_config = generate_user_id()
        failed += ok(not has_custom_config(test_user_no_config), "has_custom_config returns False for new user")

        # Verify owner schema in DB
        doc = get_config_doc("user", test_user)
        failed += ok(doc is not None, "get_config_doc finds active config")
        if doc:
            failed += ok(doc["owner_type"] == "user", "owner_type is 'user'")
            failed += ok(doc["owner_id"] == test_user, "owner_id matches user_id")
            failed += ok(doc["config_scope"] == "personal", "config_scope is 'personal'")
            failed += ok(doc["config_mode"] == "full", "config_mode is 'full'")
            failed += ok(doc["source"] == "manual", "source is 'manual'")
            failed += ok(doc["is_active"] is True, "is_active is True")
            failed += ok("files" in doc, "doc has 'files' field")
            if "files" in doc:
                failed += ok("product_master_doc" in doc["files"], "files has product_master_doc")
                failed += ok("content" in doc["files"]["product_master_doc"], "files.product_master_doc has content")
                failed += ok(doc["files"]["product_master_doc"]["content"] == "Custom product doc for test", "files.product_master_doc.content matches")

        # 8. DELETE /api/user/config soft-deletes config
        delete_user_config(test_user)
        failed += ok(not has_custom_config(test_user), "has_custom_config returns False after soft-delete")
        # Verify doc is soft-deleted (is_active=False)
        deleted_doc = get_config_doc("user", test_user)
        failed += ok(deleted_doc is None, "get_config_doc returns None after soft-delete")
        # Verify legacy-style check also returns False
        from dashboard.backend.db.client import get_sync_db
        from dashboard.backend.db.collections import COLL_USER_CONFIGS
        raw_doc = get_sync_db()[COLL_USER_CONFIGS].find_one({"owner_type": "user", "owner_id": test_user, "is_active": False})
        failed += ok(raw_doc is not None, "Soft-deleted doc exists with is_active=False")

        # 9. Old-style user_configs doc can be migrated
        old_style_doc = {
            "user_id": "usr_old_style_test",
            "product_master_doc": "old product doc",
            "starting_prompt": "old starting prompt",
            "updated_at": 12345.0,
        }
        normalized = _normalize_doc_to_new_schema(old_style_doc)
        failed += ok(normalized["owner_type"] == "user", "Migration: owner_type is 'user'")
        failed += ok(normalized["owner_id"] == "usr_old_style_test", "Migration: owner_id matches old user_id")
        failed += ok(normalized["source"] == "migration", "Migration: source is 'migration'")
        failed += ok(normalized["config_scope"] == "personal", "Migration: config_scope is 'personal'")
        failed += ok("files" in normalized, "Migration: files field present")
        if "files" in normalized:
            failed += ok(normalized["files"]["product_master_doc"]["content"] == "old product doc", "Migration: content preserved")
            failed += ok(normalized["files"]["product_master_doc"]["content_type"] == "text/plain", "Migration: content_type set")

        # Extract flat from new schema
        flat = _extract_flat_from_new_schema(normalized)
        failed += ok(flat["product_master_doc"] == "old product doc", "Extract flat works")
        failed += ok(flat["starting_prompt"] == "old starting prompt", "Extract flat preserves all keys")

    else:
        print("  SKIP DB-dependent config tests (MongoDB not available)")

    # 10. Public /api/generic-config returns generic only
    resp = client.get("/api/generic-config")
    failed += ok(resp.status_code == 200, "GET /api/generic-config returns 200")
    data = resp.json()
    failed += ok(sorted(data.keys()) == sorted(expected_keys), "/api/generic-config returns all 8 keys")
    failed += ok(data.get("copy_prompt_templates", "") != "", "/api/generic-config has non-empty copy_prompt_templates")

    # 11. Public /api/generic-config/{key} works for valid keys and 404s for invalid key
    resp = client.get("/api/generic-config/product_master_doc")
    failed += ok(resp.status_code == 200, "GET /api/generic-config/product_master_doc returns 200")
    data = resp.json()
    failed += ok(data.get("key") == "product_master_doc", "Response has correct key")

    resp = client.get("/api/generic-config/nonexistent_key")
    failed += ok(resp.status_code == 404, "GET /api/generic-config/nonexistent_key returns 404")

    return failed


# ─── Organization system tests ──────────────────────────────────────────────


def test_org_system() -> int:
    failed = 0
    print("\n[Organization system]")

    # 1. Module imports and key functions exist
    from dashboard.backend.services.org_helper import (
        can_user_edit_org_config,
        require_org_member,
        require_org_role,
        get_role_permissions,
        generate_org_id,
        generate_membership_id,
        is_public_email_domain,
        extract_domain_from_email,
        ORG_ROLES,
    )
    from dashboard.backend.services.invite_service import (
        generate_invite_id as gen_inv_id,
        generate_invite_token,
        hash_invite_token,
        build_invite_url,
        ALLOWED_INVITE_ROLES,
    )
    failed += ok(callable(can_user_edit_org_config), "can_user_edit_org_config is callable")
    failed += ok(callable(require_org_member), "require_org_member is callable")
    failed += ok(callable(require_org_role), "require_org_role is callable")

    # 2. ORG_ROLES has correct roles
    failed += ok("owner" in ORG_ROLES, "ORG_ROLES includes owner")
    failed += ok("config_admin" in ORG_ROLES, "ORG_ROLES includes config_admin")
    failed += ok("creator" in ORG_ROLES, "ORG_ROLES includes creator")
    failed += ok("member" not in ORG_ROLES, "ORG_ROLES does not include member")

    # 3. Role permissions
    owner_perms = get_role_permissions("owner")
    failed += ok(owner_perms.get("can_manage_org") is True, "Owner can manage org")
    failed += ok(owner_perms.get("can_edit_org_config") is True, "Owner can edit config")

    config_admin_perms = get_role_permissions("config_admin")
    failed += ok(config_admin_perms.get("can_manage_org") is False, "Config admin cannot manage org")
    failed += ok(config_admin_perms.get("can_edit_org_config") is True, "Config admin can edit config")

    creator_perms = get_role_permissions("creator")
    failed += ok(creator_perms.get("can_edit_org_config") is False, "Creator cannot edit config")
    failed += ok(creator_perms.get("can_generate_ads") is True, "Creator can generate ads")
    failed += ok(creator_perms.get("can_view_org_runs") is False, "Creator cannot view org runs")
    failed += ok(creator_perms.get("can_view_org_images") is False, "Creator cannot view org images")
    failed += ok(creator_perms.get("can_view_org_audit") is False, "Creator cannot view audit")

    # 4. Unknown role returns empty permissions
    unknown_perms = get_role_permissions("nonexistent")
    failed += ok(unknown_perms == {}, "Unknown role returns empty dict")

    # 5. Public email domain blocking
    failed += ok(is_public_email_domain("test@gmail.com"), "gmail.com is public")
    failed += ok(is_public_email_domain("test@yahoo.com"), "yahoo.com is public")
    failed += ok(is_public_email_domain("test@outlook.com"), "outlook.com is public")
    failed += ok(not is_public_email_domain("test@company.com"), "company.com is not public")
    failed += ok(not is_public_email_domain("test@arogyamhealth.in"), "arogyamhealth.in is not public")

    # 6. Email domain extraction
    failed += ok(extract_domain_from_email("test@company.com") == "company.com", "Extracts domain correctly")
    failed += ok(extract_domain_from_email("no-at-sign") == "", "Returns empty for invalid email")
    failed += ok(extract_domain_from_email("") == "", "Returns empty for empty email")

    # 7. org_id and membership_id generation
    oid = generate_org_id()
    failed += ok(oid.startswith("org_"), "org_id starts with org_")
    failed += ok(len(oid) > 10, "org_id has reasonable length")

    mid = generate_membership_id()
    failed += ok(mid.startswith("mem_"), "membership_id starts with mem_")
    failed += ok(len(mid) > 10, "membership_id has reasonable length")

    # 8. resolve_effective_config exists and is callable
    from dashboard.backend.services.user_config import resolve_effective_config
    failed += ok(callable(resolve_effective_config), "resolve_effective_config is callable")

    # 9. resolve_effective_config with no org returns user config
    test_user = generate_user_id()
    config = resolve_effective_config(test_user)
    from dashboard.backend.services.user_config import get_generic_config
    generic = get_generic_config()
    failed += ok(config == generic, "resolve_effective_config without org returns generic")
    failed += ok(len(config) == 8, "resolve_effective_config returns 8 keys")

    # 10. get_user_config is personal-only (not org-aware)
    from dashboard.backend.services.user_config import get_user_config
    personal_config = get_user_config(test_user)
    failed += ok(personal_config == generic, "get_user_config is personal-only, returns generic for new user")
    failed += ok(personal_config == config, "get_user_config matches resolve_effective_config without org")

    # 10. can_user_edit_org_config returns False for non-member
    fake_user = generate_user_id()
    fake_org = generate_org_id()
    result = can_user_edit_org_config(fake_user, fake_org)
    failed += ok(result is False, "can_user_edit_org_config returns False for non-member")

    # 11. require_org_member raises 403 for non-member
    from fastapi import HTTPException
    try:
        require_org_member(fake_user, fake_org)
        failed += ok(False, "require_org_member should raise for non-member")
    except HTTPException as e:
        failed += ok(e.status_code == 403, "require_org_member raises 403 for non-member")

    # 12. require_org_role raises 403 for non-member
    try:
        require_org_role(fake_user, fake_org, ("owner",))
        failed += ok(False, "require_org_role should raise for non-member")
    except HTTPException as e:
        failed += ok(e.status_code == 403, "require_org_role raises 403 for non-member")

    # 13. GET /api/orgs/me without session returns 401
    resp = client.get("/api/orgs/me")
    failed += ok(resp.status_code == 401, "GET /api/orgs/me without auth returns 401")

    # 14. POST /api/orgs without session returns 401
    resp = client.post("/api/orgs", json={"name": "Test Org"})
    failed += ok(resp.status_code == 401, "POST /api/orgs without auth returns 401")

    # 15. GET /api/orgs/{id} without session returns 401
    resp = client.get(f"/api/orgs/{generate_org_id()}")
    failed += ok(resp.status_code == 401, "GET /api/orgs/{id} without auth returns 401")

    # ── Invite system tests ─────────────────────────────────────────────

    # 16. invite module functions exist
    invite_id = gen_inv_id()
    failed += ok(invite_id.startswith("inv_"), "invite_id starts with inv_")
    failed += ok(len(invite_id) > 10, "invite_id has reasonable length")

    token = generate_invite_token()
    failed += ok(len(token) >= 40, "invite_token is at least 40 chars")

    h1 = hash_invite_token(token)
    h2 = hash_invite_token(token)
    failed += ok(h1 == h2, "hash_invite_token is deterministic")
    failed += ok(len(h1) == 64, "hash_invite_token produces 64-char hex")

    url = build_invite_url(token)
    failed += ok(token in url, "build_invite_url includes token")
    failed += ok(url.startswith("http"), "build_invite_url starts with http")

    # 17. ALLOWED_INVITE_ROLES
    failed += ok("creator" in ALLOWED_INVITE_ROLES, "ALLOWED_INVITE_ROLES includes creator")
    failed += ok("config_admin" in ALLOWED_INVITE_ROLES, "ALLOWED_INVITE_ROLES includes config_admin")
    failed += ok("owner" not in ALLOWED_INVITE_ROLES, "ALLOWED_INVITE_ROLES does not include owner")

    # ── Invite route tests (auth required) ──────────────────────────────

    # 18. POST /api/orgs/{id}/invites without session returns 401
    resp = client.post(f"/api/orgs/{generate_org_id()}/invites", json={"email": "test@example.com", "role": "creator"})
    failed += ok(resp.status_code == 401, "POST invite without auth returns 401")

    # 19. GET /api/orgs/{id}/invites without session returns 401
    resp = client.get(f"/api/orgs/{generate_org_id()}/invites")
    failed += ok(resp.status_code == 401, "GET invites without auth returns 401")

    # 20. DELETE /api/orgs/{id}/invites/{inv_id} without session returns 401
    resp = client.delete(f"/api/orgs/{generate_org_id()}/invites/{invite_id}")
    failed += ok(resp.status_code == 401, "DELETE invite without auth returns 401")

    # 21. GET /api/invites/{token} public endpoint
    resp = client.get(f"/api/invites/{token}")
    failed += ok(resp.status_code == 404, "GET /api/invites/{token} returns 404 for unknown token")

    # 22. POST /api/invites/{token}/accept without session returns 401
    resp = client.post(f"/api/invites/{token}/accept")
    failed += ok(resp.status_code == 401, "POST accept invite without auth returns 401")

    # ── Effective config endpoint ───────────────────────────────────────

    # 23. GET /api/config/effective without session returns 401
    resp = client.get("/api/config/effective")
    failed += ok(resp.status_code == 401, "GET /api/config/effective without auth returns 401")

    # 24. resolve_effective_config and get_config_doc exist
    from dashboard.backend.services.user_config import get_config_doc
    failed += ok(callable(get_config_doc), "get_config_doc is callable")

    # ── Email service ───────────────────────────────────────────────────
    from dashboard.backend.services.email_service import try_send_email, send_invite_email

    # 25. try_send_email with no provider returns sent=false, provider=none
    result = try_send_email("test@example.com", "Test", "Body", "Body")
    failed += ok(result.get("sent") is False, "try_send_email no provider: sent=false")
    failed += ok(result.get("provider") == "none", "try_send_email no provider: provider=none")

    # 26. send_invite_email with no provider returns sent=false
    result2 = send_invite_email("test@example.com", "Tester", "TestOrg", "creator", "http://example.com/invite/token")
    failed += ok(result2.get("sent") is False, "send_invite_email no provider: sent=false")
    failed += ok(result2.get("provider") == "none", "send_invite_email no provider: provider=none")

    if db_available():
        # 16. Org creation with public email domain (requires mocking user with public email)
        # Simulate via actual flow with a test user
        from dashboard.backend.auth.service import create_session
        from dashboard.backend.auth.models import generate_user_id as gen_uid

        test_uid = gen_uid()
        tok = create_session(test_uid)

        # We can't easily test the full flow without a real user in DB,
        # so we test the helper functions directly above.
        print("  SKIP full HTTP org flow tests (requires real user in MongoDB)")

    else:
        print("  SKIP DB-dependent org tests (MongoDB not available)")

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
    total += test_ownership_isolation()
    total += test_file_mapping()
    total += test_storage_backend()
    total += test_cloudinary_upload()
    total += test_config_system()
    total += test_org_system()

    print(f"\n{'='*50}")
    if total == 0:
        print("ALL TESTS PASSED")
    else:
        print(f"{total} TEST(S) FAILED")
    return total


if __name__ == "__main__":
    sys.exit(main())
