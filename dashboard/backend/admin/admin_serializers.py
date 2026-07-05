from __future__ import annotations

import copy
from typing import Any

SENSITIVE_KEYS = frozenset({
    "api_key", "encrypted_api_key", "token", "token_hash",
    "raw_token", "secret", "password", "client_secret",
    "authorization", "cookie", "session",
})


def redact_sensitive(value: Any, depth: int = 0) -> Any:
    """Recursively redact values whose keys match SENSITIVE_KEYS."""
    if depth > 20:
        return value
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and k.lower() in SENSITIVE_KEYS:
                result[k] = "[REDACTED]"
            else:
                result[k] = redact_sensitive(v, depth + 1)
        return result
    if isinstance(value, list):
        return [redact_sensitive(item, depth + 1) for item in value]
    return value


def safe_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": user.get("user_id", ""),
        "email": user.get("email", ""),
        "display_name": user.get("display_name", ""),
        "avatar_url": user.get("avatar_url", ""),
        "is_active": user.get("is_active", True),
        "is_super_admin": user.get("is_super_admin", False),
        "is_platform_admin": user.get("is_platform_admin", False),
        "created_at": user.get("created_at", 0),
        "updated_at": user.get("updated_at", 0),
    }


def safe_invite(invite: dict[str, Any]) -> dict[str, Any]:
    return {
        "invite_id": invite.get("invite_id", ""),
        "org_id": invite.get("org_id", ""),
        "email": invite.get("email", ""),
        "role": invite.get("role", ""),
        "status": invite.get("status", "pending"),
        "invited_by_user_id": invite.get("invited_by_user_id", ""),
        "invited_by_email": invite.get("invited_by_email", ""),
        "accepted_by_user_id": invite.get("accepted_by_user_id"),
        "accepted_at": invite.get("accepted_at"),
        "expires_at": invite.get("expires_at"),
        "created_at": invite.get("created_at", 0),
        "updated_at": invite.get("updated_at", 0),
    }


def safe_session(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": str(session.get("_id", "")),
        "user_id": session.get("user_id", ""),
        "created_at": session.get("created_at", 0),
        "expires_at": session.get("expires_at", 0),
        "is_expired": session.get("expires_at", 0) < __import__("time").time(),
    }


def safe_audit_log(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event.get("event_id", ""),
        "event_type": event.get("event_type", ""),
        "actor_user_id": event.get("actor_user_id", ""),
        "actor_email": event.get("actor_email", ""),
        "target_type": event.get("target_type", ""),
        "target_id": event.get("target_id", ""),
        "org_id": event.get("org_id"),
        "metadata": redact_sensitive(event.get("metadata", {})),
        "created_at": event.get("created_at", 0),
    }


def safe_provider_config(doc: dict[str, Any]) -> dict[str, Any]:
    """Return safe view of a provider config (no decrypted keys, no ciphertext, no hashes)."""
    masked = ""
    last4 = doc.get("key_last4") or doc.get("api_key_last4") or ""
    if last4:
        masked = "***" + last4
    elif doc.get("api_key") or doc.get("encrypted_api_key"):
        masked = "configured"
    return {
        "user_id": doc.get("user_id", ""),
        "provider": doc.get("provider", ""),
        "owner_type": doc.get("owner_type", "user"),
        "owner_id": doc.get("owner_id", doc.get("user_id", "")),
        "configured": bool(doc.get("api_key") or doc.get("encrypted_api_key")),
        "masked_key": masked,
        "updated_at": doc.get("updated_at", 0),
    }
