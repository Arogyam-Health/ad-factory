from typing import Any
from fastapi import APIRouter, File, Form, Request, UploadFile

from dashboard.backend.app import api_run_execute
from dashboard.backend.db.settings import settings

router = APIRouter()

def _resolve_user_id(request: Request) -> str:
    if settings.is_production:
        user = getattr(request.state, "user", None)
        if user:
            return user["user_id"]
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
    )
