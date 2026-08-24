from __future__ import annotations

"""Visual pattern (archetype) options offered per ad format.

The live copy LLM does not read copy_prompt_templates. Only
`visual_archetypes` is used, and only after copy exists, for image-prompt
assembly and Studio pattern dropdowns. Configs seeded before that key
existed fall back to the bundled template file rather than showing an
empty dropdown.
"""

import json
import random
from pathlib import Path
from typing import Any

from dashboard.backend.services.copy_system import FORMAT_ID_RE


FORMATS = ("HERO", "BA", "TEST", "FEAT", "UGC")
_BUNDLED_TEMPLATES = (
    Path(__file__).resolve().parents[1] / "copy_prompt_templates.json"
)
# Dead copy-LLM blocks. Live Structured copy reads copy_system/ instead.
RETIRED_COPY_PROMPT_KEYS = frozenset(
    {
        "system_prompt_base_rules",
        "system_prompt_format_rules",
        "prompt_tail",
        "strict_schema_note",
        "copy_requirements",
        "product_doc_bootstrap_prompt",
        "format_copy_keywords",
        "format_visual_keywords",
        "format_defaults",
        "persona_mapping",
        "cta_variants",
        "testimonial_headline_guidance",
        "testimonial_attribution_variants",
        "feature_templates",
        "template_copy_cta_map",
        "template_copy_format_overrides",
        "template_copy_en_fallbacks",
        "template_copy_hi_fallbacks",
        "template_copy_headline_sentence",
        "template_copy_support_sentence",
    }
)
_LIVE_DESCRIPTION = (
    "Visual archetypes only. Live copy reads dashboard/backend/copy_system/. "
    "Studio pattern dropdowns and post-copy image assembly use visual_archetypes."
)
LLM_DECIDE_ID = "llm_decide"
DEFAULT_VISUAL_ARCHETYPE_LLM_PROMPT = (
    "Do not lock a named visual archetype. Choose a composition that fits this "
    "format, the on-image copy, and the product packshot. Keep product labels "
    "readable, keep text in the safe field, and make one clear focal hierarchy. "
    "The image model decides layout, crop, and visual energy."
)


def _coerce_templates(templates: Any) -> dict[str, Any]:
    if isinstance(templates, dict):
        return templates
    try:
        parsed = json.loads(str(templates or ""))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def fill_missing_visual_archetypes(raw: Any) -> str:
    """Put the bundled catalog back when a stored file lost visual_archetypes."""
    parsed = _coerce_templates(raw)
    groups = _archetype_groups(parsed.get("visual_archetypes"))
    bundled = bundled_visual_archetypes()
    if groups or not bundled:
        if isinstance(raw, str):
            return raw
        return json.dumps(parsed, ensure_ascii=False, indent=2) if parsed else "{}"
    parsed["visual_archetypes"] = bundled
    parsed.setdefault("format", "v1")
    parsed.setdefault("_description", _LIVE_DESCRIPTION)
    return json.dumps(parsed, ensure_ascii=False, indent=2)


def sanitize_copy_prompt_templates_text(raw: Any) -> str:
    """Drop unused copy-LLM blocks. Keep visual_archetypes and other live keys."""
    if isinstance(raw, str) and not raw.strip():
        return fill_missing_visual_archetypes("{}")
    parsed = _coerce_templates(raw)
    if not parsed:
        return fill_missing_visual_archetypes("{}")
    retired = RETIRED_COPY_PROMPT_KEYS.intersection(parsed)
    if not retired:
        return fill_missing_visual_archetypes(raw)
    cleaned = {
        key: value
        for key, value in parsed.items()
        if key not in RETIRED_COPY_PROMPT_KEYS
    }
    cleaned["_description"] = _LIVE_DESCRIPTION
    if "format" not in cleaned and parsed.get("format"):
        cleaned["format"] = parsed["format"]
    return fill_missing_visual_archetypes(cleaned)


def llm_decide_archetype(prompt: str = "") -> dict[str, Any]:
    text = str(prompt or "").strip() or DEFAULT_VISUAL_ARCHETYPE_LLM_PROMPT
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        lines.append(line if line.startswith("-") else f"- {line}")
    return {
        "id": LLM_DECIDE_ID,
        "label": "Leave it to the image model",
        "layout_lines": lines,
        "direction_lines": [
            "- The image model decides the visual archetype. Do not force a catalog pattern.",
        ],
    }


def pick_random_archetype(
    items: list[dict[str, Any]],
    *,
    seed: int,
    used_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Pick a catalog pattern at random, preferring ones not yet used in this batch."""
    if not items:
        return {}
    unused = [
        item
        for item in items
        if str(item.get("id") or "").strip() not in (used_ids or set())
    ]
    pool = unused or items
    return pool[random.Random(seed).randrange(len(pool))]


def bundled_visual_archetypes() -> dict[str, Any]:
    try:
        parsed = json.loads(_BUNDLED_TEMPLATES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    raw = parsed.get("visual_archetypes") if isinstance(parsed, dict) else None
    return raw if isinstance(raw, dict) else {}


def default_archetype_for_format(fmt: str) -> dict[str, Any]:
    ident = str(fmt or "").strip().upper()
    slug = ident.lower() or "format"
    return {
        "id": f"{slug}_default",
        "label": f"Default {ident} layout",
        "layout_lines": [
            f"- Use a clean {ident} composition with one obvious focal hierarchy.",
            "- Keep product labels readable and fully inside the safe field.",
        ],
        "direction_lines": [
            f"- Placeholder {ident} pattern. Edit this archetype so the layout is meaningful.",
        ],
    }


def format_ids_from_formats(formats: Any) -> list[str]:
    parsed = _coerce_templates(formats)
    ids: list[str] = []
    for key, value in parsed.items():
        if str(key).startswith("_") or not isinstance(value, dict):
            continue
        ident = str(key or "").strip().upper()
        if FORMAT_ID_RE.match(ident) and ident not in ids:
            ids.append(ident)
    return ids


def _archetype_groups(raw: Any) -> dict[str, list[Any]]:
    groups: dict[str, list[Any]] = {}
    if not isinstance(raw, dict):
        return groups
    for key, value in raw.items():
        if str(key).startswith("_") or not isinstance(value, list):
            continue
        ident = str(key or "").strip().upper()
        if FORMAT_ID_RE.match(ident):
            groups[ident] = value
    return groups


def sync_visual_archetypes(templates: Any, formats: Any) -> tuple[str, list[str], list[str]]:
    """Add default archetypes for new formats and drop removed ones."""
    wanted = format_ids_from_formats(formats)
    if not wanted:
        if isinstance(templates, str):
            return templates, [], []
        return json.dumps(_coerce_templates(templates), ensure_ascii=False, indent=2), [], []
    parsed = _coerce_templates(templates)
    groups = _archetype_groups(parsed.get("visual_archetypes"))
    added: list[str] = []
    removed: list[str] = []
    next_arch: dict[str, Any] = {}
    for fmt in wanted:
        items = groups.get(fmt)
        if isinstance(items, list) and any(isinstance(item, dict) and item.get("id") for item in items):
            next_arch[fmt] = items
            continue
        next_arch[fmt] = [default_archetype_for_format(fmt)]
        added.append(fmt)
    for ident in groups:
        if ident not in next_arch:
            removed.append(ident)
    parsed["visual_archetypes"] = next_arch
    if "_description" not in parsed:
        parsed["_description"] = _LIVE_DESCRIPTION
    return json.dumps(parsed, ensure_ascii=False, indent=2), added, removed


def visual_archetype_save_notice(templates: Any, formats: Any) -> str:
    """Explain keys that Studio will not turn into chips or pattern menus."""
    raw = _coerce_templates(templates).get("visual_archetypes")
    if not isinstance(raw, dict):
        return ""
    wanted = set(format_ids_from_formats(formats))
    invalid: list[str] = []
    orphan: list[str] = []
    for key, value in raw.items():
        if str(key).startswith("_") or not isinstance(value, list):
            continue
        ident = str(key or "").strip().upper()
        if not FORMAT_ID_RE.match(ident):
            invalid.append(str(key))
        elif wanted and ident not in wanted:
            orphan.append(ident)
    parts: list[str] = []
    if invalid:
        parts.append(
            "Ignored visual archetype keys "
            + ", ".join(invalid)
            + ". Format ids must match [A-Z][A-Z0-9_]{0,15}, for example HERO_V4. "
            "Copy Prompt Templates does not create Studio format chips."
        )
    if orphan:
        parts.append(
            "Patterns for "
            + ", ".join(orphan)
            + " are stored, but Studio chips come from Ad Formats. Add that id there to see a chip."
        )
    return " ".join(parts)


def format_visual_archetypes(
    templates: Any = None,
    formats: Any = None,
) -> dict[str, list[dict[str, str]]]:
    """Return `{format: [{id, label}, ...]}` for the visual pattern selector."""
    wanted = format_ids_from_formats(formats)
    groups = _archetype_groups(_coerce_templates(templates).get("visual_archetypes"))
    if not any(groups.get(fmt) for fmt in (wanted or FORMATS)):
        groups = _archetype_groups(bundled_visual_archetypes())
    keys = list(wanted)
    for ident in groups:
        if ident not in keys:
            keys.append(ident)
    if not keys:
        keys = list(FORMATS)
    result: dict[str, list[dict[str, str]]] = {}
    for fmt in keys:
        items = groups.get(fmt) or []
        result[fmt] = [
            {
                "id": str(item.get("id") or "").strip(),
                "label": str(item.get("label") or item.get("id") or "").strip(),
            }
            for item in items
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        ]
    return result
