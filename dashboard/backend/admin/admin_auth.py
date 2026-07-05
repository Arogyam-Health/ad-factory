from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException

from dashboard.backend.auth.service import require_user
from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import COLL_USERS


_SUPER_ADMIN_CACHE: set[str] | None = None


def get_super_admin_emails() -> set[str]:
    global _SUPER_ADMIN_CACHE
    if _SUPER_ADMIN_CACHE is None:
        raw = os.getenv("SUPER_ADMIN_EMAILS", "")
        _SUPER_ADMIN_CACHE = {e.strip().lower() for e in raw.split(",") if e.strip()}
    return _SUPER_ADMIN_CACHE


def bootstrap_super_admin(user: dict[str, Any]) -> dict[str, Any]:
    user_id = user["user_id"]
    email = (user.get("email") or "").strip().lower()
    super_admins = get_super_admin_emails()
    if not email or email not in super_admins:
        return user
    if user.get("is_super_admin"):
        return user
    user["is_super_admin"] = True
    try:
        get_sync_db()[COLL_USERS].update_one(
            {"user_id": user_id},
            {"$set": {"is_super_admin": True, "updated_at": __import__("time").time()}},
        )
    except Exception:
        pass
    return user


def require_super_admin(session_token: str | None = None) -> dict[str, Any]:
    user = require_user(session_token)
    if not user.get("is_super_admin"):
        raise HTTPException(status_code=403, detail="Super admin access required")
    return user


def require_active_user(session_token: str | None = None) -> dict[str, Any]:
    user = require_user(session_token)
    if user.get("is_active") is False:
        raise HTTPException(status_code=401, detail="Account is disabled")
    return user


def require_super_admin_dependency(session: str | None = None) -> dict[str, Any]:
    return require_super_admin(session)
