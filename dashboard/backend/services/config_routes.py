from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

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
    rollback_config_to_version as _rollback_config,
    copy_config as _copy_config,
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
