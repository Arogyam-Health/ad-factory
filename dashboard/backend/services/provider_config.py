from __future__ import annotations

import time
from typing import Any, Optional
from urllib.parse import urlparse

from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import COLL_PROVIDER_CONFIGS
from dashboard.backend.security.crypto import decrypt_value, encrypt_value


PROVIDER_FIELDS = {
    "opencode": ["api_url", "api_key", "default_model"],
    "google_gemini": ["api_key", "default_model"],
}
_PROVIDERS = frozenset(PROVIDER_FIELDS)
_MAX_SECRET_LENGTH = 4096
_MAX_URL_LENGTH = 2048
_MAX_MODEL_LENGTH = 256


def _validate_provider(provider: str) -> str:
    value = str(provider or "").strip()
    if value not in _PROVIDERS:
        raise ValueError("Unsupported provider")
    return value


def _validate_config(provider: str, raw_config: dict[str, Any]) -> dict[str, str]:
    if not isinstance(raw_config, dict):
        raise ValueError("Provider config must be an object")
    allowed = set(PROVIDER_FIELDS[provider])
    if set(raw_config) - allowed:
        raise ValueError("Provider config contains unsupported fields")

    clean: dict[str, str] = {}
    for key, value in raw_config.items():
        if not isinstance(value, str):
            raise ValueError(f"Provider field {key} must be text")
        value = value.strip()
        limit = (
            _MAX_SECRET_LENGTH
            if key == "api_key"
            else _MAX_URL_LENGTH
            if key == "api_url"
            else _MAX_MODEL_LENGTH
        )
        if len(value) > limit:
            raise ValueError(f"Provider field {key} is too long")
        if key == "api_url" and value:
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("API URL must use http or https")
        clean[key] = value
    return clean


def _safe_config(doc: dict[str, Any]) -> dict[str, Any]:
    stored = doc.get("config", {})
    return {
        "provider": doc["provider"],
        "config": {
            "api_url": str(stored.get("api_url") or ""),
            "default_model": str(stored.get("default_model") or ""),
            "has_secret": bool(
                stored.get("encrypted_api_key") or stored.get("api_key")
            ),
        },
        "updated_at": doc.get("updated_at", 0),
    }


def get_provider_config(user_id: str, provider: str) -> Optional[dict[str, Any]]:
    provider = _validate_provider(provider)
    doc = get_sync_db()[COLL_PROVIDER_CONFIGS].find_one(
        {"user_id": user_id, "provider": provider},
    )
    return _safe_config(doc) if doc is not None else None


def set_provider_config(
    user_id: str, provider: str, raw_config: dict[str, Any]
) -> dict[str, Any]:
    provider = _validate_provider(provider)
    clean = _validate_config(provider, raw_config)
    collection = get_sync_db()[COLL_PROVIDER_CONFIGS]
    existing = collection.find_one({"user_id": user_id, "provider": provider}) or {}
    existing_config = existing.get("config", {})
    stored = {
        "api_url": str(existing_config.get("api_url") or ""),
        "default_model": str(existing_config.get("default_model") or ""),
    }
    existing_secret = (
        existing_config.get("encrypted_api_key") or existing_config.get("api_key")
    )
    if existing_secret:
        stored["encrypted_api_key"] = str(existing_secret)
    if "api_url" in clean:
        stored["api_url"] = clean["api_url"]
    if "default_model" in clean:
        stored["default_model"] = clean["default_model"]
    if clean.get("api_key"):
        stored["encrypted_api_key"] = encrypt_value(clean["api_key"])

    now = time.time()
    collection.update_one(
        {"user_id": user_id, "provider": provider},
        {
            "$setOnInsert": {
                "user_id": user_id,
                "provider": provider,
                "created_at": now,
            },
            "$set": {"config": stored, "updated_at": now},
        },
        upsert=True,
    )
    return _safe_config(
        {
            "provider": provider,
            "config": stored,
            "updated_at": now,
        }
    )


def delete_provider_config(user_id: str, provider: str) -> None:
    provider = _validate_provider(provider)
    get_sync_db()[COLL_PROVIDER_CONFIGS].delete_one(
        {"user_id": user_id, "provider": provider},
    )


def get_decrypted_provider_key(user_id: str, provider: str, key_name: str) -> Optional[str]:
    if key_name != "api_key":
        raise ValueError("Unsupported provider secret")
    provider = _validate_provider(provider)
    doc = get_sync_db()[COLL_PROVIDER_CONFIGS].find_one(
        {"user_id": user_id, "provider": provider}
    )
    if doc is None:
        return None
    stored = doc.get("config", {})
    encrypted = stored.get("encrypted_api_key") or stored.get("api_key")
    return decrypt_value(str(encrypted)) if encrypted else None


def get_materialized_provider_config(
    user_id: str, provider: str
) -> Optional[dict[str, str]]:
    safe = get_provider_config(user_id, provider)
    if safe is None:
        return None
    config = safe["config"]
    return {
        "api_url": str(config.get("api_url") or ""),
        "api_key": get_decrypted_provider_key(user_id, provider, "api_key") or "",
        "default_model": str(config.get("default_model") or ""),
    }


def get_all_provider_configs(user_id: str) -> list[dict[str, Any]]:
    docs = get_sync_db()[COLL_PROVIDER_CONFIGS].find({"user_id": user_id})
    return [
        _safe_config(doc)
        for doc in docs
        if str(doc.get("provider") or "") in _PROVIDERS
    ]
