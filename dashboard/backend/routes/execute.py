from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, File, Form, Request, UploadFile

from dashboard.backend.app import RUNS_ROOT, api_run_execute
from dashboard.backend.chatgpt_runtime_patch import install_chatgpt_watchdog
from dashboard.backend.db.settings import settings
from dashboard.backend.reference_library import (
    api_delete_reference_image,
    api_reference_images,
    api_upload_reference_images,
)
from dashboard.backend.reference_workspace import (
    api_delete_reference_product_image,
    api_save_reference_starting_prompt,
    api_upload_reference_product_doc,
    api_upload_reference_product_images,
)
from dashboard.backend.reference_workspace_v2 import (
    api_reference_workspace_v2,
    api_run_execute_reference_workspace_v2,
)

install_chatgpt_watchdog()
router = APIRouter()


def _resolve_user_id(request: Request) -> str:
    if settings.is_production:
        user = getattr(request.state, "user", None)
        if user:
            return user["user_id"]
    return "dev_user"


def _latest_reference_job_error(run_id: str) -> str:
    job_dir = RUNS_ROOT / run_id / "context" / "active_jobs"
    if not job_dir.exists():
        return ""
    candidates = sorted(
        job_dir.glob("*.result.json"),
        key=lambda path: path.stat().st_mtime if path.exists() else 0,
        reverse=True,
    )
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        output = str(payload.get("stderr") or payload.get("stdout") or "").strip()
        if output:
            return output[-2400:]
    return ""


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


@router.get("/api/reference-images")
def _reference_images(request: Request) -> dict[str, Any]:
    _resolve_user_id(request)
    return api_reference_images()


@router.post("/api/reference-images")
async def _upload_reference_images(request: Request, files: list[UploadFile] = File(...)) -> dict[str, Any]:
    _resolve_user_id(request)
    return await api_upload_reference_images(files)


@router.delete("/api/reference-images")
def _delete_reference_image(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    _resolve_user_id(request)
    return api_delete_reference_image(payload)


@router.get("/api/reference-workspace")
def _reference_workspace(request: Request) -> dict[str, Any]:
    _resolve_user_id(request)
    return api_reference_workspace_v2()


@router.post("/api/reference-workspace/product-images")
async def _upload_reference_product_images(
    request: Request,
    files: list[UploadFile] = File(...),
    replace: bool = Form(False),
) -> dict[str, Any]:
    _resolve_user_id(request)
    return await api_upload_reference_product_images(files, replace=replace)


@router.delete("/api/reference-workspace/product-images")
def _delete_reference_product_image(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    _resolve_user_id(request)
    return api_delete_reference_product_image(payload)


@router.post("/api/reference-workspace/product-document")
async def _upload_reference_product_document(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    _resolve_user_id(request)
    return await api_upload_reference_product_doc(file)


@router.post("/api/reference-workspace/starting-prompt")
def _save_reference_starting_prompt(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    _resolve_user_id(request)
    return api_save_reference_starting_prompt(payload)


@router.post("/api/runs/execute-reference")
async def _run_execute_reference(
    request: Request,
    config: str = Form(...),
    reference_image_files: list[UploadFile] | None = File(None),
    product_info_file: UploadFile | None = File(None),
    input_image_files: list[UploadFile] | None = File(None),
    clear_input_images: bool = Form(False),
) -> dict[str, Any]:
    _resolve_user_id(request)
    return await api_run_execute_reference_workspace_v2(
        config=config,
        reference_image_files=reference_image_files,
        product_info_file=product_info_file,
        input_image_files=input_image_files,
        clear_input_images=clear_input_images,
    )


@router.get("/api/runs/{run_id}/reference-status")
def _reference_run_status(request: Request, run_id: str) -> dict[str, Any]:
    _resolve_user_id(request)
    from dashboard.backend.reference_flow import api_reference_run_status

    status = api_reference_run_status(run_id)
    if status.get("status") == "error":
        detail = _latest_reference_job_error(run_id)
        if detail:
            status["job_error"] = detail
            status["error"] = detail
            status["message"] = f"Reference job failed: {detail}"
    return status
