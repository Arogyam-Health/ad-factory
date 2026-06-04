from typing import Any
from fastapi import APIRouter

from dashboard.backend.app import api_progress

router = APIRouter()

@router.get("/api/progress/{batch_key}")
def _progress(batch_key: str) -> dict[str, Any]:
    return api_progress(batch_key)
