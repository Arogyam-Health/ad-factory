from fastapi import APIRouter, HTTPException

router = APIRouter()

def _local_only(run_id: str = "") -> None:
    del run_id
    raise HTTPException(
        status_code=410,
        detail="Import, export, and file content are available only through localhost",
    )


router.add_api_route(
    "/api/runs/{run_id}/export-on-image-copy", _local_only, methods=["GET"]
)
router.add_api_route(
    "/api/runs/{run_id}/import-on-image-copy", _local_only, methods=["POST"]
)
router.add_api_route("/api/file-content", _local_only, methods=["GET"])
