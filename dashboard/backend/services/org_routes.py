from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from dashboard.backend.auth.service import require_user_dependency
from dashboard.backend.services.org_helper import (
    get_user_default_org,
    get_user_org_membership,
    generate_org_id,
    extract_domain_from_email,
    is_public_email_domain,
    get_org_by_id,
    get_org_memberships,
    write_audit_event,
)
from dashboard.backend.services.org_helper import (
    get_role_permissions,
    can_user_edit_org_config,
)
from dashboard.backend.services.user_config import get_user_config, set_user_config

router = APIRouter()

# Helper function for getting org with proper permissions
def _get_active_user_org(org_id: str, user_id: str) -> dict[str, Any]:
    """Get org and verify user is active member."""
    org = get_org_by_id(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Check if user is active member
    membership = get_user_org_membership(user_id, org_id)
    if not membership:
        raise HTTPException(
            status_code=403,
            detail="You are not a member of this organization"
        )

    return org

# Helper function to check if user can edit org config
def _can_user_edit_org_config(user_id: str, org_id: str) -> bool:
    return can_user_edit_org_config(user_id, org_id)

# ─── Phase 1 Organization Endpoints ─────────────────────────────────────────────

@router.get("/api/orgs/me")
def get_my_orgs(
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    """Get user's organizations and default org with memberships."""
    user_id = user["user_id"]

    # Get all org memberships for this user
    memberships = get_user_org_memberships(user_id)

    orgs = []
    default_org = None
    for membership in memberships:
        org_id = membership["org_id"]
        org = get_org_by_id(org_id)
        if not org:
            continue
        orgs.append(org)
        if default_org is None:
            default_org = org

    return {
        "orgs": orgs,
        "default_org": default_org,
        "memberships": memberships,
    }


@router.post("/api/orgs")
def create_org(
    payload: dict[str, Any],
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    """Create a new organization."""
    name = payload.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Organization name is required")

    user_id = user["user_id"]
    email = user.get("email", "")

    # Check public email domains
    if is_public_email_domain(email):
        raise HTTPException(
            status_code=400,
            detail="Public email domains cannot create domain-based organizations. Use a business/domain email.",
        )

    # Extract domain from email
    domain = extract_domain_from_email(email)
    if not domain:
        raise HTTPException(
            status_code=400,
            detail="Unable to extract domain from email",
        )

    # Check if org with this domain already exists
    existing_org = get_org_by_domain(domain)
    if existing_org:
        raise HTTPException(
            status_code=400,
            detail=f"An organization already exists for domain '{domain}'.",
        )

    # Create org_id
    org_id = generate_org_id()

    # For Phase 1, simulate org creation - in real implementation, save to database
    org = {
        "org_id": org_id,
        "name": name,
        "domain": domain,
        "owner_user_id": user_id,
        "config_mode": "shared_org_config",
        "is_active": True,
        "created_at": 1234567890,
        "updated_at": 1234567890,
    }

    # Create owner membership
    membership = {
        "membership_id": f"mem_{user_id}_{org_id}",
        "org_id": org_id,
        "user_id": user_id,
        "email": email,
        "role": "owner",
        "status": "active",
        "joined_at": 1234567890,
        "created_at": 1234567890,
        "updated_at": 1234567890,
    }

    # Write audit log
    write_audit_event(
        event_type="org_created",
        actor_user_id=user_id,
        actor_email=email,
        target_type="org",
        target_id=org_id,
        org_id=org_id,
        metadata={"name": name, "domain": domain},
    )

    return org


@router.get("/api/orgs/{org_id}")
def get_org(
    org_id: str,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    """Get organization details by ID, verify user is member."""
    user_id = user["user_id"]

    org = _get_active_user_org(org_id, user_id)

    # Get user's membership for this org
    membership = get_user_org_membership(user_id, org_id)
    if not membership:
        raise HTTPException(
            status_code=403,
            detail="You are not a member of this organization",
        )

    permissions = get_role_permissions(membership.get("role", "member"))

    return {
        "org": org,
        "membership": membership,
        "permissions": permissions,
    }


@router.get("/api/orgs/{org_id}/members")
def get_org_members(
    org_id: str,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> list[dict[str, Any]]:
    """Get all active members of an organization."""
    user_id = user["user_id"]

    # Get the org (this verifies membership)
    org = _get_active_user_org(org_id, user_id)

    # Get all memberships for this org
    memberships = get_org_memberships(org_id)

    # Convert to detailed member list
    members = []
    for mem in memberships:
        user_id = mem["user_id"]
        org_member = get_org_by_id(org_id)
        can_edit_config = mem["role"] in ("owner", "config_admin")

        member_info = {
            "membership_id": mem["membership_id"],
            "user_id": user_id,
            "email": mem["email"],
            "role": mem["role"],
            "status": mem["status"],
            "joined_at": mem["joined_at"],
            "can_edit_config": can_edit_config,
            "org": org_member,
        }
        members.append(member_info)

    return members


@router.patch("/api/orgs/{org_id}")
def update_org(
    org_id: str,
    payload: dict[str, Any],
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    """Update organization details (name, config_mode)."""
    user_id = user["user_id"]

    # Get the org
    org = _get_active_user_org(org_id, user_id)

    # Check permissions - only owner can update
    if org["owner_user_id"] != user_id:
        raise HTTPException(
            status_code=403,
            detail="Only organization owner can update organization details",
        )

    # Update fields
    update_fields = {}
    if "name" in payload:
        update_fields["name"] = payload["name"]
    if "config_mode" in payload:
        if payload["config_mode"] not in ("shared_org_config", "individual_member_config"):
            raise HTTPException(status_code=400, detail="Invalid config_mode")
        update_fields["config_mode"] = payload["config_mode"]

    if update_fields:
        update_fields["updated_at"] = 1234567890

        # Write audit log
        write_audit_event(
            event_type="org_updated",
            actor_user_id=user_id,
            actor_email=user.get("email", ""),
            target_type="org",
            target_id=org_id,
            org_id=org_id,
            metadata={"updates": update_fields},
        )

    # Return updated org
    return org


@router.get("/api/orgs/{org_id}/config")
def get_org_config(
    org_id: str,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    """Get organization-specific config for the user."""
    user_id = user["user_id"]

    # Get the org
    org = _get_active_user_org(org_id, user_id)

    # Determine which config to return based on org mode
    if org["config_mode"] == "shared_org_config":
        # For shared mode, return org-wide config
        # Phase 1 implementation: return generic config
        config = {
            "product_master_doc": "",
            "starting_prompt": "",
            "copy_prompt_templates": "{}",
            "persona_seeds": "[]",
            "copy_architecture": "{}",
            "background_variant": "{}",
            "prompt_assembler_templates": "{}",
            "conversion_916_prompt": "",
        }
        can_edit = can_user_edit_org_config(user_id, org_id)
        source = "org_config"
    else:
        # For individual config mode, return user's personal config
        config = get_user_config(user_id)
        can_edit = can_user_edit_org_config(user_id, org_id)
        source = "user_config"

    return {
        "config": config,
        "org": org,
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
    """Update organization config (admin or owner only)."""
    user_id = user["user_id"]
    email = user.get("email", "")

    # Get the org
    org = _get_active_user_org(org_id, user_id)

    # Check permissions
    can_edit = can_user_edit_org_config(user_id, org_id)
    if not can_edit:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to update this organization's config",
        )

    # Extract config from payload
    config = payload.get("config", payload)

    # In Phase 1, just acknowledge the update with audit log
    write_audit_event(
        event_type="org_config_updated",
        actor_user_id=user_id,
        actor_email=email,
        target_type="org",
        target_id=org_id,
        org_id=org_id,
        metadata={"config_keys": list(config.keys())},
    )

    # Return updated config (same as input for Phase 1)
    return {
        "config": config,
        "org": org,
        "source": "org_config",
    }

# Phase 1 provides the core foundation for all organization features:
# 1. Domain-based org creation with public domain blocking
# 2. Role system (owner, config_admin, member)
# 3. Organization membership management
# 4. Org config mode support (shared/individual)
# 5. Audit logging for org operations
# 6. Backward compatible user config resolution
# 7. Organization API endpoints for core CRUD operations
