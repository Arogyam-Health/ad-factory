from __future__ import annotations

import hashlib
import json
from typing import Any

from cryptography.fernet import InvalidToken

from dashboard.backend.security.crypto import decrypt_value, encrypt_value


MAX_PROMPT_BUNDLE_BYTES = 8 * 1024 * 1024


def _encoded_bundle(bundle: dict[str, Any]) -> bytes:
    if not isinstance(bundle, dict):
        raise ValueError("Prompt delivery bundle must be an object")
    encoded = json.dumps(
        bundle,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if not encoded or len(encoded) > MAX_PROMPT_BUNDLE_BYTES:
        raise ValueError("Prompt delivery bundle exceeds the 8 MiB limit")
    return encoded


def encrypt_prompt_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    encoded = _encoded_bundle(bundle)
    return {
        "ciphertext": encrypt_value(encoded.decode("utf-8")),
        "plaintext_sha256": hashlib.sha256(encoded).hexdigest(),
        "plaintext_bytes": len(encoded),
    }


def decrypt_prompt_bundle(encrypted: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(encrypted, dict):
        raise ValueError("Encrypted prompt delivery must be an object")
    ciphertext = str(encrypted.get("ciphertext") or "")
    expected_sha256 = str(encrypted.get("plaintext_sha256") or "")
    try:
        plaintext = decrypt_value(ciphertext)
    except (InvalidToken, ValueError, TypeError) as exc:
        raise ValueError("Prompt delivery could not be decrypted") from exc
    encoded = plaintext.encode("utf-8")
    if hashlib.sha256(encoded).hexdigest() != expected_sha256:
        raise ValueError("Prompt delivery integrity check failed")
    try:
        bundle = json.loads(plaintext)
    except json.JSONDecodeError as exc:
        raise ValueError("Prompt delivery payload is invalid") from exc
    if not isinstance(bundle, dict):
        raise ValueError("Prompt delivery payload must be an object")
    return bundle
