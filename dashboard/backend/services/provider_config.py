from __future__ import annotations

import time
from typing import Any, Optional

from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import COLL_PROVIDER_CONFIGS
from dashboard.backend.security.crypto import decrypt_value, encrypt_value, mask_key


PROVIDER_FIELDS = {
    "opencode": ["api_url", "api_key", "default_model"],
    "google_gemini": ["api_key", "default_model"],
}


def get_provider_config(user_id: str, provider: str) -> Optional[dict[str, Any]]:
    doc = get_sync_db()[COLL_PROVIDER_CONFIGS].find_one(
        {"user_id": user_id, "provider": provider},
    )
    if doc is None:
        return None
    config = {
        "provider": doc["provider"],
        "config": {},
    }
    for key, value in doc.get("config", {}).items():
        if key.endswith("_key") or key.endswith("_secret"):
            config["config"][key] = mask_key(decrypt_value(value))
        else:
            config["config"][key] = value
    config["config"]["_has_keys"] = any(
        k.endswith("_key") or k.endswith("_secret") for k in doc.get("config", {})
    )
    return config


def set_provider_config(user_id: str, provider: str, raw_config: dict[str, Any]) -> dict[str, Any]:
    existing_doc = get_sync_db()[COLL_PROVIDER_CONFIGS].find_one(
        {"user_id": user_id, "provider": provider},
    )
    existing_config = existing_doc.get("config", {}) if existing_doc else {}

    merged_config = dict(existing_config)
    for key, value in raw_config.items():
        if key.endswith("_key") or key.endswith("_secret"):
            merged_config[key] = encrypt_value(str(value))
        else:
            merged_config[key] = value

    get_sync_db()[COLL_PROVIDER_CONFIGS].update_one(
        {"user_id": user_id, "provider": provider},
        {"$set": {
            "config": merged_config,
            "updated_at": time.time(),
        }},
        upsert=True,
    )
    return get_provider_config(user_id, provider)


def delete_provider_config(user_id: str, provider: str) -> None:
    get_sync_db()[COLL_PROVIDER_CONFIGS].delete_one(
        {"user_id": user_id, "provider": provider},
    )


def get_decrypted_provider_key(user_id: str, provider: str, key_name: str) -> Optional[str]:
    doc = get_sync_db()[COLL_PROVIDER_CONFIGS].find_one(
        {"user_id": user_id, "provider": provider},
    )
    if doc is None:
        return None
    encrypted = doc.get("config", {}).get(key_name)
    if encrypted is None:
        return None
    return decrypt_value(encrypted)


def get_all_provider_configs(user_id: str) -> list[dict[str, Any]]:
    docs = get_sync_db()[COLL_PROVIDER_CONFIGS].find(
        {"user_id": user_id},
    )
    results = []
    for doc in docs:
        config = {}
        for key, value in doc.get("config", {}).items():
            if key.endswith("_key") or key.endswith("_secret"):
                config[key] = mask_key(decrypt_value(value))
            else:
                config[key] = value
        config["_has_keys"] = any(
            k.endswith("_key") or k.endswith("_secret") for k in doc.get("config", {})
        )
        results.append({
            "provider": doc["provider"],
            "config": config,
        })
    return results
