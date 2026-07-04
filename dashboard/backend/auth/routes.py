from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

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


@router.get("/api/auth/me")
def auth_me(user: dict[str, Any] = Depends(require_user_dependency)) -> dict[str, Any]:
    return {
        "user_id": user["user_id"],
        "email": user.get("email", ""),
        "display_name": user.get("display_name", ""),
        "avatar_url": user.get("avatar_url", ""),
        "is_admin": user.get("is_admin", False),
    }


@router.get("/api/auth/google/login")
def auth_google_login():
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
    )
    return RedirectResponse(url=auth_url)


@router.get("/api/auth/google/callback")
def auth_google_callback(code: str = Query(...)):
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

    session_token = create_session(user_id)
    response = RedirectResponse(url=settings.frontend_origin)
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
    }
