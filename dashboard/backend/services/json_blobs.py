from __future__ import annotations

from typing import Any, Optional

from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import COLL_JSON_BLOBS


def get_json_blob(user_id: str, blob_type: str, name: str = "default") -> Optional[dict[str, Any]]:
    """Migration-window read only for existing JSON content."""
    doc = get_sync_db()[COLL_JSON_BLOBS].find_one(
        {"user_id": user_id, "blob_type": blob_type, "name": name},
    )
    if doc is None:
        return None
    return doc.get("data")


def set_json_blob(user_id: str, blob_type: str, data: Any, name: str = "default") -> dict[str, Any]:
    del user_id, blob_type, data, name
    raise ValueError("JSON content must be written to localhost")


def delete_json_blob(user_id: str, blob_type: str, name: str = "default") -> None:
    get_sync_db()[COLL_JSON_BLOBS].delete_one(
        {"user_id": user_id, "blob_type": blob_type, "name": name},
    )


def list_json_blobs(user_id: str, blob_type: Optional[str] = None) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"user_id": user_id}
    if blob_type:
        query["blob_type"] = blob_type
    docs = get_sync_db()[COLL_JSON_BLOBS].find(
        query,
        {"data": 0},
    ).sort("blob_type", 1).sort("name", 1)
    return [
        {
            "blob_type": d["blob_type"],
            "name": d.get("name", "default"),
            "updated_at": d.get("updated_at", 0),
        }
        for d in docs
    ]


def clone_default_blobs_to_user(user_id: str) -> int:
    del user_id
    return 0
