from __future__ import annotations

"""Visual pattern (archetype) options offered per ad format.

The live copy LLM does not read copy_prompt_templates. Only
`visual_archetypes` is used, and only after copy exists, for image-prompt
assembly and Studio pattern dropdowns. Configs seeded before that key
existed fall back to the bundled template file rather than showing an
empty dropdown.
"""

import json
from pathlib import Path
from typing import Any


FORMATS = ("HERO", "BA", "TEST", "FEAT", "UGC")
_BUNDLED_TEMPLATES = (
    Path(__file__).resolve().parents[1] / "copy_prompt_templates.json"
)


def _coerce_templates(templates: Any) -> dict[str, Any]:
    if isinstance(templates, dict):
        return templates
    try:
        parsed = json.loads(str(templates or ""))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def bundled_visual_archetypes() -> dict[str, Any]:
    try:
        parsed = json.loads(_BUNDLED_TEMPLATES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    raw = parsed.get("visual_archetypes") if isinstance(parsed, dict) else None
    return raw if isinstance(raw, dict) else {}


def format_visual_archetypes(templates: Any = None) -> dict[str, list[dict[str, str]]]:
    """Return `{format: [{id, label}, ...]}` for the visual pattern selector."""
    raw = _coerce_templates(templates).get("visual_archetypes")
    if not isinstance(raw, dict) or not any(raw.get(fmt) for fmt in FORMATS):
        raw = bundled_visual_archetypes()
    result: dict[str, list[dict[str, str]]] = {}
    for fmt in FORMATS:
        items = raw.get(fmt) if isinstance(raw, dict) else None
        result[fmt] = [
            {
                "id": str(item.get("id") or "").strip(),
                "label": str(item.get("label") or item.get("id") or "").strip(),
            }
            for item in (items if isinstance(items, list) else [])
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        ]
    return result
