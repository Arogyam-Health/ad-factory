from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from dashboard.backend.db.settings import settings


def _derive_fernet_key(key_bytes: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"ad-factory-encryption-salt",
        iterations=600000,
    )
    return base64.urlsafe_b64encode(kdf.derive(key_bytes))


def _get_fernet() -> Fernet:
    return Fernet(_derive_fernet_key(settings.encryption_key_bytes))


def encrypt_value(plaintext: str) -> str:
    if not plaintext:
        return ""
    f = _get_fernet()
    return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_value(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    f = _get_fernet()
    return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return key[:4] + "****" + key[-4:]


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_token(length: int = 48) -> str:
    return secrets.token_urlsafe(length)


def sign_session(data: str) -> str:
    h = hmac.new(
        settings.app_secret_key.encode("utf-8"),
        data.encode("utf-8"),
        hashlib.sha256,
    )
    return h.hexdigest()


def verify_session(data: str, signature: str) -> bool:
    expected = sign_session(data)
    return hmac.compare_digest(expected, signature)
