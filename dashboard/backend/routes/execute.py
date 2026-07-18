from typing import Any
from fastapi import APIRouter, Body, File, Form, UploadFile

from dashboard.backend.app import api_run_execute
from dashboard.backend.chatgpt_runtime_patch import install_chatgpt_watchdog
from dashboard.backend.reference_flow import api_reference_run_status
from dashboard.backend.reference_library import (
    api_delete_reference_image,
    api_reference_images,
    api_upload_reference_images,
)
from dashboard.backend.reference_workspace import (
    api_delete_reference_product_image,
    api_reference_workspace,
    api_run_execute_reference_workspace,
    api_save_reference_starting_prompt,
    api_upload_reference_product_doc,
    api_upload_reference_product_images,
)

install_chatgpt_watchdog()
router = APIRouter()


@router.post("/api/runs/execute")
async def _run_execute(
    config: str = Form(...),
    product_info_file: UploadFile | None = File(None),
    mechanism_file: UploadFile | None = File(None),
    faq_file: UploadFile | None = File(None),
    image_source_file: UploadFile | None = File(None),
    input_image_files: list[UploadFile] | None = File(None),
    clear_input_images: bool = Form(False),
) -> dict[str, Any]:
    return await api_run_execute(
        config=config,
        product_info_file=product_info_file,
        mechanism_file=mechanism_file,
        faq_file=faq_file,
        image_source_file=image_source_file,
        input_image_files=input_image_files,
        clear_input_images=clear_input_images,
    )


@router.get("/api/reference-images")
def _reference_images() -> dict[str, Any]:
    return api_reference_images()


@router.post("/api/reference-images")
async def _upload_reference_images(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    return await api_upload_reference_images(files)


@router.delete("/api/reference-images")
def _delete_reference_image(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return api_delete_reference_image(payload)


@router.get("/api/reference-workspace")
def _reference_workspace() -> dict[str, Any]:
    return api_reference_workspace()


@router.post("/api/reference-workspace/product-images")
async def _upload_reference_product_images(
    files: list[UploadFile] = File(...),
    replace: bool = Form(False),
) -> dict[str, Any]:
    return await api_upload_reference_product_images(files, replace=replace)


@router.delete("/api/reference-workspace/product-images")
def _delete_reference_product_image(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return api_delete_reference_product_image(payload)


@router.post("/api/reference-workspace/product-document")
async def _upload_reference_product_document(file: UploadFile = File(...)) -> dict[str, Any]:
    return await api_upload_reference_product_doc(file)


@router.post("/api/reference-workspace/starting-prompt")
def _save_reference_starting_prompt(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return api_save_reference_starting_prompt(payload)


@router.post("/api/runs/execute-reference")
async def _run_execute_reference(
    config: str = Form(...),
    reference_image_files: list[UploadFile] | None = File(None),
    product_info_file: UploadFile | None = File(None),
    input_image_files: list[UploadFile] | None = File(None),
    clear_input_images: bool = Form(False),
) -> dict[str, Any]:
    return await api_run_execute_reference_workspace(
        config=config,
        reference_image_files=reference_image_files,
        product_info_file=product_info_file,
        input_image_files=input_image_files,
        clear_input_images=clear_input_images,
    )


@router.get("/api/runs/{run_id}/reference-status")
def _reference_run_status(run_id: str) -> dict[str, Any]:
    return api_reference_run_status(run_id)
