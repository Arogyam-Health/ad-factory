from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from dashboard.backend.auth.service import require_user_dependency
from dashboard.backend.services.provider_config import (
    get_provider_config,
    set_provider_config,
    delete_provider_config,
    get_all_provider_configs,
    get_decrypted_provider_key,
)

router = APIRouter()


@router.get("/api/user/provider-config")
def list_provider_configs(
    user: dict[str, Any] = Depends(require_user_dependency),
) -> list[dict[str, Any]]:
    return get_all_provider_configs(user["user_id"])


@router.get("/api/user/provider-config/{provider}")
def get_provider(
    provider: str,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    config = get_provider_config(user["user_id"], provider)
    if config is None:
        raise HTTPException(status_code=404, detail="Provider config not found")
    return config


@router.put("/api/user/provider-config/{provider}")
def save_provider(
    provider: str,
    payload: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    raw_config = payload.get("config", payload)
    return set_provider_config(user["user_id"], provider, raw_config)


@router.delete("/api/user/provider-config/{provider}")
def remove_provider(
    provider: str,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, str]:
    delete_provider_config(user["user_id"], provider)
    return {"status": "deleted"}
