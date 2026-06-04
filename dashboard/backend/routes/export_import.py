from typing import Any
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from dashboard.backend.app import (
    api_export_on_image_copy,
    api_import_on_image_copy,
    api_file_content,
)

router = APIRouter()

@router.get("/api/runs/{run_id}/export-on-image-copy")
def _export_on_image_copy(run_id: str) -> StreamingResponse:
    return api_export_on_image_copy(run_id)

@router.post("/api/runs/{run_id}/import-on-image-copy")
async def _import_on_image_copy(
    run_id: str,
    file: UploadFile = File(...),
    confirm: bool = False,
) -> dict[str, Any]:
    return await api_import_on_image_copy(run_id, file, confirm)

@router.get("/api/file-content")
def _file_content(path: str, max_lines: int = 400) -> dict[str, Any]:
    return api_file_content(path, max_lines)
