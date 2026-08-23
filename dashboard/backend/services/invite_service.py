from __future__ import annotations

import secrets
import time
import uuid
from typing import Any, Optional

from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import COLL_ORG_INVITES, COLL_ORGS, COLL_ORG_MEMBERS
from dashboard.backend.db.settings import settings
from dashboard.backend.security.crypto import hash_token


INVITE_EXPIRY_SECONDS = 7 * 24 * 3600
ALLOWED_INVITE_ROLES = ("creator", "config_admin")


def generate_invite_id() -> str:
    """Generate a unique invite ID with inv_ prefix."""
    return f"inv_{uuid.uuid4().hex}"


def generate_invite_token() -> str:
    """Generate a cryptographically secure invite token."""
    return secrets.token_urlsafe(40)


def hash_invite_token(token: str) -> str:
    """Deterministic hash of invite token for DB storage."""
    return hash_token(token)


def build_invite_url(token: str) -> str:
    """Build full invite URL using frontend_origin from settings."""
    origin = settings.frontend_origin.rstrip("/")
    return f"{origin}/invite/{token}"


def get_invite_by_token(token: str) -> Optional[dict[str, Any]]:
    """Look up invite by token_hash."""
    token_hash = hash_invite_token(token)
    try:
        return get_sync_db()[COLL_ORG_INVITES].find_one({"token_hash": token_hash})
    except Exception:
        return None


def create_invite(
    org_id: str,
    email: str,
    role: str,
    invited_by_user_id: str,
    invited_by_email: str,
) -> dict[str, Any]:
    """Create a new invite document in MongoDB.
    Returns dict with invite details (no token_hash).
    """
    invite_id = generate_invite_id()
    token = generate_invite_token()
    token_hash = hash_invite_token(token)
    now = time.time()
    expires_at = now + INVITE_EXPIRY_SECONDS

    doc = {
        "invite_id": invite_id,
        "org_id": org_id,
        "email": email.lower(),
        "role": role,
        "token_hash": token_hash,
        "status": "pending",
        "invited_by_user_id": invited_by_user_id,
        "invited_by_email": invited_by_email,
        "accepted_by_user_id": None,
        "accepted_at": None,
        "expires_at": expires_at,
        "created_at": now,
        "updated_at": now,
    }
    get_sync_db()[COLL_ORG_INVITES].insert_one(doc)

    return {
        "invite_id": invite_id,
        "org_id": org_id,
        "email": email.lower(),
        "role": role,
        "status": "pending",
        "expires_at": expires_at,
        "created_at": now,
        "raw_token": token,
    }


def revoke_pending_invites_for_email(org_id: str, email: str) -> None:
    """Revoke any pending invites for the same org+email."""
    now = time.time()
    get_sync_db()[COLL_ORG_INVITES].update_many(
        {"org_id": org_id, "email": email.lower(), "status": "pending"},
        {"$set": {"status": "revoked", "updated_at": now}},
    )


def find_active_membership(org_id: str, email: str) -> Optional[dict[str, Any]]:
    """Find active membership by org_id + email (lowercase)."""
    try:
        return get_sync_db()[COLL_ORG_MEMBERS].find_one({
            "org_id": org_id,
            "email": email.lower(),
            "status": "active",
        })
    except Exception:
        return None
