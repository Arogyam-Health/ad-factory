from __future__ import annotations

import re
import urllib.parse
from typing import Any, Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from dashboard.backend.admin.admin_auth import bootstrap_super_admin
from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import COLL_USERS
from dashboard.backend.auth.service import (
    create_session,
    create_user_from_google,
    delete_session,
    exchange_google_code,
    find_user_by_email,
    find_user_by_google_id,
    get_current_user_from_cookie,
    require_user,
    require_user_dependency,
)
from dashboard.backend.db.settings import settings
from dashboard.backend.security.crypto import mask_key

router = APIRouter()

# Only same-origin invite paths may be resumed after the OAuth round trip.
_RETURN_TO_ALLOWED = re.compile(r"^/invite/[A-Za-z0-9_\-]{1,256}$")


def sanitize_return_to(value: str) -> str:
    """Return a safe same-origin redirect path, or "" when not allowed."""
    candidate = str(value or "").strip()
    if not candidate or not _RETURN_TO_ALLOWED.match(candidate):
        return ""
    return candidate


def _post_login_redirect(return_to: str) -> str:
    target = sanitize_return_to(return_to)
    origin = settings.frontend_origin.rstrip("/")
    return f"{origin}{target}" if target else settings.frontend_origin


@router.get("/api/auth/me")
def auth_me(user: dict[str, Any] = Depends(require_user_dependency)) -> dict[str, Any]:
    if user.get("is_active") is False:
        raise HTTPException(status_code=401, detail="Account is disabled")
    return {
        "user_id": user["user_id"],
        "email": user.get("email", ""),
        "display_name": user.get("display_name", ""),
        "avatar_url": user.get("avatar_url", ""),
        "is_admin": user.get("is_admin", False),
        "is_super_admin": user.get("is_super_admin", False),
    }


@router.get("/api/auth/google/login")
def auth_google_login(login_hint: str = "", return_to: str = ""):
    if not settings.google_client_id:
        return {"status": "error", "message": "Google OAuth not configured"}
    redirect_uri = settings.google_redirect_uri
    auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={settings.google_client_id}"
        f"&redirect_uri={redirect_uri}"
        "&response_type=code"
        "&scope=openid%20email%20profile"
        "&access_type=offline"
        "&prompt=select_account"
    )
    if login_hint:
        auth_url += f"&login_hint={login_hint}"
    target = sanitize_return_to(return_to)
    if target:
        auth_url += f"&state={urllib.parse.quote(target, safe='')}"
    return RedirectResponse(url=auth_url)


@router.get("/api/auth/google/callback")
def auth_google_callback(code: str = Query(...), state: str = Query("")):
    redirect_uri = settings.google_redirect_uri
    if not settings.google_client_id or not settings.google_client_secret:
        return {"status": "error", "message": "Google OAuth not configured"}
    try:
        user_info = exchange_google_code(code, redirect_uri)
    except Exception as e:
        return {"status": "error", "message": f"OAuth exchange failed: {e}"}

    google_id = user_info["id"]
    email = user_info.get("email", "")
    display_name = user_info.get("name", "")
    avatar_url = user_info.get("picture", "")

    existing = find_user_by_google_id(google_id)
    if existing is None and email:
        existing = find_user_by_email(email)

    if existing:
        user_id = existing["user_id"]
    else:
        user = create_user_from_google(google_id, email, display_name, avatar_url)
        user_id = user["user_id"]

    bootstrap_super_admin(get_sync_db()[COLL_USERS].find_one({"user_id": user_id}) or {})

    session_token = create_session(user_id)
    response = RedirectResponse(url=_post_login_redirect(state))
    response.set_cookie(
        key="session",
        value=session_token,
        httponly=True,
        samesite="lax",
        max_age=settings.session_expire_minutes * 60,
    )
    return response


@router.post("/api/auth/logout")
def auth_logout(session: Optional[str] = Cookie(None)):
    if session:
        delete_session(session)
    response = {"status": "ok"}
    return response


@router.get("/api/auth/status")
def auth_status(session: Optional[str] = Cookie(None)) -> dict[str, Any]:
    user = get_current_user_from_cookie(session)
    if user is None:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "user_id": user["user_id"],
        "email": user.get("email", ""),
        "display_name": user.get("display_name", ""),
        "is_super_admin": user.get("is_super_admin", False),
    }
