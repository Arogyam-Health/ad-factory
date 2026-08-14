from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from dashboard.backend.auth.service import require_user_dependency
from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import COLL_ORGS, COLL_ORG_MEMBERS, COLL_ORG_INVITES, COLL_USER_CONFIGS, COLL_USERS
from dashboard.backend.services.org_helper import (
    get_user_default_org,
    get_user_org_membership,
    get_user_org_memberships,
    generate_org_id,
    generate_membership_id,
    extract_domain_from_email,
    is_public_email_domain,
    get_org_by_id,
    get_org_by_domain,
    get_org_memberships,
    write_audit_event,
    get_role_permissions,
    can_user_edit_org_config,
    require_org_member,
    require_org_role,
)
from dashboard.backend.services.user_config import (
    create_or_update_config,
    resolve_effective_config,
    get_generic_config,
    parse_expected_version,
    extract_config_files,
    validate_config_files,
    CONFIG_KEYS,
    ConfigVersionConflict,
)

router = APIRouter()


def _json_safe(d: dict | None) -> dict | None:
    """Strip MongoDB _id fields so FastAPI can serialize the dict."""
    if d is None:
        return None
    return {k: v for k, v in d.items() if k != "_id"}


def _json_safe_list(items: list[dict]) -> list[dict]:
    """Strip MongoDB _id from a list of dicts."""
    return [_json_safe(d) for d in items]


def _get_active_user_org(org_id: str, user_id: str) -> dict[str, Any]:
    """Get org and verify user is active member."""
    org = get_org_by_id(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    require_org_member(user_id, org_id)
    return org


@router.get("/api/orgs/me")
def get_my_orgs(
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    """Get user's organizations and default org with memberships."""
    user_id = user["user_id"]

    memberships = get_user_org_memberships(user_id)

    orgs = []
    default_org = None
    for membership in memberships:
        org_id = membership["org_id"]
        org = get_org_by_id(org_id)
        if not org:
            continue
        permissions = get_role_permissions(membership.get("role", "creator"))
        orgs.append({**_json_safe(org), "permissions": permissions})
        if default_org is None:
            default_org = {**_json_safe(org), "permissions": permissions}

    return {
        "orgs": orgs,
        "default_org": _json_safe(default_org),
        "memberships": _json_safe_list(memberships),
    }


@router.post("/api/orgs")
def create_org(
    payload: dict[str, Any],
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    """Create a new organization and persist to MongoDB."""
    name = payload.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Organization name is required")

    user_id = user["user_id"]
    email = user.get("email", "")

    domain = extract_domain_from_email(email) if not is_public_email_domain(email) else None

    if domain:
        existing_org = get_org_by_domain(domain)
        if existing_org:
            raise HTTPException(
                status_code=400,
                detail=f"An organization already exists for domain '{domain}'.",
            )

    org_id = generate_org_id()
    membership_id = generate_membership_id()
    now = time.time()
    db = get_sync_db()

    org = {
        "org_id": org_id,
        "name": name,
        "owner_user_id": user_id,
        "config_mode": "shared_org_config",
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    if domain:
        org["domain"] = domain
    db[COLL_ORGS].insert_one(org)

    membership = {
        "membership_id": membership_id,
        "org_id": org_id,
        "user_id": user_id,
        "email": email,
        "role": "owner",
        "status": "active",
        "joined_at": now,
        "created_at": now,
        "updated_at": now,
    }
    db[COLL_ORG_MEMBERS].insert_one(membership)

    try:
        from dashboard.backend.services.config_version_service import copy_config

        copy_config(
            source_owner_type="user",
            source_owner_id=user_id,
            target_owner_type="org",
            target_owner_id=org_id,
            actor_user_id=user_id,
            actor_email=email,
            mode="replace",
            reason="create_org",
            org_id=org_id,
        )
    except ValueError:
        pass

    write_audit_event(
        event_type="org_created",
        actor_user_id=user_id,
        actor_email=email,
        target_type="org",
        target_id=org_id,
        org_id=org_id,
        metadata={"name": name, "domain": domain},
    )

    updated_org = get_sync_db()[COLL_ORGS].find_one({"org_id": org_id})

    return {
        "org": _json_safe(updated_org or org),
        "membership": _json_safe(membership),
    }


@router.get("/api/orgs/{org_id}")
def get_org(
    org_id: str,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    """Get organization details by ID, verify user is member."""
    user_id = user["user_id"]

    org = _get_active_user_org(org_id, user_id)

    membership = require_org_member(user_id, org_id)
    permissions = get_role_permissions(membership.get("role", "creator"))

    return {
        "org": _json_safe(org),
        "membership": _json_safe(membership),
        "permissions": permissions,
    }


@router.get("/api/orgs/{org_id}/members")
def get_org_members(
    org_id: str,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> list[dict[str, Any]]:
    """Get all active members of an organization."""
    user_id = user["user_id"]

    _get_active_user_org(org_id, user_id)

    memberships = get_org_memberships(org_id)
    db = get_sync_db()
    user_ids = [mem["user_id"] for mem in memberships if mem.get("user_id")]
    users_by_id = {
        doc["user_id"]: doc
        for doc in db[COLL_USERS].find(
            {"user_id": {"$in": user_ids}},
            {"_id": 0, "user_id": 1, "display_name": 1, "email": 1},
        )
    }

    members = []
    for mem in memberships:
        uid = mem["user_id"]
        permissions = get_role_permissions(mem.get("role", "creator"))
        user_doc = users_by_id.get(uid) or {}

        member_info = {
            "membership_id": mem.get("membership_id", ""),
            "user_id": uid,
            "email": mem.get("email", "") or user_doc.get("email", ""),
            "display_name": user_doc.get("display_name", "") or mem.get("email", ""),
            "role": mem.get("role", "creator"),
            "status": mem.get("status", "active"),
            "joined_at": mem.get("joined_at", 0),
            "permissions": permissions,
        }
        members.append(member_info)

    return members


@router.patch("/api/orgs/{org_id}")
def update_org(
    org_id: str,
    payload: dict[str, Any],
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    """Update organization details (name, config_mode) — persists to MongoDB."""
    user_id = user["user_id"]

    org = _get_active_user_org(org_id, user_id)

    if org["owner_user_id"] != user_id:
        raise HTTPException(
            status_code=403,
            detail="Only organization owner can update organization details",
        )

    update_fields = {}
    config_mode_changed = False
    if "name" in payload:
        update_fields["name"] = payload["name"]
    if "config_mode" in payload:
        if payload["config_mode"] not in ("shared_org_config", "individual_member_config"):
            raise HTTPException(status_code=400, detail="Invalid config_mode")
        if org.get("config_mode") != payload["config_mode"]:
            config_mode_changed = True
        update_fields["config_mode"] = payload["config_mode"]

    if update_fields:
        update_fields["updated_at"] = time.time()
        get_sync_db()[COLL_ORGS].update_one(
            {"org_id": org_id},
            {"$set": update_fields},
        )

        if config_mode_changed:
            write_audit_event(
                event_type="org_config_mode_changed",
                actor_user_id=user_id,
                actor_email=user.get("email", ""),
                target_type="org",
                target_id=org_id,
                org_id=org_id,
                metadata={"updates": update_fields},
            )
        else:
            write_audit_event(
                event_type="org_updated",
                actor_user_id=user_id,
                actor_email=user.get("email", ""),
                target_type="org",
                target_id=org_id,
                org_id=org_id,
                metadata={"updates": update_fields},
            )

    updated_org = get_org_by_id(org_id)
    return updated_org or org


@router.delete("/api/orgs/{org_id}")
def delete_org(
    org_id: str,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    """Soft-delete an organization (owner only). Deactivates org, removes memberships and invites."""
    user_id = user["user_id"]
    email = user.get("email", "")

    org = _get_active_user_org(org_id, user_id)

    if org["owner_user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Only the organization owner can delete it")

    db = get_sync_db()
    now = time.time()

    # Soft-delete org
    db[COLL_ORGS].update_one(
        {"org_id": org_id},
        {"$set": {"is_active": False, "updated_at": now, "deleted_by_user_id": user_id}},
    )

    # Remove all memberships
    db[COLL_ORG_MEMBERS].update_many(
        {"org_id": org_id, "status": "active"},
        {"$set": {"status": "removed", "removed_at": now}},
    )

    # Remove all pending invites
    db[COLL_ORG_INVITES].update_many(
        {"org_id": org_id, "status": "pending"},
        {"$set": {"status": "revoked", "revoked_at": now}},
    )

    # Deactivate org config
    db[COLL_USER_CONFIGS].update_many(
        {"owner_type": "org", "owner_id": org_id, "is_active": True},
        {"$set": {"is_active": False, "updated_at": now}},
    )

    write_audit_event(
        event_type="org_deleted",
        actor_user_id=user_id,
        actor_email=email,
        target_type="org",
        target_id=org_id,
        org_id=org_id,
        metadata={"name": org.get("name", "")},
    )

    return {"ok": True, "message": "Organization deleted"}


@router.get("/api/orgs/{org_id}/config")
def get_org_config(
    org_id: str,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    """Get organization-specific config for the user (resolves via org mode)."""
    user_id = user["user_id"]

    org = _get_active_user_org(org_id, user_id)
    membership = require_org_member(user_id, org_id)
    can_edit = can_user_edit_org_config(user_id, org_id)

    if org["config_mode"] == "shared_org_config":
        from dashboard.backend.services.user_config import get_config_doc, _extract_flat_from_new_schema
        generic = get_generic_config()
        doc = get_config_doc("org", org_id)
        if doc:
            org_files = _extract_flat_from_new_schema(doc)
            config = dict(generic)
            for k in CONFIG_KEYS:
                val = org_files.get(k, "")
                if val:
                    config[k] = val
        else:
            config = dict(generic)
        source = "org_shared"
    else:
        config = resolve_effective_config(user_id, org_id)
        source = "user_personal"

    return {
        "config": config,
        "org": _json_safe(org),
        "mode": org["config_mode"],
        "can_edit": can_edit,
        "source": source,
    }


@router.put("/api/orgs/{org_id}/config")
def update_org_config(
    org_id: str,
    payload: dict[str, Any],
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    """Update organization config — only owner/config_admin can update."""
    user_id = user["user_id"]
    email = user.get("email", "")

    org = _get_active_user_org(org_id, user_id)

    can_edit = can_user_edit_org_config(user_id, org_id)
    if not can_edit:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to update this organization's config",
        )

    try:
        config = validate_config_files(extract_config_files(payload))
        result = create_or_update_config(
            owner_type="org",
            owner_id=org_id,
            files=config,
            actor_user_id=user_id,
            config_scope="organization",
            source="org_config_update",
            actor_email=email,
            change_reason="manual_edit",
            org_id=org_id,
            expected_version=parse_expected_version(payload),
        )
    except ConfigVersionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "config_version_conflict",
                "current_version": exc.current_version,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    write_audit_event(
        event_type="org_config_updated",
        actor_user_id=user_id,
        actor_email=email,
        target_type="org",
        target_id=org_id,
        org_id=org_id,
        metadata={"config_keys": list(config.keys())},
    )

    from dashboard.backend.services.user_config import get_config_doc as _get_cfg_doc
    config_doc = _get_cfg_doc("org", org_id)
    config_id = config_doc.get("config_id") if config_doc else None

    generic = get_generic_config()
    merged = dict(generic)
    for k in CONFIG_KEYS:
        val = result.get(k, "")
        if val:
            merged[k] = val

    permissions = get_role_permissions(
        get_user_org_membership(user_id, org_id).get("role", "creator")
        if get_user_org_membership(user_id, org_id) else "creator"
    )

    return {
        "config": merged,
        "org": _json_safe(org),
        "source": "org_config_update",
        "config_id": config_id,
        "can_edit": can_edit,
        "can_view_versions": permissions.get("can_manage_org", False) or can_edit,
        "can_rollback": can_edit,
        "can_copy": can_edit and permissions.get("can_manage_org", False) is not False,
    }
