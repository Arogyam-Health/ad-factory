from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from dashboard.backend.auth.service import require_user_dependency
from dashboard.backend.services.json_blobs import (
    clone_default_blobs_to_user,
    get_json_blob,
    set_json_blob,
    delete_json_blob,
    list_json_blobs,
)

router = APIRouter()


@router.get("/api/user/json-blobs")
def list_blobs(
    blob_type: Optional[str] = Query(None),
    user: dict[str, Any] = Depends(require_user_dependency),
) -> list[dict[str, Any]]:
    return list_json_blobs(user["user_id"], blob_type)


@router.get("/api/user/json-blobs/{blob_type}")
def get_blob(
    blob_type: str,
    name: str = Query("default"),
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    data = get_json_blob(user["user_id"], blob_type, name)
    if data is None:
        raise HTTPException(status_code=404, detail="Blob not found")
    return {"blob_type": blob_type, "name": name, "data": data}


@router.put("/api/user/json-blobs/{blob_type}")
def save_blob(
    blob_type: str,
    payload: dict[str, Any] = Body(...),
    name: str = Query("default"),
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    data = payload.get("data", payload)
    return set_json_blob(user["user_id"], blob_type, data, name)


@router.delete("/api/user/json-blobs/{blob_type}")
def remove_blob(
    blob_type: str,
    name: str = Query("default"),
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, str]:
    delete_json_blob(user["user_id"], blob_type, name)
    return {"status": "deleted"}


@router.post("/api/user/json-blobs/bootstrap")
def bootstrap_blobs(
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    count = clone_default_blobs_to_user(user["user_id"])
    return {"cloned": count}
