from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEPLOYMENT_DEV = "development"
DEPLOYMENT_PROD = "production"


@dataclass
class Settings:
    deployment_mode: str = field(default_factory=lambda: os.getenv(
        "DEPLOYMENT_MODE", DEPLOYMENT_DEV
    ))
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
    storage_provider: str = field(default_factory=lambda: os.getenv(
        "STORAGE_PROVIDER", "local"
    ))
    super_admin_emails: str = field(default_factory=lambda: os.getenv(
        "SUPER_ADMIN_EMAILS", ""
    ))

    @property
    def is_production(self) -> bool:
        return self.deployment_mode == DEPLOYMENT_PROD

    @property
    def is_dev(self) -> bool:
        return self.deployment_mode == DEPLOYMENT_DEV

    @property
    def encryption_key_bytes(self) -> bytes:
        key = self.encryption_key
        if len(key) < 32:
            key = key.ljust(32, "x")
        return key[:32].encode("utf-8")


settings = Settings()


def validate_production_settings() -> None:
    if not settings.is_production:
        return
    errors: list[str] = []
    if not settings.mongodb_uri or settings.mongodb_uri.startswith("mongodb://localhost"):
        errors.append("MONGODB_URI must point to a remote MongoDB (not localhost) in production")
    if not settings.app_secret_key or "change-me" in settings.app_secret_key.lower():
        errors.append("APP_SECRET_KEY must be changed from the default in production")
    if not settings.encryption_key or "change-me" in settings.encryption_key.lower():
        errors.append("ENCRYPTION_KEY must be changed from the default in production")
    if not settings.google_client_id or not settings.google_client_secret:
        errors.append("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set in production")
    if not settings.google_redirect_uri or "localhost" in settings.google_redirect_uri.lower():
        errors.append("GOOGLE_REDIRECT_URI must be set to the production URL in production")
    origins = settings.cors_origins
    if any("*" in o for o in origins):
        errors.append("CORS_ORIGINS must not contain wildcard in production")
    if not origins:
        errors.append("CORS_ORIGINS must be set in production")
    if errors:
        print("[startup] PRODUCTION VALIDATION FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    print("[startup] Production settings validated OK")
