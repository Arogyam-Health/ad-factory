from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from dashboard.backend.pipeline.paths import COPY_ARCH_PATH, COPY_PROMPTS_PATH, PERSONA_SEEDS_PATH
from dashboard.backend.pipeline.user_overrides import resolve_user_config as _resolve_user_config

_PERSONA_SEED_MAPPING: dict[str, Any] = {
    "seed_to_payload": {
        "core_pattern": {"field": "pain_points", "wrap_list": True, "prefix": None},
        "common_indian_moments": {"field": "trust_anchors", "wrap_list": True, "prefix": None},
        "objections_raw": {"field": "objections", "wrap_list": True, "prefix": None},
        "how_kit_solves": {"field": "how_kit_solves", "wrap_list": False, "prefix": None},
        "guardrail": {"field": "guardrails", "wrap_list": True, "prefix": "Guardrail: "},
    },
    "persona_fallbacks": {
        "core_pattern": "Daily routine feels heavy and hard to sustain.",
        "common_indian_moments": "Everyday situations make it harder to stay consistent.",
        "objections_raw": "Past plans felt too strict and difficult to maintain.",
        "how_kit_solves": {},
        "guardrail": "",
    },
    "static_fields": {
        "trigger_scenarios": [],
        "language_bank": [],
        "grounded_mechanism_map": [],
    },
    "hindi_ready_default": "टोन संकेत: सरल, भरोसेमंड, व्यावहारिक",
}

_TESTIMONIAL_GUIDANCE: dict[str, Any] = {
    "EN": {
        "first_person_pattern": "\\b(i|i'm|i've|i'd|my|me)\\b",
        "weight_pattern": "\\b(weight|obesity|excess\\s*weight|kg|kilo)\\b",
        "suffix": "It finally fit my weight-loss routine.",
        "desire_template": "\"I finally found {desire_phrase} for my weight-loss goal.\"",
        "fallback": "\"I finally found a routine I can follow for weight loss every day.\"",
        "desire_field": "desire_en",
    },
    "HI": {
        "first_person_pattern": "(मैं|मेरी|मेरा|मुझे|मैंने)",
        "weight_pattern": "(वजन|मोटापा|किलो|kg)",
        "suffix": "यह मेरे वजन घटाने के लिए काम आया।",
        "desire_template": "\"मुझे आखिर {desire_phrase} वाला रूटीन मिला जो वजन घटाने में मदद करता है।\"",
        "fallback": "\"मुझे आखिर ऐसा रूटीन मिला जिसे मैं रोज निभा सकूं और वजन घटा सकूं।\"",
        "desire_field": "desire_hi",
    },
}


def load_format_visual_archetypes() -> dict[str, list[dict[str, str]]]:
    from dashboard.backend.services.visual_archetypes import format_visual_archetypes

    return format_visual_archetypes(_resolve_copy_prompts())



def classify_hook_structure(headline: str) -> str:
    """Classify a headline opening pattern for hypothesis sanity checks.

    The classifier only verifies whether a pattern is present.
    It does not prescribe which pattern should be used — that's the LLM's job.
    A headline is question-led if it opens as a question or contains an
    early question mark. Everything else is classified by visible pattern cues.
    """
    text = (headline or "").strip().lower().replace("’", "'")
    if not text:
        return "proof_led"
    if text.startswith(("why ", "what ", "how ", "when ", "can ", "could ", "will ", "want ", "need ", "tired ")) or "?" in text[:50]:
        return "question_led"
    if text.startswith(("stop", "start", "try", "see", "check", "take")):
        return "command_led"
    contrast_terms = ["before", "after", "without", "instead", " but ", " yet ", " still ", "doesn't have to", "even with", " not ", ", not ", " vs ", " versus ", "rather than"]
    if any(term in f" {text} " for term in contrast_terms):
        return "contrast_loop"
    if text.startswith(("i ", "my ")) or "felt" in text or "struggled" in text:
        return "confession_led"
    if text.startswith(("finally", "trusted", "proven")) or "70,000" in text or "doctor" in text:
        return "proof_led"
    return "proof_led"


def headline_for_candidate(candidate: dict[str, Any], lang: str = "EN") -> str:
    copy = candidate.get("copy") if isinstance(candidate.get("copy"), dict) else {}
    block = copy.get(lang) if isinstance(copy.get(lang), dict) else {}
    return str(block.get("headline") or "").strip()


def copy_text_for_candidate(candidate: dict[str, Any], lang: str = "EN") -> str:
    copy = candidate.get("copy") if isinstance(candidate.get("copy"), dict) else {}
    block = copy.get(lang) if isinstance(copy.get(lang), dict) else {}
    parts: list[str] = []
    for key in ["headline", "subheadline", "support_line", "trust_line", "attribution", "cta"]:
        value = block.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    bullets = block.get("bullets")
    if isinstance(bullets, list):
        parts.extend(str(item).strip() for item in bullets if str(item).strip())
    return " ".join(parts)


def cta_for_candidate(candidate: dict[str, Any], lang: str = "EN") -> str:
    copy = candidate.get("copy") if isinstance(candidate.get("copy"), dict) else {}
    block = copy.get(lang) if isinstance(copy.get(lang), dict) else {}
    return str(block.get("cta") or "").strip()


def hook_structure_mismatch(candidate: dict[str, Any], planned_ad: dict[str, Any]) -> str | None:
    hypothesis = planned_ad.get("hypothesis") if isinstance(planned_ad.get("hypothesis"), dict) else {}
    if hypothesis.get("type") != "hook_structure":
        return None
    expected = str(hypothesis.get("variant") or "").strip()
    if not expected:
        return None
    headline = headline_for_candidate(candidate, "EN")
    actual = classify_hook_structure(headline)
    if actual != expected:
        return f"Expected hook_structure {expected}, but EN headline classified as {actual}: {headline!r}"
    return None


def hypothesis_mismatch(candidate: dict[str, Any], planned_ad: dict[str, Any]) -> str | None:
    hypothesis = planned_ad.get("hypothesis") if isinstance(planned_ad.get("hypothesis"), dict) else {}
    hyp_type = hypothesis.get("type")
    expected = str(hypothesis.get("variant") or "").strip()
    if not hyp_type or hyp_type == "none" or not expected:
        return None
    if hyp_type == "hook_structure":
        return hook_structure_mismatch(candidate, planned_ad)
    if hyp_type == "concept_angle":
        actual = str(candidate.get("concept_angle") or "").strip()
        if actual != expected:
            return f"Expected concept_angle {expected}, but candidate returned {actual or 'blank'}"
    return None


HYPOTHESIS_VARIABLES: dict[str, dict[str, Any]] = {}

def resolve_language_mode(config: dict[str, Any]) -> str:
    mode = str(config.get("language_mode") or "ALL").strip().upper()
    if mode in {"EN", "HI", "HINGLISH", "ALL"}:
        return mode
    return "ALL"


def assembler_language_mode(config: dict[str, Any]) -> str:
    mode = resolve_language_mode(config)
    if mode == "EN":
        return "EN"
    if mode == "HI":
        return "HI"
    if mode == "HINGLISH":
        return "HINGLISH"
    return "BOTH"

def parse_persona_library() -> list[dict[str, Any]]:
    path = PERSONA_SEEDS_PATH
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return [{"number": int(e["persona_number"]), "name": str(e["persona_name"])} for e in data]


def _persona_name_to_slug(name: str) -> str:
    """Convert a persona name to a filename-safe slug.

    Examples:
        "Always Hungry" -> "always_hungry"
        "35+ Slow Progress Dieter" -> "35_slow_progress_dieter"
        "Ayurveda-First Buyer" -> "ayurveda_first_buyer"
    """
    s = name.strip().lower()
    s = re.sub(r"[+\-]+", " ", s)
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


_PERSONA_SLUG_TO_NUMBER: dict[str, int] = {}


def _build_persona_slug_index() -> dict[str, int]:
    global _PERSONA_SLUG_TO_NUMBER
    if _PERSONA_SLUG_TO_NUMBER:
        return _PERSONA_SLUG_TO_NUMBER
    for entry in parse_persona_library():
        slug = _persona_name_to_slug(entry["name"])
        _PERSONA_SLUG_TO_NUMBER[slug] = int(entry["number"])
    return _PERSONA_SLUG_TO_NUMBER


def persona_slug(persona_number: int) -> str:
    """Return the slug for a persona number. Falls back to P{NN:02d} if unknown."""
    for entry in parse_persona_library():
        if int(entry["number"]) == int(persona_number):
            return _persona_name_to_slug(entry["name"])
    return f"P{int(persona_number):02d}"


def persona_number_from_slug(slug: str) -> int | None:
    """Return the persona number for a slug. Returns None if unknown."""
    index = _build_persona_slug_index()
    return index.get(slug)


def _load_persona_seeds() -> dict[int, dict[str, str]]:
    path = PERSONA_SEEDS_PATH
    if not path.exists():
        print(f"WARNING: {path} not found. Using empty persona seeds.", file=sys.stderr)
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    seeds: dict[int, dict[str, str]] = {}
    for entry in data:
        pn = int(entry.get("persona_number", 0))
        if pn < 1:
            continue
        raw_attempts = str(entry.get("failed_attempts", "")).strip()
        raw_why = str(entry.get("why_it_failed", "")).strip()
        objections_raw = (raw_attempts + " " + raw_why).strip()
        seeds[pn] = {
            "core_pattern": str(entry.get("core_pattern", "")),
            "common_indian_moments": str(entry.get("common_indian_moments", "")),
            "objections_raw": objections_raw,
            "how_kit_solves": str(entry.get("relevant_ok_kit_role", "")),
            "guardrail": str(entry.get("guardrail", "")),
        }
    return seeds


PERSONA_SEED_INPUTS = _load_persona_seeds()


def _resolve_persona_seeds() -> dict[int, dict[str, Any]]:
    user_seeds_raw = _resolve_user_config("persona_seeds")
    if user_seeds_raw:
        try:
            parsed = json.loads(user_seeds_raw) if isinstance(user_seeds_raw, str) else user_seeds_raw
            if isinstance(parsed, list):
                return {s["persona_number"]: s for s in parsed if "persona_number" in s}
        except (json.JSONDecodeError, TypeError):
            pass
    return PERSONA_SEED_INPUTS


def _load_copy_architecture() -> dict[str, Any]:
    path = COPY_ARCH_PATH
    if not path.exists():
        print(f"WARNING: {path} not found. Copy architecture templates disabled.", file=sys.stderr)
        return {"headline_architectures": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"WARNING: Failed to load {path}: {exc}", file=sys.stderr)
        return {"headline_architectures": {}}


COPY_ARCH = _load_copy_architecture()


def _resolve_copy_architecture() -> dict[str, Any]:
    user_arch_raw = _resolve_user_config("copy_architecture")
    if user_arch_raw:
        try:
            parsed = json.loads(user_arch_raw) if isinstance(user_arch_raw, str) else user_arch_raw
            if isinstance(parsed, dict) and parsed:
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return COPY_ARCH


def _load_copy_prompts() -> dict[str, Any]:
    path = COPY_PROMPTS_PATH
    if not path.exists():
        print(f"WARNING: {path} not found. Prompt templates disabled.", file=sys.stderr)
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"WARNING: Failed to load {path}: {exc}", file=sys.stderr)
        return {}


def _hypothesis_variant_label(variant_id: str) -> str:
    acronyms = {"pas", "bab", "fab"}
    if variant_id in acronyms:
        return variant_id.upper()
    return variant_id.replace("_", " ").title()


def _build_hypothesis_variables() -> dict[str, dict[str, Any]]:
    hv: dict[str, dict[str, Any]] = {
        "none": {
            "label": "No hypothesis test",
            "description": "Generate ads normally without controlled A/B testing.",
            "options": [],
        }
    }

    arch_types = {
        "hook_structure": {
            "label": "Hook Structure (H1)",
            "description": "Test which headline opening pattern performs best: question vs. proof vs. contrast vs. confession vs. command.",
        },
        "concept_angle": {
            "label": "Concept Angle (H2)",
            "description": "Test which messaging angle drives better results: pain vs. outcome vs. proof vs. authority vs. curiosity vs. comparison vs. offer vs. story.",
        },
    }
    arch = _resolve_copy_architecture().get("headline_architectures", {})
    for hyp_type, meta in arch_types.items():
        options = [{"id": vid, "label": _hypothesis_variant_label(vid)} for vid in arch.get(hyp_type, {})]
        hv[hyp_type] = {**meta, "options": options}

    return hv


COPY_PROMPTS = _load_copy_prompts()


def _resolve_copy_prompts() -> dict[str, Any]:
    user_templates_raw = _resolve_user_config("copy_prompt_templates")
    if user_templates_raw:
        try:
            parsed = json.loads(user_templates_raw) if isinstance(user_templates_raw, str) else user_templates_raw
            if isinstance(parsed, dict) and parsed:
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return COPY_PROMPTS


HYPOTHESIS_VARIABLES = _build_hypothesis_variables()


def _invalidate_config_cache(full_path: Path) -> None:
    """Reload the in-memory global for a config file that was just saved."""
    if full_path == PERSONA_SEEDS_PATH:
        PERSONA_SEED_INPUTS.clear()
        PERSONA_SEED_INPUTS.update(_load_persona_seeds())
    elif full_path == COPY_ARCH_PATH:
        COPY_ARCH.clear()
        COPY_ARCH.update(_load_copy_architecture())
        HYPOTHESIS_VARIABLES.clear()
        HYPOTHESIS_VARIABLES.update(_build_hypothesis_variables())
    elif full_path == COPY_PROMPTS_PATH:
        COPY_PROMPTS.clear()
        COPY_PROMPTS.update(_load_copy_prompts())
    # prompt_assembler_templates.json and background_variant.json are used by
    # subprocess scripts (generate_ads.py, chatgpt_web_sutomation.py),
    # not cached in this process — no reload needed.



def _headline_architecture_group(group: str) -> str:
    return group


def _entry_direction(entry: dict[str, Any]) -> str:
    return str(entry.get("meaning") or entry.get("intent") or entry.get("direction") or entry.get("template") or "")


def _compact_creative_entry(entry_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = ["meaning", "intent", "headline_role", "support_role", "avoid_skeletons", "avoid"]
    out: dict[str, Any] = {"id": entry_id}
    for key in allowed_keys:
        value = entry.get(key)
        if value not in (None, "", []):
            out[key] = value
    return out


def _framework_item(group: str, item_id: str) -> dict[str, str]:
    arch_group = _headline_architecture_group(group)
    items = _resolve_copy_architecture().get("headline_architectures", {}).get(arch_group, {})
    if not isinstance(items, dict) or not items:
        return {"id": item_id, "direction": ""}
    entry = items.get(item_id)
    if not isinstance(entry, dict):
        first_id, first_entry = next(iter(items.items()))
        return {"id": first_id, "direction": _entry_direction(first_entry)}
    return {"id": item_id, "direction": _entry_direction(entry)}


def _hypothesis_guidance(hyp_type: str, variant: str) -> str:
    headline_group = _headline_architecture_group(hyp_type)
    headline_entry = _resolve_copy_architecture().get("headline_architectures", {}).get(headline_group, {}).get(variant)
    if isinstance(headline_entry, dict):
        return _entry_direction(headline_entry)
    aux_entry = _resolve_copy_architecture().get("non_headline_hypotheses", {}).get(hyp_type, {}).get(variant)
    if isinstance(aux_entry, dict):
        return _entry_direction(aux_entry)
    return ""


def _select_headline_architecture(persona_number: int, fmt: str) -> dict[str, Any]:
    arch = _resolve_copy_architecture().get("headline_architectures", {})
    hook_arch = arch.get("hook_structure", {})
    hook_keys = list(hook_arch.keys())
    if hook_keys:
        idx = (persona_number + sum(ord(c) for c in fmt)) % len(hook_keys)
        hook_id = hook_keys[idx]
        return {"source": "hook_structure", "variant": hook_id, **hook_arch[hook_id]}
    return {"source": "four_us", "variant": "four_us", "template": "", "examples": []}


def _persona_theme(persona_seed: dict[str, Any]) -> str:
    text = " ".join(str(persona_seed.get(key) or "").lower() for key in ["pain", "desire", "friction", "proof", "tone", "persona_name"])
    if any(word in text for word in ["craving", "hunger", "snack", "food noise", "willpower"]):
        return "cravings"
    if any(word in text for word in ["digestion", "gut", "bloat", "stomach", "acidity"]):
        return "digestion"
    if any(word in text for word in ["event", "wedding", "outfit", "photo", "deadline"]):
        return "event_deadline"
    if any(word in text for word in ["busy", "professional", "work", "travel", "schedule", "office"]):
        return "busy_life"
    return ""


def _compact_product_truth() -> dict[str, Any]:
    return {
        "source": "attached_product_master_doc",
        "instruction": "Use the attached product master doc as the only source for product claims. Do not treat the request JSON as a product-claim source.",
        "hard_bans": [
            "no fat burner",
            "no metabolism boost",
            "no cure claims",
            "no guaranteed results",
            "no disease treatment claims",
            "no price in on-image copy",
            "no product component names in headline",
            "no protocol mechanics in headline",
        ],
    }


def build_copy_requirements(persona_number: int, fmt: str, format_sequence_index: int, variation_seed: str = "") -> dict[str, Any]:
    persona_seed = _resolve_persona_seeds().get(persona_number, {})

    return {
        "selection_mode": "llm_choose",
    }


def compact_format_rules_for_copy(fmt: str, format_rules: dict[str, Any]) -> dict[str, Any]:
    return {"format": fmt, "rules": []}

def build_ad_copy_system_prompt(fmt: str, formats: list[str] | None = None) -> str:
    fmt = fmt.strip().upper()
    prompts = COPY_PROMPTS
    base_rules = prompts.get("system_prompt_base_rules", [])
    parts = list(base_rules)
    if formats:
        fr_map = prompts.get("system_prompt_format_rules", {})
        for f in sorted(formats):
            rules = fr_map.get(f)
            if rules:
                parts.append("")
                parts.append(f"Ad format rules for {f}:")
                parts.extend(rules)
    return "\n".join(parts)


def build_strict_schema_note(fmt: str, languages: list[str] | None = None) -> str:
    fmt = fmt.strip().upper()
    prompts = _resolve_copy_prompts().get("strict_schema_note", {})
    field_map = prompts.get("field_map", {})
    if fmt == "ALL":
        copy_fields = "the fields required by each ad's format (see Ad format rules above)"
    else:
        copy_fields = field_map.get(fmt, prompts.get("default_fields", "headline, cta"))
    parts = [
        prompts.get("intro", ""),
        prompts.get("persona_fields_en", ""),
    ]
    if languages and any("HINGLISH" in l.upper() for l in languages):
        parts.append(prompts.get("language_extension", ""))
    parts.append(prompts.get("format_closure_template", "").format(fmt=fmt or "this", copy_fields=copy_fields))
    return " ".join(parts)


def build_ad_prompt_tail(fmt: str, formats: list[str] | None = None, total_ad_count: int = 1) -> str:
    fmt = fmt.strip().upper()
    tail = _resolve_copy_prompts().get("prompt_tail", {})
    support_map = tail.get("support_target_map", {})
    support_target = support_map.get(fmt, tail.get("default_support_target", "subheadline"))
    display_fmt = fmt if fmt != "ALL" else "ad"
    lines = [line.format(fmt=display_fmt, support_target=support_target) for line in tail.get("lines", [])]
    if total_ad_count > 1 or (len(formats or []) > 1):
        lines = [l for l in lines if "one ad only" not in l]
        lines.insert(1, f"Return all {total_ad_count} ads matching the JSON skeleton below. The payload above has data for each ad.")
    skeleton = build_response_skeleton(fmt, formats=formats)
    if skeleton:
        lines.append(f"\nReturn your response using exactly this JSON skeleton (replace placeholder values with your actual copy):\n{skeleton}")
    return "\n".join(lines)


def build_response_skeleton(fmt: str, formats: list[str] | None = None) -> str:
    fmt = fmt.strip().upper()
    skeletons = _resolve_copy_prompts().get("response_skeleton", {})
    base = copy.deepcopy(skeletons.get("default", {}))

    if fmt != "ALL" or not formats:
        # Single-format skeleton
        if not isinstance(base.get("ads"), list) or not base["ads"]:
            if fmt in {"HERO", "UGC"}:
                base = {"format": "{format}", "copy": {"EN": {"headline": "", "support_line": "", "cta": ""}}}
            elif fmt == "BA":
                base = {"format": "{format}", "copy": {"EN": {"headline": "", "bullets": ["", "", "", ""], "cta": ""}}}
            elif fmt == "FEAT":
                base = {"format": "{format}", "copy": {"EN": {"headline": "", "bullets": ["", ""], "cta": ""}}}
            else:
                base = {"format": "{format}", "copy": {"EN": {"headline": "", "trust_line": "", "cta": ""}}}
        else:
            fmt_override = skeletons.get(fmt, {})
            if fmt_override and "copy" in fmt_override:
                if "copy" in base["ads"][0]:
                    base["ads"][0]["copy"] = copy.deepcopy(fmt_override["copy"])
        placeholder_fmt = fmt if fmt != "ALL" else "<FORMAT>"
        raw = json.dumps(base, ensure_ascii=False, indent=2)
        return raw.replace("{format}", placeholder_fmt)

    # Multi-format skeleton: one example ad per unique format in the batch
    seen: set[str] = set()
    example_ads: list[dict] = []
    for f in formats:
        f = f.strip().upper()
        if f in seen:
            continue
        seen.add(f)
        if not isinstance(base.get("ads"), list) or not base["ads"]:
            if f in {"HERO", "UGC"}:
                fallback_copy = {"EN": {"headline": "", "support_line": "", "cta": ""}}
            elif f == "BA":
                fallback_copy = {"EN": {"headline": "", "bullets": ["", "", "", ""], "cta": ""}}
            elif f == "FEAT":
                fallback_copy = {"EN": {"headline": "", "bullets": ["", ""], "cta": ""}}
            else:
                fallback_copy = {"EN": {"headline": "", "trust_line": "", "cta": ""}}
            ad = {"format": f, "persona": {"number": 0, "name": ""}, "headline_angle": "", "concept_angle": "", "copy": fallback_copy}
        else:
            ad = copy.deepcopy(base["ads"][0])
        ad["format"] = f
        fmt_override = skeletons.get(f, {})
        if fmt_override and "copy" in fmt_override:
            if "copy" in ad:
                ad["copy"] = copy.deepcopy(fmt_override["copy"])
        example_ads.append(ad)
    base["ads"] = example_ads
    return json.dumps(base, ensure_ascii=False, indent=2)


def build_generation_payload_for_llm(context: dict[str, Any]) -> dict[str, Any]:
    seen: dict[tuple[str, int], dict[str, Any]] = {}
    total = 0
    for item in context.get("ads") or []:
        if not isinstance(item, dict):
            continue
        fmt = str(item.get("format") or "").strip().upper()
        persona = item.get("persona") if isinstance(item.get("persona"), dict) else {}
        pn = persona.get("persona_number")
        if not isinstance(pn, int):
            continue
        key = (fmt, pn)
        total += 1
        if key not in seen:
            format_rules = item.get("format_rules") if isinstance(item.get("format_rules"), dict) else {}
            copy_requirements = item.get("copy_requirements") if isinstance(item.get("copy_requirements"), dict) else {}
            seen[key] = {
                "format": fmt,
                "persona": persona,
                "format_rules": compact_format_rules_for_copy(fmt, format_rules),
                "copy_requirements": copy_requirements,
                "count": 1,
            }
        else:
            seen[key]["count"] += 1

    compact_ads = list(seen.values())

    return {
        "generated_at": context.get("generated_at"),
        "run_id": context.get("run_id"),
        "language_mode": context.get("language_mode"),
        "context_source": context.get("context_source"),
        "product_doc": {
            "attached_in_session": True,
            "source_file": context.get("product_file_path"),
            "instruction": "Read and use the attached product master doc as source of truth for all product claims.",
        },
        "product_truth": _compact_product_truth(),
        "ads": compact_ads,
    }


TARGET_LANGS_MAP = {"EN": ["EN"], "HI": ["HI"], "HINGLISH": ["HINGLISH"], "ALL": ["EN", "HI", "HINGLISH"]}

def validate_generated_copy_payload(copy_json: dict[str, Any], planned_ads: list[dict[str, Any]], language_mode: str = "ALL") -> str | None:
    target_langs = TARGET_LANGS_MAP.get(language_mode, ["EN", "HI"])
    ads = copy_json.get("ads") if isinstance(copy_json, dict) else None
    if not isinstance(ads, list):
        return "Generated payload did not include an ads array"
    if len(ads) < len(planned_ads):
        return f"Generated ads count {len(ads)} is lower than planned count {len(planned_ads)}"

    planned_keys = {
        (str(item.get("format") or "").strip().upper(), int((item.get("persona") or {}).get("persona_number") or 0))
        for item in planned_ads
        if isinstance(item, dict) and isinstance(item.get("persona"), dict)
    }
    seen_keys: set[tuple[str, int]] = set()
    for ad in ads:
        if not isinstance(ad, dict):
            return "Generated ads payload contains a non-object item"
        fmt = str(ad.get("format") or "").strip().upper()
        persona = ad.get("persona") if isinstance(ad.get("persona"), dict) else {}
        persona_number = persona.get("number")
        if not isinstance(persona_number, int):
            persona_number = persona.get("persona_number")
        if not isinstance(persona_number, int):
            return f"Generated ad for format {fmt or '?'} is missing persona number"
        seen_keys.add((fmt, persona_number))
        copy = ad.get("copy") if isinstance(ad.get("copy"), dict) else {}
        for lang in target_langs:
            block = copy.get(lang) if isinstance(copy.get(lang), dict) else {}
            if not str(block.get("headline") or "").strip():
                return f"Generated ad {fmt}/P{persona_number} is missing {lang} headline"
            if fmt in {"HERO", "UGC"} and not str(block.get("subheadline") or block.get("support_line") or "").strip():
                return f"Generated ad {fmt}/P{persona_number} is missing {lang} subheadline"
            if fmt in {"BA", "FEAT"}:
                bullets = block.get("bullets") if isinstance(block.get("bullets"), list) else []
                min_bullets = 4 if fmt == "BA" else 2
                if len([item for item in bullets if isinstance(item, str) and item.strip()]) < min_bullets:
                    return f"Generated ad {fmt}/P{persona_number} has insufficient {lang} bullets"

    missing = sorted(planned_keys - seen_keys)
    if missing:
        return "Generated payload is missing planned ads: " + ", ".join(f"{fmt}/P{persona}" for fmt, persona in missing)
    return None


def validate_single_ad(ad: dict[str, Any], target_langs: list[str]) -> str | None:
    """Validate a single ad. Returns error string or None if valid."""
    if not isinstance(ad, dict):
        return "Non-object ad item"
    fmt = str(ad.get("format") or "").strip().upper()
    persona = ad.get("persona") if isinstance(ad.get("persona"), dict) else {}
    persona_number = persona.get("number") or persona.get("persona_number")
    if not isinstance(persona_number, int):
        return f"Ad {fmt} missing persona number"
    copy = ad.get("copy") if isinstance(ad.get("copy"), dict) else {}
    if not copy:
        return f"Ad {fmt}/P{persona_number} missing copy"
    for lang in target_langs:
        block = copy.get(lang) if isinstance(copy.get(lang), dict) else {}
        if not str(block.get("headline") or "").strip():
            return f"Ad {fmt}/P{persona_number} missing {lang} headline"
        if fmt in {"HERO", "UGC"} and not str(block.get("subheadline") or block.get("support_line") or "").strip():
            return f"Ad {fmt}/P{persona_number} missing {lang} subheadline/support_line"
        if fmt in {"BA", "FEAT"}:
            bullets = block.get("bullets") if isinstance(block.get("bullets"), list) else []
            min_bullets = 4 if fmt == "BA" else 2
            if len([item for item in bullets if isinstance(item, str) and item.strip()]) < min_bullets:
                return f"Ad {fmt}/P{persona_number} insufficient {lang} bullets ({min_bullets} required)"
    return None


def filter_valid_ads(copy_json: dict[str, Any], planned_ads: list[dict[str, Any]], language_mode: str = "ALL") -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Filter ads to keep only valid ones. Returns (filtered_copy_json, failed_ads, warnings)."""
    target_langs = TARGET_LANGS_MAP.get(language_mode, ["EN", "HI"])
    ads = copy_json.get("ads") if isinstance(copy_json, dict) else []
    if not isinstance(ads, list):
        return copy_json, [], ["No ads array in payload"]

    valid_ads = []
    failed_ads = []
    for ad in ads:
        err = validate_single_ad(ad, target_langs)
        if err:
            failed_ads.append({"ad": ad, "error": err})
        else:
            valid_ads.append(ad)

    warnings = [f"{item['error']}" for item in failed_ads]

    filtered = dict(copy_json) if isinstance(copy_json, dict) else {}
    filtered["ads"] = valid_ads
    return filtered, failed_ads, warnings


def extract_generated_ad_candidate(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    def normalize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
        cloned = json.loads(json.dumps(candidate, ensure_ascii=False))
        persona = cloned.get("persona") if isinstance(cloned.get("persona"), dict) else {}
        if not isinstance(persona, dict):
            persona = {}
        if not isinstance(persona.get("persona_number"), int) and isinstance(cloned.get("persona_number"), int):
            persona["persona_number"] = cloned.get("persona_number")
        if not isinstance(persona.get("number"), int) and isinstance(cloned.get("persona_number"), int):
            persona["number"] = cloned.get("persona_number")
        if not isinstance(persona.get("persona_name"), str) and isinstance(cloned.get("persona_name"), str):
            persona["persona_name"] = cloned.get("persona_name")
        if not isinstance(persona.get("name"), str) and isinstance(cloned.get("persona_name"), str):
            persona["name"] = cloned.get("persona_name")
        if persona:
            cloned["persona"] = persona
        return cloned

    ads = payload.get("ads")
    if isinstance(ads, list):
        for item in ads:
            if isinstance(item, dict) and item.get("copy"):
                return normalize_candidate(item)
    if payload.get("format") and payload.get("copy"):
        return normalize_candidate(payload)
    return None


def detect_template_leakage(candidate: dict[str, Any] | None) -> str | None:
    if not isinstance(candidate, dict):
        return None
    copy_raw = candidate.get("copy") if isinstance(candidate.get("copy"), dict) else {}
    texts: list[str] = []
    for lang in ("EN", "HI"):
        block = copy_raw.get(lang) if isinstance(copy_raw.get(lang), dict) else copy_raw if lang == "EN" else {}
        for key in ("headline", "subheadline", "support_line", "trust_line"):
            val = block.get(key) if isinstance(block, dict) else None
            if isinstance(val, str) and val.strip():
                texts.append(val.strip())
        bullets = block.get("bullets") if isinstance(block, dict) and isinstance(block.get("bullets"), list) else []
        for b in bullets:
            if isinstance(b, str) and b.strip():
                texts.append(b.strip())

    for text in texts:
        low = text.lower()
        if any(label in low for label in ("structured_system", "cravings_down", "desired_outcome")):
            return f"Template label leaked into copy: {text!r}"
        if re.search(r"\ba\s+clear\s+.{1,30}\s+system\s+for\b", low):
            return f"Template sentence pattern detected: {text!r}"
        if "simple steps rooted in" in low:
            return f"Template sentence pattern detected: {text!r}"
        if re.match(r"^a\s+doctor-formulated\s+ayurvedic\s+kit:", low):
            return f"Colon-led feature list support line: {text!r}"
    if len(texts) >= 2:
        sup = texts[1] if len(texts) > 1 else ""
        if sup.count(",") >= 3 and sum(1 for kw in ("appetite", "digestion", "coach", "tracker", "15-day", "morning", "night") if kw in sup.lower()) >= 3:
            return f"Support line has stacked feature list: {sup!r}"
    return None


def hydrate_generated_ad_candidate(candidate: dict[str, Any], planned_ad: dict[str, Any]) -> dict[str, Any]:
    hydrated = json.loads(json.dumps(candidate, ensure_ascii=False))

    planned_format = str(planned_ad.get("format") or "").strip().upper()
    candidate_format = str(hydrated.get("format") or "").strip().upper()
    hydrated["format"] = candidate_format or planned_format

    planned_persona = planned_ad.get("persona") if isinstance(planned_ad.get("persona"), dict) else {}
    candidate_persona = hydrated.get("persona") if isinstance(hydrated.get("persona"), dict) else {}

    persona_number = candidate_persona.get("number")
    if not isinstance(persona_number, int):
        persona_number = candidate_persona.get("persona_number")
    if not isinstance(persona_number, int):
        planned_number = planned_persona.get("persona_number")
        if isinstance(planned_number, int):
            persona_number = planned_number

    persona_name = candidate_persona.get("name")
    if not isinstance(persona_name, str) or not persona_name.strip():
        persona_name = candidate_persona.get("persona_name")
    if not isinstance(persona_name, str) or not persona_name.strip():
        planned_name = planned_persona.get("persona_name")
        if isinstance(planned_name, str):
            persona_name = planned_name

    merged_persona = dict(candidate_persona)
    if isinstance(persona_number, int):
        merged_persona["number"] = persona_number
        merged_persona["persona_number"] = persona_number
    if isinstance(persona_name, str) and persona_name.strip():
        clean_name = persona_name.strip()
        merged_persona["name"] = clean_name
        merged_persona["persona_name"] = clean_name
    for k in ["pain_en", "desire_en", "friction_en", "proof_needed_en", "tone_cue_en",
              "pain_hi", "desire_hi", "friction_hi", "proof_needed_hi", "tone_cue_hi"]:
        if k not in merged_persona and k in planned_persona:
            merged_persona[k] = planned_persona[k]
    if merged_persona:
        hydrated["persona"] = merged_persona

    copy_payload = hydrated.get("copy") if isinstance(hydrated.get("copy"), dict) else {}
    normalized_copy: dict[str, Any] = {}
    if copy_payload:
        has_lang_keys = any(k in copy_payload for k in ["EN", "HI"])
        if has_lang_keys:
            for lang in ["EN", "HI"]:
                lang_block = copy_payload.get(lang)
                normalized_copy[lang] = lang_block if isinstance(lang_block, dict) else {}
        else:
            normalized_copy["EN"] = copy_payload
            normalized_copy["HI"] = {}
    else:
        normalized_copy = {"EN": {}, "HI": {}}
    hydrated["copy"] = normalized_copy

    return hydrated


def _normalize_how_kit_solves(value: Any) -> dict[str, str]:
    if isinstance(value, str):
        trimmed = value.strip()
        return {"kit_lever": trimmed} if trimmed else {}
    if isinstance(value, dict):
        if isinstance(value.get("how_kit_solves"), dict):
            value = value["how_kit_solves"]
        allowed = ["failure_point", "kit_lever", "causal_bridge", "support_line_instruction"]
        return {k: str(value.get(k, "")).strip() for k in allowed if str(value.get(k, "")).strip()}
    return {}


def _build_persona_payload_field(seed_field_value: Any, config: dict[str, Any]) -> Any:
    wrap = config.get("wrap_list", False)
    prefix = config.get("prefix")
    if wrap:
        val = str(seed_field_value) if seed_field_value else ""
        return [f"{prefix}{val}" if prefix and val else val]
    return seed_field_value

def build_persona_payload(persona_number: int, personas: list[dict[str, Any]]) -> dict[str, Any]:
    persona_name = f"Persona {persona_number}"
    for item in personas:
        if int(item.get("number") or 0) == persona_number:
            name = str(item.get("name") or "").strip()
            if name:
                persona_name = name
            break
    seed = _resolve_persona_seeds().get(persona_number, {})
    mapping = _PERSONA_SEED_MAPPING
    seed_to_payload = mapping.get("seed_to_payload", {})
    fallbacks = mapping.get("persona_fallbacks", {})

    payload: dict[str, Any] = {
        "persona_number": persona_number,
        "persona_name": persona_name,
    }

    for seed_key, field_cfg in seed_to_payload.items():
        raw = seed.get(seed_key, fallbacks.get(seed_key, ""))
        if seed_key == "how_kit_solves":
            raw = _normalize_how_kit_solves(raw)
        payload[field_cfg["field"]] = _build_persona_payload_field(raw, field_cfg)

    static = mapping.get("static_fields", {})
    for key, val in static.items():
        payload[key] = val

    hindi_default = mapping.get("hindi_ready_default", "")
    if hindi_default and payload.get("hindi_ready") in (None, []):
        payload["hindi_ready"] = [hindi_default]

    return payload
