from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import COLL_JSON_BLOBS


def get_json_blob(user_id: str, blob_type: str, name: str = "default") -> Optional[dict[str, Any]]:
    doc = get_sync_db()[COLL_JSON_BLOBS].find_one(
        {"user_id": user_id, "blob_type": blob_type, "name": name},
    )
    if doc is None:
        return None
    return doc.get("data")


def set_json_blob(user_id: str, blob_type: str, data: Any, name: str = "default") -> dict[str, Any]:
    now = time.time()
    get_sync_db()[COLL_JSON_BLOBS].update_one(
        {"user_id": user_id, "blob_type": blob_type, "name": name},
        {"$set": {
            "data": data,
            "updated_at": now,
        }},
        upsert=True,
    )
    return {"blob_type": blob_type, "name": name, "updated": True}


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
    BLOB_DEFAULTS = {
        "persona_seeds": "persona_seeds.json",
        "copy_architecture": "dashboard/backend/copy_architecture.json",
        "copy_prompt_templates": "dashboard/backend/copy_prompt_templates.json",
    }
    ROOT = Path(__file__).resolve().parents[3]
    count = 0
    for blob_type, rel_path in BLOB_DEFAULTS.items():
        existing = get_json_blob(user_id, blob_type)
        if existing is not None:
            continue
        src_path = ROOT / rel_path
        if src_path.exists():
            try:
                data = json.loads(src_path.read_text(encoding="utf-8"))
                set_json_blob(user_id, blob_type, data)
                count += 1
            except Exception:
                pass
    return count
