from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Body, Depends, HTTPException, Response

from dashboard.backend.auth.service import require_user_dependency
from dashboard.backend.services.opencode_catalog import (
    build_opencode_catalog,
    choose_saved_or_default,
    list_opencode_models,
    with_default_opencode_model,
)
from dashboard.backend.services.provider_config import (
    delete_provider_config,
    get_all_provider_configs,
    get_materialized_provider_config,
    get_decrypted_provider_key,
    get_provider_config,
    set_provider_config,
)
router = APIRouter()


def _safe_catalog_url(value: str) -> bool:
    """Allow model discovery only against public HTTPS endpoints."""
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        return False
    if host == "opencode.ai" or host.endswith(".opencode.ai"):
        return True
    try:
        addresses = {
            result[4][0]
            for result in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
        }
        return bool(addresses) and all(
            ipaddress.ip_address(address).is_global for address in addresses
        )
    except (OSError, ValueError):
        return False


@router.get("/api/user/provider-config")
def list_provider_configs(
    user: dict[str, Any] = Depends(require_user_dependency),
) -> list[dict[str, Any]]:
    return get_all_provider_configs(user["user_id"])


@router.get("/api/user/provider-config/{provider}")
def get_provider(
    provider: str,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    try:
        config = get_provider_config(user["user_id"], provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if config is None:
        raise HTTPException(status_code=404, detail="Provider config not found")
    return config


@router.put("/api/user/provider-config/{provider}")
def save_provider(
    provider: str,
    payload: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    try:
        return set_provider_config(
            user["user_id"], provider, payload.get("config", payload)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/user/provider-config/{provider}")
def remove_provider(
    provider: str,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, str]:
    try:
        delete_provider_config(user["user_id"], provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "deleted"}


@router.post("/api/user/provider-config/{provider}/materialize")
def materialize_provider(
    provider: str,
    response: Response,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, str]:
    """Return an owner-scoped secret once for transfer to local execution."""
    try:
        config = get_materialized_provider_config(user["user_id"], provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if config is None:
        raise HTTPException(status_code=404, detail="Provider config not found")
    response.headers["Cache-Control"] = "no-store"
    return config


@router.get("/api/user/provider-config/opencode/catalog")
def user_opencode_catalog(
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    config = get_provider_config(user["user_id"], "opencode")
    if config is None:
        return build_opencode_catalog()

    saved = config["config"]
    api_url = str(saved.get("api_url") or "").strip()
    saved_model = str(saved.get("default_model") or "").strip()
    models: list[str] = []
    if api_url and _safe_catalog_url(api_url):
        api_key = get_decrypted_provider_key(
            user["user_id"], "opencode", "api_key"
        )
        models = list_opencode_models(api_url=api_url, api_key=api_key)

    normalized_saved_model = (
        f"opencode/{saved_model}"
        if saved_model and "/" not in saved_model
        else saved_model
    )
    models = with_default_opencode_model(models, normalized_saved_model)
    grouped: dict[str, list[str]] = {}
    for model in models:
        provider = model.split("/", 1)[0]
        grouped.setdefault(provider, []).append(model)
    for provider in grouped:
        grouped[provider] = sorted(set(grouped[provider]))
    default_model = choose_saved_or_default(models, normalized_saved_model)
    return {
        "api_url": api_url,
        "providers": sorted(grouped),
        "models_by_provider": grouped,
        "default_model": default_model,
    }
