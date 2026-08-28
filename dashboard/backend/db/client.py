from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError

from dashboard.backend.db.settings import settings

_sync_client: MongoClient | None = None  # type: ignore[assignment]
_async_client: AsyncIOMotorClient | None = None
_mock_sync_client: MongoClient | None = None  # type: ignore[assignment]


def _should_use_mock() -> bool:
    # Use in-memory mongomock for local dev when no Mongo is reachable.
    # Keeps Linux/Render behavior unchanged (production uses real Atlas).
    return not settings.is_production and "localhost" in settings.mongodb_uri


def get_sync_client() -> MongoClient:
    global _sync_client, _mock_sync_client
    if _should_use_mock():
        if _mock_sync_client is not None:
            return _mock_sync_client  # type: ignore[return-value]
        try:
            # Try real Mongo first; fall back to mongomock if unreachable
            client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=1500)
            client.admin.command("ping")
            _sync_client = client
            return _sync_client
        except Exception:
            try:
                import mongomock  # type: ignore

                _mock_sync_client = mongomock.MongoClient()  # type: ignore[assignment]
                print("[db] Using mongomock (in-memory) for local dev — MongoDB not reachable", flush=True)
                return _mock_sync_client  # type: ignore[return-value]
            except ImportError:
                pass
    if _sync_client is None:
        _sync_client = MongoClient(
            settings.mongodb_uri,
            serverSelectionTimeoutMS=5000,
        )
    return _sync_client


def get_sync_db() -> Any:
    return get_sync_client()[settings.mongodb_db_name]


def get_async_client() -> AsyncIOMotorClient:
    global _async_client
    if _should_use_mock():
        # For dev with mongomock we reuse sync mock for async paths via sync client
        # (async routes that need DB will use get_sync_db in dev)
        if _async_client is None:
            _async_client = AsyncIOMotorClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000)  # type: ignore[assignment]
        return _async_client
    if _async_client is None:
        _async_client = AsyncIOMotorClient(
            settings.mongodb_uri,
            serverSelectionTimeoutMS=5000,
        )
    return _async_client


def get_async_db() -> Any:
    return get_async_client()[settings.mongodb_db_name]


def ping() -> bool:
    try:
        get_sync_client().admin.command("ping")
        return True
    except ServerSelectionTimeoutError:
        return False


def close_sync() -> None:
    global _sync_client
    if _sync_client:
        _sync_client.close()
        _sync_client = None


def close_async() -> None:
    global _async_client
    if _async_client:
        _async_client.close()
        _async_client = None
