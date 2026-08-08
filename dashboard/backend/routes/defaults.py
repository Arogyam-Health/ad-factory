from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


def _local_only() -> None:
    raise HTTPException(
        status_code=410,
        detail="Configuration and content are available only through the paired localhost data plane",
    )


for _path, _methods in (
    ("/api/defaults", ["GET"]),
    ("/api/opencode/catalog", ["GET"]),
    ("/api/input-images", ["DELETE"]),
    ("/api/upload-input-images", ["POST"]),
    ("/api/product-doc", ["GET", "POST"]),
    ("/api/prompt-file-content", ["GET", "POST"]),
    ("/api/input-prompt", ["GET", "POST"]),
    ("/api/config/provider", ["POST"]),
    ("/api/google/models", ["GET"]),
):
    router.add_api_route(_path, _local_only, methods=_methods)
