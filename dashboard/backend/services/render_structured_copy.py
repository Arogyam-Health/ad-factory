from __future__ import annotations

"""Live structured-copy assembler.

Copy-LLM layers come from dashboard/backend/copy_system/ via copy_system.py.
copy_prompt_templates.json is read only for visual_archetypes after copy exists.
copy_starting_prompt is sent as starting_prompt when non-empty.
"""

import hashlib
import html
import json
import random
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import requests

from dashboard.backend.services.copy_system import (
    OPTIONAL_COPY_FIELDS,
    compact,
    copy_repair_task,
    copy_task,
    extra_persona_language_keys,
    format_layer,
    format_output_fields,
    guardrails,
    hypothesis_layer,
    language_layers,
    language_persona_map,
    normalize_format_id,
    persona_fallbacks,
    persona_source_map,
    pick_persona_field,
    resolve_language_ids,
)
from dashboard.backend.services.llm_trace import MAX_TRACE_TEXT
from dashboard.backend.services.user_config import resolve_selected_concept
from dashboard.backend.services.visual_archetypes import (
    FORMATS,
    LLM_DECIDE_ID,
    _archetype_groups,
    bundled_visual_archetypes,
    format_ids_from_formats,
    llm_decide_archetype,
    pick_random_archetype,
)
from dashboard.backend.services import generate_ads


GenerateCallable = Callable[[dict[str, Any], bool], dict[str, Any]]
TraceCallback = Callable[[dict[str, Any]], None]
ProviderTransport = Callable[[dict[str, Any]], dict[str, Any]]


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


def _persona(
    number: int,
    source: dict[str, Any],
    effective_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    aliases = persona_source_map(effective_config)
    fallbacks = persona_fallbacks(effective_config, "EN")
    name = pick_persona_field(source, aliases.get("name") or ["persona_name", "name"])
    payload = {
        "number": number,
        "name": name or f"Persona {number}",
        "pain_en": pick_persona_field(source, aliases.get("pain_en") or ["pain_en"])
        or fallbacks.get("pain_en")
        or "",
        "desire_en": pick_persona_field(source, aliases.get("desire_en") or ["desire_en"])
        or fallbacks.get("desire_en")
        or "",
        "friction_en": pick_persona_field(source, aliases.get("friction_en") or ["friction_en"])
        or fallbacks.get("friction_en")
        or "",
        "proof_needed_en": pick_persona_field(
            source, aliases.get("proof_needed_en") or ["proof_needed_en"]
        )
        or fallbacks.get("proof_needed_en")
        or "",
        "tone_cue_en": pick_persona_field(source, aliases.get("tone_cue_en") or ["tone_cue_en"])
        or fallbacks.get("tone_cue_en")
        or "",
    }
    for key in extra_persona_language_keys(effective_config):
        value = str(source.get(key) or "").strip()
        if value:
            payload[key] = value
    return payload


def _persona_for_llm(
    persona: dict[str, Any],
    languages: tuple[str, ...],
    effective_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "number": persona.get("number"),
        "name": persona.get("name"),
    }
    for lang in languages:
        for dest, source in language_persona_map(effective_config, lang).items():
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
    wanted = format_ids_from_formats(effective_config.get("ad_formats"))
    groups = _archetype_groups(
        templates.get("visual_archetypes") if isinstance(templates, dict) else None
    )
    if not any(groups.get(fmt) for fmt in (wanted or FORMATS)):
        groups = _archetype_groups(bundled_visual_archetypes())
    keys = list(wanted)
    for ident in groups:
        if ident not in keys:
            keys.append(ident)
    if not keys:
        keys = list(FORMATS)
    catalog: dict[str, list[dict[str, Any]]] = {}
    for fmt in keys:
        items = groups.get(fmt) or []
        catalog[fmt] = [item for item in items if isinstance(item, dict)]
    return catalog


def _resolve_archetype(
    fmt: str,
    archetype_id: str,
    catalog: dict[str, list[dict[str, Any]]],
    *,
    seed: int = 1,
    used_ids: set[str] | None = None,
    llm_prompt: str = "",
) -> dict[str, Any]:
    wanted = str(archetype_id or "").strip()
    if wanted == LLM_DECIDE_ID:
        return llm_decide_archetype(llm_prompt)
    for item in catalog.get(fmt) or []:
        if str(item.get("id") or "").strip() == wanted:
            return item
    if wanted:
        try:
            return generate_ads.find_visual_archetype(fmt, wanted)
        except RuntimeError:
            pass
    items = catalog.get(fmt) or []
    picked = pick_random_archetype(items, seed=seed, used_ids=used_ids)
    if picked:
        return picked
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
            normalized_format = normalize_format_id(fmt)
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
                    "persona": _persona(
                        number,
                        personas.get(number, {}),
                        effective_config,
                    ),
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
    extra_fields: tuple[str, ...] | list[str] = (),
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
    allowed: list[str] = []
    seen: set[str] = set()
    for key in (
        "headline",
        "cta",
        "subheadline",
        "support_line",
        "context_line",
        "trust_line",
        "attribution",
        "bullets",
        *extra_fields,
    ):
        ident = str(key or "").strip()
        if ident and ident not in seen:
            seen.add(ident)
            allowed.append(ident)
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
    if not wanted or wanted == LLM_DECIDE_ID:
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
            effective_config,
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


def reject_legacy_copy_llm_request(request: dict[str, Any]) -> None:
    """Refuse the old plan-dump request that leaked image keys and product_truths."""
    if not isinstance(request, dict):
        raise ValueError("Copy request is invalid")
    if "requirements" in request:
        raise ValueError("Copy request must not include the legacy requirements object")
    schema = request.get("output_schema")
    if not isinstance(schema, dict) or "ads" not in schema or "product_truths" in schema:
        raise ValueError("Copy output_schema must contain only ads")
    if any("product_truths" in item for item in (schema.get("ads") or []) if isinstance(item, dict)):
        raise ValueError("Copy output_schema must not ask for product_truths")
    languages = request.get("languages")
    if (
        not isinstance(languages, list)
        or not languages
        or not all(isinstance(item, dict) and str(item.get("id") or "").strip() for item in languages)
    ):
        raise ValueError("Copy languages must be layer objects")
    planned = request.get("planned_ads")
    if not isinstance(planned, list) or not planned:
        raise ValueError("Copy planned_ads are missing")
    language_ids = {
        str(item.get("id") or "").strip().upper()
        for item in languages
        if isinstance(item, dict)
    }
    for ad in planned:
        if not isinstance(ad, dict):
            raise ValueError("Copy planned_ads entries must be objects")
        fmt = ad.get("format")
        if not isinstance(fmt, dict) or not str(fmt.get("id") or "").strip():
            raise ValueError("Copy planned_ads.format must be a layer object")
        for key in (
            "background_group_key",
            "share_background_across_personas",
            "creative_index",
            "creative_total",
            "concept_angle",
        ):
            if key in ad:
                raise ValueError(f"Copy request must not include {key}")
        hypothesis = ad.get("hypothesis")
        if isinstance(hypothesis, dict) and (
            "hypothesis_id" in hypothesis or "variant" in hypothesis
        ):
            raise ValueError("Copy hypothesis must use style layers, not plan variants")
        persona = ad.get("persona") if isinstance(ad.get("persona"), dict) else {}
        if "HI" not in language_ids:
            if any(str(key).endswith("_hi") for key in persona):
                raise ValueError("Copy persona must not send Hindi fillers on a non-Hindi run")
        if "HINGLISH" not in language_ids:
            if any(str(key).endswith("_hinglish") for key in persona):
                raise ValueError("Copy persona must not send Hinglish fillers on a non-Hinglish run")
    if not isinstance(request.get("guardrails"), list) or not request["guardrails"]:
        raise ValueError("Copy request is missing guardrails")
    if not str(request.get("product_document") or "").strip():
        raise ValueError("Copy request is missing the product document")


def assemble_copy_llm_request(
    *,
    planned: list[dict[str, Any]],
    languages: tuple[str, ...],
    effective_config: dict[str, Any],
    product_document: str,
    starting_prompt: str = "",
) -> dict[str, Any]:
    catalog = _archetype_catalog(effective_config)
    has_hypothesis = any(
        isinstance(item.get("hypothesis"), dict)
        and str(item.get("hypothesis", {}).get("type") or "") not in {"", "none"}
        for item in planned
    )
    request = compact(
        {
            "task": copy_task(effective_config),
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
            "languages": language_layers(effective_config, languages),
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
    reject_legacy_copy_llm_request(request)
    return request


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
    effective_config: dict[str, Any] | None = None,
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
                    extra_fields=format_output_fields(
                        effective_config,
                        _ad_format_id(plan),
                    ),
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
    optional = OPTIONAL_COPY_FIELDS
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


def _background(
    raw: dict[str, Any],
    templates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    T = templates if isinstance(templates, dict) and templates else generate_ads.PROMPT_ASSEMBLER_TEMPLATES
    configured = T.get("background_field_defaults") if isinstance(T.get("background_field_defaults"), dict) else {}
    defaults = {
        **generate_ads.DEFAULT_BACKGROUND_FIELDS,
        **{
            key: value
            for key, value in configured.items()
            if value not in (None, "", [])
        },
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
            str(value.get("id") if isinstance(value, dict) else value)
            for value in (languages if isinstance(languages, list) else [])
            if str(value.get("id") if isinstance(value, dict) else value).strip()
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


BROWSER_WARMUP_TASK = (
    "Read this product context completely. Confirm you have read it. "
    "Do not generate ads yet."
)
_BROWSER_SESSION_PREFIX = "bcs_"


def parse_browser_copy_json(text: str) -> dict[str, Any]:
    clean = html.unescape(str(text or "")).replace("\xa0", " ")
    clean = re.sub(r"(?i)<br\s*/?>", "\n", clean)
    clean = re.sub(r"<[^>]+>", "", clean)
    clean = clean.strip()
    for prefix in ("ChatGPT said:", "Gemini said:"):
        if clean.startswith(prefix):
            clean = clean[len(prefix) :].strip()
    if clean.startswith("```"):
        clean = clean.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    start = clean.find("{")
    end = clean.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Browser copy response is not JSON")
    parsed = json.loads(clean[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Browser copy response is not a JSON object")
    return parsed


def assemble_browser_warmup_message(
    *,
    product_document: str,
    starting_prompt: str = "",
) -> dict[str, Any]:
    document = str(product_document or "").strip()
    if not document:
        raise ValueError("Product Master Doc is empty")
    return compact(
        {
            "task": BROWSER_WARMUP_TASK,
            "starting_prompt": starting_prompt,
            "product_document": document,
        }
    ) or {
        "task": BROWSER_WARMUP_TASK,
        "product_document": document,
    }


def assemble_browser_chunk_request(
    *,
    planned: list[dict[str, Any]],
    languages: tuple[str, ...],
    effective_config: dict[str, Any],
    product_document: str,
    starting_prompt: str = "",
) -> dict[str, Any]:
    request = assemble_copy_llm_request(
        planned=planned,
        languages=languages,
        effective_config=effective_config,
        product_document=product_document,
        starting_prompt=starting_prompt,
    )
    request.pop("product_document", None)
    request.pop("starting_prompt", None)
    return compact(request) or request


def _chunk_items(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    step = max(1, min(int(size or 10), 500))
    return [items[index : index + step] for index in range(0, len(items), step)]


def _prompts_from_copy_batch(
    *,
    copy_batch: dict[str, Any],
    languages: tuple[str, ...],
    effective_config: dict[str, Any],
    run_id: str,
    run_number: int,
    share_background: bool,
    background_locks: dict[str, Any],
    catalog: dict[str, list[dict[str, Any]]],
    llm_prompt: str,
) -> list[dict[str, Any]]:
    backgrounds = _resolve_backgrounds(effective_config)
    templates = _json_config(
        effective_config.get("prompt_assembler_templates"), {}
    ) or None
    background_cache: dict[str, tuple[dict[str, Any], int]] = {}
    prompts: list[dict[str, Any]] = []
    used_archetypes: dict[str, set[str]] = {}
    for ad_index, ad in enumerate(copy_batch["ads"], start=1):
        fmt = str(ad["format"]).upper()
        persona = ad["persona"]
        persona_no = int(persona["number"])
        archetype_id = str(ad.get("visual_archetype") or "").strip()
        used_ids = used_archetypes.setdefault(fmt, set())
        archetype = _resolve_archetype(
            fmt,
            archetype_id,
            catalog,
            seed=int(run_number) + ad_index * 17 + persona_no * 13,
            used_ids=used_ids,
            llm_prompt=llm_prompt,
        )
        archetype_id = str(archetype.get("id") or archetype_id)
        if archetype_id and archetype_id != LLM_DECIDE_ID:
            used_ids.add(archetype_id)
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
                    generate_ads.get_background_by_id(backgrounds, fmt, slot_id),
                    templates,
                ) if slot_id else _background(
                    _pick_background_slot(
                        backgrounds,
                        fmt,
                        int(run_number) + ad_index - 1,
                    ),
                    templates,
                )
            except RuntimeError:
                selected_background = _background(
                    _pick_background_slot(
                        backgrounds,
                        fmt,
                        int(run_number) + ad_index - 1,
                    ),
                    templates,
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
                ),
                templates,
            )
            background_seed = random.Random(
                int(run_number) + ad_index * 101
            ).randint(1, 2_147_483_647)
            background_cache[cache_key] = (selected_background, background_seed)
        sentence = generate_ads.build_seeded_background_sentence(
            selected_background,
            background_seed,
            "4:5",
            templates,
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
                templates,
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
    return prompts


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
    languages = resolve_language_ids(
        effective_config,
        settings.get("language_mode") or "EN",
    )
    product_document = str(effective_config.get("product_master_doc") or "").strip()
    if not product_document:
        raise ValueError("Product Master Doc is empty")
    starting_prompt = str(effective_config.get("copy_starting_prompt") or "").strip()
    catalog = _archetype_catalog(effective_config)
    llm_prompt = str(effective_config.get("visual_archetype_llm_prompt") or "").strip()
    batch_size = max(1, min(int(settings.get("batch_size") or 10), 500))
    chunks = _chunk_items(planned, batch_size)
    chunk_requests: list[dict[str, Any]] = []
    raw_responses: list[dict[str, Any]] = []
    normalized_ads: list[dict[str, Any]] = []
    repair_count = 0
    last_response: dict[str, Any] = {}
    request: dict[str, Any] = {}
    response: dict[str, Any] = {}
    for chunk in chunks:
        request = assemble_copy_llm_request(
            planned=chunk,
            languages=languages,
            effective_config=effective_config,
            product_document=product_document,
            starting_prompt=starting_prompt,
        )
        chunk_requests.append(request)
        response = generate(request, False)
        last_response = response
        raw_responses.append(response)
        copy_batch = _normalize_copy(response, chunk, languages, effective_config)
        error = _validation_error(copy_batch, languages, effective_config)
        if error:
            repair_count += 1
            response = generate(
                {
                    "task": copy_repair_task(effective_config),
                    "validation_error": error,
                    "required_output_schema": request["output_schema"],
                    "original_request": request,
                    "invalid_response": response,
                },
                True,
            )
            last_response = response
            raw_responses[-1] = response
            copy_batch = _normalize_copy(response, chunk, languages, effective_config)
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
        normalized_ads.extend(copy_batch["ads"])
    copy_batch = {"default_aspect_ratio": "4:5", "ads": normalized_ads}

    prompts = _prompts_from_copy_batch(
        copy_batch=copy_batch,
        languages=languages,
        effective_config=effective_config,
        run_id=run_id,
        run_number=run_number,
        share_background=share_background,
        background_locks=background_locks,
        catalog=catalog,
        llm_prompt=llm_prompt,
    )
    return {
        "run_id": run_id,
        "status": "completed",
        "provider": provider_name,
        "model": provider_model,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "request_sha256": _sha256_json(chunk_requests or request),
        "response_sha256": _sha256_json(raw_responses or last_response or response),
        "copy_sha256": _sha256_json(copy_batch),
        "copy_count": len(copy_batch["ads"]),
        "prompt_count": len(prompts),
        "prompt_ids": [prompt["prompt_id"] for prompt in prompts],
        "repair_count": repair_count,
        "batch_size": max(1, min(batch_size, 500)),
        "prompts": prompts,
    }


def browser_copy_turn(
    *,
    transport: ProviderTransport,
    engine: str,
    session_id: str,
    action: str,
    prompt: str,
    expect_json: bool,
    label: str,
    trace_callback: TraceCallback | None = None,
) -> str:
    started = time.monotonic()
    http_status: int | None = None
    response_text = ""

    def emit(
        *,
        status: str,
        code: str = "",
        error_detail: str = "",
        request: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        if trace_callback is None:
            return False, "not_configured"
        try:
            payload = request if isinstance(request, dict) else {}
            trace_callback(
                {
                    "provider": "browser",
                    "model": engine,
                    "api_model": engine,
                    "endpoint": "",
                    "label": label,
                    "status": status,
                    "http_status": http_status,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "error_code": code,
                    "error_detail": error_detail,
                    "request": _trace_request_payload(payload),
                    "response": {
                        "usage": {},
                        "content": _trace_text(response_text),
                    },
                }
            )
            return True, ""
        except Exception as exc:
            return False, type(exc).__name__

    parsed_request: dict[str, Any] = {}
    if prompt:
        try:
            loaded = json.loads(prompt)
            if isinstance(loaded, dict):
                parsed_request = loaded
        except json.JSONDecodeError:
            parsed_request = {"prompt": prompt}

    relayed = transport(
        {
            "provider": "browser",
            "engine": engine,
            "action": action,
            "session_id": session_id,
            "prompt": prompt,
            "expect_json": expect_json,
        }
    )
    if not isinstance(relayed, dict):
        raise TypeError("Provider relay result is invalid")
    transport_error = str(relayed.get("transport_error") or "")
    if transport_error:
        if action == "close":
            return ""
        trace_persisted, trace_error = emit(
            status="failed",
            code="provider_relay_transport_error",
            request=parsed_request,
        )
        raise ProviderCallError(
            code="provider_relay_transport_error",
            provider="browser",
            model=engine,
            duration_ms=int((time.monotonic() - started) * 1000),
            error_detail=transport_error[:100],
            trace_persisted=trace_persisted,
            trace_persistence_error=trace_error,
        )
    http_status = int(relayed.get("http_status") or 0)
    response_text = str(relayed.get("body") or "")
    if action == "close":
        return response_text
    if not 200 <= http_status < 300:
        detail = _safe_provider_error_text(response_text, "")
        trace_persisted, trace_error = emit(
            status="failed",
            code="provider_http_error",
            error_detail=detail,
            request=parsed_request,
        )
        raise ProviderCallError(
            code="provider_http_error",
            provider="browser",
            model=engine,
            duration_ms=int((time.monotonic() - started) * 1000),
            http_status=http_status,
            error_detail=detail,
            trace_persisted=trace_persisted,
            trace_persistence_error=trace_error,
        )
    if expect_json and not response_text.strip():
        trace_persisted, trace_error = emit(
            status="failed",
            code="provider_invalid_response",
            request=parsed_request,
        )
        raise ProviderCallError(
            code="provider_invalid_response",
            provider="browser",
            model=engine,
            duration_ms=int((time.monotonic() - started) * 1000),
            http_status=http_status,
            error_detail="Browser copy response is empty",
            trace_persisted=trace_persisted,
            trace_persistence_error=trace_error,
        )
    if not expect_json and not response_text.strip():
        trace_persisted, trace_error = emit(
            status="failed",
            code="provider_invalid_response",
            request=parsed_request,
        )
        raise ProviderCallError(
            code="provider_invalid_response",
            provider="browser",
            model=engine,
            duration_ms=int((time.monotonic() - started) * 1000),
            http_status=http_status,
            error_detail="Browser warmup produced no reply",
            trace_persisted=trace_persisted,
            trace_persistence_error=trace_error,
        )
    emit(status="completed", request=parsed_request)
    return response_text


def generate_browser_structured_prompt_bundle(
    *,
    run_id: str,
    run_number: int,
    settings: dict[str, Any],
    effective_config: dict[str, Any],
    provider_name: str,
    provider_model: str,
    transport: ProviderTransport,
    reuse_locks: dict[str, dict[str, Any]] | None = None,
    trace_callback: TraceCallback | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    engine = str(provider_model or settings.get("model") or "").strip().lower()
    if engine not in {"chatgpt", "gemini"}:
        raise ValueError("Structured copy model is invalid")
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
    languages = resolve_language_ids(
        effective_config,
        settings.get("language_mode") or "EN",
    )
    product_document = str(effective_config.get("product_master_doc") or "").strip()
    if not product_document:
        raise ValueError("Product Master Doc is empty")
    starting_prompt = str(effective_config.get("copy_starting_prompt") or "").strip()
    catalog = _archetype_catalog(effective_config)
    llm_prompt = str(effective_config.get("visual_archetype_llm_prompt") or "").strip()
    batch_size = max(1, min(int(settings.get("batch_size") or 10), 500))
    session_id = _BROWSER_SESSION_PREFIX + uuid.uuid4().hex
    chunks = _chunk_items(planned, batch_size)
    chunk_requests: list[dict[str, Any]] = []
    raw_responses: list[dict[str, Any]] = []
    normalized_ads: list[dict[str, Any]] = []
    repair_count = 0
    last_response: dict[str, Any] = {}

    def turn(
        action: str,
        payload: dict[str, Any] | str,
        *,
        expect_json: bool,
        label: str,
    ) -> str:
        prompt = (
            payload
            if isinstance(payload, str)
            else json.dumps(payload, ensure_ascii=False)
        )
        return browser_copy_turn(
            transport=transport,
            engine=engine,
            session_id=session_id,
            action=action,
            prompt=prompt,
            expect_json=expect_json,
            label=label,
            trace_callback=trace_callback,
        )

    try:
        warmup = assemble_browser_warmup_message(
            product_document=product_document,
            starting_prompt=starting_prompt,
        )
        turn("new", warmup, expect_json=False, label="warmup")
        for chunk in chunks:
            request = assemble_browser_chunk_request(
                planned=chunk,
                languages=languages,
                effective_config=effective_config,
                product_document=product_document,
                starting_prompt=starting_prompt,
            )
            chunk_requests.append(request)
            try:
                response = parse_browser_copy_json(
                    turn("continue", request, expect_json=True, label="copy")
                )
            except ValueError as exc:
                raise ProviderCallError(
                    code="provider_invalid_response",
                    provider=provider_name,
                    model=engine,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    http_status=200,
                    error_detail=str(exc),
                ) from exc
            last_response = response
            raw_responses.append(response)
            copy_batch = _normalize_copy(response, chunk, languages, effective_config)
            error = _validation_error(copy_batch, languages, effective_config)
            if error:
                repair_count += 1
                try:
                    response = parse_browser_copy_json(
                        turn(
                            "repair",
                            {
                                "task": copy_repair_task(effective_config),
                                "validation_error": error,
                                "required_output_schema": request["output_schema"],
                                "original_request": request,
                                "invalid_response": response,
                            },
                            expect_json=True,
                            label="repair",
                        )
                    )
                except ValueError as exc:
                    raise ProviderCallError(
                        code="provider_invalid_response",
                        provider=provider_name,
                        model=engine,
                        duration_ms=int((time.monotonic() - started) * 1000),
                        http_status=200,
                        error_detail=str(exc),
                    ) from exc
                last_response = response
                raw_responses[-1] = response
                copy_batch = _normalize_copy(
                    response, chunk, languages, effective_config
                )
                error = _validation_error(copy_batch, languages, effective_config)
            if error:
                raise ProviderCallError(
                    code="provider_invalid_output",
                    provider=provider_name,
                    model=engine,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    http_status=200,
                    error_detail=_safe_model_output_detail(error, response),
                )
            normalized_ads.extend(copy_batch["ads"])
    finally:
        try:
            turn("close", "", expect_json=False, label="close")
        except Exception:
            pass

    copy_batch = {"default_aspect_ratio": "4:5", "ads": normalized_ads}
    prompts = _prompts_from_copy_batch(
        copy_batch=copy_batch,
        languages=languages,
        effective_config=effective_config,
        run_id=run_id,
        run_number=run_number,
        share_background=share_background,
        background_locks=background_locks,
        catalog=catalog,
        llm_prompt=llm_prompt,
    )
    return {
        "run_id": run_id,
        "status": "completed",
        "provider": provider_name,
        "model": engine,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "request_sha256": _sha256_json(chunk_requests),
        "response_sha256": _sha256_json(raw_responses or last_response),
        "copy_sha256": _sha256_json(copy_batch),
        "copy_count": len(copy_batch["ads"]),
        "prompt_count": len(prompts),
        "prompt_ids": [prompt["prompt_id"] for prompt in prompts],
        "repair_count": repair_count,
        "batch_size": batch_size,
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
