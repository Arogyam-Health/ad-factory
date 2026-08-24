from __future__ import annotations

"""Live structured-copy assembler.

Copy-LLM layers come from dashboard/backend/copy_system/ via copy_system.py.
copy_prompt_templates.json is read only for visual_archetypes after copy exists.
copy_starting_prompt is sent as starting_prompt when non-empty.
"""

import hashlib
import json
import random
import re
import time
from pathlib import Path
from typing import Any, Callable

import requests

from dashboard.backend.services.copy_system import (
    compact,
    format_layer,
    format_output_fields,
    guardrails,
    hypothesis_layer,
)
from dashboard.backend.services.llm_trace import MAX_TRACE_TEXT
from dashboard.backend.services.user_config import resolve_selected_concept
from dashboard.backend.services.visual_archetypes import bundled_visual_archetypes
from scripts import generate_ads


GenerateCallable = Callable[[dict[str, Any], bool], dict[str, Any]]
TraceCallback = Callable[[dict[str, Any]], None]
ProviderTransport = Callable[[dict[str, Any]], dict[str, Any]]
_LANGUAGES = {
    "EN": ("EN",),
    "HI": ("HI",),
    "HINGLISH": ("HINGLISH",),
    "ALL": ("EN", "HI", "HINGLISH"),
    "BOTH": ("EN", "HI", "HINGLISH"),
}


class ProviderCallError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        provider: str,
        model: str,
        duration_ms: int,
        http_status: int | None = None,
        error_detail: str = "",
        trace_persisted: bool = False,
        trace_persistence_error: str = "",
    ) -> None:
        super().__init__(code)
        self.code = code
        self.provider = provider
        self.model = model
        self.duration_ms = duration_ms
        self.http_status = http_status
        self.error_detail = error_detail
        self.trace_persisted = trace_persisted
        self.trace_persistence_error = trace_persistence_error


def _json_config(value: Any, fallback: Any) -> Any:
    if isinstance(value, type(fallback)):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except json.JSONDecodeError:
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


_BUNDLED_BACKGROUNDS: dict[str, Any] | None = None


def _bundled_backgrounds() -> dict[str, Any]:
    global _BUNDLED_BACKGROUNDS
    if _BUNDLED_BACKGROUNDS is None:
        try:
            parsed = json.loads(generate_ads.BACKGROUNDS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            parsed = {}
        _BUNDLED_BACKGROUNDS = parsed if isinstance(parsed, dict) else {}
    return _BUNDLED_BACKGROUNDS


def _resolve_backgrounds(effective_config: dict[str, Any]) -> dict[str, Any]:
    stored = _json_config(effective_config.get("background_variant"), {})
    if isinstance(stored, dict) and isinstance(stored.get("variants"), list) and stored["variants"]:
        return stored
    bundled = _bundled_backgrounds()
    return bundled if bundled.get("variants") else stored


def _pick_background_slot(backgrounds: dict[str, Any], fmt: str, seed: int) -> dict[str, Any]:
    try:
        return generate_ads.pick_background_slot(backgrounds, fmt, seed)
    except RuntimeError:
        bundled = _bundled_backgrounds()
        if bundled is not backgrounds:
            return generate_ads.pick_background_slot(bundled, fmt, seed)
        raise


def _persona_map(effective_config: dict[str, Any]) -> dict[int, dict[str, Any]]:
    seeds = _json_config(effective_config.get("persona_seeds"), [])
    values = seeds if isinstance(seeds, list) else list(seeds.values())
    return {
        int(item.get("persona_number") or item.get("number")): item
        for item in values
        if isinstance(item, dict)
        and str(item.get("persona_number") or item.get("number") or "").isdigit()
    }


def _persona(number: int, source: dict[str, Any]) -> dict[str, Any]:
    name = str(source.get("persona_name") or source.get("name") or f"Persona {number}")
    pain = str(source.get("core_pattern") or source.get("pain_en") or "").strip()
    desire = str(source.get("relevant_ok_kit_role") or source.get("desire_en") or "").strip()
    friction = str(source.get("why_it_failed") or source.get("friction_en") or "").strip()
    proof = str(source.get("guardrail") or source.get("proof_needed_en") or "").strip()
    tone = str(source.get("tone_cue_en") or source.get("tone") or "").strip()
    payload = {
        "number": number,
        "name": name,
        "pain_en": pain or "The current routine is difficult to sustain.",
        "desire_en": desire or "A practical routine that fits daily life.",
        "friction_en": friction or "Past approaches felt difficult to maintain.",
        "proof_needed_en": proof or "Use verified product facts only.",
        "tone_cue_en": tone or "Practical, empathetic, and confidence-building.",
    }
    for key in (
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
    ):
        value = str(source.get(key) or "").strip()
        if value:
            payload[key] = value
    return payload


def _persona_for_llm(
    persona: dict[str, Any],
    languages: tuple[str, ...],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "number": persona.get("number"),
        "name": persona.get("name"),
    }
    if "EN" in languages:
        mapping = {
            "pain": "pain_en",
            "desire": "desire_en",
            "friction": "friction_en",
            "proof_needed": "proof_needed_en",
            "tone_cue": "tone_cue_en",
        }
        for dest, source in mapping.items():
            value = str(persona.get(source) or "").strip()
            if value:
                payload[dest] = value
    if "HI" in languages:
        mapping = {
            "pain_hi": "pain_hi",
            "desire_hi": "desire_hi",
            "friction_hi": "friction_hi",
            "proof_needed_hi": "proof_needed_hi",
            "tone_cue_hi": "tone_cue_hi",
        }
        for dest, source in mapping.items():
            value = str(persona.get(source) or "").strip()
            if value:
                payload[dest] = value
    if "HINGLISH" in languages:
        mapping = {
            "pain_hinglish": "pain_hinglish",
            "desire_hinglish": "desire_hinglish",
            "friction_hinglish": "friction_hinglish",
            "proof_needed_hinglish": "proof_needed_hinglish",
            "tone_cue_hinglish": "tone_cue_hinglish",
        }
        for dest, source in mapping.items():
            value = str(persona.get(source) or "").strip()
            if value:
                payload[dest] = value
    return compact(payload) or {"number": persona.get("number"), "name": persona.get("name")}


def _hypothesis_from_settings(settings: dict[str, Any]) -> dict[str, Any]:
    raw = settings.get("hypothesis")
    if not isinstance(raw, dict):
        return {"type": "none", "variant": ""}
    hyp_type = str(raw.get("type") or "none").strip().lower() or "none"
    variant = str(raw.get("variant") or "").strip()
    return {"type": hyp_type, "variant": variant}


def _archetype_catalog(effective_config: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    templates = _json_config(effective_config.get("copy_prompt_templates"), {})
    raw = templates.get("visual_archetypes") if isinstance(templates, dict) else None
    if not isinstance(raw, dict) or not any(raw.get(fmt) for fmt in ("HERO", "BA", "TEST", "FEAT", "UGC")):
        raw = bundled_visual_archetypes()
    catalog: dict[str, list[dict[str, Any]]] = {}
    for fmt in ("HERO", "BA", "TEST", "FEAT", "UGC"):
        items = raw.get(fmt) if isinstance(raw, dict) else None
        catalog[fmt] = [item for item in (items if isinstance(items, list) else []) if isinstance(item, dict)]
    return catalog


def _resolve_archetype(
    fmt: str,
    archetype_id: str,
    catalog: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    wanted = str(archetype_id or "").strip()
    for item in catalog.get(fmt) or []:
        if str(item.get("id") or "").strip() == wanted:
            return item
    if wanted:
        try:
            return generate_ads.find_visual_archetype(fmt, wanted)
        except RuntimeError:
            pass
    items = catalog.get(fmt) or []
    if items:
        return items[0]
    return generate_ads.default_visual_archetype(fmt)


def _reuse_keys(
    fmt: str,
    persona_no: int | None,
    visual_archetype: str,
    share_across_personas: bool,
) -> list[str]:
    fmt = str(fmt or "").strip().upper()
    persona = f"P{int(persona_no):02d}" if isinstance(persona_no, int) else ""
    arch = str(visual_archetype or "").strip()
    if share_across_personas:
        return [key for key in [f"{fmt}::{arch}" if arch else "", fmt] if key]
    return [
        key
        for key in [
            f"{fmt}::{persona}::{arch}" if persona and arch else "",
            f"{fmt}::{persona}" if persona else "",
        ]
        if key
    ]


def _apply_visual_pattern_reuse(
    plan: list[dict[str, Any]],
    locks: dict[str, dict[str, Any]],
    *,
    share_across_personas: bool,
) -> list[dict[str, Any]]:
    if not locks:
        return plan
    out: list[dict[str, Any]] = []
    for item in plan:
        entry = dict(item)
        fmt = str(entry.get("format") or "").strip().upper()
        persona = entry.get("persona")
        persona_no = (
            int(persona["number"])
            if isinstance(persona, dict) and isinstance(persona.get("number"), int)
            else None
        )
        lock = None
        reuse_key = ""
        for key in _reuse_keys(fmt, persona_no, str(entry.get("visual_archetype") or ""), share_across_personas):
            if key in locks:
                lock = locks[key]
                reuse_key = key
                break
        if not lock:
            for key in _reuse_keys(fmt, persona_no, "", share_across_personas):
                if key in locks:
                    lock = locks[key]
                    reuse_key = key
                    break
        if lock:
            entry["visual_archetype"] = lock["visual_archetype"]
            entry["visual_pattern_reused_from_run_id"] = lock.get(
                "visual_pattern_reused_from_run_id", ""
            )
            entry["visual_pattern_reuse_key"] = reuse_key
        out.append(entry)
    return out


def _lookup_background_lock(
    locks: dict[str, dict[str, Any]],
    fmt: str,
    persona_no: int | None,
    visual_archetype: str,
    share_across_personas: bool,
) -> dict[str, Any] | None:
    for key in _reuse_keys(fmt, persona_no, visual_archetype, share_across_personas):
        if key in locks:
            return locks[key]
    for key in _reuse_keys(fmt, persona_no, "", share_across_personas):
        if key in locks:
            return locks[key]
    return None


def _planned_ads(
    settings: dict[str, Any], effective_config: dict[str, Any]
) -> list[dict[str, Any]]:
    selected = settings.get("selected_personas")
    if not isinstance(selected, list) or not selected:
        raise ValueError("Select at least one persona")
    global_formats = settings.get("global_formats")
    if not isinstance(global_formats, list) or not global_formats:
        raise ValueError("Select at least one ad format")
    per_persona = settings.get("formats_by_persona")
    per_persona = per_persona if isinstance(per_persona, dict) else {}
    multiplier = max(1, min(int(settings.get("multiplier") or 1), 20))
    personas = _persona_map(effective_config)
    archetype_map = settings.get("visual_archetypes_by_format")
    archetype_map = archetype_map if isinstance(archetype_map, dict) else {}
    share_background = bool(settings.get("share_background_across_personas"))
    hypothesis = _hypothesis_from_settings(settings)
    creative_concept = resolve_selected_concept(
        effective_config.get("concept"),
        settings.get("selected_concept"),
    )
    planned: list[dict[str, Any]] = []
    for raw_number in selected:
        number = int(raw_number)
        formats = per_persona.get(str(number))
        formats = formats if isinstance(formats, list) and formats else global_formats
        for fmt in formats:
            normalized_format = str(fmt or "").upper()
            if normalized_format not in {"HERO", "BA", "TEST", "FEAT", "UGC"}:
                raise ValueError("Unsupported ad format")
            forced_archetype = str(archetype_map.get(normalized_format) or "").strip()
            background_group_key = (
                normalized_format
                if share_background
                else f"{normalized_format}::P{number:02d}"
            )
            for creative_index in range(1, multiplier + 1):
                item = {
                    "format": normalized_format,
                    "creative_index": creative_index,
                    "creative_total": multiplier,
                    "persona": _persona(number, personas.get(number, {})),
                    "background_group_key": background_group_key,
                    "share_background_across_personas": share_background,
                }
                if (
                    hypothesis["type"] == "concept_angle"
                    and hypothesis["variant"]
                ):
                    item["concept_angle"] = hypothesis["variant"]
                if forced_archetype:
                    item["visual_archetype"] = forced_archetype
                if hypothesis["type"] not in {"", "none"}:
                    item["hypothesis"] = {
                        "type": hypothesis["type"],
                        "variant": hypothesis["variant"],
                        "hypothesis_id": f"{hypothesis['type']}-{hypothesis['variant']}",
                    }
                if creative_concept:
                    item["creative_concept"] = creative_concept
                planned.append(item)
    if not planned or len(planned) > 500:
        raise ValueError("Structured ad plan exceeds the 500-ad limit")
    return planned


def _normalized_language_block(
    candidate: dict[str, Any],
    language: str,
) -> dict[str, Any]:
    copy = (
        candidate.get("copy")
        if isinstance(candidate.get("copy"), dict)
        else {}
    )
    nested = next(
        (
            value
            for key, value in copy.items()
            if str(key).upper() == language
            and isinstance(value, dict)
        ),
        {},
    )
    alias = candidate.get(f"copy_{language.lower()}")
    alias = alias if isinstance(alias, dict) else {}
    direct_copy = (
        copy
        if any(
            key in copy
            for key in ("headline", "cta", "support_line", "subheadline")
        )
        else {}
    )
    sources = (
        [candidate, direct_copy, alias, nested]
        if language == "EN"
        else [direct_copy, alias, nested]
    )
    allowed = (
        "headline",
        "cta",
        "subheadline",
        "support_line",
        "context_line",
        "trust_line",
        "attribution",
        "bullets",
    )
    block: dict[str, Any] = {}
    for source in sources:
        for key in allowed:
            value = source.get(key)
            if value not in (None, "", []):
                block[key] = value
        if (
            not block.get("support_line")
            and not block.get("subheadline")
            and str(source.get("body") or "").strip()
        ):
            block["support_line"] = str(source["body"]).strip()
    return block


def _ad_format_id(ad: dict[str, Any]) -> str:
    raw = ad.get("format")
    if isinstance(raw, dict):
        return str(raw.get("id") or "").upper()
    return str(raw or "").upper()


def _pattern_for_llm(
    fmt: str,
    archetype_id: str,
    catalog: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    wanted = str(archetype_id or "").strip()
    if not wanted:
        return None
    for item in catalog.get(fmt) or []:
        if str(item.get("id") or "").strip() == wanted:
            payload = {"id": wanted}
            label = str(item.get("label") or "").strip()
            if label:
                payload["label"] = label
            return payload
    return {"id": wanted}


def _llm_planned_ad(
    plan: dict[str, Any],
    *,
    effective_config: dict[str, Any],
    languages: tuple[str, ...],
    catalog: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    fmt = _ad_format_id(plan)
    payload: dict[str, Any] = {
        "format": format_layer(effective_config, fmt),
        "persona": _persona_for_llm(
            plan.get("persona") if isinstance(plan.get("persona"), dict) else {},
            languages,
        ),
    }
    hypothesis = plan.get("hypothesis") if isinstance(plan.get("hypothesis"), dict) else {}
    layer = hypothesis_layer(
        effective_config,
        str(hypothesis.get("type") or ""),
        str(hypothesis.get("variant") or ""),
    )
    if layer:
        payload["hypothesis"] = layer
    if isinstance(plan.get("creative_concept"), dict):
        payload["creative_concept"] = plan["creative_concept"]
    pattern = _pattern_for_llm(
        fmt,
        str(plan.get("visual_archetype") or ""),
        catalog,
    )
    if pattern:
        payload["format_pattern"] = pattern
    return compact(payload) or payload


def _copy_output_schema(
    planned: list[dict[str, Any]],
    languages: tuple[str, ...],
    effective_config: dict[str, Any],
) -> dict[str, Any]:
    ads = []
    for plan in planned:
        fmt = _ad_format_id(plan)
        fields = format_output_fields(effective_config, fmt)
        block = {
            field: (
                ["string"]
                if field == "bullets"
                else "string"
            )
            for field in fields
        }
        ads.append(
            {
                "copy": {
                    language: dict(block)
                    for language in languages
                }
            }
        )
    return {"ads": ads}


def _normalize_copy(
    response: dict[str, Any],
    planned: list[dict[str, Any]],
    languages: tuple[str, ...],
) -> dict[str, Any]:
    candidates = response.get("ads") if isinstance(response.get("ads"), list) else []
    ads = []
    for index, plan in enumerate(planned):
        candidate = (
            candidates[index]
            if index < len(candidates) and isinstance(candidates[index], dict)
            else {}
        )
        merged = {
            **plan,
            "format": _ad_format_id(plan),
            "copy": {
                language: _normalized_language_block(
                    candidate,
                    language,
                )
                for language in languages
            },
        }
        angle = str(plan.get("concept_angle") or "").strip()
        if angle:
            merged["concept_angle"] = angle
        ads.append(merged)
    return {"default_aspect_ratio": "4:5", "ads": ads}


def _validation_error(
    copy_batch: dict[str, Any],
    languages: tuple[str, ...],
    effective_config: dict[str, Any] | None = None,
) -> str | None:
    optional = {"trust_line"}
    for ad in copy_batch.get("ads", []):
        fmt = _ad_format_id(ad)
        fields = format_output_fields(effective_config, fmt)
        copy = ad.get("copy") if isinstance(ad.get("copy"), dict) else {}
        for language in languages:
            block = copy.get(language) if isinstance(copy.get(language), dict) else {}
            for field in fields:
                if field in optional:
                    continue
                value = block.get(field)
                if field == "bullets":
                    items = value if isinstance(value, list) else []
                    if not any(str(item or "").strip() for item in items):
                        return f"{field}_missing"
                    continue
                if field == "support_line":
                    text = str(
                        block.get("support_line")
                        or block.get("subheadline")
                        or ""
                    ).strip()
                else:
                    text = str(value or "").strip()
                if not text:
                    return f"{field}_missing"
    return None


def _background(raw: dict[str, Any]) -> dict[str, Any]:
    defaults = {
        "base": "a clean product arrangement",
        "surface": ["neutral studio surface"],
        "environment": ["minimal studio"],
        "lighting": ["soft daylight"],
        "mood": ["calm confidence"],
        "camera": ["eye-level product shot"],
        "color_tone": ["balanced brand colors"],
    }
    return {
        **defaults,
        **raw,
        **{
            key: value
            for key, value in raw.items()
            if value not in (None, "", [])
        },
    }


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _safe_provider_error_detail(
    response: requests.Response | None,
    api_key: str,
) -> str:
    if response is None:
        return ""
    return _safe_provider_error_text(
        str(getattr(response, "text", "") or ""),
        api_key,
    )


def _safe_provider_error_text(detail: str, api_key: str) -> str:
    detail = str(detail or "")[:4000]
    if api_key:
        detail = detail.replace(api_key, "[REDACTED]")
    detail = re.sub(
        r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+",
        "Bearer [REDACTED]",
        detail,
    )
    detail = re.sub(
        r"\bsk-[A-Za-z0-9_-]{8,}\b",
        "[REDACTED]",
        detail,
    )
    detail = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]+", " ", detail)
    return detail[:2000]


def _safe_model_output_detail(
    validation_error: str,
    response: dict[str, Any],
) -> str:
    raw = json.dumps(
        response,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    raw = re.sub(
        r'(?i)("(?:api[_-]?key|authorization)"\s*:\s*")[^"]*(")',
        r"\1[REDACTED]\2",
        raw,
    )
    raw = re.sub(
        r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+",
        "Bearer [REDACTED]",
        raw,
    )
    raw = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", raw)
    return f"{validation_error}. Raw model response: {raw}"[:2000]


def _trace_request_metadata(request: dict[str, Any]) -> dict[str, Any]:
    planned = request.get("planned_ads")
    languages = request.get("languages")
    return {
        "task": str(request.get("task") or ""),
        "planned_ad_count": len(planned) if isinstance(planned, list) else 0,
        "languages": [
            str(value)
            for value in (languages if isinstance(languages, list) else [])
        ],
        "request_sha256": _sha256_json(request),
    }


def _trace_text(value: Any, api_key: str = "") -> str:
    text = str(value or "")
    if api_key:
        text = text.replace(api_key, "[REDACTED]")
    text = re.sub(
        r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+",
        "Bearer [REDACTED]",
        text,
    )
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED]", text)
    return text[:MAX_TRACE_TEXT]


def _trace_request_payload(request: dict[str, Any], api_key: str = "") -> dict[str, Any]:
    payload = _trace_request_metadata(request)
    payload["prompt"] = _trace_text(
        json.dumps(request, ensure_ascii=False),
        api_key,
    )
    return payload


def generate_structured_prompt_bundle(
    *,
    run_id: str,
    run_number: int,
    settings: dict[str, Any],
    effective_config: dict[str, Any],
    provider_name: str,
    provider_model: str,
    generate: GenerateCallable,
    reuse_locks: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    planned = _planned_ads(settings, effective_config)
    share_background = bool(settings.get("share_background_across_personas"))
    reuse_locks = reuse_locks if isinstance(reuse_locks, dict) else {}
    visual_locks = reuse_locks.get("visual") if isinstance(reuse_locks.get("visual"), dict) else {}
    background_locks = (
        reuse_locks.get("background") if isinstance(reuse_locks.get("background"), dict) else {}
    )
    planned = _apply_visual_pattern_reuse(
        planned,
        visual_locks,
        share_across_personas=share_background,
    )
    languages = _LANGUAGES.get(
        str(settings.get("language_mode") or "EN").upper(),
        ("EN",),
    )
    product_document = str(effective_config.get("product_master_doc") or "").strip()
    if not product_document:
        raise ValueError("Product Master Doc is empty")
    starting_prompt = str(effective_config.get("copy_starting_prompt") or "").strip()
    catalog = _archetype_catalog(effective_config)
    has_hypothesis = any(
        isinstance(item.get("hypothesis"), dict)
        and str(item.get("hypothesis", {}).get("type") or "") not in {"", "none"}
        for item in planned
    )
    request = compact(
        {
            "task": "Generate structured advertising copy as JSON",
            "starting_prompt": starting_prompt,
            "product_document": product_document,
            "planned_ads": [
                _llm_planned_ad(
                    item,
                    effective_config=effective_config,
                    languages=languages,
                    catalog=catalog,
                )
                for item in planned
            ],
            "languages": list(languages),
            "guardrails": guardrails(
                effective_config,
                hypothesis=has_hypothesis,
            ),
            "output_schema": _copy_output_schema(
                planned,
                languages,
                effective_config,
            ),
        }
    )
    response = generate(request, False)
    copy_batch = _normalize_copy(response, planned, languages)
    error = _validation_error(copy_batch, languages, effective_config)
    repair_count = 0
    if error:
        repair_count = 1
        response = generate(
            {
                "task": "Repair structured copy validation errors and return JSON only",
                "validation_error": error,
                "required_output_schema": request["output_schema"],
                "original_request": request,
                "invalid_response": response,
            },
            True,
        )
        copy_batch = _normalize_copy(response, planned, languages)
        error = _validation_error(copy_batch, languages, effective_config)
    if error:
        raise ProviderCallError(
            code="provider_invalid_output",
            provider=provider_name,
            model=provider_model,
            duration_ms=int((time.monotonic() - started) * 1000),
            http_status=200,
            error_detail=_safe_model_output_detail(error, response),
        )

    backgrounds = _resolve_backgrounds(effective_config)
    templates = _json_config(
        effective_config.get("prompt_assembler_templates"), {}
    ) or None
    background_cache: dict[str, tuple[dict[str, Any], int]] = {}
    prompts: list[dict[str, Any]] = []
    for ad_index, ad in enumerate(copy_batch["ads"], start=1):
        fmt = str(ad["format"]).upper()
        persona = ad["persona"]
        persona_no = int(persona["number"])
        archetype_id = str(ad.get("visual_archetype") or "").strip()
        archetype = _resolve_archetype(fmt, archetype_id, catalog)
        archetype_id = str(archetype.get("id") or archetype_id)
        lock = _lookup_background_lock(
            background_locks,
            fmt,
            persona_no,
            archetype_id,
            share_background,
        )
        cache_key = (
            fmt if share_background else f"{fmt}::P{persona_no:02d}"
        )
        selected_background: dict[str, Any]
        background_seed: int
        if lock:
            slot_id = str(lock.get("background_slot") or "").strip()
            try:
                selected_background = _background(
                    generate_ads.get_background_by_id(backgrounds, fmt, slot_id)
                ) if slot_id else _background(
                    _pick_background_slot(
                        backgrounds,
                        fmt,
                        int(run_number) + ad_index - 1,
                    )
                )
            except RuntimeError:
                selected_background = _background(
                    _pick_background_slot(
                        backgrounds,
                        fmt,
                        int(run_number) + ad_index - 1,
                    )
                )
            seed = lock.get("background_seed")
            background_seed = (
                int(seed)
                if isinstance(seed, int)
                else random.Random(int(run_number) + ad_index * 101).randint(
                    1, 2_147_483_647
                )
            )
            background_cache[cache_key] = (selected_background, background_seed)
        elif cache_key in background_cache:
            selected_background, background_seed = background_cache[cache_key]
        else:
            selected_background = _background(
                _pick_background_slot(
                    backgrounds,
                    fmt,
                    int(run_number) + ad_index - 1,
                )
            )
            background_seed = random.Random(
                int(run_number) + ad_index * 101
            ).randint(1, 2_147_483_647)
            background_cache[cache_key] = (selected_background, background_seed)
        sentence = generate_ads.build_seeded_background_sentence(
            selected_background,
            background_seed,
            "4:5",
        )
        angle = str(ad.get("concept_angle") or "").strip()
        if not angle:
            hyp = ad.get("hypothesis") if isinstance(ad.get("hypothesis"), dict) else {}
            angle = str(hyp.get("variant") or "").strip()
        angle = angle or "none"
        concept = {"concept_angle": angle}
        for language in languages:
            block = generate_ads.parse_copy_block(
                fmt,
                language,
                ad["copy"][language],
            )
            text = generate_ads.render_prompt(
                fmt,
                language,
                "4:5",
                persona,
                block,
                concept,
                selected_background,
                background_seed,
                sentence,
                archetype,
                templates=templates,
                creative_concept=(
                    ad.get("creative_concept")
                    if isinstance(ad.get("creative_concept"), dict)
                    else None
                ),
            )
            prompt_id = "prm_" + hashlib.sha256(
                f"{run_id}:{ad_index}:{language}".encode("utf-8")
            ).hexdigest()[:24]
            concept_angle = str(concept["concept_angle"])
            # The stem is what the user reads and what the browser automation writes
            # to disk; prompt_id stays the stable internal key.
            display_stem = Path(
                generate_ads.prompt_filename(
                    fmt,
                    int(persona["number"]),
                    str(persona["name"]),
                    language,
                    concept_angle,
                )
            ).stem
            prompts.append(
                {
                    "prompt_id": prompt_id,
                    "text": text,
                    "format": fmt,
                    "persona_number": int(persona["number"]),
                    "persona_name": str(persona["name"]),
                    "language": language,
                    "aspect_ratio": "4:5",
                    "concept_angle": concept_angle,
                    "visual_archetype": archetype_id,
                    "background_id": str(selected_background.get("id") or ""),
                    "background_seed": background_seed,
                    "display_stem": display_stem,
                    "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                }
            )
    batch_size = int(settings.get("batch_size") or 10)
    return {
        "run_id": run_id,
        "status": "completed",
        "provider": provider_name,
        "model": provider_model,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "request_sha256": _sha256_json(request),
        "response_sha256": _sha256_json(response),
        "copy_sha256": _sha256_json(copy_batch),
        "copy_count": len(copy_batch["ads"]),
        "prompt_count": len(prompts),
        "prompt_ids": [prompt["prompt_id"] for prompt in prompts],
        "repair_count": repair_count,
        "batch_size": max(1, min(batch_size, 500)),
        "prompts": prompts,
    }


def provider_generate_callable(
    provider: str,
    model: str,
    config: dict[str, str],
    *,
    trace_callback: TraceCallback | None = None,
    transport: ProviderTransport | None = None,
) -> GenerateCallable:
    api_key = str(config.get("api_key") or "")
    if not api_key:
        raise ValueError("Provider API key is not configured")

    api_model = (
        model.removeprefix("opencode/")
        if provider == "opencode"
        else model
    )

    def generate(request: dict[str, Any], repair: bool = False) -> dict[str, Any]:
        started = time.monotonic()
        response: requests.Response | None = None
        endpoint = ""
        http_status: int | None = None
        response_text = ""

        def emit(
            *,
            status: str,
            code: str = "",
            error_detail: str = "",
            usage: dict[str, Any] | None = None,
            response_content: str = "",
        ) -> tuple[bool, str]:
            if trace_callback is None:
                return False, "not_configured"
            try:
                trace_callback(
                    {
                        "provider": provider,
                        "model": model,
                        "api_model": api_model,
                        "endpoint": endpoint,
                        "label": "repair" if repair else "copy",
                        "status": status,
                        "http_status": http_status,
                        "duration_ms": int(
                            (time.monotonic() - started) * 1000
                        ),
                        "error_code": code,
                        "error_detail": error_detail,
                        "request": _trace_request_payload(request, api_key),
                        "response": {
                            "usage": usage or {},
                            "content": _trace_text(response_content, api_key),
                        },
                    }
                )
                return True, ""
            except Exception as exc:
                return False, type(exc).__name__

        try:
            if provider == "google_gemini":
                endpoint = (
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{api_model}:generateContent"
                )
                request_body = {
                    "contents": [
                        {
                            "role": "user",
                            "parts": [
                                {
                                    "text": json.dumps(
                                        request, ensure_ascii=False
                                    )
                                }
                            ],
                        }
                    ],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "temperature": 0.3 if repair else 0.7,
                    },
                }
            else:
                api_url = str(config.get("api_url") or "").rstrip("/")
                if not api_url.startswith(("http://", "https://")):
                    raise ValueError("OpenCode API URL is invalid")
                endpoint = f"{api_url}/chat/completions"
                request_body = {
                    "model": api_model,
                    "messages": [
                        {
                            "role": "user",
                            "content": json.dumps(
                                request, ensure_ascii=False
                            ),
                        }
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.3 if repair else 0.7,
                }

            if transport is not None:
                relayed = transport(
                    {
                        "provider": provider,
                        "endpoint": endpoint,
                        "api_key": api_key,
                        "request_body": request_body,
                    }
                )
                if not isinstance(relayed, dict):
                    raise TypeError("Provider relay result is invalid")
                transport_error = str(
                    relayed.get("transport_error") or ""
                )
                if transport_error:
                    trace_persisted, trace_error = emit(
                        status="failed",
                        code="provider_relay_transport_error",
                    )
                    raise ProviderCallError(
                        code="provider_relay_transport_error",
                        provider=provider,
                        model=model,
                        duration_ms=int(
                            (time.monotonic() - started) * 1000
                        ),
                        error_detail=transport_error[:100],
                        trace_persisted=trace_persisted,
                        trace_persistence_error=trace_error,
                    )
                http_status = int(relayed.get("http_status") or 0)
                response_text = str(relayed.get("body") or "")
                if not 200 <= http_status < 300:
                    detail = _safe_provider_error_text(
                        response_text,
                        api_key,
                    )
                    trace_persisted, trace_error = emit(
                        status="failed",
                        code="provider_http_error",
                        error_detail=detail,
                        response_content=response_text,
                    )
                    raise ProviderCallError(
                        code="provider_http_error",
                        provider=provider,
                        model=model,
                        duration_ms=int(
                            (time.monotonic() - started) * 1000
                        ),
                        http_status=http_status,
                        error_detail=detail,
                        trace_persisted=trace_persisted,
                        trace_persistence_error=trace_error,
                    )
                raw = json.loads(response_text)
            else:
                headers = (
                    {"x-goog-api-key": api_key}
                    if provider == "google_gemini"
                    else {"Authorization": f"Bearer {api_key}"}
                )
                response = requests.post(
                    endpoint,
                    headers=headers,
                    json=request_body,
                    timeout=None,
                )
                http_status = int(response.status_code)
                response_text = str(response.text or "")
                response.raise_for_status()
                raw = response.json()

            if provider == "google_gemini":
                text = raw["candidates"][0]["content"]["parts"][0]["text"]
                usage = raw.get("usageMetadata") or {}
            else:
                text = raw["choices"][0]["message"]["content"]
                usage = raw.get("usage") or {}
            clean = str(text).strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            parsed = json.loads(clean)
            if not isinstance(parsed, dict):
                raise ValueError("Provider response is not a JSON object")
            emit(status="completed", usage=usage, response_content=clean)
            return parsed
        except requests.Timeout as exc:
            trace_persisted, trace_error = emit(
                status="failed",
                code="provider_timeout",
            )
            raise ProviderCallError(
                code="provider_timeout",
                provider=provider,
                model=model,
                duration_ms=int((time.monotonic() - started) * 1000),
                trace_persisted=trace_persisted,
                trace_persistence_error=trace_error,
            ) from exc
        except requests.RequestException as exc:
            detail = _safe_provider_error_detail(response, api_key)
            trace_persisted, trace_error = emit(
                status="failed",
                code="provider_http_error",
                error_detail=detail,
                response_content=response_text,
            )
            raise ProviderCallError(
                code="provider_http_error",
                provider=provider,
                model=model,
                duration_ms=int((time.monotonic() - started) * 1000),
                http_status=http_status,
                error_detail=detail,
                trace_persisted=trace_persisted,
                trace_persistence_error=trace_error,
            ) from exc
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            detail = _safe_provider_error_text(response_text, api_key)
            trace_persisted, trace_error = emit(
                status="failed",
                code="provider_invalid_response",
                error_detail=detail,
                response_content=response_text,
            )
            raise ProviderCallError(
                code="provider_invalid_response",
                provider=provider,
                model=model,
                duration_ms=int((time.monotonic() - started) * 1000),
                http_status=http_status,
                error_detail=detail,
                trace_persisted=trace_persisted,
                trace_persistence_error=trace_error,
            ) from exc

    return generate
