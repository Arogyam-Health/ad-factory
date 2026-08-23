from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from dashboard.backend.auth.service import require_user_dependency
router = APIRouter()


def _local_only(
    user: dict[str, Any] = Depends(require_user_dependency),
) -> None:
    del user
    raise HTTPException(
        status_code=410,
        detail="JSON configuration resources are stored on the paired localhost device",
    )


router.add_api_route("/api/user/json-blobs", _local_only, methods=["GET"])
router.add_api_route(
    "/api/user/json-blobs/{blob_type}",
    _local_only,
    methods=["GET", "PUT", "DELETE"],
)
router.add_api_route(
    "/api/user/json-blobs/bootstrap", _local_only, methods=["POST"]
)
