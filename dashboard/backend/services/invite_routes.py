from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from dashboard.backend.auth.service import require_user_dependency
from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import COLL_ORGS, COLL_ORG_MEMBERS, COLL_ORG_INVITES, COLL_USER_CONFIGS
from dashboard.backend.services.email_service import send_invite_email
from dashboard.backend.services.invite_service import (
    create_invite,
    revoke_pending_invites_for_email,
    find_active_membership,
    get_invite_by_token,
    hash_invite_token,
    build_invite_url,
    ALLOWED_INVITE_ROLES,
)
from dashboard.backend.services.org_helper import (
    get_org_by_id,
    get_user_org_membership,
    get_org_memberships,
    generate_membership_id,
    write_audit_event,
    get_role_permissions,
    require_org_member,
    require_org_role,
    extract_domain_from_email,
)
from dashboard.backend.services.user_config import (
    create_or_update_config,
    get_config_doc,
    resolve_effective_config,
    get_generic_config,
    CONFIG_KEYS,
)

router = APIRouter()


def _json_safe(d: dict | None) -> dict | None:
    """Strip MongoDB _id fields so FastAPI can serialize the dict."""
    if d is None:
        return None
    return {k: v for k, v in d.items() if k != "_id"}


def _get_active_org(org_id: str) -> dict[str, Any]:
    org = get_org_by_id(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


def _require_org_owner(user_id: str, org_id: str) -> dict[str, Any]:
    return require_org_role(user_id, org_id, ("owner",))


def _can_invite_in_phase2(role: str) -> bool:
    return role == "owner"


# ─── Invite endpoints ──────────────────────────────────────────────────────


@router.post("/api/orgs/{org_id}/invites")
def create_org_invite(
    org_id: str,
    payload: dict[str, Any],
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    """Create an invite for a new member (owner only)."""
    user_id = user["user_id"]
    email = user.get("email", "")
    display_name = user.get("display_name", "") or user.get("name", "") or email

    org = _get_active_org(org_id)
    _require_org_owner(user_id, org_id)

    target_email = payload.get("email", "").strip().lower()
    if not target_email:
        raise HTTPException(status_code=400, detail="Email is required")

    role = payload.get("role", "").strip()
    if not role:
        raise HTTPException(status_code=400, detail="Role is required")
    if role not in ALLOWED_INVITE_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"Role must be one of: {', '.join(ALLOWED_INVITE_ROLES)}",
        )

    # Check if already an active member
    existing = find_active_membership(org_id, target_email)
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"User {target_email} is already an active member of this organization.",
        )

    # Revoke any old pending invites for same org+email
    revoke_pending_invites_for_email(org_id, target_email)

    # Create new invite
    invite = create_invite(
        org_id=org_id,
        email=target_email,
        role=role,
        invited_by_user_id=user_id,
        invited_by_email=email,
    )

    raw_token = invite["raw_token"]
    invite_url = build_invite_url(raw_token)

    # Send email
    invite_url_for_audit = invite_url
    email_result = send_invite_email(
        to_email=target_email,
        inviter_name=display_name,
        org_name=org.get("name", "Organization"),
        role=role,
        invite_url=invite_url,
    )

    audit_meta = {
        "invite_id": invite["invite_id"],
        "target_email": target_email,
        "role": role,
        "email_sent": email_result.get("sent", False),
        "email_provider": email_result.get("provider", "none"),
    }

    if email_result.get("sent"):
        write_audit_event(
            event_type="invite_email_sent",
            actor_user_id=user_id,
            actor_email=email,
            target_type="invite",
            target_id=invite["invite_id"],
            org_id=org_id,
            metadata=audit_meta,
        )
    elif email_result.get("provider") != "none":
        write_audit_event(
            event_type="invite_email_failed",
            actor_user_id=user_id,
            actor_email=email,
            target_type="invite",
            target_id=invite["invite_id"],
            org_id=org_id,
            metadata={**audit_meta, "email_error": email_result.get("error")},
        )

    write_audit_event(
        event_type="invite_created",
        actor_user_id=user_id,
        actor_email=email,
        target_type="invite",
        target_id=invite["invite_id"],
        org_id=org_id,
        metadata=audit_meta,
    )

    # Never return token_hash, but always return invite_url so frontend can show it
    resp = {
        "invite": {
            "invite_id": invite["invite_id"],
            "org_id": org_id,
            "email": target_email,
            "role": role,
            "status": "pending",
            "expires_at": invite["expires_at"],
            "created_at": invite["created_at"],
        },
        "invite_url": invite_url,
        "email_sent": email_result.get("sent", False),
        "email_provider": email_result.get("provider", "none"),
    }
    if email_result.get("error"):
        resp["email_error"] = email_result["error"]
    return resp


@router.get("/api/orgs/{org_id}/invites")
def list_org_invites(
    org_id: str,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    """List invites for an org (owner only)."""
    user_id = user["user_id"]
    _get_active_org(org_id)
    _require_org_owner(user_id, org_id)

    invites = list(
        get_sync_db()[COLL_ORG_INVITES]
        .find({"org_id": org_id})
        .sort("created_at", -1)
    )

    result = []
    for inv in invites:
        result.append({
            "invite_id": inv.get("invite_id", ""),
            "email": inv.get("email", ""),
            "role": inv.get("role", ""),
            "status": inv.get("status", "pending"),
            "invited_by_email": inv.get("invited_by_email", ""),
            "accepted_by_user_id": inv.get("accepted_by_user_id"),
            "accepted_at": inv.get("accepted_at"),
            "expires_at": inv.get("expires_at"),
            "created_at": inv.get("created_at"),
            "updated_at": inv.get("updated_at"),
        })

    return {"invites": result}


@router.delete("/api/orgs/{org_id}/invites/{invite_id}")
def revoke_org_invite(
    org_id: str,
    invite_id: str,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    """Revoke a pending invite (owner only)."""
    user_id = user["user_id"]
    email = user.get("email", "")
    _get_active_org(org_id)
    _require_org_owner(user_id, org_id)

    invite = get_sync_db()[COLL_ORG_INVITES].find_one({
        "invite_id": invite_id,
        "org_id": org_id,
    })
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")

    status = invite.get("status", "")
    if status == "accepted":
        raise HTTPException(
            status_code=400,
            detail="Accepted invite cannot be revoked.",
        )
    if status == "revoked":
        return {"status": "revoked", "invite_id": invite_id}

    now = time.time()
    get_sync_db()[COLL_ORG_INVITES].update_one(
        {"invite_id": invite_id},
        {"$set": {"status": "revoked", "updated_at": now}},
    )

    write_audit_event(
        event_type="invite_revoked",
        actor_user_id=user_id,
        actor_email=email,
        target_type="invite",
        target_id=invite_id,
        org_id=org_id,
        metadata={
            "invite_id": invite_id,
            "target_email": invite.get("email", ""),
            "role": invite.get("role", ""),
        },
    )

    return {"status": "revoked", "invite_id": invite_id}


@router.get("/api/invites/{token}")
def get_invite_details(token: str) -> dict[str, Any]:
    """Public: look up invite details by token."""
    invite = get_invite_by_token(token)
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")

    status = invite.get("status", "")
    if status == "revoked":
        return {"valid": False, "status": "revoked", "message": "Invite has been revoked."}
    if status == "accepted":
        return {"valid": False, "status": "accepted", "message": "Invite has already been accepted."}

    # Check expiry
    now = time.time()
    expires_at = invite.get("expires_at", 0)
    if expires_at and now > expires_at:
        get_sync_db()[COLL_ORG_INVITES].update_one(
            {"_id": invite["_id"]},
            {"$set": {"status": "expired", "updated_at": now}},
        )
        return {"valid": False, "status": "expired", "message": "Invite has expired."}

    # Valid pending
    org = get_org_by_id(invite.get("org_id", ""))
    return {
        "valid": True,
        "invite": {
            "email": invite.get("email", ""),
            "role": invite.get("role", ""),
            "org_id": invite.get("org_id", ""),
            "org_name": org.get("name", "Organization") if org else "Organization",
            "org_domain": org.get("domain", "") if org else "",
            "expires_at": invite.get("expires_at", 0),
        },
    }


@router.post("/api/invites/{token}/accept")
def accept_invite(
    token: str,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    """Accept an invite (logged-in user, email must match)."""
    user_id = user["user_id"]
    user_email = (user.get("email", "") or "").strip().lower()

    invite = get_invite_by_token(token)
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")

    status = invite.get("status", "")
    if status == "revoked":
        raise HTTPException(status_code=400, detail="Invite has been revoked.")
    if status == "accepted":
        raise HTTPException(status_code=400, detail="Invite has already been accepted.")

    # Check expiry
    now = time.time()
    expires_at = invite.get("expires_at", 0)
    if expires_at and now > expires_at:
        get_sync_db()[COLL_ORG_INVITES].update_one(
            {"_id": invite["_id"]},
            {"$set": {"status": "expired", "updated_at": now}},
        )
        raise HTTPException(status_code=400, detail="Invite has expired.")

    invite_email = (invite.get("email", "") or "").strip().lower()
    if user_email != invite_email:
        raise HTTPException(
            status_code=403,
            detail=f"This invite was sent to {invite_email}. Please login with that email.",
        )

    org_id = invite.get("org_id", "")
    org = get_org_by_id(org_id)
    if not org:
        raise HTTPException(status_code=400, detail="Organization no longer exists.")

    org_name = org.get("name", "Organization")

    # Check if user already has active membership
    existing_member = find_active_membership(org_id, user_email)
    if existing_member:
        # Mark invite accepted if still pending
        get_sync_db()[COLL_ORG_INVITES].update_one(
            {"_id": invite["_id"]},
            {"$set": {"status": "accepted", "accepted_by_user_id": user_id, "accepted_at": now, "updated_at": now}},
        )
        write_audit_event(
            event_type="invite_accepted",
            actor_user_id=user_id,
            actor_email=user_email,
            target_type="invite",
            target_id=invite.get("invite_id", ""),
            org_id=org_id,
            metadata={"target_email": user_email, "role": invite.get("role", "creator")},
        )
        return {
            "status": "accepted",
            "org": _json_safe(org),
            "membership": _json_safe(existing_member),
        }

    target_role = invite.get("role", "creator")
    membership_id = generate_membership_id()
    now = time.time()

    membership_doc = {
        "membership_id": membership_id,
        "org_id": org_id,
        "user_id": user_id,
        "email": user_email,
        "role": target_role,
        "status": "active",
        "joined_at": now,
        "created_at": now,
        "updated_at": now,
    }
    get_sync_db()[COLL_ORG_MEMBERS].insert_one(membership_doc)

    # Update invite
    get_sync_db()[COLL_ORG_INVITES].update_one(
        {"_id": invite["_id"]},
        {"$set": {"status": "accepted", "accepted_by_user_id": user_id, "accepted_at": now, "updated_at": now}},
    )

    config_mode = org.get("config_mode", "shared_org_config")
    if config_mode == "individual_member_config":
        from dashboard.backend.services.user_config import has_custom_config as user_has_config
        if not user_has_config(user_id):
            try:
                # Copy org config (or generic fallback) into user's personal config
                from dashboard.backend.services.user_config import _extract_flat_from_new_schema
                org_doc = get_config_doc("org", org_id)
                if org_doc:
                    org_config = _extract_flat_from_new_schema(org_doc)
                else:
                    org_config = get_generic_config()
                create_or_update_config(
                    owner_type="user",
                    owner_id=user_id,
                    files=org_config,
                    actor_user_id=user_id,
                    config_scope="personal",
                    source="invite_member_copy",
                )
                write_audit_event(
                    event_type="member_personal_config_created",
                    actor_user_id=user_id,
                    actor_email=user_email,
                    target_type="user",
                    target_id=user_id,
                    org_id=org_id,
                    metadata={"source": "invite_member_copy", "config_mode": "individual_member_config"},
                )
            except Exception:
                pass

    write_audit_event(
        event_type="invite_accepted",
        actor_user_id=user_id,
        actor_email=user_email,
        target_type="invite",
        target_id=invite.get("invite_id", ""),
        org_id=org_id,
        metadata={"target_email": user_email, "role": target_role},
    )
    write_audit_event(
        event_type="member_added",
        actor_user_id=user_id,
        actor_email=user_email,
        target_type="user",
        target_id=user_id,
        org_id=org_id,
        metadata={"target_email": user_email, "role": target_role, "membership_id": membership_id},
    )

    return {
        "status": "accepted",
        "org": _json_safe(org),
        "membership": _json_safe(membership_doc),
    }


# ─── Member management endpoints ───────────────────────────────────────────


@router.patch("/api/orgs/{org_id}/members/{target_user_id}/role")
def change_member_role(
    org_id: str,
    target_user_id: str,
    payload: dict[str, Any],
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    """Change a member's role (owner only)."""
    user_id = user["user_id"]
    email = user.get("email", "")
    _get_active_org(org_id)
    _require_org_owner(user_id, org_id)

    new_role = payload.get("role", "").strip()
    if new_role not in ("creator", "config_admin"):
        raise HTTPException(
            status_code=400,
            detail="Role must be 'creator' or 'config_admin'.",
        )

    target_member = get_sync_db()[COLL_ORG_MEMBERS].find_one({
        "org_id": org_id,
        "user_id": target_user_id,
        "status": "active",
    })
    if not target_member:
        raise HTTPException(status_code=404, detail="Member not found in this organization.")

    if target_member.get("role") == "owner":
        raise HTTPException(status_code=400, detail="Cannot change owner role through this endpoint.")

    old_role = target_member.get("role", "")
    now = time.time()
    get_sync_db()[COLL_ORG_MEMBERS].update_one(
        {"_id": target_member["_id"]},
        {"$set": {"role": new_role, "updated_at": now}},
    )

    write_audit_event(
        event_type="member_role_changed",
        actor_user_id=user_id,
        actor_email=email,
        target_type="user",
        target_id=target_user_id,
        org_id=org_id,
        metadata={"target_email": target_member.get("email", ""), "old_role": old_role, "new_role": new_role},
    )

    updated_member = get_sync_db()[COLL_ORG_MEMBERS].find_one({"_id": target_member["_id"]})
    permissions = get_role_permissions(new_role)

    return {"membership": _json_safe(updated_member), "permissions": permissions}


@router.delete("/api/orgs/{org_id}/members/{target_user_id}")
def remove_member(
    org_id: str,
    target_user_id: str,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    """Remove a member from the org (owner only, cannot remove owner in Phase 2)."""
    user_id = user["user_id"]
    email = user.get("email", "")
    _get_active_org(org_id)
    _require_org_owner(user_id, org_id)

    target_member = get_sync_db()[COLL_ORG_MEMBERS].find_one({
        "org_id": org_id,
        "user_id": target_user_id,
        "status": "active",
    })
    if not target_member:
        raise HTTPException(status_code=404, detail="Member not found in this organization.")

    if target_member.get("role") == "owner":
        raise HTTPException(status_code=400, detail="Cannot remove the organization owner in Phase 2.")

    now = time.time()
    get_sync_db()[COLL_ORG_MEMBERS].update_one(
        {"_id": target_member["_id"]},
        {
            "$set": {
                "status": "removed",
                "removed_at": now,
                "removed_by_user_id": user_id,
                "updated_at": now,
            }
        },
    )

    write_audit_event(
        event_type="member_removed",
        actor_user_id=user_id,
        actor_email=email,
        target_type="user",
        target_id=target_user_id,
        org_id=org_id,
        metadata={"target_email": target_member.get("email", ""), "role": target_member.get("role", "")},
    )

    return {"status": "removed", "user_id": target_user_id}


# ─── Config sources endpoint ───────────────────────────────────────────


@router.get("/api/config/sources")
def get_config_sources(
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    """Return all config sources available to the user: personal + each org."""
    user_id = user["user_id"]
    from dashboard.backend.services.user_config import has_custom_config as user_has_config
    from dashboard.backend.services.org_helper import get_user_org_memberships, get_org_by_id

    has_personal = user_has_config(user_id)
    memberships = get_user_org_memberships(user_id)

    sources: list[dict[str, Any]] = [
        {
            "type": "personal",
            "label": "My Config",
            "has_custom": has_personal,
        }
    ]

    for mem in memberships:
        org_id = mem.get("org_id", "")
        org = get_org_by_id(org_id)
        if org:
            role = mem.get("role", "creator")
            sources.append({
                "type": "org",
                "org_id": org_id,
                "org_name": org.get("name", ""),
                "config_mode": org.get("config_mode", "shared_org_config"),
                "role": role,
                "can_edit": role in ("owner", "config_admin"),
            })

    return {"sources": sources}


# ─── Effective config endpoint ─────────────────────────────────────────────


@router.get("/api/config/effective")
def get_effective_config(
    org_id: str | None = None,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    """Get the effective config for the user, optionally within an org context."""
    user_id = user["user_id"]
    generic = get_generic_config()

    if org_id:
        org = _get_active_org(org_id)
        membership = require_org_member(user_id, org_id)
        role = membership.get("role", "creator")
        can_edit = role in ("owner", "config_admin")

        config_mode = org.get("config_mode", "shared_org_config")
        if config_mode == "shared_org_config":
            config = resolve_effective_config(user_id, org_id)
            doc = get_config_doc("org", org_id)
            config_id = doc.get("config_id") if doc else None
            from dashboard.backend.services.org_helper import get_user_org_memberships
            memberships_x = get_user_org_memberships(user_id)
            available_orgs = []
            for m in memberships_x:
                r = m.get("role", "")
                if r in ("owner", "config_admin"):
                    o = get_org_by_id(m["org_id"])
                    if o:
                        available_orgs.append({
                            "org_id": o["org_id"],
                            "name": o.get("name", ""),
                            "config_mode": o.get("config_mode", "shared_org_config"),
                        })

            return {
                "config": config,
                "source": "org_shared",
                "owner_type": "org",
                "owner_id": org_id,
                "org": _json_safe(org),
                "membership": _json_safe(membership),
                "mode": config_mode,
                "can_edit": can_edit,
                "can_view_versions": can_edit,
                "can_rollback": can_edit,
                "can_copy": role == "owner" or can_edit,
                "config_id": config_id,
                "available_orgs": available_orgs,
            }
        else:
            config = resolve_effective_config(user_id, org_id)
            doc = get_config_doc("user", user_id)
            config_id = doc.get("config_id") if doc else None
            from dashboard.backend.services.org_helper import get_user_org_memberships
            memberships_x = get_user_org_memberships(user_id)
            available_orgs = []
            for m in memberships_x:
                r = m.get("role", "")
                if r in ("owner", "config_admin"):
                    o = get_org_by_id(m["org_id"])
                    if o:
                        available_orgs.append({
                            "org_id": o["org_id"],
                            "name": o.get("name", ""),
                            "config_mode": o.get("config_mode", "shared_org_config"),
                        })
            return {
                "config": config,
                "source": "user_personal",
                "owner_type": "user",
                "owner_id": user_id,
                "org": _json_safe(org),
                "membership": _json_safe(membership),
                "mode": config_mode,
                "can_edit": can_edit,
                "can_view_versions": can_edit,
                "can_rollback": can_edit,
                "can_copy": role in ("owner", "config_admin"),
                "config_id": config_id,
                "available_orgs": available_orgs,
            }

    # No org_id provided — always return personal config by default
    from dashboard.backend.services.user_config import has_custom_config as user_has_config

    config = resolve_effective_config(user_id)
    has_custom = user_has_config(user_id)
    doc = get_config_doc("user", user_id) if has_custom else None
    config_id = doc.get("config_id") if doc else None

    from dashboard.backend.services.org_helper import get_user_org_memberships
    memberships = get_user_org_memberships(user_id)
    available_orgs = []
    for m in memberships:
        role = m.get("role", "")
        if role in ("owner", "config_admin"):
            o = get_org_by_id(m["org_id"])
            if o:
                available_orgs.append({
                    "org_id": o["org_id"],
                    "name": o.get("name", ""),
                    "config_mode": o.get("config_mode", "shared_org_config"),
                })

    return {
        "config": config,
        "source": "user_personal" if has_custom else "generic",
        "owner_type": "user" if has_custom else "generic",
        "owner_id": user_id if has_custom else None,
        "org": None,
        "membership": None,
        "mode": "personal",
        "can_edit": True,
        "can_view_versions": True,
        "can_rollback": True,
        "can_copy": len(available_orgs) > 0,
        "config_id": config_id,
        "available_orgs": available_orgs,
    }
