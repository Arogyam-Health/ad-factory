from typing import Any
from fastapi import APIRouter, Body

from dashboard.backend.app import (
    api_batch_generate_images_45,
    api_batch_generate_images_916,
    api_batch_generate_images_both,
)

router = APIRouter()

@router.post("/api/batch/generate-images-45")
def _batch_generate_45(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return api_batch_generate_images_45(payload)

@router.post("/api/batch/generate-images-916")
def _batch_generate_916(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return api_batch_generate_images_916(payload)

@router.post("/api/batch/generate-images-both")
def _batch_generate_both(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return api_batch_generate_images_both(payload)
