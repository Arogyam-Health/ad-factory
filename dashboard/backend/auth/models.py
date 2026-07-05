from __future__ import annotations

import hashlib
import secrets
import time
from typing import Any, Optional

from pydantic import BaseModel, Field


def generate_user_id() -> str:
    return "usr_" + secrets.token_hex(16)


def generate_session_token() -> str:
    return "sess_" + secrets.token_urlsafe(32)


class UserDocument(BaseModel):
    user_id: str = Field(default_factory=generate_user_id)
    email: str = ""
    display_name: str = ""
    google_id: str = ""
    avatar_url: str = ""
    is_active: bool = True
    is_admin: bool = False
    is_super_admin: bool = False
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class AuthIdentityDocument(BaseModel):
    provider: str
    provider_user_id: str
    user_id: str
    email: str = ""
    display_name: str = ""
    created_at: float = Field(default_factory=time.time)


class SessionDocument(BaseModel):
    token: str = Field(default_factory=generate_session_token)
    user_id: str
    expires_at: float
    created_at: float = Field(default_factory=time.time)

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at
