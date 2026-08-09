from __future__ import annotations

import hashlib
import json
import random
import time
from typing import Any, Callable

import requests

from scripts import generate_ads


GenerateCallable = Callable[[dict[str, Any], bool], dict[str, Any]]
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
    ) -> None:
        super().__init__(code)
        self.code = code
        self.provider = provider
        self.model = model
        self.duration_ms = duration_ms
        self.http_status = http_status


def _json_config(value: Any, fallback: Any) -> Any:
    if isinstance(value, type(fallback)):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except json.JSONDecodeError:
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


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
    return {
        "number": number,
        "name": str(source.get("persona_name") or source.get("name") or f"Persona {number}"),
        "pain_en": str(source.get("core_pattern") or "The current routine is difficult to sustain."),
        "desire_en": str(source.get("relevant_ok_kit_role") or "A practical routine that fits daily life."),
        "friction_en": str(source.get("why_it_failed") or "Past approaches felt difficult to maintain."),
        "proof_needed_en": str(source.get("guardrail") or "Use verified product facts only."),
        "tone_cue_en": "Practical, empathetic, and confidence-building.",
        "pain_hi": "मौजूदा रूटीन को लगातार निभाना मुश्किल है।",
        "desire_hi": "रोज़मर्रा में फिट होने वाला आसान रूटीन चाहिए।",
        "friction_hi": "पुराने तरीके लगातार निभाना मुश्किल था।",
        "proof_needed_hi": "केवल सत्यापित प्रोडक्ट तथ्यों का उपयोग करें।",
        "tone_cue_hi": "सरल, भरोसेमंद और व्यावहारिक।",
    }


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
    planned: list[dict[str, Any]] = []
    for raw_number in selected:
        number = int(raw_number)
        formats = per_persona.get(str(number))
        formats = formats if isinstance(formats, list) and formats else global_formats
        for fmt in formats:
            normalized_format = str(fmt or "").upper()
            if normalized_format not in {"HERO", "BA", "TEST", "FEAT", "UGC"}:
                raise ValueError("Unsupported ad format")
            for creative_index in range(1, multiplier + 1):
                planned.append(
                    {
                        "format": normalized_format,
                        "creative_index": creative_index,
                        "creative_total": multiplier,
                        "concept_angle": "desired_outcome",
                        "persona": _persona(number, personas.get(number, {})),
                    }
                )
    if not planned or len(planned) > 500:
        raise ValueError("Structured ad plan exceeds the 500-ad limit")
    return planned


def _normalize_copy(
    response: dict[str, Any], planned: list[dict[str, Any]]
) -> dict[str, Any]:
    candidates = response.get("ads") if isinstance(response.get("ads"), list) else []
    ads = []
    for index, plan in enumerate(planned):
        candidate = (
            candidates[index]
            if index < len(candidates) and isinstance(candidates[index], dict)
            else {}
        )
        ads.append(
            {
                **plan,
                "concept_angle": str(
                    candidate.get("concept_angle")
                    or plan.get("concept_angle")
                    or "desired_outcome"
                ),
                "copy": (
                    candidate.get("copy")
                    if isinstance(candidate.get("copy"), dict)
                    else {}
                ),
            }
        )
    return {"default_aspect_ratio": "4:5", "ads": ads}


def _validation_error(
    copy_batch: dict[str, Any], languages: tuple[str, ...]
) -> str | None:
    for ad in copy_batch.get("ads", []):
        fmt = str(ad.get("format") or "")
        copy = ad.get("copy") if isinstance(ad.get("copy"), dict) else {}
        for language in languages:
            block = copy.get(language) if isinstance(copy.get(language), dict) else {}
            if not str(block.get("headline") or "").strip():
                return "headline_missing"
            if not str(block.get("cta") or "").strip():
                return "cta_missing"
            if fmt in {"HERO", "UGC"} and not str(
                block.get("support_line") or block.get("subheadline") or ""
            ).strip():
                return "support_line_missing"
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


def generate_structured_prompt_bundle(
    *,
    run_id: str,
    run_number: int,
    settings: dict[str, Any],
    effective_config: dict[str, Any],
    provider_name: str,
    provider_model: str,
    generate: GenerateCallable,
) -> dict[str, Any]:
    started = time.monotonic()
    planned = _planned_ads(settings, effective_config)
    languages = _LANGUAGES.get(
        str(settings.get("language_mode") or "EN").upper(),
        ("EN",),
    )
    product_document = str(effective_config.get("product_master_doc") or "").strip()
    if not product_document:
        raise ValueError("Product Master Doc is empty")
    request = {
        "task": "Generate structured advertising copy as JSON",
        "product_document": product_document,
        "planned_ads": planned,
        "languages": list(languages),
        "requirements": {
            "json_only": True,
            "preserve_persona_and_format": True,
            "no_unverified_claims": True,
        },
    }
    response = generate(request, False)
    copy_batch = _normalize_copy(response, planned)
    error = _validation_error(copy_batch, languages)
    repair_count = 0
    if error:
        repair_count = 1
        response = generate(
            {
                "task": "Repair structured copy validation errors and return JSON only",
                "validation_error": error,
                "original_request": request,
                "invalid_response": response,
            },
            True,
        )
        copy_batch = _normalize_copy(response, planned)
        error = _validation_error(copy_batch, languages)
    if error:
        raise ValueError("Structured copy validation failed")

    backgrounds = _json_config(effective_config.get("background_variant"), {})
    templates = _json_config(
        effective_config.get("prompt_assembler_templates"), {}
    ) or None
    prompts: list[dict[str, Any]] = []
    for ad_index, ad in enumerate(copy_batch["ads"], start=1):
        fmt = str(ad["format"]).upper()
        persona = ad["persona"]
        selected_background = _background(
            generate_ads.pick_background_slot(
                backgrounds,
                fmt,
                int(run_number) + ad_index - 1,
            )
        )
        background_seed = random.Random(
            int(run_number) + ad_index * 101
        ).randint(1, 2_147_483_647)
        sentence = generate_ads.build_seeded_background_sentence(
            selected_background,
            background_seed,
            "4:5",
        )
        concept = generate_ads.resolve_concept_fields(ad, fmt, persona)
        archetype = generate_ads.default_visual_archetype(fmt)
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
            )
            prompt_id = "prm_" + hashlib.sha256(
                f"{run_id}:{ad_index}:{language}".encode("utf-8")
            ).hexdigest()[:24]
            prompts.append(
                {
                    "prompt_id": prompt_id,
                    "text": text,
                    "format": fmt,
                    "persona_number": int(persona["number"]),
                    "persona_name": str(persona["name"]),
                    "language": language,
                    "aspect_ratio": "4:5",
                    "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                }
            )
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
        "prompts": prompts,
    }


def provider_generate_callable(
    provider: str,
    model: str,
    config: dict[str, str],
) -> GenerateCallable:
    api_key = str(config.get("api_key") or "")
    if not api_key:
        raise ValueError("Provider API key is not configured")

    def generate(request: dict[str, Any], repair: bool = False) -> dict[str, Any]:
        started = time.monotonic()
        response: requests.Response | None = None
        try:
            if provider == "google_gemini":
                response = requests.post(
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{model}:generateContent",
                    headers={"x-goog-api-key": api_key},
                    json={
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
                    },
                    timeout=(10, 120),
                )
                response.raise_for_status()
                raw = response.json()
                text = raw["candidates"][0]["content"]["parts"][0]["text"]
            else:
                api_url = str(config.get("api_url") or "").rstrip("/")
                if not api_url.startswith(("http://", "https://")):
                    raise ValueError("OpenCode API URL is invalid")
                response = requests.post(
                    f"{api_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": model,
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
                    },
                    timeout=(10, 120),
                )
                response.raise_for_status()
                raw = response.json()
                text = raw["choices"][0]["message"]["content"]
            clean = str(text).strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            parsed = json.loads(clean)
            if not isinstance(parsed, dict):
                raise ValueError("Provider response is not a JSON object")
            return parsed
        except requests.Timeout as exc:
            raise ProviderCallError(
                code="provider_timeout",
                provider=provider,
                model=model,
                duration_ms=int((time.monotonic() - started) * 1000),
            ) from exc
        except requests.RequestException as exc:
            raise ProviderCallError(
                code="provider_http_error",
                provider=provider,
                model=model,
                duration_ms=int((time.monotonic() - started) * 1000),
                http_status=response.status_code if response is not None else None,
            ) from exc
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderCallError(
                code="provider_invalid_response",
                provider=provider,
                model=model,
                duration_ms=int((time.monotonic() - started) * 1000),
                http_status=response.status_code if response is not None else None,
            ) from exc

    return generate
