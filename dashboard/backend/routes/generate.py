from typing import Any
from fastapi import APIRouter, Body

from dashboard.backend.app import (
    api_run_generate_916,
    api_run_generate_916_selected,
    api_run_generate_images_45,
    api_run_generate_images_916_from_45,
)

router = APIRouter()

@router.post("/api/runs/{run_id}/generate-916")
def _generate_916(run_id: str) -> dict[str, Any]:
    return api_run_generate_916(run_id)

@router.post("/api/runs/{run_id}/generate-916-selected")
def _generate_916_selected(run_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return api_run_generate_916_selected(run_id, payload)

@router.post("/api/runs/{run_id}/generate-images-45")
def _generate_images_45(run_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return api_run_generate_images_45(run_id, payload)

@router.post("/api/runs/{run_id}/generate-images-916-from-45")
def _generate_images_916_from_45(run_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return api_run_generate_images_916_from_45(run_id, payload)
