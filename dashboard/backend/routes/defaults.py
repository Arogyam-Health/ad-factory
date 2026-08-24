import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from dashboard.backend.auth.service import require_user_dependency
from dashboard.backend.services.copy_system import (
    format_catalog,
    hypothesis_catalog,
    language_mode_catalog,
)
from dashboard.backend.services.user_config import (
    get_generic_config,
    parse_concept_catalog,
    resolve_effective_config,
    resolve_effective_config_for_user,
)
from dashboard.backend.services.visual_archetypes import format_visual_archetypes

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
    return hypothesis_catalog(config)


_PUBLIC_STUDIO_TTL = 20.0
_public_studio_cache: dict[str, Any] = {"ts": 0.0, "data": None}


_REPO_ROOT = Path(__file__).resolve().parents[3]
_GUIDE_PATH = _REPO_ROOT / "docs" / "OPERATOR_PLATE_GUIDE.md"
_PUBLISHED_DOCS = {
    "OPERATOR_PLATE_GUIDE.md": _GUIDE_PATH,
    "STRUCTURED_COPY_SYSTEM.md": _REPO_ROOT / "docs" / "STRUCTURED_COPY_SYSTEM.md",
    "DEVELOPER_CLOUD_MIGRATION.md": _REPO_ROOT / "docs" / "DEVELOPER_CLOUD_MIGRATION.md",
    "LOCAL_AGENT_README.md": _REPO_ROOT / "docs" / "LOCAL_AGENT_README.md",
    "LOCAL_AGENT_UBUNTU.md": _REPO_ROOT / "docs" / "LOCAL_AGENT_UBUNTU.md",
    "LOCAL_AGENT_WINDOWS.md": _REPO_ROOT / "docs" / "LOCAL_AGENT_WINDOWS.md",
    "LOCAL_AGENT_MAC.md": _REPO_ROOT / "docs" / "LOCAL_AGENT_MAC.md",
    "LOCAL_FIRST_OPERATIONS.md": _REPO_ROOT / "docs" / "LOCAL_FIRST_OPERATIONS.md",
    "DASHBOARD_EDITABLE_FIELDS.md": _REPO_ROOT / "DASHBOARD_EDITABLE_FIELDS.md",
    "README.md": _REPO_ROOT / "docs" / "README.md",
}


def _published_doc_path(name: str) -> Path | None:
    key = Path(str(name or "")).name
    if not key.endswith(".md"):
        key = f"{key}.md"
    return _PUBLISHED_DOCS.get(key)


def _studio_payload(config: dict[str, Any], *, source: str) -> dict[str, Any]:
    return {
        "source": source,
        "config": config,
        "personas": _persona_summaries(config),
        "formats": format_catalog(config),
        "format_patterns": format_visual_archetypes(
            _parse_json(config.get("copy_prompt_templates"), {}),
            _parse_json(config.get("ad_formats"), {}),
        ),
        "language_modes": language_mode_catalog(config),
        "concepts": parse_concept_catalog(config.get("concept")),
        "hypothesis": {
            "variables": _hypothesis_variables(config),
            "default": {"type": "none", "variant": ""},
        },
        "default_files": {
            "product_info": "generic: product_master_doc",
            "playbook": "generic dashboard config",
        },
        "can_run": False,
    }


@router.get("/api/guide")
def operator_guide() -> dict[str, str]:
    try:
        markdown = _GUIDE_PATH.read_text(encoding="utf-8")
    except OSError:
        markdown = ""
    return {"title": "Operator plate guide", "markdown": markdown}


@router.get("/api/docs/{name}")
def published_doc(name: str) -> dict[str, str]:
    path = _published_doc_path(name)
    if path is None:
        raise HTTPException(status_code=404, detail="Doc not found")
    try:
        markdown = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=404, detail="Doc not found") from exc
    return {"title": path.stem.replace("_", " "), "markdown": markdown}


@router.get("/api/public/studio")
def public_studio() -> JSONResponse:
    """Unauthenticated generic plate: personas, files, and rules for visitors."""
    now = time.time()
    cached = _public_studio_cache["data"]
    if cached is not None and now - float(_public_studio_cache["ts"] or 0) < _PUBLIC_STUDIO_TTL:
        payload = cached
    else:
        payload = _studio_payload(get_generic_config(), source="generic")
        _public_studio_cache["ts"] = now
        _public_studio_cache["data"] = payload
    return JSONResponse(payload, headers={"Cache-Control": "public, max-age=20"})


@router.get("/api/defaults")
def dashboard_defaults(
    user: dict[str, Any] = Depends(require_user_dependency),
    org_id: str = "",
) -> dict[str, Any]:
    """Return bounded UI defaults derived from Mongo-backed dashboard config."""
    clean_org = str(org_id or "").strip()
    config = (
        resolve_effective_config(str(user["user_id"]), clean_org)
        if clean_org
        else resolve_effective_config_for_user(user["user_id"])
    )
    return {
        "personas": _persona_summaries(config),
        "formats": format_catalog(config),
        "format_patterns": format_visual_archetypes(
            _parse_json(config.get("copy_prompt_templates"), {}),
            _parse_json(config.get("ad_formats"), {}),
        ),
        "language_modes": language_mode_catalog(config),
        "concepts": parse_concept_catalog(config.get("concept")),
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
