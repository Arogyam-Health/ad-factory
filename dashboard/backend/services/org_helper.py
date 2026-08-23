from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import (
    COLL_AGENT_JOBS,
    COLL_AUDIT_LOGS,
    COLL_CONFIG_VERSIONS,
    COLL_FILE_MAP,
    COLL_IMAGES,
    COLL_LLM_TRACES,
    COLL_LOCAL_CONFIG_REFERENCES,
    COLL_ORG_INVITES,
    COLL_ORG_MEMBERS,
    COLL_ORGS,
    COLL_PROMPT_DELIVERIES,
    COLL_PROMPTS,
    COLL_RENDER_COPY_JOBS,
    COLL_RUN_COUNTERS,
    COLL_RUNS,
    COLL_USER_CONFIGS,
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

# Unique placeholder so Gmail/personal orgs never share Mongo's null domain key.
PERSONAL_DOMAIN_PREFIX = "personal:"


def personal_org_domain(org_id: str) -> str:
    return f"{PERSONAL_DOMAIN_PREFIX}{org_id}"


def is_personal_org_domain(domain: str | None) -> bool:
    return bool(domain) and str(domain).startswith(PERSONAL_DOMAIN_PREFIX)


def public_org_domain(domain: str | None) -> str | None:
    if not domain or is_personal_org_domain(domain):
        return None
    return str(domain)


def public_org_dict(org: dict | None) -> dict | None:
    """Strip Mongo _id and hide personal-org domain placeholders."""
    if org is None:
        return None
    out = {k: v for k, v in org.items() if k != "_id"}
    visible = public_org_domain(out.get("domain"))
    if visible:
        out["domain"] = visible
    else:
        out.pop("domain", None)
    return out


def assign_personal_org_domains(db) -> int:
    """Replace null/missing org domains with unique personal:{org_id} values."""
    updated = 0
    query = {"$or": [{"domain": None}, {"domain": {"$exists": False}}]}
    for org in db[COLL_ORGS].find(query, {"org_id": 1}):
        org_id = org.get("org_id")
        if not org_id:
            continue
        db[COLL_ORGS].update_one(
            {"_id": org["_id"]},
            {"$set": {"domain": personal_org_domain(org_id)}},
        )
        updated += 1
    return updated

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
    if not domain or is_personal_org_domain(domain):
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
    """Write a bounded metadata-only audit event."""
    from dashboard.backend.control_plane_policy import validate_metadata_document

    now = time.time()
    event_id = f"evt_{uuid.uuid4().hex}"
    del request

    doc = {
        "event_id": event_id,
        "event_type": event_type,
        "actor_user_id": actor_user_id,
        "actor_email": actor_email,
        "target_type": target_type,
        "target_id": target_id,
        "org_id": org_id,
        "metadata": metadata or {},
        "created_at": now,
    }

    try:
        validate_metadata_document("audit_logs", doc)
        get_sync_db()[COLL_AUDIT_LOGS].insert_one(doc)
    except Exception:
        pass

# ───────────────────────────────────────────────────────────────────────────────────────────────
# ROLE PERMISSIONS - Phase 1 permission model
# ───────────────────────────────────────────────────────────────────────────────────────────────

_ORG_ROLE_PERMISSIONS = {
    "owner": {
        "can_manage_org": True,
        "can_invite_members": True,
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
        "can_invite_members": True,
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


def _collection(db: Any, name: str) -> Any | None:
    if isinstance(db, dict):
        return db.get(name)
    return db[name]


def _delete_many(db: Any, name: str, query: dict[str, Any]) -> int:
    coll = _collection(db, name)
    if coll is None:
        return 0
    return int(getattr(coll.delete_many(query), "deleted_count", 0) or 0)


def _delete_one(db: Any, name: str, query: dict[str, Any]) -> int:
    coll = _collection(db, name)
    if coll is None:
        return 0
    return int(getattr(coll.delete_one(query), "deleted_count", 0) or 0)


def _org_run_ids(db: Any, org_id: str) -> list[str]:
    coll = _collection(db, COLL_RUNS)
    if coll is None:
        return []
    try:
        cursor = coll.find(
            {"$or": [{"owner_id": org_id}, {"org_id": org_id}]},
            {"run_id": 1},
        )
    except Exception:
        return []
    run_ids: list[str] = []
    for doc in cursor or []:
        if isinstance(doc, dict) and doc.get("run_id"):
            run_ids.append(str(doc["run_id"]))
    return run_ids


def purge_org_owned_documents(db: Any, org_id: str) -> dict[str, int]:
    """Hard-delete every Mongo document that belongs to this organization."""
    run_ids = _org_run_ids(db, org_id)
    run_q = {"run_id": {"$in": run_ids}} if run_ids else None
    owner_q = {"owner_id": org_id}
    org_q = {"org_id": org_id}
    report = {
        "configs": _delete_many(db, COLL_USER_CONFIGS, {"owner_type": "org", "owner_id": org_id}),
        "versions": _delete_many(
            db,
            COLL_CONFIG_VERSIONS,
            {"$or": [{"owner_type": "org", "owner_id": org_id}, org_q]},
        ),
        "local_refs": _delete_many(db, COLL_LOCAL_CONFIG_REFERENCES, owner_q),
        "members": _delete_many(db, COLL_ORG_MEMBERS, org_q),
        "invites": _delete_many(db, COLL_ORG_INVITES, org_q),
        "audit_logs": _delete_many(db, COLL_AUDIT_LOGS, org_q),
        "run_counters": _delete_many(db, COLL_RUN_COUNTERS, owner_q),
        "llm_traces": _delete_many(db, COLL_LLM_TRACES, org_q),
        "agent_jobs": _delete_many(db, COLL_AGENT_JOBS, owner_q),
        "render_copy_jobs": _delete_many(db, COLL_RENDER_COPY_JOBS, owner_q),
        "prompts": 0,
        "images": 0,
        "file_map": 0,
        "prompt_deliveries": 0,
        "runs": 0,
        "orgs": 0,
    }
    if run_q:
        report["prompts"] = _delete_many(db, COLL_PROMPTS, run_q)
        report["images"] = _delete_many(db, COLL_IMAGES, run_q)
        report["file_map"] = _delete_many(db, COLL_FILE_MAP, run_q)
        report["prompt_deliveries"] = _delete_many(db, COLL_PROMPT_DELIVERIES, run_q)
        report["agent_jobs"] += _delete_many(db, COLL_AGENT_JOBS, run_q)
        report["render_copy_jobs"] += _delete_many(db, COLL_RENDER_COPY_JOBS, run_q)
        report["llm_traces"] += _delete_many(db, COLL_LLM_TRACES, run_q)
        report["runs"] = _delete_many(db, COLL_RUNS, run_q)
    report["orgs"] = _delete_one(db, COLL_ORGS, org_q)
    return report

# ───────────────────────────────────────────────────────────────────────────────────────────────
# PUBLIC API EXPORTS
# ───────────────────────────────────────────────────────────────────────────────────────────────

__all__ = [
    # Core org helpers
    "generate_org_id",
    "generate_membership_id",
    "extract_domain_from_email",
    "is_public_email_domain",
    "personal_org_domain",
    "is_personal_org_domain",
    "public_org_domain",
    "public_org_dict",
    "assign_personal_org_domains",
    "purge_org_owned_documents",
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