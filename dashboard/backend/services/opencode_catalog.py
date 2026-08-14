from __future__ import annotations

import json
import os
from typing import Any

import httpx


DEFAULT_OPENCODE_API_URL = os.getenv(
    "OPENCODE_API_URL", "http://127.0.0.1:4090"
)

PREFERRED_FREE_OPENCODE_MODELS = (
    "opencode/mimo-v2.5-free",
    "opencode/north-mini-code-free",
    "opencode/nemotron-3-ultra-free",
    "opencode/deepseek-v4-flash-free",
)


def next_free_opencode_model(current: str = "") -> str:
    current = (current or "").strip()
    for model in PREFERRED_FREE_OPENCODE_MODELS:
        if model != current:
            return model
    return PREFERRED_FREE_OPENCODE_MODELS[0] if PREFERRED_FREE_OPENCODE_MODELS else ""


def list_opencode_models(
    api_url: str | None = None, api_key: str | None = None
) -> list[str]:
    url = (
        (api_url or "").strip()
        or os.getenv("OPENCODE_API_URL", "").strip()
        or DEFAULT_OPENCODE_API_URL
    )
    key = (api_key or "").strip() or os.getenv("OPENCODE_API_KEY", "").strip()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        with httpx.Client(timeout=httpx.Timeout(15, connect=10)) as client:
            response = client.get(f"{url.rstrip('/')}/models", headers=headers)
        if response.status_code != 200:
            return []
        data = response.json()
    except (httpx.HTTPError, OSError, json.JSONDecodeError):
        return []
    if isinstance(data, list):
        model_ids = [
            model["id"] if isinstance(model, dict) else str(model)
            for model in data
        ]
    elif isinstance(data, dict):
        models_data = data.get("data") or data.get("models") or []
        model_ids = [
            model["id"] if isinstance(model, dict) else str(model)
            for model in models_data
        ]
    else:
        return []
    return [model for model in model_ids if "/" in model] or [
        f"opencode/{model}" for model in model_ids
    ]


def choose_openai_gpt52(models: list[str]) -> str:
    if not models:
        return ""
    preferred_free = list(PREFERRED_FREE_OPENCODE_MODELS)
    for preferred in preferred_free:
        if preferred in models:
            return preferred
    for model in models:
        if "free" in model.lower():
            return model
    preferred = "openai/gpt-5.2"
    if preferred in models:
        return preferred
    for model in models:
        if model.lower().startswith("openai/") and "gpt-5.2" in model.lower():
            return model
    for model in models:
        if model.lower().startswith("openai/"):
            return model
    non_copilot = [
        model
        for model in models
        if not model.lower().startswith("github-copilot/")
    ]
    return non_copilot[0] if non_copilot else models[0]


def sanitize_dashboard_model(selected: str, models: list[str]) -> str:
    chosen = (selected or "").strip()
    if chosen and (not models or chosen in models):
        return chosen
    return choose_openai_gpt52(models)


def build_opencode_catalog() -> dict[str, Any]:
    models = list_opencode_models()
    grouped: dict[str, list[str]] = {}
    for model in models:
        provider = model.split("/", 1)[0]
        grouped.setdefault(provider, []).append(model)
    for provider in grouped:
        grouped[provider] = sorted(grouped[provider])
    return {
        "api_url": DEFAULT_OPENCODE_API_URL,
        "providers": sorted(grouped),
        "models_by_provider": grouped,
        "default_model": choose_openai_gpt52(models),
    }
