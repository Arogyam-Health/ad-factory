from typing import Any, Optional
from fastapi import APIRouter, Body, Cookie, Request

from dashboard.backend.app import (
    api_batch_generate_images_45,
    api_batch_generate_images_916,
    api_batch_generate_images_both,
)
from dashboard.backend.auth.service import get_current_user_from_cookie

router = APIRouter()


def _resolve_user_id(request: Request, session: Optional[str]) -> str:
    try:
        user = get_current_user_from_cookie(session)
        return user.get("user_id", "") if user else ""
    except Exception:
        return ""


@router.post("/api/batch/generate-images-45")
def _batch_generate_45(request: Request, payload: dict[str, Any] = Body(...), session: Optional[str] = Cookie(None)) -> dict[str, Any]:
    return api_batch_generate_images_45(payload, user_id=_resolve_user_id(request, session))


@router.post("/api/batch/generate-images-916")
def _batch_generate_916(request: Request, payload: dict[str, Any] = Body(...), session: Optional[str] = Cookie(None)) -> dict[str, Any]:
    return api_batch_generate_images_916(payload, user_id=_resolve_user_id(request, session))


@router.post("/api/batch/generate-images-both")
def _batch_generate_both(request: Request, payload: dict[str, Any] = Body(...), session: Optional[str] = Cookie(None)) -> dict[str, Any]:
    return api_batch_generate_images_both(payload, user_id=_resolve_user_id(request, session))
