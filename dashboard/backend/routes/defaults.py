import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from dashboard.backend.auth.service import require_user_dependency
from dashboard.backend.services.user_config import resolve_effective_config_for_user

router = APIRouter()


def _parse_json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError):
        return fallback


def _persona_summaries(config: dict[str, Any]) -> list[dict[str, Any]]:
    raw = _parse_json(config.get("persona_seeds"), [])
    values = raw if isinstance(raw, list) else list(raw.values()) if isinstance(raw, dict) else []
    summaries = []
    for index, persona in enumerate(values, start=1):
        if not isinstance(persona, dict):
            continue
        number = persona.get("persona_number", persona.get("number", index))
        try:
            number = int(number)
        except (TypeError, ValueError):
            continue
        summaries.append({
            "number": number,
            "name": str(
                persona.get("persona_name")
                or persona.get("name")
                or f"Persona {number}"
            )[:160],
        })
    return summaries


def _hypothesis_variables(config: dict[str, Any]) -> dict[str, Any]:
    variables: dict[str, Any] = {
        "none": {
            "label": "No hypothesis test",
            "description": "Generate ads normally without controlled A/B testing.",
            "options": [],
        }
    }
    architecture = _parse_json(config.get("copy_architecture"), {})
    headline = (
        architecture.get("headline_architectures", {})
        if isinstance(architecture, dict)
        else {}
    )
    definitions = {
        "hook_structure": "Hook Structure (H1)",
        "concept_angle": "Concept Angle (H2)",
    }
    for key, label in definitions.items():
        choices = headline.get(key, {}) if isinstance(headline, dict) else {}
        variables[key] = {
            "label": label,
            "options": [
                {
                    "id": str(choice)[:80],
                    "label": str(choice).replace("_", " ").title()[:120],
                }
                for choice in choices
            ] if isinstance(choices, dict) else [],
        }
    return variables


@router.get("/api/defaults")
def dashboard_defaults(
    user: dict[str, Any] = Depends(require_user_dependency),
) -> dict[str, Any]:
    """Return bounded UI defaults derived from Mongo-backed dashboard config."""
    config = resolve_effective_config_for_user(user["user_id"])
    return {
        "personas": _persona_summaries(config),
        "formats": ["HERO", "BA", "TEST", "FEAT", "UGC"],
        "format_patterns": {},
        "image_sources": [],
        "input_images": [],
        "product_doc": {},
        "default_files": {
            "product_info": "MongoDB: product_master_doc",
            "playbook": "MongoDB dashboard config",
        },
        "opencode": {
            "api_url": "",
            "providers": [],
            "models_by_provider": {},
            "default_model": "",
        },
        "provider": {
            "current": "opencode",
            "google_api_key": False,
            "opencode_api_url": "",
            "google_model": "",
            "google_models": [],
        },
        "hypothesis": {
            "variables": _hypothesis_variables(config),
            "default": {"type": "none", "variant": ""},
        },
        "batch_size": 10,
    }


def _local_only() -> None:
    raise HTTPException(
        status_code=410,
        detail="Configuration and content are available only through the paired localhost data plane",
    )


for _path, _methods in (
    ("/api/opencode/catalog", ["GET"]),
    ("/api/input-images", ["DELETE"]),
    ("/api/upload-input-images", ["POST"]),
    ("/api/product-doc", ["GET", "POST"]),
    ("/api/prompt-file-content", ["GET", "POST"]),
    ("/api/input-prompt", ["GET", "POST"]),
    ("/api/config/provider", ["POST"]),
    ("/api/google/models", ["GET"]),
):
    router.add_api_route(_path, _local_only, methods=_methods)
