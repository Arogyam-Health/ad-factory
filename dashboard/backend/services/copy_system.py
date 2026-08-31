from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


COPY_SYSTEM_DIR = Path(__file__).resolve().parent.parent / "copy_system"

COPY_SYSTEM_KEYS = [
    "ad_formats",
    "ad_languages",
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
    "ad_support_shapes",
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
    "support_shape": "ad_support_shapes",
}

_BUNDLED: dict[str, dict[str, Any]] | None = None
FORMAT_ID_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,15}$")
FALLBACK_FORMAT_IDS = ["HERO", "BA", "TEST", "FEAT", "UGC"]
FALLBACK_LANGUAGE_IDS = ["EN", "HI", "HINGLISH"]
FALLBACK_LANGUAGE_MODES = {
    "EN": ["EN"],
    "HI": ["HI"],
    "HINGLISH": ["HINGLISH"],
    "ALL": ["EN", "HI", "HINGLISH"],
    "BOTH": ["EN", "HI", "HINGLISH"],
}
FALLBACK_PERSONA_SOURCE_MAP = {
    "name": ["persona_name", "name"],
    "pain_en": ["core_pattern", "pain_en"],
    "desire_en": ["relevant_ok_kit_role", "desire_en"],
    "friction_en": ["why_it_failed", "friction_en"],
    "proof_needed_en": ["guardrail", "proof_needed_en"],
    "tone_cue_en": ["tone_cue_en", "tone"],
}
FALLBACK_PERSONA_EN = {
    "pain_en": "The current routine is difficult to sustain.",
    "desire_en": "A practical routine that fits daily life.",
    "friction_en": "Past approaches felt difficult to maintain.",
    "proof_needed_en": "Use verified product facts only.",
    "tone_cue_en": "Practical, empathetic, and confidence-building.",
}
FALLBACK_COPY_TASK = "Generate structured advertising copy as JSON"
FALLBACK_COPY_REPAIR_TASK = "Repair structured copy validation errors and return JSON only"
FALLBACK_NO_HYPOTHESIS_LABEL = "No hypothesis test"
FALLBACK_NO_HYPOTHESIS_CATALOG = (
    "Generate the strongest natural ad. No persuasion variable is under test."
)
FALLBACK_PERSONA_MAPS = {
    "EN": {
        "pain": "pain_en",
        "desire": "desire_en",
        "friction": "friction_en",
        "proof_needed": "proof_needed_en",
        "tone_cue": "tone_cue_en",
    },
    "HI": {
        "pain_hi": "pain_hi",
        "desire_hi": "desire_hi",
        "friction_hi": "friction_hi",
        "proof_needed_hi": "proof_needed_hi",
        "tone_cue_hi": "tone_cue_hi",
    },
    "HINGLISH": {
        "pain_hinglish": "pain_hinglish",
        "desire_hinglish": "desire_hinglish",
        "friction_hinglish": "friction_hinglish",
        "proof_needed_hinglish": "proof_needed_hinglish",
        "tone_cue_hinglish": "tone_cue_hinglish",
    },
}
OPTIONAL_COPY_FIELDS = frozenset({"trust_line"})


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


def is_format_id(value: Any) -> bool:
    ident = str(value or "").strip().upper()
    return bool(ident and FORMAT_ID_RE.match(ident))


def normalize_format_id(value: Any) -> str:
    ident = str(value or "").strip().upper()
    if not FORMAT_ID_RE.match(ident):
        raise ValueError("Unsupported ad format")
    return ident


def _format_entries(config: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for key, value in _file(config, "ad_formats").items():
        if str(key).startswith("_") or not isinstance(value, dict):
            continue
        ident = str(key or "").strip().upper()
        if FORMAT_ID_RE.match(ident):
            entries[ident] = value
    return entries


def format_catalog(config: dict[str, Any] | None) -> list[dict[str, str]]:
    items = []
    for ident, entry in _format_entries(config).items():
        items.append(
            {
                "id": ident,
                "label": _text(entry.get("label")) or ident,
            }
        )
    return items or [
        {"id": ident, "label": ident} for ident in FALLBACK_FORMAT_IDS
    ]


def format_layer(config: dict[str, Any] | None, fmt: str) -> dict[str, Any]:
    ident = str(fmt or "").strip().upper()
    entry = _format_entries(config).get(ident)
    # Transparent: return every field from config as-is, plus id
    if isinstance(entry, dict):
        payload: dict[str, Any] = {"id": ident}
        for key, value in entry.items():
            payload[str(key)] = value
        # Ensure id is correct
        payload["id"] = ident
        return payload
    return {"id": ident}


def _language_entries(config: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for key, value in _file(config, "ad_languages").items():
        if str(key).startswith("_") or not isinstance(value, dict):
            continue
        ident = str(key or "").strip().upper()
        if ident:
            entries[ident] = value
    return entries


def _language_modes(config: dict[str, Any] | None) -> dict[str, list[str]]:
    raw = _file(config, "ad_languages").get("_modes")
    modes: dict[str, list[str]] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            ident = str(key or "").strip().upper()
            langs = value.get("languages") if isinstance(value, dict) else value
            if ident and isinstance(langs, list) and langs:
                modes[ident] = [
                    str(item).strip().upper()
                    for item in langs
                    if str(item).strip()
                ]
    return modes or dict(FALLBACK_LANGUAGE_MODES)


def language_mode_catalog(config: dict[str, Any] | None) -> list[dict[str, Any]]:
    raw = _file(config, "ad_languages").get("_modes")
    items: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        for key, value in raw.items():
            ident = str(key or "").strip().upper()
            if not ident or ident == "BOTH":
                continue
            entry = value if isinstance(value, dict) else {}
            langs = [
                str(item).strip().upper()
                for item in (entry.get("languages") or [])
                if str(item).strip()
            ] or FALLBACK_LANGUAGE_MODES.get(ident, [ident])
            items.append(
                {
                    "id": ident,
                    "label": _text(entry.get("label")) or ident,
                    "languages": langs,
                }
            )
    return items or [
        {
            "id": ident,
            "label": ident,
            "languages": list(langs),
        }
        for ident, langs in FALLBACK_LANGUAGE_MODES.items()
        if ident != "BOTH"
    ]


def is_language_mode(config: dict[str, Any] | None, value: Any) -> bool:
    ident = str(value or "").strip().upper()
    return bool(ident and ident in _language_modes(config))


def resolve_language_ids(config: dict[str, Any] | None, mode: Any) -> tuple[str, ...]:
    ident = str(mode or "EN").strip().upper() or "EN"
    langs = _language_modes(config).get(ident) or FALLBACK_LANGUAGE_MODES.get(ident) or ["EN"]
    return tuple(langs)


def language_layers(
    config: dict[str, Any] | None,
    languages: tuple[str, ...] | list[str],
) -> list[dict[str, Any]]:
    entries = _language_entries(config)
    layers: list[dict[str, Any]] = []
    for lang in languages:
        ident = str(lang or "").strip().upper()
        if not ident:
            continue
        entry = entries.get(ident) or {}
        # Transparent: send every field from language config as-is
        payload: dict[str, Any] = {"id": ident}
        if isinstance(entry, dict):
            for key, value in entry.items():
                payload[str(key)] = value
        payload["id"] = ident
        layers.append(payload)
    return layers


def copy_task(config: dict[str, Any] | None) -> str:
    return _text(_file(config, "ad_guardrails").get("task")) or FALLBACK_COPY_TASK


def copy_repair_task(config: dict[str, Any] | None) -> str:
    return (
        _text(_file(config, "ad_guardrails").get("repair_task"))
        or FALLBACK_COPY_REPAIR_TASK
    )


def persona_source_map(config: dict[str, Any] | None) -> dict[str, list[str]]:
    raw = _file(config, "ad_languages").get("_persona_source_map")
    mapped: dict[str, list[str]] = {}
    if isinstance(raw, dict):
        for dest, sources in raw.items():
            ident = str(dest or "").strip()
            keys = sources if isinstance(sources, list) else [sources]
            cleaned = [str(item).strip() for item in keys if str(item).strip()]
            if ident and cleaned:
                mapped[ident] = cleaned
    return mapped or {key: list(value) for key, value in FALLBACK_PERSONA_SOURCE_MAP.items()}


def persona_fallbacks(config: dict[str, Any] | None, lang: str = "EN") -> dict[str, str]:
    ident = str(lang or "EN").strip().upper() or "EN"
    entry = _language_entries(config).get(ident) or {}
    raw = entry.get("persona_fallbacks")
    if isinstance(raw, dict) and raw:
        return {
            str(key): _text(value)
            for key, value in raw.items()
            if str(key).strip() and _text(value)
        }
    if ident == "EN":
        return dict(FALLBACK_PERSONA_EN)
    return {}


def extra_persona_language_keys(config: dict[str, Any] | None) -> list[str]:
    keys: list[str] = []
    seen = set(FALLBACK_PERSONA_EN)
    seen.update({"name", "number"})
    for entry in _language_entries(config).values():
        raw = entry.get("persona_map") if isinstance(entry, dict) else {}
        if not isinstance(raw, dict):
            continue
        for source in raw.values():
            key = str(source).strip()
            if key and key not in seen and key not in keys:
                keys.append(key)
    return keys or [
        "pain_hi",
        "desire_hi",
        "friction_hi",
        "proof_needed_hi",
        "tone_cue_hi",
        "pain_hinglish",
        "desire_hinglish",
        "friction_hinglish",
        "proof_needed_hinglish",
        "tone_cue_hinglish",
    ]


def pick_persona_field(source: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = _text(source.get(key))
        if value:
            return value
    return ""


def language_persona_map(config: dict[str, Any] | None, lang: str) -> dict[str, str]:
    ident = str(lang or "").strip().upper()
    entry = _language_entries(config).get(ident) or {}
    raw = entry.get("persona_map")
    if isinstance(raw, dict) and raw:
        return {
            str(dest): str(source)
            for dest, source in raw.items()
            if str(dest).strip() and str(source).strip()
        }
    return dict(FALLBACK_PERSONA_MAPS.get(ident) or {})


def format_output_fields(config: dict[str, Any] | None, fmt: str) -> list[str]:
    layer = format_layer(config, fmt)
    fields = layer.get("output_fields")
    if isinstance(fields, list) and fields:
        return [str(item) for item in fields]
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
    guard = _file(config, "ad_guardrails")
    variables: dict[str, Any] = {
        "none": {
            "label": _text(guard.get("no_hypothesis_label")) or FALLBACK_NO_HYPOTHESIS_LABEL,
            "description": (
                _text(guard.get("no_hypothesis_catalog")) or FALLBACK_NO_HYPOTHESIS_CATALOG
            ),
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
    # Transparent: send every field from config as-is
    payload: dict[str, Any] = {"type": ident}
    if variant:
        payload["style"] = variant
    if not key:
        return payload
    raw = _file(config, key)
    if isinstance(raw, dict):
        meta = raw.get("_meta") if isinstance(raw.get("_meta"), dict) else {}
        if isinstance(meta, dict):
            for mk, mv in meta.items():
                if str(mk).strip():
                    payload[str(mk)] = mv
        style_entry = raw.get(variant) if variant else None
        if isinstance(style_entry, dict):
            for sk, sv in style_entry.items():
                if str(sk).strip():
                    payload[str(sk)] = sv
    return payload
