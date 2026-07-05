from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from dashboard.backend.auth.service import require_user_dependency
from dashboard.backend.services.user_config import (
    get_user_config,
    set_user_config,
    delete_user_config,
    has_custom_config,
)

router = APIRouter()


@router.get("/api/user/config")
def read_config(
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    config = get_user_config(user["user_id"])
    return {
        "config": config,
        "has_custom": has_custom_config(user["user_id"]),
    }


@router.put("/api/user/config")
def save_config(
    payload: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    config = payload.get("config", payload)
    try:
        updated = set_user_config(user["user_id"], config)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save config: {exc}")
    return {"status": "ok", "config": updated}


@router.delete("/api/user/config")
def clear_config(
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, str]:
    try:
        delete_user_config(user["user_id"])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete config: {exc}")
    return {"status": "deleted"}
