from fastapi import APIRouter, HTTPException

router = APIRouter()


def _local_only(run_id: str) -> None:
    del run_id
    raise HTTPException(
        status_code=410,
        detail=(
            "Image generation, including selected prompts, is available only "
            "through the paired localhost data plane"
        ),
    )


for _suffix in (
    "generate-916",
    "generate-916-selected",
    "generate-images-45",
    "generate-images-916-from-45",
):
    router.add_api_route(
        f"/api/runs/{{run_id}}/{_suffix}", _local_only, methods=["POST"]
    )
