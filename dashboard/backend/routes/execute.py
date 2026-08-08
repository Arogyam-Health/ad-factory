from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from dashboard.backend.app import api_run_execute
from dashboard.backend.auth.service import get_current_user_from_cookie
from dashboard.backend.chatgpt_runtime_patch import install_chatgpt_watchdog
from dashboard.backend.db.settings import settings

install_chatgpt_watchdog()
router = APIRouter()


def _resolve_user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user:
        return user["user_id"]
    session_token = request.cookies.get("session")
    user = get_current_user_from_cookie(session_token)
    if user:
        return user["user_id"]
    if settings.is_production:
        return ""
    return "dev_user"


@router.post("/api/runs/execute")
async def _run_execute(
    request: Request,
    config: str = Form(...),
    product_info_file: UploadFile | None = File(None),
    mechanism_file: UploadFile | None = File(None),
    faq_file: UploadFile | None = File(None),
    image_source_file: UploadFile | None = File(None),
    input_image_files: list[UploadFile] | None = File(None),
    clear_input_images: bool = Form(False),
    org_id: str = Form(""),
) -> dict[str, Any]:
    user_id = _resolve_user_id(request)
    return await api_run_execute(
        config=config,
        product_info_file=product_info_file,
        mechanism_file=mechanism_file,
        faq_file=faq_file,
        image_source_file=image_source_file,
        input_image_files=input_image_files,
        clear_input_images=clear_input_images,
        user_id=user_id,
        org_id=org_id,
    )


def _reference_local_only() -> None:
    raise HTTPException(
        status_code=410,
        detail="Reference content and execution are available only through the paired local agent",
    )


@router.api_route("/api/reference-images", methods=["GET", "POST", "DELETE"])
def _disabled_reference_images() -> None:
    _reference_local_only()


@router.api_route("/api/reference-workspace", methods=["GET"])
@router.api_route(
    "/api/reference-workspace/{legacy_path:path}",
    methods=["GET", "POST", "PUT", "DELETE"],
)
def _disabled_reference_workspace(legacy_path: str = "") -> None:
    del legacy_path
    _reference_local_only()


@router.post("/api/runs/execute-reference")
def _disabled_reference_execution() -> None:
    _reference_local_only()


@router.get("/api/runs/{run_id}/reference-status")
def _disabled_reference_status(run_id: str) -> None:
    del run_id
    _reference_local_only()
