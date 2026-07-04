from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError

from dashboard.backend.db.settings import settings

_sync_client: MongoClient | None = None
_async_client: AsyncIOMotorClient | None = None


def get_sync_client() -> MongoClient:
    global _sync_client
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
