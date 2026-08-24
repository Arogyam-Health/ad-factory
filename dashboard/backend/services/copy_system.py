from __future__ import annotations

import json
from pathlib import Path
from typing import Any


COPY_SYSTEM_DIR = Path(__file__).resolve().parent.parent / "copy_system"

COPY_SYSTEM_KEYS = [
    "ad_formats",
    "ad_hooks",
    "ad_angles",
    "ad_frameworks",
    "ad_proof",
    "ad_objections",
    "ad_value_props",
    "ad_awareness",
    "ad_emotions",
    "ad_specificity",
    "ad_feature_focus",
    "ad_guardrails",
]

HYPOTHESIS_FILES = {
    "hook_structure": "ad_hooks",
    "concept_angle": "ad_angles",
    "copy_framework": "ad_frameworks",
    "proof_strategy": "ad_proof",
    "objection_strategy": "ad_objections",
    "value_proposition": "ad_value_props",
    "awareness_stage": "ad_awareness",
    "emotional_driver": "ad_emotions",
    "specificity_level": "ad_specificity",
    "feature_focus": "ad_feature_focus",
}

_BUNDLED: dict[str, dict[str, Any]] | None = None


def _text(value: Any) -> str:
    return str(value or "").strip()


def parse_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def compact(value: Any) -> Any:
    """Drop empty strings, empty lists, empty dicts, and None."""
    if isinstance(value, dict):
        cleaned = {
            key: compact(item)
            for key, item in value.items()
            if compact(item) not in (None, "", [], {})
        }
        return cleaned
    if isinstance(value, list):
        cleaned_list = [
            item
            for item in (compact(entry) for entry in value)
            if item not in (None, "", [], {})
        ]
        return cleaned_list
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip()
    return value


def bundled_copy_system() -> dict[str, dict[str, Any]]:
    global _BUNDLED
    if _BUNDLED is None:
        loaded: dict[str, dict[str, Any]] = {}
        for key in COPY_SYSTEM_KEYS:
            path = COPY_SYSTEM_DIR / f"{key}.json"
            try:
                loaded[key] = parse_object(path.read_text(encoding="utf-8"))
            except OSError:
                loaded[key] = {}
        _BUNDLED = loaded
    return _BUNDLED


def bundled_copy_system_text() -> dict[str, str]:
    out: dict[str, str] = {}
    for key in COPY_SYSTEM_KEYS:
        path = COPY_SYSTEM_DIR / f"{key}.json"
        try:
            out[key] = path.read_text(encoding="utf-8")
        except OSError:
            out[key] = "{}"
    return out


def _file(config: dict[str, Any] | None, key: str) -> dict[str, Any]:
    if not isinstance(config, dict) or key not in config:
        return bundled_copy_system().get(key) or {}
    return parse_object(config.get(key))


def _styles(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    styles: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if key.startswith("_") or not isinstance(value, dict):
            continue
        styles[str(key)] = value
    return styles


def format_layer(config: dict[str, Any] | None, fmt: str) -> dict[str, Any]:
    ident = str(fmt or "").strip().upper()
    entry = _file(config, "ad_formats").get(ident)
    payload: dict[str, Any] = {"id": ident}
    if isinstance(entry, dict):
        description = _text(entry.get("description"))
        skeleton = _text(entry.get("skeleton"))
        fields = [
            _text(item)
            for item in (entry.get("output_fields") or [])
            if _text(item)
        ]
        if description:
            payload["description"] = description
        if skeleton:
            payload["skeleton"] = skeleton
        if fields:
            payload["output_fields"] = fields
        label = _text(entry.get("label"))
        if label:
            payload["label"] = label
    return compact(payload) or {"id": ident}


def format_output_fields(config: dict[str, Any] | None, fmt: str) -> list[str]:
    layer = format_layer(config, fmt)
    fields = layer.get("output_fields")
    if isinstance(fields, list) and fields:
        return [str(item) for item in fields]
    ident = str(fmt or "").upper()
    if ident in {"HERO", "UGC"}:
        return ["headline", "support_line", "cta"]
    if ident == "TEST":
        return ["headline", "attribution", "trust_line", "cta"]
    if ident in {"BA", "FEAT"}:
        return ["headline", "bullets", "cta"]
    return ["headline", "cta"]


def guardrails(config: dict[str, Any] | None, *, hypothesis: bool) -> list[str]:
    raw = _file(config, "ad_guardrails")
    lines = [
        _text(item)
        for item in (raw.get("always") if isinstance(raw.get("always"), list) else [])
        if _text(item)
    ]
    if not hypothesis:
        note = _text(raw.get("no_hypothesis"))
        if note:
            lines.append(note)
    return lines


def hypothesis_catalog(config: dict[str, Any] | None) -> dict[str, Any]:
    variables: dict[str, Any] = {
        "none": {
            "label": "No hypothesis test",
            "description": "Generate the strongest natural ad. No persuasion variable is under test.",
            "options": [],
        }
    }
    for hyp_type, key in HYPOTHESIS_FILES.items():
        raw = _file(config, key)
        meta = raw.get("_meta") if isinstance(raw.get("_meta"), dict) else {}
        options = []
        for style_id, style in _styles(raw).items():
            options.append(
                {
                    "id": style_id,
                    "label": _text(style.get("label")) or style_id.replace("_", " ").title(),
                }
            )
        variables[hyp_type] = {
            "label": _text(meta.get("label")) or hyp_type.replace("_", " ").title(),
            "description": _text(meta.get("instruction")),
            "options": options,
        }
    return variables


def hypothesis_layer(
    config: dict[str, Any] | None,
    hyp_type: str,
    style: str,
) -> dict[str, Any] | None:
    ident = str(hyp_type or "").strip().lower()
    variant = str(style or "").strip()
    if not ident or ident in {"none", ""}:
        return None
    key = HYPOTHESIS_FILES.get(ident)
    payload: dict[str, Any] = {"type": ident}
    if variant:
        payload["style"] = variant
    if not key:
        return compact(payload)
    raw = _file(config, key)
    meta = raw.get("_meta") if isinstance(raw.get("_meta"), dict) else {}
    if _text(meta.get("label")):
        payload["type_label"] = _text(meta.get("label"))
    if _text(meta.get("instruction")):
        payload["instruction"] = _text(meta.get("instruction"))
    style_entry = raw.get(variant) if variant else None
    if isinstance(style_entry, dict):
        if _text(style_entry.get("label")):
            payload["label"] = _text(style_entry.get("label"))
        if _text(style_entry.get("instruction")):
            payload["instruction"] = _text(style_entry.get("instruction"))
        if _text(style_entry.get("definition")):
            payload["definition"] = _text(style_entry.get("definition"))
        if _text(style_entry.get("skeleton")):
            payload["skeleton"] = _text(style_entry.get("skeleton"))
    return compact(payload)
