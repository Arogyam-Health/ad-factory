from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Settings:
    mongodb_uri: str = field(default_factory=lambda: os.getenv(
        "MONGODB_URI", "mongodb://localhost:27017/ad-factory?retryWrites=true"
    ))
    mongodb_db_name: str = field(default_factory=lambda: os.getenv(
        "MONGODB_DB_NAME", "ad_factory"
    ))
    app_secret_key: str = field(default_factory=lambda: os.getenv(
        "APP_SECRET_KEY", "change-me-in-production-use-a-real-secret"
    ))
    encryption_key: str = field(default_factory=lambda: os.getenv(
        "ENCRYPTION_KEY", "change-me-32-char-minimum-encryption-key!"
    ))
    google_client_id: str = field(default_factory=lambda: os.getenv(
        "GOOGLE_CLIENT_ID", ""
    ))
    google_client_secret: str = field(default_factory=lambda: os.getenv(
        "GOOGLE_CLIENT_SECRET", ""
    ))
    google_redirect_uri: str = field(default_factory=lambda: os.getenv(
        "GOOGLE_REDIRECT_URI", "http://localhost:4090/api/auth/google/callback"
    ))
    frontend_origin: str = field(default_factory=lambda: os.getenv(
        "FRONTEND_ORIGIN", "http://localhost:4090"
    ))
    cors_origins: list[str] = field(default_factory=lambda: [
        x.strip() for x in os.getenv("CORS_ORIGINS", "http://localhost:4090,http://127.0.0.1:4090").split(",") if x.strip()
    ])
    session_expire_minutes: int = int(os.getenv("SESSION_EXPIRE_MINUTES", "1440"))
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
    password_hash_rounds: int = 12

    @property
    def encryption_key_bytes(self) -> bytes:
        key = self.encryption_key
        if len(key) < 32:
            key = key.ljust(32, "x")
        return key[:32].encode("utf-8")


settings = Settings()
