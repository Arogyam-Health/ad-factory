from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Response

from dashboard.backend.auth.service import require_user_dependency
from dashboard.backend.services.provider_config import (
    delete_provider_config,
    get_all_provider_configs,
    get_materialized_provider_config,
    get_provider_config,
    set_provider_config,
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
    try:
        config = get_provider_config(user["user_id"], provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if config is None:
        raise HTTPException(status_code=404, detail="Provider config not found")
    return config


@router.put("/api/user/provider-config/{provider}")
def save_provider(
    provider: str,
    payload: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    try:
        return set_provider_config(
            user["user_id"], provider, payload.get("config", payload)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/user/provider-config/{provider}")
def remove_provider(
    provider: str,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, str]:
    try:
        delete_provider_config(user["user_id"], provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "deleted"}


@router.post("/api/user/provider-config/{provider}/materialize")
def materialize_provider(
    provider: str,
    response: Response,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, str]:
    """Return an owner-scoped secret once for transfer to local execution."""
    try:
        config = get_materialized_provider_config(user["user_id"], provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if config is None:
        raise HTTPException(status_code=404, detail="Provider config not found")
    response.headers["Cache-Control"] = "no-store"
    return config


@router.get("/api/user/provider-config/opencode/catalog")
def user_opencode_catalog(
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    from dashboard.backend.app import build_opencode_catalog

    return build_opencode_catalog()
