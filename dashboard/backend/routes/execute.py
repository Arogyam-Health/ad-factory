from fastapi import APIRouter, HTTPException

router = APIRouter()


def _local_only() -> None:
    raise HTTPException(
        status_code=410,
        detail="Content and execution are available only through the paired localhost data plane",
    )


router.add_api_route("/api/runs/execute", _local_only, methods=["POST"])


@router.api_route("/api/reference-images", methods=["GET", "POST", "DELETE"])
def _disabled_reference_images() -> None:
    _local_only()


@router.api_route("/api/reference-workspace", methods=["GET"])
@router.api_route(
    "/api/reference-workspace/{legacy_path:path}",
    methods=["GET", "POST", "PUT", "DELETE"],
)
def _disabled_reference_workspace(legacy_path: str = "") -> None:
    del legacy_path
    _local_only()


@router.post("/api/runs/execute-reference")
def _disabled_reference_execution() -> None:
    _local_only()


@router.get("/api/runs/{run_id}/reference-status")
def _disabled_reference_status(run_id: str) -> None:
    del run_id
    _local_only()
