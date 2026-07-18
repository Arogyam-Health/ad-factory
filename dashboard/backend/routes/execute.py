from typing import Any
from fastapi import APIRouter, File, Form, UploadFile

from dashboard.backend.app import api_run_execute
from dashboard.backend.reference_flow import api_reference_run_status, api_run_execute_reference

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


@router.post("/api/runs/execute-reference")
async def _run_execute_reference(
    config: str = Form(...),
    reference_image_files: list[UploadFile] = File(...),
    product_info_file: UploadFile | None = File(None),
    input_image_files: list[UploadFile] | None = File(None),
    clear_input_images: bool = Form(False),
) -> dict[str, Any]:
    return await api_run_execute_reference(
        config=config,
        reference_image_files=reference_image_files,
        product_info_file=product_info_file,
        input_image_files=input_image_files,
        clear_input_images=clear_input_images,
    )


@router.get("/api/runs/{run_id}/reference-status")
def _reference_run_status(run_id: str) -> dict[str, Any]:
    return api_reference_run_status(run_id)
