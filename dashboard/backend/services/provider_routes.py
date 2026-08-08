from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from dashboard.backend.auth.service import require_user_dependency
router = APIRouter()


@router.get("/api/user/provider-config")
def list_provider_configs(
    user: dict[str, Any] = Depends(require_user_dependency),
) -> list[dict[str, Any]]:
    return []


@router.get("/api/user/provider-config/{provider}")
def get_provider(
    provider: str,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    raise HTTPException(
        status_code=410,
        detail="Provider config is available only from the paired localhost device",
    )


@router.put("/api/user/provider-config/{provider}")
def save_provider(
    provider: str,
    payload: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    raise HTTPException(
        status_code=410,
        detail="Write provider config directly to the paired localhost device",
    )


@router.delete("/api/user/provider-config/{provider}")
def remove_provider(
    provider: str,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, str]:
    raise HTTPException(
        status_code=410,
        detail="Delete provider config directly from the paired localhost device",
    )


@router.get("/api/user/provider-config/opencode/catalog")
def user_opencode_catalog(
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    from dashboard.backend.app import build_opencode_catalog

    return build_opencode_catalog()
