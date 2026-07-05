from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, Optional

from fastapi import Cookie, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from dashboard.backend.auth.models import (
    AuthIdentityDocument,
    SessionDocument,
    UserDocument,
    generate_session_token,
    generate_user_id,
)
from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import (
    COLL_USERS,
    COLL_AUTH_IDENTITIES,
    COLL_SESSIONS,
)
from dashboard.backend.db.settings import settings
from dashboard.backend.security.crypto import hash_token


GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


def find_user_by_id(user_id: str) -> Optional[dict[str, Any]]:
    return get_sync_db()[COLL_USERS].find_one({"user_id": user_id})


def find_user_by_google_id(google_id: str) -> Optional[dict[str, Any]]:
    return get_sync_db()[COLL_USERS].find_one({"google_id": google_id})


def find_user_by_email(email: str) -> Optional[dict[str, Any]]:
    return get_sync_db()[COLL_USERS].find_one({"email": email})


def create_user_from_google(google_id: str, email: str, display_name: str, avatar_url: str = "") -> dict[str, Any]:
    now = time.time()
    user_doc = {
        "user_id": generate_user_id(),
        "email": email,
        "display_name": display_name,
        "google_id": google_id,
        "avatar_url": avatar_url,
        "is_active": True,
        "is_admin": False,
        "created_at": now,
        "updated_at": now,
    }
    get_sync_db()[COLL_USERS].insert_one(user_doc)
    _create_auth_identity("google", google_id, user_doc["user_id"], email, display_name)
    return user_doc


def _create_auth_identity(provider: str, provider_user_id: str, user_id: str, email: str = "", display_name: str = "") -> dict[str, Any]:
    doc = {
        "provider": provider,
        "provider_user_id": provider_user_id,
        "user_id": user_id,
        "email": email,
        "display_name": display_name,
        "created_at": time.time(),
    }
    get_sync_db()[COLL_AUTH_IDENTITIES].insert_one(doc)
    return doc


def create_session(user_id: str, expire_minutes: int = 0) -> str:
    if expire_minutes <= 0:
        expire_minutes = settings.session_expire_minutes
    token = generate_session_token()
    token_hash = hash_token(token)
    doc = {
        "token": token_hash,
        "user_id": user_id,
        "expires_at": time.time() + expire_minutes * 60,
        "created_at": time.time(),
    }
    get_sync_db()[COLL_SESSIONS].insert_one(doc)
    return token


def find_session_by_token(token: str) -> Optional[dict[str, Any]]:
    token_hash = hash_token(token)
    session = get_sync_db()[COLL_SESSIONS].find_one({"token": token_hash})
    if session is None:
        return None
    if session["expires_at"] < time.time():
        get_sync_db()[COLL_SESSIONS].delete_one({"token": token_hash})
        return None
    return session


def delete_session(token: str) -> None:
    token_hash = hash_token(token)
    get_sync_db()[COLL_SESSIONS].delete_one({"token": token_hash})


def delete_all_user_sessions(user_id: str) -> None:
    get_sync_db()[COLL_SESSIONS].delete_many({"user_id": user_id})


def exchange_google_code(code: str, redirect_uri: str) -> dict[str, Any]:
    data = {
        "code": code,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    req = urllib.request.Request(
        GOOGLE_TOKEN_URL,
        data=urllib.parse.urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req) as resp:
        token_data = json.loads(resp.read())

    access_token = token_data["access_token"]
    user_req = urllib.request.Request(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(user_req) as resp:
        user_info = json.loads(resp.read())

    return user_info


def get_current_user_from_cookie(session_token: Optional[str] = None) -> Optional[dict[str, Any]]:
    if not session_token:
        return None
    session = find_session_by_token(session_token)
    if session is None:
        return None
    return find_user_by_id(session["user_id"])


def require_user(session_token: Optional[str] = None) -> dict[str, Any]:
    user = get_current_user_from_cookie(session_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_user_dependency(session: Optional[str] = Cookie(None)) -> dict[str, Any]:
    user = require_user(session)
    if user.get("is_active") is False:
        raise HTTPException(status_code=403, detail="User account is disabled")
    return user
