from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from dashboard.backend.auth.service import require_user_dependency
from dashboard.backend.services.user_config import (
    delete_user_config,
    get_user_config,
    has_custom_config,
    parse_expected_version,
    extract_config_files,
    set_user_config,
    validate_config_files,
    ConfigVersionConflict,
)

router = APIRouter()


@router.get("/api/user/config")
def read_config(
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    return {
        "config": get_user_config(user["user_id"]),
        "has_custom": has_custom_config(user["user_id"]),
    }


@router.put("/api/user/config")
def save_config(
    payload: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    try:
        config = validate_config_files(extract_config_files(payload))
        updated = set_user_config(
            user["user_id"],
            config,
            actor_user_id=user["user_id"],
            expected_version=parse_expected_version(payload),
        )
    except ConfigVersionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "config_version_conflict",
                "current_version": exc.current_version,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to save config") from exc
    return {"status": "ok", "config": updated}


@router.delete("/api/user/config")
def clear_config(
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, str]:
    try:
        delete_user_config(user["user_id"])
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to delete config") from exc
    return {"status": "deleted"}
