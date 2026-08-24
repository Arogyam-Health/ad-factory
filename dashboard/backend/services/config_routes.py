from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from dashboard.backend.auth.service import require_user_dependency
from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import COLL_USER_CONFIGS
from dashboard.backend.services.config_permissions import (
    can_view_config,
    can_edit_config,
    can_copy_config,
    can_rollback_config,
    can_view_versions,
    can_view_version_snapshot,
)
from dashboard.backend.services.config_version_service import (
    get_config_versions as _get_config_versions,
    get_config_version as _get_config_version,
    delete_config_version as _delete_config_version,
    delete_old_config_versions as _delete_old_config_versions,
    rollback_config_to_version as _rollback_config,
    copy_config as _copy_config,
    create_config_version_before_update,
    extract_files_for_hash,
    calculate_changed_keys,
)
from dashboard.backend.services.org_helper import (
    get_org_by_id,
    get_user_org_membership,
    require_org_member,
    write_audit_event,
)
from dashboard.backend.services.user_config import (
    get_config_doc,
    get_generic_config,
    resolve_effective_config,
    CONFIG_KEYS,
)

router = APIRouter()


def _require_config_access(config_id: str, user_id: str) -> dict:
    """Look up config doc and verify user can view it."""
    doc = get_sync_db()[COLL_USER_CONFIGS].find_one({"config_id": config_id, "is_active": True})
    if doc is None:
        raise HTTPException(status_code=404, detail="Config not found")
    if not can_view_config(user_id, doc, doc.get("owner_id")):
        raise HTTPException(status_code=403, detail="You do not have access to this config")
    return doc


@router.get("/api/config/{config_id}/versions")
def list_config_versions(
    config_id: str,
    limit: int = 50,
    offset: int = 0,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict:
    """List config versions (without snapshot)."""
    user_id = user["user_id"]
    doc = _require_config_access(config_id, user_id)

    if not can_view_versions(user_id, doc, doc.get("owner_id")):
        raise HTTPException(status_code=403, detail="You do not have permission to view version history")

    result = _get_config_versions(config_id, limit=limit, offset=offset)

    write_audit_event(
        event_type="config_version_viewed",
        actor_user_id=user_id,
        actor_email=user.get("email", ""),
        target_type="config",
        target_id=config_id,
        org_id=doc.get("owner_id") if doc.get("owner_type") == "org" else None,
        metadata={"config_id": config_id, "limit": limit, "offset": offset},
    )

    return result


@router.get("/api/config/{config_id}/versions/{version_id}")
def get_config_version_detail(
    config_id: str,
    version_id: str,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict:
    """Get full version detail including snapshot (permission-restricted)."""
    user_id = user["user_id"]
    doc = _require_config_access(config_id, user_id)

    if not can_view_version_snapshot(user_id, doc, doc.get("owner_id")):
        raise HTTPException(status_code=403, detail="You do not have permission to view version snapshots")

    version = _get_config_version(config_id, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")

    write_audit_event(
        event_type="config_version_viewed",
        actor_user_id=user_id,
        actor_email=user.get("email", ""),
        target_type="config_version",
        target_id=version_id,
        org_id=doc.get("owner_id") if doc.get("owner_type") == "org" else None,
        metadata={"config_id": config_id, "version_id": version_id},
    )

    return version


@router.delete("/api/config/{config_id}/versions/{version_id}")
def delete_config_version(
    config_id: str,
    version_id: str,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict:
    user_id = user["user_id"]
    user_email = user.get("email", "")
    doc = _require_config_access(config_id, user_id)
    if not can_rollback_config(user_id, doc, doc.get("owner_id")):
        raise HTTPException(status_code=403, detail="You do not have permission to delete versions")
    if not _delete_config_version(config_id, version_id):
        raise HTTPException(status_code=404, detail="Version not found")
    write_audit_event(
        event_type="config_version_deleted",
        actor_user_id=user_id,
        actor_email=user_email,
        target_type="config_version",
        target_id=version_id,
        org_id=doc.get("owner_id") if doc.get("owner_type") == "org" else None,
        metadata={"config_id": config_id, "version_id": version_id},
    )
    return {"status": "deleted", "version_id": version_id}


@router.post("/api/config/{config_id}/prune-old-versions")
def prune_old_config_versions(
    config_id: str,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict:
    user_id = user["user_id"]
    user_email = user.get("email", "")
    doc = _require_config_access(config_id, user_id)
    if not can_rollback_config(user_id, doc, doc.get("owner_id")):
        raise HTTPException(status_code=403, detail="You do not have permission to delete versions")
    result = _delete_old_config_versions(config_id)
    write_audit_event(
        event_type="config_versions_pruned",
        actor_user_id=user_id,
        actor_email=user_email,
        target_type="config",
        target_id=config_id,
        org_id=doc.get("owner_id") if doc.get("owner_type") == "org" else None,
        metadata={"config_id": config_id, **result},
    )
    return {"status": "pruned", **result}


@router.post("/api/config/{config_id}/rollback/{version_id}")
def rollback_config(
    config_id: str,
    version_id: str,
    payload: dict[str, Any],
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict:
    """Rollback config to a specific version."""
    user_id = user["user_id"]
    user_email = user.get("email", "")

    doc = _require_config_access(config_id, user_id)

    if not can_rollback_config(user_id, doc, doc.get("owner_id")):
        raise HTTPException(status_code=403, detail="You do not have permission to rollback this config")

    reason = payload.get("reason", "manual_rollback")

    try:
        result = _rollback_config(
            config_id=config_id,
            version_id=version_id,
            actor_user_id=user_id,
            actor_email=user_email,
            reason=reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    write_audit_event(
        event_type="config_rolled_back",
        actor_user_id=user_id,
        actor_email=user_email,
        target_type="config",
        target_id=config_id,
        org_id=doc.get("owner_id") if doc.get("owner_type") == "org" else None,
        metadata={
            "config_id": config_id,
            "version_id": version_id,
            "reason": reason,
            "owner_type": doc.get("owner_type", ""),
            "owner_id": doc.get("owner_id", ""),
        },
    )

    return result


@router.post("/api/orgs/{org_id}/configs/copy")
def copy_org_config(
    org_id: str,
    payload: dict[str, Any],
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict:
    """Copy config between org and members."""
    user_id = user["user_id"]
    user_email = user.get("email", "")

    org = get_org_by_id(org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    require_org_member(user_id, org_id)

    if not can_copy_config(user_id, org_id):
        raise HTTPException(status_code=403, detail="You do not have permission to copy configs")

    source_type = payload.get("source_type", "")
    target_type = payload.get("target_type", "")
    mode = payload.get("mode", "replace_all")
    reason = payload.get("reason", "config_copy")

    if source_type not in ("org", "member"):
        raise HTTPException(status_code=400, detail="source_type must be 'org' or 'member'")
    if target_type not in ("org", "member"):
        raise HTTPException(status_code=400, detail="target_type must be 'org' or 'member'")
    if mode not in ("replace_all", "merge_missing"):
        raise HTTPException(status_code=400, detail="mode must be 'replace_all' or 'merge_missing'")

    if source_type == "org":
        source_owner_type = "org"
        source_owner_id = org_id
    else:
        source_user_id = payload.get("source_user_id", "")
        if not source_user_id:
            raise HTTPException(status_code=400, detail="source_user_id required for member source")
        membership = get_user_org_membership(source_user_id, org_id)
        if membership is None:
            raise HTTPException(status_code=400, detail="Source user is not a member of this org")
        source_owner_type = "user"
        source_owner_id = source_user_id

    if target_type == "org":
        target_owner_type = "org"
        target_owner_id = org_id
    else:
        target_user_id = payload.get("target_user_id", "")
        if not target_user_id:
            raise HTTPException(status_code=400, detail="target_user_id required for member target")
        membership = get_user_org_membership(target_user_id, org_id)
        if membership is None:
            raise HTTPException(status_code=400, detail="Target user is not a member of this org")
        target_owner_type = "user"
        target_owner_id = target_user_id

    try:
        result = _copy_config(
            source_owner_type=source_owner_type,
            source_owner_id=source_owner_id,
            target_owner_type=target_owner_type,
            target_owner_id=target_owner_id,
            actor_user_id=user_id,
            actor_email=user_email,
            mode=mode,
            reason=reason,
            org_id=org_id,
        )
    except ValueError as e:
        write_audit_event(
            event_type="config_copy_failed",
            actor_user_id=user_id,
            actor_email=user_email,
            target_type="config",
            target_id="",
            org_id=org_id,
            metadata={
                "source_type": source_type,
                "source_owner_id": source_owner_id,
                "target_type": target_type,
                "target_owner_id": target_owner_id,
                "mode": mode,
                "error": str(e),
            },
        )
        raise HTTPException(status_code=400, detail=str(e))

    write_audit_event(
        event_type="config_copied",
        actor_user_id=user_id,
        actor_email=user_email,
        target_type="config",
        target_id="",
        org_id=org_id,
        metadata={
            "source_owner_type": source_owner_type,
            "source_owner_id": source_owner_id,
            "target_owner_type": target_owner_type,
            "target_owner_id": target_owner_id,
            "mode": mode,
            "reason": reason,
        },
    )

    return {
        "status": "copied",
        "config": result,
        "source_type": source_type,
        "target_type": target_type,
        "mode": mode,
    }


@router.post("/api/config/{config_id}/save-version")
def manual_save_version(
    config_id: str,
    payload: dict[str, Any],
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict:
    """Manually save a version snapshot of the current config (no auto-save)."""
    user_id = user["user_id"]
    user_email = user.get("email", "")

    doc = _require_config_access(config_id, user_id)

    if not can_edit_config(user_id, doc, doc.get("owner_id")):
        raise HTTPException(status_code=403, detail="You do not have permission to save versions")

    reason = payload.get("reason", "").strip() or "manual_save"
    changed_keys = payload.get("changed_keys", [])

    before_files = extract_files_for_hash(doc)
    snapshot = {"files": {k: doc.get("files", {}).get(k, {}) for k in CONFIG_KEYS}}

    before_hash = ""
    import hashlib, json
    raw = json.dumps(before_files, sort_keys=True, separators=(",", ":"))
    before_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    from dashboard.backend.services.config_version_service import generate_version_id
    version_doc = {
        "version_id": generate_version_id(),
        "config_id": config_id,
        "owner_type": doc.get("owner_type", ""),
        "owner_id": doc.get("owner_id", ""),
        "org_id": doc.get("org_id") if doc.get("owner_type") == "org" else None,
        "changed_by_user_id": user_id,
        "changed_by_email": user_email,
        "change_reason": reason,
        "changed_keys": changed_keys,
        "before_hash": before_hash,
        "after_hash": before_hash,
        "snapshot": snapshot,
        "created_at": __import__("time").time(),
    }

    get_sync_db()[COLL_CONFIG_VERSIONS].insert_one(version_doc)

    write_audit_event(
        event_type="config_version_saved",
        actor_user_id=user_id,
        actor_email=user_email,
        target_type="config",
        target_id=config_id,
        org_id=version_doc.get("org_id"),
        metadata={"config_id": config_id, "reason": reason, "changed_keys": changed_keys},
    )

    return {"status": "saved", "version_id": version_doc["version_id"]}


@router.post("/api/config/{config_id}/copy-to-org")
def copy_config_to_org(
    config_id: str,
    payload: dict[str, Any],
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict:
    """Copy config (personal or org) into a target org's shared config."""
    user_id = user["user_id"]
    user_email = user.get("email", "")

    doc = _require_config_access(config_id, user_id)

    source_owner_type = doc.get("owner_type", "user")
    source_owner_id = doc.get("owner_id", user_id)

    target_org_id = payload.get("org_id", "").strip()
    if not target_org_id:
        raise HTTPException(status_code=400, detail="org_id is required")

    org = get_org_by_id(target_org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    require_org_member(user_id, target_org_id)

    if not can_copy_config(user_id, target_org_id):
        raise HTTPException(status_code=403, detail="You do not have permission to copy config to this org")

    if source_owner_type == "org":
        require_org_member(user_id, source_owner_id)

    reason = payload.get("reason", "").strip() or "copy_config_to_org"

    try:
        result = _copy_config(
            source_owner_type=source_owner_type,
            source_owner_id=source_owner_id,
            target_owner_type="org",
            target_owner_id=target_org_id,
            actor_user_id=user_id,
            actor_email=user_email,
            mode="replace_all",
            reason=reason,
            org_id=target_org_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    write_audit_event(
        event_type="config_copied_to_org",
        actor_user_id=user_id,
        actor_email=user_email,
        target_type="config",
        target_id=config_id,
        org_id=target_org_id,
        metadata={
            "config_id": config_id,
            "source_owner_type": source_owner_type,
            "source_owner_id": source_owner_id,
            "target_org_id": target_org_id,
            "reason": reason,
        },
    )

    return {
        "status": "copied",
        "org_id": target_org_id,
        "config": result,
        "reason": reason,
    }


@router.post("/api/config/{config_id}/copy-to-personal")
def copy_config_to_personal(
    config_id: str,
    payload: dict[str, Any] | None = Body(default=None),
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict:
    """Copy an org (or other viewable) config onto the signed-in user's personal plate."""
    user_id = user["user_id"]
    user_email = user.get("email", "")
    body = payload or {}

    doc = _require_config_access(config_id, user_id)
    source_owner_type = str(doc.get("owner_type") or "user")
    source_owner_id = str(doc.get("owner_id") or "")

    if source_owner_type == "user" and source_owner_id == user_id:
        raise HTTPException(status_code=400, detail="This plate is already your personal config.")

    if source_owner_type == "org":
        require_org_member(user_id, source_owner_id)

    reason = str(body.get("reason") or "").strip() or "copy_config_to_personal"

    try:
        result = _copy_config(
            source_owner_type=source_owner_type,
            source_owner_id=source_owner_id,
            target_owner_type="user",
            target_owner_id=user_id,
            actor_user_id=user_id,
            actor_email=user_email,
            mode="replace_all",
            reason=reason,
            org_id=source_owner_id if source_owner_type == "org" else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    write_audit_event(
        event_type="config_copied_to_personal",
        actor_user_id=user_id,
        actor_email=user_email,
        target_type="config",
        target_id=config_id,
        org_id=source_owner_id if source_owner_type == "org" else None,
        metadata={
            "config_id": config_id,
            "source_owner_type": source_owner_type,
            "source_owner_id": source_owner_id,
            "reason": reason,
        },
    )

    return {
        "status": "copied",
        "owner_type": "user",
        "owner_id": user_id,
        "config": result,
        "reason": reason,
    }
