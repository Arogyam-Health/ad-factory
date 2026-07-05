from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Optional

from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import COLL_CONFIG_VERSIONS, COLL_USER_CONFIGS
from dashboard.backend.services.user_config import CONFIG_KEYS


def generate_version_id() -> str:
    return f"ver_{uuid.uuid4().hex}"


def canonical_hash(value: dict) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def extract_files_for_hash(config_doc: dict) -> dict:
    files = config_doc.get("files", {})
    result = {}
    for k in CONFIG_KEYS:
        entry = files.get(k, {})
        if isinstance(entry, dict):
            result[k] = {
                "content": entry.get("content", ""),
                "content_type": entry.get("content_type", "text/plain"),
            }
    return result


def _normalize_incoming_files(files: dict) -> dict:
    """Normalize incoming files dict to standard files{} sub-dict format."""
    result = {}
    for k in CONFIG_KEYS:
        if k in files:
            val = files[k]
            if isinstance(val, dict):
                result[k] = {
                    "content": val.get("content", ""),
                    "content_type": val.get("content_type", "text/plain"),
                }
            else:
                result[k] = {
                    "content": str(val),
                    "content_type": "text/plain",
                }
    return result


def calculate_changed_keys(before_files: dict, after_files: dict) -> list[str]:
    changed = []
    all_keys = set(before_files.keys()) | set(after_files.keys())
    for k in CONFIG_KEYS:
        before_content = ""
        after_content = ""
        if k in before_files:
            entry = before_files[k]
            before_content = entry.get("content", "") if isinstance(entry, dict) else str(entry)
        if k in after_files:
            entry = after_files[k]
            after_content = entry.get("content", "") if isinstance(entry, dict) else str(entry)
        if before_content != after_content:
            changed.append(k)
    return changed


def create_config_version_before_update(
    config_doc: dict,
    new_files: dict,
    changed_by_user_id: str,
    changed_by_email: str | None,
    change_reason: str,
    org_id: str | None = None,
) -> dict | None:
    before_files = extract_files_for_hash(config_doc)
    after_files = _normalize_incoming_files(new_files)

    changed_keys = calculate_changed_keys(before_files, after_files)
    if not changed_keys:
        return None

    before_hash = canonical_hash(before_files)
    after_hash = canonical_hash(after_files)

    if before_hash == after_hash:
        return None

    snapshot = {
        "files": {
            k: config_doc.get("files", {}).get(k, {}) for k in CONFIG_KEYS
        }
    }

    version_doc = {
        "version_id": generate_version_id(),
        "config_id": config_doc.get("config_id", ""),
        "owner_type": config_doc.get("owner_type", ""),
        "owner_id": config_doc.get("owner_id", ""),
        "org_id": org_id,
        "changed_by_user_id": changed_by_user_id,
        "changed_by_email": changed_by_email or "",
        "change_reason": change_reason,
        "changed_keys": changed_keys,
        "before_hash": before_hash,
        "after_hash": after_hash,
        "snapshot": snapshot,
        "created_at": time.time(),
    }

    get_sync_db()[COLL_CONFIG_VERSIONS].insert_one(version_doc)
    return version_doc


def get_config_versions(config_id: str, limit: int = 50, offset: int = 0) -> dict:
    coll = get_sync_db()[COLL_CONFIG_VERSIONS]
    total = coll.count_documents({"config_id": config_id})
    cursor = coll.find(
        {"config_id": config_id},
        {"snapshot": 0},
    ).sort("created_at", -1).skip(offset).limit(limit)

    versions = []
    for v in cursor:
        versions.append({
            "version_id": v.get("version_id", ""),
            "config_id": v.get("config_id", ""),
            "owner_type": v.get("owner_type", ""),
            "owner_id": v.get("owner_id", ""),
            "org_id": v.get("org_id"),
            "changed_by_user_id": v.get("changed_by_user_id", ""),
            "changed_by_email": v.get("changed_by_email", ""),
            "change_reason": v.get("change_reason", ""),
            "changed_keys": v.get("changed_keys", []),
            "before_hash": v.get("before_hash", ""),
            "after_hash": v.get("after_hash", ""),
            "created_at": v.get("created_at", 0),
        })

    return {"versions": versions, "total": total, "limit": limit, "offset": offset}


def get_config_version(config_id: str, version_id: str) -> dict | None:
    v = get_sync_db()[COLL_CONFIG_VERSIONS].find_one({
        "config_id": config_id,
        "version_id": version_id,
    })
    if v is None:
        return None
    return {
        "version_id": v.get("version_id", ""),
        "config_id": v.get("config_id", ""),
        "owner_type": v.get("owner_type", ""),
        "owner_id": v.get("owner_id", ""),
        "org_id": v.get("org_id"),
        "changed_by_user_id": v.get("changed_by_user_id", ""),
        "changed_by_email": v.get("changed_by_email", ""),
        "change_reason": v.get("change_reason", ""),
        "changed_keys": v.get("changed_keys", []),
        "before_hash": v.get("before_hash", ""),
        "after_hash": v.get("after_hash", ""),
        "snapshot": v.get("snapshot", {}),
        "created_at": v.get("created_at", 0),
    }


def rollback_config_to_version(
    config_id: str,
    version_id: str,
    actor_user_id: str,
    actor_email: str | None,
    reason: str | None = None,
) -> dict:
    from dashboard.backend.services.user_config import get_config_doc, _extract_flat_from_new_schema, create_or_update_config

    version_doc = get_config_version(config_id, version_id)
    if version_doc is None:
        raise ValueError("Version not found")

    owner_type = version_doc.get("owner_type", "")
    owner_id = version_doc.get("owner_id", "")
    org_id = version_doc.get("org_id")

    existing_doc = get_config_doc(owner_type, owner_id)
    if existing_doc is None:
        raise ValueError("Active config not found")

    snapshot_files = version_doc.get("snapshot", {}).get("files", {})
    flat_files = {}
    for k in CONFIG_KEYS:
        entry = snapshot_files.get(k, {})
        flat_files[k] = entry.get("content", "") if isinstance(entry, dict) else ""

    create_config_version_before_update(
        config_doc=existing_doc,
        new_files=flat_files,
        changed_by_user_id=actor_user_id,
        changed_by_email=actor_email,
        change_reason="rollback_before",
        org_id=org_id,
    )

    result = create_or_update_config(
        owner_type=owner_type,
        owner_id=owner_id,
        files=flat_files,
        actor_user_id=actor_user_id,
        config_scope=existing_doc.get("config_scope", "personal"),
        source="rollback_restore",
        actor_email=actor_email,
        change_reason=f"rollback_to_{version_id}",
        org_id=org_id,
        create_version=False,
    )

    return {
        "status": "rolled_back",
        "config": result,
        "config_id": config_id,
        "rolled_back_to_version_id": version_id,
    }


def copy_config(
    source_owner_type: str,
    source_owner_id: str,
    target_owner_type: str,
    target_owner_id: str,
    actor_user_id: str,
    actor_email: str | None,
    mode: str,
    reason: str,
    org_id: str | None = None,
) -> dict:
    from dashboard.backend.services.user_config import get_config_doc, _extract_flat_from_new_schema, create_or_update_config

    source_doc = get_config_doc(source_owner_type, source_owner_id)
    if source_doc is None:
        raise ValueError("Source config not found")

    source_files = _extract_flat_from_new_schema(source_doc)
    target_doc = get_config_doc(target_owner_type, target_owner_id)

    if mode == "merge_missing":
        target_files = {}
        if target_doc:
            target_files = _extract_flat_from_new_schema(target_doc)
        for k in CONFIG_KEYS:
            existing = target_files.get(k, "")
            if not existing:
                target_files[k] = source_files.get(k, "")
        final_files = target_files
    else:
        final_files = dict(source_files)

    if target_doc:
        create_config_version_before_update(
            config_doc=target_doc,
            new_files=final_files,
            changed_by_user_id=actor_user_id,
            changed_by_email=actor_email,
            change_reason=reason,
            org_id=org_id,
        )

    result = create_or_update_config(
        owner_type=target_owner_type,
        owner_id=target_owner_id,
        files=final_files,
        actor_user_id=actor_user_id,
        config_scope=target_doc.get("config_scope", "personal") if target_doc else "personal",
        source=reason,
        actor_email=actor_email,
        change_reason=reason,
        org_id=org_id,
        create_version=False,
    )

    return result
