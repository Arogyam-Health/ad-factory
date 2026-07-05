from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import (
    COLL_ORGS,
    COLL_ORG_MEMBERS,
    COLL_ORG_INVITES,
    COLL_AUDIT_LOGS,
    COLL_CONFIG_VERSIONS,
)
from fastapi import HTTPException

# Lazy imports inside function bodies to avoid circular imports with user_config.py

# ───────────────────────────────────────────────────────────────────────────────────────────────
# ORG MODEL DEFINITIONS - Essential for Phase 1 foundation
# ───────────────────────────────────────────────────────────────────────────────────────────────

PUBLIC_EMAIL_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "icloud.com",
    "proton.me",
    "protonmail.com",
    "rediffmail.com",
}

ORG_MODES = ("shared_org_config", "individual_member_config")

ORG_ROLES = ("owner", "config_admin", "creator")

# ───────────────────────────────────────────────────────────────────────────────────────────────
# CORE ORG FUNCTIONS - Basic Phase 1 implementation
# ───────────────────────────────────────────────────────────────────────────────────────────────

def generate_org_id() -> str:
    """Generate a stable org ID with org_ prefix."""
    return f"org_{uuid.uuid4().hex}"


def generate_membership_id() -> str:
    """Generate a unique membership ID with mem_ prefix."""
    return f"mem_{uuid.uuid4().hex}"


def extract_domain_from_email(email: str) -> str:
    """Extract domain from email address (lowercase)."""
    if "@" not in email:
        return ""
    return email.split("@", 1)[1].lower()


def is_public_email_domain(email: str) -> bool:
    """Block common public email domains from auto-creating orgs."""
    if "@" not in email:
        return True
    domain = email.split("@", 1)[1].lower()
    return domain in PUBLIC_EMAIL_DOMAINS


def get_org_by_id(org_id: str) -> Optional[dict[str, Any]]:
    """Retrieve organization by ID."""
    if not org_id:
        return None
    try:
        return get_sync_db()[COLL_ORGS].find_one({"org_id": org_id, "is_active": True})
    except Exception:
        return None


def get_org_by_domain(domain: str) -> Optional[dict[str, Any]]:
    """Retrieve organization by domain (lowercase)."""
    if not domain:
        return None
    try:
        return get_sync_db()[COLL_ORGS].find_one({"domain": domain.lower(), "is_active": True})
    except Exception:
        return None


def get_user_org_membership(user_id: str, org_id: str) -> Optional[dict[str, Any]]:
    """Get user's active membership in a specific organization."""
    if not user_id or not org_id:
        return None
    try:
        return get_sync_db()[COLL_ORG_MEMBERS].find_one({
            "user_id": user_id,
            "org_id": org_id,
            "status": "active",
        })
    except Exception:
        return None


def get_user_org_memberships(user_id: str) -> list[dict[str, Any]]:
    """Get all active org memberships for a user."""
    if not user_id:
        return []
    try:
        return list(get_sync_db()[COLL_ORG_MEMBERS].find({
            "user_id": user_id,
            "status": "active",
        }))
    except Exception:
        return []


def get_user_default_org(user_id: str) -> Optional[dict[str, Any]]:
    """
    Get the user's default organization (first active org).
    Phase 1: Simple implementation for leader decisions.
    """
    if not user_id:
        return None

    memberships = get_user_org_memberships(user_id)
    if not memberships:
        return None

    org_id = memberships[0]["org_id"]
    return get_org_by_id(org_id)


def get_org_memberships(org_id: str) -> list[dict[str, Any]]:
    """Get all active memberships for an organization."""
    if not org_id:
        return []
    try:
        return list(get_sync_db()[COLL_ORG_MEMBERS].find({
            "org_id": org_id,
            "status": "active",
        }))
    except Exception:
        return []


def has_custom_config(user_id: str, org_id: Optional[str] = None) -> bool:
    """Check if user has custom config (personal or org-level)."""
    try:
        if org_id:
            return get_user_org_membership(user_id, org_id) is not None
        from dashboard.backend.services.user_config import has_custom_config as get_user_custom_config
        return get_user_custom_config(user_id)
    except Exception:
        return False


def write_audit_event(
    event_type: str,
    actor_user_id: str,
    actor_email: str,
    target_type: str,
    target_id: str,
    org_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    request: Optional[Any] = None,
) -> None:
    """Write an audit event to the audit_logs collection."""
    now = time.time()
    event_id = f"evt_{uuid.uuid4().hex}"
    ip = None
    user_agent = None

    if request is not None:
        try:
            ip = request.client.host if request.client else None
        except Exception:
            pass
        user_agent = getattr(request.headers, "get", lambda x: None)("user-agent")

    doc = {
        "event_id": event_id,
        "event_type": event_type,
        "actor_user_id": actor_user_id,
        "actor_email": actor_email,
        "target_type": target_type,
        "target_id": target_id,
        "org_id": org_id,
        "metadata": metadata or {},
        "ip": ip,
        "user_agent": user_agent,
        "created_at": now,
    }

    try:
        get_sync_db()[COLL_AUDIT_LOGS].insert_one(doc)
    except Exception:
        pass

# ───────────────────────────────────────────────────────────────────────────────────────────────
# ROLE PERMISSIONS - Phase 1 permission model
# ───────────────────────────────────────────────────────────────────────────────────────────────

_ORG_ROLE_PERMISSIONS = {
    "owner": {
        "can_manage_org": True,
        "can_invite_members": False,
        "can_remove_members": True,
        "can_change_roles": True,
        "can_edit_org_config": True,
        "can_generate_ads": True,
        "can_view_org_runs": True,
        "can_view_org_images": True,
        "can_view_org_audit": True,
    },
    "config_admin": {
        "can_manage_org": False,
        "can_invite_members": False,
        "can_remove_members": False,
        "can_change_roles": False,
        "can_edit_org_config": True,
        "can_generate_ads": True,
        "can_view_org_runs": True,
        "can_view_org_images": True,
        "can_view_org_audit": False,
    },
    "creator": {
        "can_manage_org": False,
        "can_invite_members": False,
        "can_remove_members": False,
        "can_change_roles": False,
        "can_edit_org_config": False,
        "can_generate_ads": True,
        "can_view_org_runs": False,
        "can_view_org_images": False,
        "can_view_org_audit": False,
    },
}


def get_role_permissions(role: str) -> dict[str, Any]:
    """Get permissions for a given role."""
    return _ORG_ROLE_PERMISSIONS.get(role, {}).copy()


def can_user_edit_org_config(user_id: str, org_id: str) -> bool:
    """Check if user has permission to edit org config."""
    try:
        membership = get_user_org_membership(user_id, org_id)
        if not membership:
            return False
        return membership.get("role") in ("owner", "config_admin")
    except Exception:
        return False


def require_org_member(user_id: str, org_id: str) -> dict[str, Any]:
    """Return membership or raise 403 if user is not an active member."""
    membership = get_user_org_membership(user_id, org_id)
    if not membership:
        raise HTTPException(
            status_code=403,
            detail="You are not a member of this organization",
        )
    return membership


def require_org_role(user_id: str, org_id: str, allowed_roles: tuple[str, ...]) -> dict[str, Any]:
    """Return membership or raise 403 if user lacks required role."""
    membership = require_org_member(user_id, org_id)
    role = membership.get("role", "")
    if role not in allowed_roles:
        raise HTTPException(
            status_code=403,
            detail=f"Requires one of these roles: {', '.join(allowed_roles)}",
        )
    return membership

# ───────────────────────────────────────────────────────────────────────────────────────────────
# PUBLIC API EXPORTS
# ───────────────────────────────────────────────────────────────────────────────────────────────

__all__ = [
    # Core org helpers
    "generate_org_id",
    "generate_membership_id",
    "extract_domain_from_email",
    "is_public_email_domain",
    "get_org_by_id",
    "get_org_by_domain",
    "get_user_org_membership",
    "get_user_org_memberships",
    "get_user_default_org",
    "get_org_memberships",
    "has_custom_config",
    "write_audit_event",
    # Role permissions
    "get_role_permissions",
    "can_user_edit_org_config",
    "require_org_member",
    "require_org_role",
]

# Re-export commonly needed functions for convenience
is_public_email_domain = is_public_email_domain
extract_domain_from_email = extract_domain_from_email