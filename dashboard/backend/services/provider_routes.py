from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from dashboard.backend.auth.service import require_user_dependency
from dashboard.backend.services.provider_config import (
    get_provider_config,
    set_provider_config,
    delete_provider_config,
    get_all_provider_configs,
    get_decrypted_provider_key,
)

router = APIRouter()


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
    config = get_provider_config(user["user_id"], provider)
    if config is None:
        raise HTTPException(status_code=404, detail="Provider config not found")
    return config


@router.put("/api/user/provider-config/{provider}")
def save_provider(
    provider: str,
    payload: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    raw_config = payload.get("config", payload)
    return set_provider_config(user["user_id"], provider, raw_config)


@router.delete("/api/user/provider-config/{provider}")
def remove_provider(
    provider: str,
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, str]:
    delete_provider_config(user["user_id"], provider)
    return {"status": "deleted"}


@router.get("/api/user/provider-config/opencode/catalog")
def user_opencode_catalog(
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    from dashboard.backend.app import build_opencode_catalog, list_opencode_models

    config = get_provider_config(user["user_id"], "opencode")
    if config is None:
        return build_opencode_catalog()

    cfg = config.get("config", {})
    saved_url = (cfg.get("api_url") or "").strip()
    if not saved_url:
        return build_opencode_catalog()

    raw_key = get_decrypted_provider_key(user["user_id"], "opencode", "api_key")
    models = list_opencode_models(api_url=saved_url, api_key=raw_key)
    grouped: dict[str, list[str]] = {}
    for model in models:
        provider = model.split("/", 1)[0]
        grouped.setdefault(provider, []).append(model)
    for provider in grouped:
        grouped[provider] = sorted(grouped[provider])
    providers = sorted(grouped.keys())
    default_model = ""
    if models:
        saved_model = (cfg.get("default_model") or "").strip()
        prefixed_saved_model = f"opencode/{saved_model}" if saved_model and "/" not in saved_model else saved_model
        free_models = [model for model in models if "free" in model.lower()]
        if prefixed_saved_model in models and (not free_models or "free" in prefixed_saved_model.lower()):
            default_model = prefixed_saved_model
        else:
            from dashboard.backend.app import choose_openai_gpt52
            default_model = choose_openai_gpt52(models)
    return {
        "api_url": saved_url,
        "providers": providers,
        "models_by_provider": grouped,
        "default_model": default_model,
    }
