from typing import Any
from fastapi import APIRouter

from dashboard.backend.app import api_kill_chrome, api_launch_visible_browser, api_stop_generation

router = APIRouter()

@router.post("/api/launch-visible-browser")
def _launch_visible_browser() -> dict[str, Any]:
    return api_launch_visible_browser()


@router.post("/api/kill-chrome")
def _kill_chrome() -> dict[str, Any]:
    return api_kill_chrome()


@router.post("/api/stop-generation")
def _stop_generation() -> dict[str, Any]:
    return api_stop_generation()
