from __future__ import annotations

from typing import Any, Optional

from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import COLL_PROVIDER_CONFIGS


PROVIDER_FIELDS = {
    "opencode": ["api_url", "api_key", "default_model"],
    "google_gemini": ["api_key", "default_model"],
}


def get_provider_config(user_id: str, provider: str) -> Optional[dict[str, Any]]:
    """Migration-window read only: never return legacy config bodies or secrets."""
    doc = get_sync_db()[COLL_PROVIDER_CONFIGS].find_one(
        {"user_id": user_id, "provider": provider},
        {"_id": 0, "provider": 1, "updated_at": 1},
    )
    if doc is None:
        return None
    return {
        "provider": doc["provider"],
        "status": "migration_required",
        "updated_at": doc.get("updated_at", 0),
    }


def set_provider_config(user_id: str, provider: str, raw_config: dict[str, Any]) -> dict[str, Any]:
    del user_id, provider, raw_config
    raise ValueError("Provider configuration must be written to localhost")


def delete_provider_config(user_id: str, provider: str) -> None:
    get_sync_db()[COLL_PROVIDER_CONFIGS].delete_one(
        {"user_id": user_id, "provider": provider},
    )


def get_decrypted_provider_key(user_id: str, provider: str, key_name: str) -> Optional[str]:
    del user_id, provider, key_name
    raise ValueError("Provider secrets are available only from localhost")


def get_all_provider_configs(user_id: str) -> list[dict[str, Any]]:
    docs = get_sync_db()[COLL_PROVIDER_CONFIGS].find(
        {"user_id": user_id},
        {"_id": 0, "provider": 1, "updated_at": 1},
    )
    return [
        {
            "provider": doc["provider"],
            "status": "migration_required",
            "updated_at": doc.get("updated_at", 0),
        }
        for doc in docs
    ]
