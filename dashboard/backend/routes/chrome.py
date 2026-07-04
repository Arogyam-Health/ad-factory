from typing import Any
from fastapi import APIRouter, HTTPException

from dashboard.backend.app import api_kill_chrome, api_launch_visible_browser, api_stop_generation
from dashboard.backend.db.settings import settings

router = APIRouter()


def _production_disabled(endpoint_name: str) -> None:
    if settings.is_production:
        raise HTTPException(
            status_code=400,
            detail=f"{endpoint_name} is disabled in production mode. "
                   f"Run a local agent instead (python scripts/local_agent.py).",
        )


@router.post("/api/launch-visible-browser")
def _launch_visible_browser() -> dict[str, Any]:
    _production_disabled("launch-visible-browser")
    return api_launch_visible_browser()


@router.post("/api/kill-chrome")
def _kill_chrome() -> dict[str, Any]:
    _production_disabled("kill-chrome")
    return api_kill_chrome()


@router.post("/api/stop-generation")
def _stop_generation() -> dict[str, Any]:
    _production_disabled("stop-generation")
    return api_stop_generation()
