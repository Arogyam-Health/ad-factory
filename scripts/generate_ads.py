#!/usr/bin/env python3
"""
Assembler-only ad prompt generator.

What this script does:
  - Reads externally-generated ad copy from a JSON file (no copy generation here).
  - Selects catalog backgrounds randomly from the format pool.
  - Builds seeded background sentence from `background_variant.json`.
  - Assembles full 9-section prompts per playbook and writes `output/vN/<FORMAT>_<persona>_<lang>.txt`.
  - Enforces safe-zone rules by embedding an explicit SAFE-ZONE ENFORCEMENT block.

What this script explicitly does NOT do:
  - It does not call any LLM.
  - It does not invent persona fields or ad copy.
"""

from __future__ import annotations

import sys

# FIX: Force UTF-8 on stdout to prevent Windows cp1252 encoding crashes
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

import argparse
import hashlib
import json
import random
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKGROUNDS_PATH = ROOT / "background_variant.json"
COPY_PROMPTS_PATH = ROOT / "dashboard" / "backend" / "copy_prompt_templates.json"
OUTPUT_DIR = ROOT / "output"

SUPPORTED_FORMATS = {"HERO", "BA", "TEST", "FEAT", "UGC"}
SUPPORTED_LANGS = {"EN", "HI", "HINGLISH"}
LANGUAGE_LABELS = {
    "EN": "English",
    "HI": "Hindi",
    "HINGLISH": "Hinglish",
}
SUPPORTED_CONCEPT_ANGLES = {
    "pain_point",
    "desired_outcome",
    "social_proof",
    "authority",
    "story",
    "curiosity",
    "comparison",
    "offer",
}
HEADLINE_ANGLE_TO_CONCEPT = {
    "pain": "pain_point",
    "objection": "comparison",
    "mechanism": "curiosity",
    "time": "offer",
    "proof": "social_proof",
    "sacrifice_reduction": "comparison",
}

def _load_visual_archetypes() -> dict[str, list[dict[str, Any]]]:
    path = COPY_PROMPTS_PATH
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("visual_archetypes") or {}
    except Exception:
        return {}

FORMAT_VISUAL_ARCHETYPES = _load_visual_archetypes()

PROMPT_ASSEMBLER_DIR = Path(__file__).resolve().parent
PROMPT_ASSEMBLER_TEMPLATES = json.loads((PROMPT_ASSEMBLER_DIR / "prompt_assembler_templates.json").read_text(encoding="utf-8"))

DEFAULT_PROOF_BAR_TEXT = "70,000+ Users | 3-5 kg loss with 1 Kit | 100% Ayurvedic"
DEFAULT_HEADLINE_BANS = [
    r"\b(ok\s*liquid|ok\s*tablet|ok\s*powder|okp)\b",
    r"\b(am|pm)\b|\b4\s*-?\s*hour\b|\bno\s*solid\b|\bempty\s*stomach\b",
]
DEFAULT_PROMPT_SHELL = {
    "product_lock_header": "PRODUCT LOCK BLOCK",
    "proof_bar_header": "PROOF BAR BLOCK",
    "output_spec_header": "OUTPUT SPEC",
    "layout_header": "FORMAT LAYOUT INSTRUCTIONS",
    "persona_header": "PERSONA INPUT BLOCK",
    "concept_header": "CONCEPT INPUT BLOCK",
    "exact_copy_header": "EXACT ON-IMAGE COPY - DO NOT ALTER ANYTHING",
    "negative_header": "NEGATIVE CONSTRAINTS",
    "quality_header": "QUALITY BAR - verify before accepting output",
    "visual_header": "VISUAL DIRECTION BLOCK",
    "typography_header": "TYPOGRAPHY SHARPNESS BLOCK",
    "create_ad_line": "Create the ad in {language}.",
    "exact_copy_footer": "Render every character exactly as written. No paraphrasing, no punctuation changes, no autocorrection.",
    "proof_bar_copy_line": "- Proof bar: {proof_bar_text}",
    "proof_bar_once_line": "- Proof bar is present exactly once, fully readable, horizontally centered, and does not enter the bottom restricted band.",
    "concept_path_note": "- Concept path is strategy only; do not render these labels on-image.",
    "style_generic": "{fmt} (custom format layout).",
    "persona_lines": [
        "- Persona: {persona_name} (Persona {persona_number})",
        "- Pain: {pain}",
        "- Desire: {desire}",
        "- Friction: {friction}",
        "- Proof needed: {proof}",
        "- Tone cue: {tone}",
        "- Concept angle: {concept_angle}",
    ],
    "copy_labels": {
        "headline": "- Headline: {value}",
        "support_line": "- Support line: {value}",
        "cta": "- CTA: {value}",
        "context_line": "- Context line: {value}",
        "trust_line": "- Trust line: {value}",
        "attribution": "- Attribution: {value}",
        "bullet": "- Bullet {index}: {value}",
        "left_situation": "- Left situation {index}: {value}",
        "right_shift": "- Right shift {index}: {value}",
    },
    "concept_name_line": "- Concept: {concept_label}",
    "concept_description_line": "- Description: {concept_description}",
}
DEFAULT_COPY = {
    "EN": {
        "headline": "A simpler daily wellness routine",
        "support_line": "Designed to fit into your day with clear, guided steps.",
        "trust_line": "Trusted by thousands of wellness-focused customers.",
        "cta": "See how it works",
        "test_headline": "Real routines, real trust",
    },
    "HI": {
        "headline": "रोज की वेलनेस के लिए आसान रूटीन",
        "support_line": "आपके दिन में आसानी से फिट होने वाले साफ, गाइडेड स्टेप्स।",
        "trust_line": "हजारों वेलनेस-केंद्रित ग्राहकों का भरोसा।",
        "cta": "जानें कैसे काम करता है",
        "test_headline": "असली रूटीन, असली भरोसा",
    },
    "HINGLISH": {
        "headline": "Daily wellness ke liye simple routine",
        "support_line": "Aapke day mein fit hone wale clear, guided steps.",
        "trust_line": "Thousands of wellness-focused customers ka trust.",
        "cta": "Dekhein kaise work karta hai",
        "test_headline": "Real routine, real trust",
    },
}
DEFAULT_BULLETS = {
    "BA": {
        "EN": ["Before: unsure where to start", "Before: routine felt hard to follow", "After: clearer daily steps", "After: more confidence to continue"],
        "HI": ["पहले: शुरुआत साफ नहीं थी", "पहले: रूटीन फॉलो करना मुश्किल था", "बाद में: रोज के स्टेप्स साफ हुए", "बाद में: जारी रखने का भरोसा बढ़ा"],
        "HINGLISH": ["Before: start clear nahi tha", "Before: routine follow karna hard tha", "After: daily steps clearer hue", "After: continue karne ka confidence badha"],
    },
    "default": {
        "EN": ["Clear daily steps", "Premium, guided routine"],
        "HI": ["साफ रोजाना स्टेप्स", "प्रीमियम, गाइडेड रूटीन"],
        "HINGLISH": ["Clear daily steps", "Premium guided routine"],
    },
}
DEFAULT_PERSONA_PROMPT_FIELDS = {
    "EN": {
        "required": True,
        "pain": ["pain_en"],
        "desire": ["desire_en"],
        "friction": ["friction_en"],
        "proof": ["proof_needed_en"],
        "tone": ["tone_cue_en"],
    },
    "HI": {
        "required": True,
        "pain": ["pain_hi"],
        "desire": ["desire_hi"],
        "friction": ["friction_hi"],
        "proof": ["proof_needed_hi"],
        "tone": ["tone_cue_hi"],
    },
    "HINGLISH": {
        "required": False,
        "pain": ["pain_hinglish", "pain_hi"],
        "desire": ["desire_hinglish", "desire_hi"],
        "friction": ["friction_hinglish", "friction_hi"],
        "proof": ["proof_needed_hinglish", "proof_needed_hi"],
        "tone": ["tone_cue_hinglish", "tone_cue_hi"],
    },
}
DEFAULT_BACKGROUND_FIELDS = {
    "base": "a clean product arrangement",
    "surface": ["neutral studio surface"],
    "environment": ["minimal studio"],
    "lighting": ["soft daylight"],
    "mood": ["calm confidence"],
    "camera": ["eye-level product shot"],
    "color_tone": ["balanced brand colors"],
}
DEFAULT_BACKGROUND_SENTENCE = {
    "composition": ["balanced feed composition inside the central safe field"],
    "layout_intent": ["preserve a stable center-of-interest corridor with consistent margin protection on every side"],
    "cta_safe_space": ["maintain subtle low-contrast space near the lower edge to protect feed overlay readability"],
    "crop_safety": ["maintain protected margin buffers so alternate crops do not clip meaningful scene structure"],
    "text_overlay_treatment": [
        "if a text readability panel is used, keep it in the upper text zone only as a soft vertical fade (high opacity near top, fading to transparent before the product cluster), never behind or below products"
    ],
    "edge_tone_control": [
        "keep all frame edges tonally neutral with no orange, amber, or sepia cast; no border glow and no vignette halo"
    ],
    "format_9_16": "designed for 9:16 vertical placement with key subject content constrained to the 14-65 percent safe band, positioned slightly above center, and with the lower 35 percent kept visually quiet for overlays; avoid edge glow frames and tinted border gradients",
    "format_4_5": "designed for 4:5 feed framing with key content held inside the central safe field, centered to slightly above center, while top 10 percent, bottom 15 percent, and side edge zones remain low-priority; avoid edge glow frames and tinted border gradients",
    "template": (
        "{base} on a {surface}, with {environment}, lit by {lighting}, conveying {mood}; "
        "{camera}, {composition}, {layout_intent}, {cta_safe_space}, {crop_safety}, {text_overlay_treatment}, {edge_tone_control}, {color_tone}, "
        "{format_clause}, clean premium studio ad photography, ultra-detailed, flawless commercial finish."
    ),
}


def assembler_dict(templates: dict[str, Any] | None = None) -> dict[str, Any]:
    return templates if isinstance(templates, dict) and templates else PROMPT_ASSEMBLER_TEMPLATES


def _fill(template: str, **kwargs: Any) -> str:
    text = str(template)
    for key, value in kwargs.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def _pick_first(source: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = str(source.get(key) or "").strip()
        if value:
            return value
    return ""


def prompt_shell(templates: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = assembler_dict(templates).get("prompt_shell")
    raw = raw if isinstance(raw, dict) else {}
    merged = dict(DEFAULT_PROMPT_SHELL)
    for key, value in raw.items():
        if value not in (None, "", [], {}):
            merged[key] = value
    labels = DEFAULT_PROMPT_SHELL["copy_labels"]
    extra_labels = raw.get("copy_labels") if isinstance(raw.get("copy_labels"), dict) else {}
    merged["copy_labels"] = {**labels, **{k: v for k, v in extra_labels.items() if v not in (None, "")}}
    return merged


def proof_bar_text(templates: dict[str, Any] | None = None) -> str:
    text = str(assembler_dict(templates).get("proof_bar_text") or "").strip()
    return text or DEFAULT_PROOF_BAR_TEXT


def headline_ban_patterns(templates: dict[str, Any] | None = None) -> list[str]:
    raw = assembler_dict(templates).get("headline_bans")
    if isinstance(raw, list) and raw:
        return [str(item) for item in raw if str(item).strip()]
    return list(DEFAULT_HEADLINE_BANS)


def language_label(lang: str, templates: dict[str, Any] | None = None) -> str:
    labels = assembler_dict(templates).get("language_labels")
    labels = labels if isinstance(labels, dict) else {}
    return str(labels.get(lang) or LANGUAGE_LABELS.get(lang) or lang)


def default_visual_archetype(fmt: str, templates: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = assembler_dict(templates).get("default_visual_archetype")
    raw = raw if isinstance(raw, dict) else {}
    fmt_lower = fmt.lower()
    label = _fill(str(raw.get("label") or "Default {fmt} layout"), fmt=fmt, fmt_lower=fmt_lower)
    layout = [
        _fill(str(line), fmt=fmt, fmt_lower=fmt_lower, label=label)
        for line in (raw.get("layout_lines") or [
            "- Use a clean {fmt} composition with one obvious focal hierarchy.",
            "- Keep product labels readable and fully inside the safe-zone.",
            "- Place headline, support copy, CTA, and proof bar with clear separation.",
        ])
    ]
    direction = [
        _fill(str(line), fmt=fmt, fmt_lower=fmt_lower, label=label)
        for line in (raw.get("direction_lines") or [
            "- Selected visual archetype fallback: default_{fmt_lower} - {label}",
        ])
    ]
    return {
        "id": _fill(str(raw.get("id") or "default_{fmt_lower}"), fmt=fmt, fmt_lower=fmt_lower, label=label),
        "label": label,
        "layout_lines": layout,
        "direction_lines": direction,
    }

@dataclass(frozen=True)
class CopyBlock:
    headline: str
    cta: str
    support_line: str = ""
    context_line: str = ""
    trust_line: str = ""
    attribution: str = ""
    bullets: list[str] | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assemble ad prompts from external copy JSON + catalog backgrounds")
    parser.add_argument("--copy-file", required=True, help="Path to copy batch JSON (produced by your LLM/operator step)")
    parser.add_argument("--batch", help="Output batch folder name like v8 (default: next available)")
    parser.add_argument("--seed", type=int, help="Deterministic seed for background rotation order + background sentence sampling")
    parser.add_argument("--language-mode", choices=["BOTH", "EN", "HI", "HINGLISH"], default="BOTH", help="Which prompt languages to assemble")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print plan without writing files")
    return parser.parse_args()


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def list_batches(output_dir: Path) -> list[int]:
    if not output_dir.exists():
        return []
    out: list[int] = []
    for child in output_dir.iterdir():
        if child.is_dir():
            m = re.match(r"^v(\d+)$", child.name)
            if m:
                out.append(int(m.group(1)))
    return sorted(out)


def next_batch_name(output_dir: Path) -> str:
    batches = list_batches(output_dir)
    return "v1" if not batches else f"v{batches[-1] + 1}"



def pick_background_slot(
    backgrounds: dict[str, Any],
    fmt: str,
    seed: int,
) -> dict[str, Any]:
    variants: list[dict[str, Any]] = backgrounds.get("variants", [])
    pool = [v for v in variants if fmt in (v.get("formats") or [])]
    if not pool:
        pool = [v for v in variants if isinstance(v, dict)]
    if not pool:
        raise RuntimeError(f"No background variants found for format {fmt}")
    rng = random.Random(seed)
    chosen = rng.choice(pool)
    default_overlay = backgrounds.get("default_text_overlay_treatment")
    if isinstance(default_overlay, list) and default_overlay and "text_overlay_treatment" not in chosen:
        chosen = dict(chosen)
        chosen["text_overlay_treatment"] = default_overlay
    return chosen


def get_background_by_id(backgrounds: dict[str, Any], fmt: str, bg_id: str) -> dict[str, Any]:
    variants: list[dict[str, Any]] = backgrounds.get("variants", [])
    wanted = bg_id.strip().upper()
    for item in variants:
        if str(item.get("id") or "").strip().upper() != wanted:
            continue
        formats = item.get("formats") or []
        if fmt not in formats:
            raise RuntimeError(f"Background {wanted} is not allowed for format {fmt}")
        default_overlay = backgrounds.get("default_text_overlay_treatment")
        if isinstance(default_overlay, list) and default_overlay and "text_overlay_treatment" not in item:
            item = dict(item)
            item["text_overlay_treatment"] = default_overlay
        return item
    raise RuntimeError(f"Background id not found: {wanted}")


def _choice_pool(
    bg: dict[str, Any],
    key: str,
    fallback: list[str],
) -> list[str]:
    raw = bg.get(key)
    if isinstance(raw, list) and raw:
        return [str(item) for item in raw if str(item).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return list(fallback)


def build_seeded_background_sentence(
    bg: dict[str, Any],
    seed: int,
    aspect_ratio: str,
    templates: dict[str, Any] | None = None,
) -> str:
    rng = random.Random(seed)
    sentence = assembler_dict(templates).get("background_sentence")
    sentence = sentence if isinstance(sentence, dict) else {}
    fallback = DEFAULT_BACKGROUND_SENTENCE

    def pool(key: str) -> list[str]:
        configured = sentence.get(key)
        default = fallback.get(key) or []
        default_list = default if isinstance(default, list) else [str(default)]
        configured_list = configured if isinstance(configured, list) and configured else default_list
        return _choice_pool(bg, key, [str(item) for item in configured_list])

    base = bg["base"]
    lighting = rng.choice(bg["lighting"])
    surface = rng.choice(bg["surface"])
    environment = rng.choice(bg["environment"])
    mood = rng.choice(bg["mood"])
    camera = rng.choice(bg["camera"])
    color_tone = rng.choice(bg["color_tone"])
    composition = rng.choice(pool("composition"))
    layout_intent = rng.choice(pool("layout_intent"))
    cta_safe_space = rng.choice(pool("cta_safe_space"))
    crop_safety = rng.choice(pool("crop_safety"))
    text_overlay_treatment = rng.choice(pool("text_overlay_treatment"))
    edge_tone_control = rng.choice(pool("edge_tone_control"))
    if aspect_ratio == "9:16":
        format_clause = str(sentence.get("format_9_16") or fallback["format_9_16"])
    else:
        format_clause = str(sentence.get("format_4_5") or fallback["format_4_5"])
    template = str(sentence.get("template") or fallback["template"])
    return _fill(
        template,
        base=base,
        surface=surface,
        environment=environment,
        lighting=lighting,
        mood=mood,
        camera=camera,
        composition=composition,
        layout_intent=layout_intent,
        cta_safe_space=cta_safe_space,
        crop_safety=crop_safety,
        text_overlay_treatment=text_overlay_treatment,
        edge_tone_control=edge_tone_control,
        color_tone=color_tone,
        format_clause=format_clause,
    )


def require_str(obj: dict[str, Any], key: str, ctx: str) -> str:
    val = obj.get(key)
    if not isinstance(val, str) or not val.strip():
        raise RuntimeError(f"Missing or empty string '{key}' in {ctx}")
    return val.strip()


def optional_str(obj: dict[str, Any], key: str) -> str:
    val = obj.get(key)
    return val.strip() if isinstance(val, str) and val.strip() else ""


def default_copy_text(
    fmt: str,
    lang: str,
    field: str,
    templates: dict[str, Any] | None = None,
) -> str:
    raw = assembler_dict(templates).get("default_copy")
    raw = raw if isinstance(raw, dict) else {}
    lang_block = raw.get(lang) if isinstance(raw.get(lang), dict) else {}
    fallback = DEFAULT_COPY.get(lang) or DEFAULT_COPY["EN"]
    if fmt == "TEST" and field == "headline":
        text = str(lang_block.get("test_headline") or fallback.get("test_headline") or "").strip()
        if text:
            return text
    return str(lang_block.get(field) or fallback.get(field) or "").strip()


def default_bullets(fmt: str, lang: str, templates: dict[str, Any] | None = None) -> list[str]:
    raw = assembler_dict(templates).get("default_bullets")
    raw = raw if isinstance(raw, dict) else {}
    group = raw.get(fmt) if isinstance(raw.get(fmt), dict) else raw.get("default")
    group = group if isinstance(group, dict) else {}
    fallback_group = DEFAULT_BULLETS.get(fmt) or DEFAULT_BULLETS["default"]
    items = group.get(lang)
    if isinstance(items, list) and items:
        return [str(item) for item in items if str(item).strip()]
    return list(fallback_group.get(lang) or fallback_group["EN"])


def safe_headline(
    raw: dict[str, Any],
    fmt: str,
    lang: str,
    ctx: str,
    templates: dict[str, Any] | None = None,
) -> str:
    headline = optional_str(raw, "headline") or default_copy_text(fmt, lang, "headline", templates)
    for pattern in headline_ban_patterns(templates):
        try:
            matched = re.search(pattern, headline, flags=re.IGNORECASE)
        except re.error:
            matched = None
        if matched:
            return default_copy_text(fmt, lang, "headline", templates)
    if not headline:
        raise RuntimeError(f"Missing or empty string 'headline' in {ctx}")
    return headline


def require_int(obj: dict[str, Any], key: str, ctx: str) -> int:
    val = obj.get(key)
    if not isinstance(val, int):
        raise RuntimeError(f"Missing or non-int '{key}' in {ctx}")
    return val


def clean_id(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9_]+", "_", value.strip().lower()).strip("_")


def resolve_concept_fields(ad: dict[str, Any], fmt: str, persona: dict[str, Any]) -> dict[str, Any]:
    angle = clean_id(ad.get("concept_angle"))
    if angle not in SUPPORTED_CONCEPT_ANGLES:
        headline_angle = clean_id(ad.get("headline_angle"))
        angle = HEADLINE_ANGLE_TO_CONCEPT.get(headline_angle, "desired_outcome")
    return {"concept_angle": angle}


def parse_copy_block(fmt: str, lang: str, raw: dict[str, Any], templates: dict[str, Any] | None = None) -> CopyBlock:
    ctx = f"ads[].copy.{lang} for format={fmt}"
    headline = safe_headline(raw, fmt, lang, ctx, templates)
    cta = optional_str(raw, "cta") or default_copy_text(fmt, lang, "cta", templates)
    sub_val = raw.get("subheadline") or raw.get("support_line")
    support_line = (sub_val or "").strip() if isinstance(sub_val, str) else ""
    if fmt in {"HERO", "UGC"} and not support_line:
        support_line = default_copy_text(fmt, lang, "support_line", templates)
    context_line = optional_str(raw, "context_line")
    trust_line = optional_str(raw, "trust_line")
    if fmt == "TEST" and not trust_line:
        trust_line = default_copy_text(fmt, lang, "trust_line", templates)
    attribution = optional_str(raw, "attribution")
    bullets_val = raw.get("bullets")
    bullets: list[str] | None = None
    if bullets_val is not None:
        if isinstance(bullets_val, list):
            bullets = [x.strip() for x in bullets_val if isinstance(x, str) and x.strip()]
        if not bullets:
            bullets = None
    if fmt in {"BA", "FEAT"}:
        min_bullets = 4 if fmt == "BA" else 2
        if not bullets or len(bullets) < min_bullets:
            fallback_bullets = default_bullets(fmt, lang, templates)
            bullets = (bullets or []) + fallback_bullets[len(bullets or []):min_bullets]
        if bullets:
            bullets = bullets[: max(min_bullets, len(bullets))]
    if bullets:
        if fmt == "BA":
            bullets = [strip_ba_panel_label(x) for x in bullets]
    return CopyBlock(
        headline=headline,
        cta=cta,
        support_line=support_line,
        context_line=context_line,
        trust_line=trust_line,
        attribution=attribution,
        bullets=bullets,
    )


def strip_ba_panel_label(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^\s*(?:before|after)\s*[:\-]\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\s*(?:पहले|बाद|पहले\s*में|बाद\s*में)\s*[:\-]\s*", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned


def split_ba_contrast_lines(bullets: list[str]) -> tuple[list[str], list[str]]:
    cleaned = [strip_ba_panel_label(x) for x in bullets if isinstance(x, str) and x.strip()]
    if len(cleaned) <= 1:
        return (cleaned[:1], [])
    if len(cleaned) == 2:
        return ([cleaned[0]], [cleaned[1]])
    if len(cleaned) == 3:
        return (cleaned[:2], [cleaned[2]])
    return (cleaned[:2], cleaned[2:4])


def stable_signature_seed(*parts: Any) -> int:
    joined = "|".join(str(part or "") for part in parts)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) or 1


def find_visual_archetype(fmt: str, archetype_id: str) -> dict[str, Any]:
    for item in FORMAT_VISUAL_ARCHETYPES.get(fmt, []):
        if str(item.get("id") or "").strip() == archetype_id:
            return item
    raise RuntimeError(f"Unknown visual archetype '{archetype_id}' for format {fmt}")


def pick_visual_archetype(
    fmt: str,
    persona_number: int,
    copy: CopyBlock,
    seed: int,
    forced_archetype: str | None = None,
    used_archetype_ids: set[str] | None = None,
) -> dict[str, Any]:
    variants = FORMAT_VISUAL_ARCHETYPES.get(fmt) or []
    if not variants:
        return default_visual_archetype(fmt)

    if forced_archetype and forced_archetype.strip():
        return find_visual_archetype(fmt, forced_archetype.strip())

    available_variants = variants
    if used_archetype_ids:
        unused = [item for item in variants if str(item.get("id") or "") not in used_archetype_ids]
        if unused:
            available_variants = unused

    selector_seed = stable_signature_seed(
        fmt,
        persona_number,
        seed,
        copy.headline,
        copy.cta,
        copy.support_line,
        copy.context_line,
        copy.trust_line,
        "|".join(copy.bullets or []),
    )
    rng = random.Random(selector_seed)
    return available_variants[rng.randrange(len(available_variants))]


def persona_prompt_values(
    persona: dict[str, Any],
    lang: str,
    templates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fields = assembler_dict(templates).get("persona_prompt_fields")
    fields = fields if isinstance(fields, dict) else {}
    spec = fields.get(lang) if isinstance(fields.get(lang), dict) else None
    suffix = str(lang or "").strip().lower()
    if not isinstance(spec, dict):
        spec = DEFAULT_PERSONA_PROMPT_FIELDS.get(lang) or {
            "required": lang.upper() == "EN",
            "pain": [f"pain_{suffix}"] if suffix else ["pain_en"],
            "desire": [f"desire_{suffix}"] if suffix else ["desire_en"],
            "friction": [f"friction_{suffix}"] if suffix else ["friction_en"],
            "proof": [f"proof_needed_{suffix}"] if suffix else ["proof_needed_en"],
            "tone": [f"tone_cue_{suffix}"] if suffix else ["tone_cue_en"],
        }
    required = bool(spec.get("required"))
    ctx = "ads[].persona"
    values = {
        "persona_name": require_str(persona, "name", ctx),
        "persona_number": require_int(persona, "number", ctx),
    }
    for logical in ("pain", "desire", "friction", "proof", "tone"):
        keys = spec.get(logical)
        if not isinstance(keys, list) or not keys:
            fallback = (DEFAULT_PERSONA_PROMPT_FIELDS.get(lang) or {}).get(logical)
            keys = list(fallback) if fallback else [f"{logical}_{suffix}"]
        keys = [str(item).strip() for item in keys if str(item).strip()]
        value = _pick_first(persona, keys)
        if required and not value:
            raise RuntimeError(f"Missing or empty string '{keys[0]}' in {ctx}")
        values[logical] = value
    return values


def _copy_label(shell: dict[str, Any], key: str, value: str, **extra: Any) -> str:
    labels = shell.get("copy_labels") if isinstance(shell.get("copy_labels"), dict) else {}
    template = str(labels.get(key) or DEFAULT_PROMPT_SHELL["copy_labels"][key])
    return _fill(template, value=value, **extra)


def render_prompt(
    fmt: str,
    lang: str,
    aspect_ratio: str,
    persona: dict[str, Any],
    copy: CopyBlock,
    concept: dict[str, Any],
    bg: dict[str, Any],
    bg_seed: int,
    seeded_sentence: str,
    visual_archetype: dict[str, Any],
    visual_lock: dict[str, Any] | None = None,
    templates: dict[str, Any] | None = None,
    creative_concept: dict[str, Any] | None = None,
) -> str:
    T = assembler_dict(templates)
    S = prompt_shell(T)
    persona_values = persona_prompt_values(persona, lang, T)
    persona_name = persona_values["persona_name"]
    persona_number = persona_values["persona_number"]
    pain = persona_values["pain"]
    desire = persona_values["desire"]
    friction = persona_values["friction"]
    proof = persona_values["proof"]
    tone = persona_values["tone"]
    proof_text = proof_bar_text(T)

    layout_lines = list(visual_archetype.get("layout_lines") or [])
    archetype_direction_lines = [str(line) for line in (visual_archetype.get("direction_lines") or []) if isinstance(line, str) and line.strip()]

    copy_lines: list[str] = []
    if fmt == "HERO":
        copy_lines = [
            _copy_label(S, "headline", copy.headline),
            _copy_label(S, "support_line", copy.support_line),
            _copy_label(S, "cta", copy.cta),
        ]
    elif fmt == "UGC":
        copy_lines = [
            _copy_label(S, "headline", copy.headline),
            _copy_label(S, "support_line", copy.support_line),
            _copy_label(S, "cta", copy.cta),
        ]
        if copy.context_line:
            copy_lines.insert(2, _copy_label(S, "context_line", copy.context_line))
    elif fmt == "BA":
        bullets = copy.bullets or []
        left_lines, right_lines = split_ba_contrast_lines(bullets)
        copy_lines = [_copy_label(S, "headline", copy.headline)]
        for i, line in enumerate(left_lines, start=1):
            copy_lines.append(_copy_label(S, "left_situation", line, index=i))
        for i, line in enumerate(right_lines, start=1):
            copy_lines.append(_copy_label(S, "right_shift", line, index=i))
        copy_lines.append(_copy_label(S, "cta", copy.cta))
    elif fmt == "FEAT":
        bullets = copy.bullets or []
        copy_lines = [_copy_label(S, "headline", copy.headline)]
        for i, b in enumerate(bullets, start=1):
            copy_lines.append(_copy_label(S, "bullet", b, index=i))
        copy_lines.append(_copy_label(S, "cta", copy.cta))
    elif fmt == "TEST":
        copy_lines = [
            _copy_label(S, "headline", copy.headline),
            _copy_label(S, "trust_line", copy.trust_line),
            _copy_label(S, "cta", copy.cta),
        ]
    else:
        copy_lines = []
        if copy.headline:
            copy_lines.append(_copy_label(S, "headline", copy.headline))
        if copy.support_line:
            copy_lines.append(_copy_label(S, "support_line", copy.support_line))
        if copy.attribution:
            copy_lines.append(_copy_label(S, "attribution", copy.attribution))
        if copy.trust_line:
            copy_lines.append(_copy_label(S, "trust_line", copy.trust_line))
        if copy.context_line:
            copy_lines.append(_copy_label(S, "context_line", copy.context_line))
        for i, bullet in enumerate(copy.bullets or [], start=1):
            copy_lines.append(_copy_label(S, "bullet", bullet, index=i))
        if copy.cta:
            copy_lines.append(_copy_label(S, "cta", copy.cta))

    copy_lines.append(_fill(str(S["proof_bar_copy_line"]), proof_bar_text=proof_text))

    lock = visual_lock if isinstance(visual_lock, dict) else {}
    subject_line = (lock.get("subject") or "").strip() if isinstance(lock.get("subject"), str) else ""
    if not subject_line:
        subject_line = T["subject_lines"].get(fmt) or T["subject_lines"]["default"]
    action_line = (lock.get("action") or "").strip() if isinstance(lock.get("action"), str) and lock.get("action").strip() else (T["action_lines"].get(fmt) or T["action_lines"]["default"])
    camera_line = (lock.get("camera") or "").strip() if isinstance(lock.get("camera"), str) and lock.get("camera").strip() else (T["camera_lines"].get(fmt) or T["camera_lines"]["default"])
    realism_line = (lock.get("realism") or "").strip() if isinstance(lock.get("realism"), str) and lock.get("realism").strip() else (T["realism_lines"].get(fmt) or T["realism_lines"]["default"])

    lines: list[str] = []
    canvas_spec = "1080 x 1920" if aspect_ratio == "9:16" else "1080 x 1350"
    lines.append(str(S["product_lock_header"]))
    lines.extend(T["product_lock_block"])
    lines.append("")
    lines.append(str(S["proof_bar_header"]))
    for line in T["proof_bar_block"]:
        lines.append(_fill(str(line), proof_bar_text=proof_text))
    lines.append("")
    lines.append(str(S["output_spec_header"]))
    style_descriptions = T.get("style_descriptions") if isinstance(T.get("style_descriptions"), dict) else {}
    style_description = style_descriptions.get(fmt) or _fill(str(S["style_generic"]), fmt=fmt)
    archetype_id = visual_archetype['id']
    archetype_label = visual_archetype['label']
    for line_tpl in T["output_spec_lines"]:
        lines.append(line_tpl.format(
            canvas_spec=canvas_spec,
            aspect_ratio=aspect_ratio,
            style_description=style_description,
            archetype_id=archetype_id,
            archetype_label=archetype_label,
        ))
    lines.append("")
    lines.append(str(S["layout_header"]))
    lines.extend(layout_lines)
    lines.append("")
    lines.append(str(S["persona_header"]))
    for line in S["persona_lines"]:
        lines.append(_fill(
            str(line),
            persona_name=persona_name,
            persona_number=persona_number,
            pain=pain,
            desire=desire,
            friction=friction,
            proof=proof,
            tone=tone,
            concept_angle=concept["concept_angle"],
        ))
    lines.append(str(S["concept_path_note"]))
    if isinstance(creative_concept, dict):
        concept_label = str(
            creative_concept.get("label") or creative_concept.get("id") or ""
        ).strip()
        concept_description = str(creative_concept.get("description") or "").strip()
        if concept_label or concept_description:
            lines.append("")
            lines.append(str(S["concept_header"]))
            lines.append(_fill(str(S["concept_name_line"]), concept_label=concept_label))
            lines.append(_fill(str(S["concept_description_line"]), concept_description=concept_description))
    lines.append("")
    lines.append(_fill(str(S["create_ad_line"]), language=language_label(lang, T)))
    lines.append("")
    lines.append(str(S["exact_copy_header"]))
    lines.extend(copy_lines)
    lines.append(str(S["exact_copy_footer"]))
    lines.append(str(S["proof_bar_once_line"]))
    lines.append("")
    lines.append(str(S["negative_header"]))
    negative = list(T["negative_constraints"])
    if fmt == "UGC":
        negative[8:8] = T["ugc_extra_constraints"]
    if fmt == "BA":
        negative.extend(T["ba_extra_constraint"])
    lines.extend(negative)
    lines.append("")
    lines.append(str(S["quality_header"]))
    lines.extend(T["quality_bar_lines"])
    lines.append("")
    lines.append(str(S["visual_header"]))
    bg_title = (bg.get("title") or "Catalog background").strip()
    lighting_line = (lock.get("lighting") or "").strip() if isinstance(lock.get("lighting"), str) and lock.get("lighting").strip() else T["lighting_default"]
    props_line = (lock.get("props") or "").strip() if isinstance(lock.get("props"), str) and lock.get("props").strip() else T["props_default"]
    surfaces_line = (lock.get("surfaces") or "").strip() if isinstance(lock.get("surfaces"), str) and lock.get("surfaces").strip() else T["surfaces_default"]
    mood_line = (lock.get("mood") or "").strip() if isinstance(lock.get("mood"), str) and lock.get("mood").strip() else T["mood_default"]
    for line_tpl in T["visual_direction_lines"]:
        lines.append(line_tpl.format(
            bg_id=bg["id"],
            bg_title=bg_title,
            bg_seed=bg_seed,
            seeded_sentence=seeded_sentence,
            subject_line=subject_line,
            action_line=action_line,
            camera_line=camera_line,
            lighting_line=lighting_line,
            props_line=props_line,
            surfaces_line=surfaces_line,
            mood_line=mood_line,
            realism_line=realism_line,
            archetype_id=archetype_id,
            archetype_label=archetype_label,
        ))
    if fmt == "HERO":
        lines.append(T["hero_anti_convergence_rule"])
    lines.extend(archetype_direction_lines)
    if fmt == "BA":
        lines.extend(T["ba_panel_anchors"])
    if lock:
        lines.append(T["visual_match_lock"])
        if aspect_ratio == "9:16":
            lines.append(T["visual_match_lock_916"])
    lines.append("")
    lines.append(str(S["typography_header"]))
    lines.extend(T["typography_lines"])
    lines.append("")
    lines.extend(T["typography_extra_lines"])
    lines.append("")
    if aspect_ratio == "9:16":
        lines.append(T["safezone_916"])
    else:
        lines.append(T["safezone_45"])
    if aspect_ratio == "9:16":
        lines.append("")
        lines.append(T["outpaint_lock"])
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def validate_prompt_text(text: str, out_path: Path) -> None:
    if re.search(r"Background\s*seed\s*:\s*\d+", text, flags=re.IGNORECASE) is None:
        raise RuntimeError(f"Missing 'Background seed' in {out_path}")
    if re.search(r"Seeded\s+background\s+direction\s*\(single sentence, exact\)\s*:", text, flags=re.IGNORECASE) is None:
        raise RuntimeError(f"Missing seeded background label in {out_path}")
    if "SAFE-ZONE ENFORCEMENT" not in text:
        raise RuntimeError(f"Missing SAFE-ZONE ENFORCEMENT block in {out_path}")
    non_empty_lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(non_empty_lines) < 45:
        raise RuntimeError(f"Prompt too short ({len(non_empty_lines)} non-empty lines): {out_path}")


def aspect_ratio_folder(aspect_ratio: str) -> str:
    return "96" if aspect_ratio == "9:16" else "45"


def persona_name_to_slug(name: str, persona_number_fallback: int = 0) -> str:
    """Convert a persona name to a filename-safe slug.

    Examples:
        "Always Hungry" -> "always_hungry"
        "35+ Slow Progress Dieter" -> "35_slow_progress_dieter"
        "Ayurveda-First Buyer" -> "ayurveda_first_buyer"
    """
    import re
    s = (name or "").strip().lower()
    s = re.sub(r"[+\-]+", " ", s)
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or f"P{persona_number_fallback:02d}"


def prompt_filename(fmt: str, persona_number: int, persona_name: str, lang: str, concept_angle: str, creative_index: int = 1, creative_total: int = 1) -> str:
    """Canonical prompt filename: <FMT>_<persona_slug>_<LANG>_<angle>[_A<NN>].txt.

    The concept_angle is REQUIRED and is the dedup key for the on-image copy.
    The optional _A<NN> suffix is for multiplier runs (same fmt+persona+lang
    with N creative variations). The same stem (without extension) is reused
    for the generated image filename by the web automation scripts.
    """
    if not concept_angle:
        raise ValueError("concept_angle is required for prompt_filename()")
    slug = persona_name_to_slug(persona_name, persona_number)
    variant_suffix = f"_A{creative_index:02d}" if creative_total > 1 else ""
    return f"{fmt}_{slug}_{lang}_{concept_angle}{variant_suffix}.txt"


def classify_hook_structure(headline: str) -> str:
    """Classify headline opening pattern into hook_structure_class."""
    text = (headline or "").strip().lower()
    if not text:
        return "proof_led"
    if text.startswith("why ") or text.startswith("what ") or text.startswith("how ") or text.startswith("when ") or "?" in text[:30]:
        return "question_led"
    if text.startswith("finally") or text.startswith("trusted") or text.startswith("proven") or "70,000" in text or "doctor" in text:
        return "proof_led"
    contrast_terms = ["before", "after", "without", "instead", " but ", " yet ", " still ", "doesn’t have to", "doesn't have to", "even with"]
    if any(term in f" {text} " for term in contrast_terms):
        return "contrast_loop"
    if text.startswith("i ") or text.startswith("my ") or "felt" in text or "struggled" in text:
        return "confession_led"
    if text.startswith("stop") or text.startswith("start") or text.startswith("try") or text.startswith("see"):
        return "command_led"
    return "proof_led"


def classify_proof_style(headline: str, support_line: str) -> str:
    """Classify trust framing into proof_style_class."""
    combined = f"{(headline or '').lower()} {(support_line or '').lower()}"
    if "doctor" in combined or "ayurvedic" in combined or "dr." in combined or "formulated" in combined:
        return "authority_anchor"
    if "70,000" in combined or "user" in combined or "people" in combined or "review" in combined or "trusted" in combined:
        return "social_proof"
    if "but" in combined or "skeptical" in combined or "doubt" in combined or "worried" in combined or "tried" in combined:
        return "objection_flip"
    if "simple" in combined or "clear" in combined or "5-minute" in combined or "easy" in combined:
        return "routine_clarity"
    if "step" in combined or "routine" in combined or "morning" in combined or "night" in combined or "ok liquid" in combined:
        return "mechanism_explainer"
    return "mechanism_explainer"


def classify_cta_voice(cta: str) -> str:
    """Classify CTA tone into cta_voice_class."""
    text = (cta or "").strip().lower()
    if "today" in text or "now" in text or "start" in text or "act" in text:
        return "urgent_start"
    if "fit" in text or "risk" in text or "try" in text or "safe" in text:
        return "reassurance_start"
    if "test" in text or "challenge" in text or "15-day" in text:
        return "challenge_action"
    if "learn" in text or "how" in text or "works" in text or "discover" in text:
        return "discovery_action"
    if "see" in text or "view" in text or "check" in text or "steps" in text or "details" in text:
        return "guided_next_step"
    return "guided_next_step"


def get_opening_pattern_4tok(text: str) -> str:
    """Extract first 4 normalized tokens from headline."""
    words = re.findall(r"[a-zA-Z\u0900-\u097F]+", (text or "").strip().lower())
    return "_".join(words[:4]) if words else ""


def get_copy_skeleton(fmt: str, headline: str, support_line: str, bullets: list[str], cta: str) -> str:
    """Derive high-level copy structure tag."""
    has_question = "?" in (headline or "")
    has_contrast = any(w in (headline or "").lower() for w in ["without", "instead", "but", "before", "after"])
    has_mechanism = any(w in f"{(headline or '').lower()} {(support_line or '').lower()}" for w in ["routine", "step", "liquid", "tablet", "powder", "digestion", "cravings"])
    has_time = any(w in f"{(headline or '').lower()} {(support_line or '').lower()}" for w in ["15-day", "15 day", "15 days", "morning", "night", "evening", "daily"])
    has_proof = any(w in f"{(headline or '').lower()} {(support_line or '').lower()}" for w in ["doctor", "70,000", "proven", "trusted", "ayurvedic"])

    if has_question and has_mechanism:
        return "question_mechanism_cta"
    if has_contrast and has_mechanism:
        return "contrast_mechanism_cta"
    if has_proof and has_time:
        return "proof_time_cta"
    if has_mechanism and has_time:
        return "pain_mechanism_time"
    if has_contrast:
        return "pain_agitate_solve"
    if has_proof:
        return "proof_then_routine"
    if has_time:
        return "micro_story_then_action"
    return "problem_reframe_then_next_step"


def has_protocol_mechanics(text: str) -> bool:
    lowered = (text or "").lower()
    return any(w in lowered for w in ["am", "pm", "4-hour", "4 hour", "empty stomach", "no solid", "liquid", "tablet", "powder"])


def has_social_proof_number(text: str) -> bool:
    lowered = (text or "").lower()
    return bool(re.search(r"\d{2,}|70,?000|lakh|crore", lowered))


def get_background_scene_category(bg: dict[str, Any]) -> str:
    """Infer scene category from background metadata."""
    title = (bg.get("title") or "").lower()
    if any(w in title for w in ["kitchen", "counter", "stove", "fridge", "dining"]):
        return "kitchen"
    if any(w in title for w in ["bedroom", "bed", "nightstand", "sleep"]):
        return "bedroom"
    if any(w in title for w in ["office", "desk", "workstation", "laptop", "computer"]):
        return "office"
    if any(w in title for w in ["studio", "backdrop", "seamless", "pedestal", "gradient"]):
        return "studio"
    if any(w in title for w in ["outdoor", "garden", "park", "nature", "balcony"]):
        return "outdoor"
    if any(w in title for w in ["living", "sofa", "couch", "lounge", "tv"]):
        return "living_room"
    if any(w in title for w in ["clinical", "medical", "hospital", "white room", "lab"]):
        return "clinical"
    return "lifestyle"


def main() -> int:
    args = parse_args()
    copy_path = Path(args.copy_file)
    payload = load_json(copy_path)

    ads = payload.get("ads")
    if not isinstance(ads, list) or not ads:
        raise RuntimeError("copy file must contain non-empty 'ads' array")

    backgrounds = load_json(BACKGROUNDS_PATH)

    seed = args.seed if args.seed is not None else random.SystemRandom().randint(10_000_000, 2_147_483_647)
    render_langs = ["EN", "HI", "HINGLISH"] if args.language_mode == "BOTH" else [args.language_mode]

    for i, ad in enumerate(ads):
        ctx = f"ads[{i}]"
        if not isinstance(ad, dict):
            raise RuntimeError(f"{ctx} must be an object")

        fmt = require_str(ad, "format", ctx).upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,15}", fmt):
            raise RuntimeError(f"{ctx}.format must be a format id like HERO or STORY")

        aspect_ratio = (ad.get("aspect_ratio") or payload.get("default_aspect_ratio") or "4:5").strip()
        if aspect_ratio not in {"4:5", "9:16"}:
            raise RuntimeError(f"{ctx}.aspect_ratio must be '4:5' or '9:16'")

        persona = ad.get("persona")
        if not isinstance(persona, dict):
            raise RuntimeError(f"{ctx}.persona must be an object")
        require_int(persona, "number", f"{ctx}.persona")
        require_str(persona, "name", f"{ctx}.persona")
        for k in ["pain_en", "desire_en", "friction_en", "proof_needed_en", "tone_cue_en", "pain_hi", "desire_hi", "friction_hi", "proof_needed_hi", "tone_cue_hi"]:
            require_str(persona, k, f"{ctx}.persona")

        resolve_concept_fields(ad, fmt, persona)

        copy = ad.get("copy")
        if not isinstance(copy, dict):
            raise RuntimeError(f"{ctx}.copy must be an object with language blocks")
        for lang in render_langs:
            if lang not in copy or not isinstance(copy[lang], dict):
                raise RuntimeError(f"{ctx}.copy must include {lang} object")
            cb = parse_copy_block(fmt, lang, copy[lang])

            if fmt in {"HERO", "UGC"} and not cb.support_line:
                raise RuntimeError(f"{ctx}.copy.{lang}.support_line required for {fmt}")
            if fmt in {"BA", "FEAT"}:
                min_bullets = 4 if fmt == "BA" else 2
                if not cb.bullets or len(cb.bullets) < min_bullets:
                    raise RuntimeError(f"{ctx}.copy.{lang}.bullets must have >={min_bullets} items for {fmt}")
            if fmt == "TEST":
                if not cb.trust_line:
                    raise RuntimeError(f"{ctx}.copy.{lang}.trust_line required for TEST")

    batch_name = args.batch or next_batch_name(OUTPUT_DIR)
    batch_dir = OUTPUT_DIR / batch_name

    if args.dry_run:
        print(f"OK (dry-run). Would write batch: {batch_name}")
        print(f"Seed: {seed}")
        print(f"Ads: {len(ads)}")
        return 0

    batch_dir.mkdir(parents=True, exist_ok=True)
    timestamp = now_utc_iso()
    run_archetype_usage: dict[str, set[str]] = {}
    background_group_cache: dict[str, dict[str, Any]] = {}

    for i, ad in enumerate(ads):
        fmt = str(ad["format"]).upper()
        aspect_ratio = (ad.get("aspect_ratio") or payload.get("default_aspect_ratio") or "4:5").strip()
        ratio_dir = batch_dir / aspect_ratio_folder(aspect_ratio)
        ratio_dir.mkdir(parents=True, exist_ok=True)
        persona = ad["persona"]
        persona_number = int(persona["number"])
        creative_index = int(ad.get("creative_index") or 1)
        creative_total = int(ad.get("creative_total") or 1)
        angle = (ad.get("headline_angle") or "").strip()
        concept = resolve_concept_fields(ad, fmt, persona)

        for stale_lang in ["EN", "HI", "HINGLISH"]:
            if stale_lang in render_langs:
                continue
            slug = persona_name_to_slug(persona.get("name", ""), persona_number)
            for stale_path in ratio_dir.glob(f"{fmt}_{slug}_{stale_lang}*.txt"):
                stale_path.unlink()

        background_group_key = str(ad.get("background_group_key") or "").strip()
        cached_background = background_group_cache.get(background_group_key) if background_group_key else None
        if cached_background:
            bg = cached_background["background"]
            bg_seed = cached_background["background_seed"]
        else:
            forced_bg = ad.get("background_slot") or ad.get("background_slot_id")
            if isinstance(forced_bg, str) and forced_bg.strip():
                bg = get_background_by_id(backgrounds, fmt, forced_bg)
            else:
                bg = pick_background_slot(backgrounds, fmt, seed)

            forced_seed = ad.get("background_seed")
            if isinstance(forced_seed, int) and forced_seed > 0:
                bg_seed = forced_seed
            else:
                bg_seed = random.Random(seed + i * 101).randint(1, 2_147_483_647)
            if background_group_key:
                background_group_cache[background_group_key] = {"background": bg, "background_seed": bg_seed}
        visual_lock = ad.get("visual_lock") if isinstance(ad.get("visual_lock"), dict) else {}
        seeded_sentence = build_seeded_background_sentence(bg, bg_seed, aspect_ratio)
        if isinstance(visual_lock.get("seeded_background_direction"), str) and visual_lock.get("seeded_background_direction").strip():
            seeded_sentence = visual_lock.get("seeded_background_direction").strip()
            if aspect_ratio == "9:16":
                seeded_sentence += "; maintain base scene identity and arrangement, only adapt spacing for 9:16 safe bands"

        selector_lang = "EN" if isinstance(ad.get("copy"), dict) and isinstance(ad["copy"].get("EN"), dict) else render_langs[0]
        selector_copy = parse_copy_block(fmt, selector_lang, ad["copy"][selector_lang])
        forced_archetype = ""
        if isinstance(ad.get("visual_archetype"), str) and ad.get("visual_archetype", "").strip():
            forced_archetype = ad["visual_archetype"].strip()
        elif isinstance(visual_lock.get("visual_archetype"), str) and visual_lock.get("visual_archetype", "").strip():
            forced_archetype = visual_lock["visual_archetype"].strip()
        used_archetypes_for_format = run_archetype_usage.setdefault(fmt, set())
        visual_archetype = pick_visual_archetype(
            fmt,
            persona_number,
            selector_copy,
            bg_seed,
            forced_archetype=forced_archetype,
            used_archetype_ids=used_archetypes_for_format,
        )
        used_archetypes_for_format.add(visual_archetype["id"])

        rendered: dict[str, str] = {}
        for lang in render_langs:
            cb = parse_copy_block(fmt, lang, ad["copy"][lang])
            out_text = render_prompt(
                fmt,
                lang,
                aspect_ratio,
                persona,
                cb,
                concept,
                bg,
                bg_seed,
                seeded_sentence,
                visual_archetype,
                visual_lock=visual_lock,
            )
            out_path = ratio_dir / prompt_filename(fmt, persona_number, persona.get("name", ""), lang, concept.get("concept_angle", ""), creative_index, creative_total)
            validate_prompt_text(out_text, out_path)
            out_path.write_text(out_text, encoding="utf-8")
            prompt_meta = {
                "type": "ad_prompt",
                "format": fmt,
                "persona": f"P{persona_number:02d}",
                "persona_number": persona_number,
                "persona_name": persona.get("name", ""),
                "language": lang,
                "aspect_ratio": aspect_ratio,
                "creative_index": creative_index,
                "creative_total": creative_total,
                "multiplier": creative_total,
                "background_group_key": background_group_key,
                "headline_angle": angle or None,
                "concept_angle": concept.get("concept_angle", ""),
                "hypothesis": ad.get("hypothesis") if isinstance(ad.get("hypothesis"), dict) else {},
                "hypothesis_type": (ad.get("hypothesis") or {}).get("type", "") if isinstance(ad.get("hypothesis"), dict) else "",
                "hypothesis_variant": (ad.get("hypothesis") or {}).get("variant", "") if isinstance(ad.get("hypothesis"), dict) else "",
                "background": {
                    "slot": bg["id"],
                    "name": bg.get("title", ""),
                    "source": "catalog",
                    "seed": bg_seed,
                    "seeded_direction": seeded_sentence,
                    "scene_category": get_background_scene_category(bg),
                    "base": bg.get("base", ""),
                    "formats": bg.get("formats", []),
                },
                "visual_archetype": {
                    "id": visual_archetype["id"],
                    "label": visual_archetype["label"],
                    "forced": bool(forced_archetype),
                    "reused_from_run_id": ad.get("visual_pattern_reused_from_run_id") or "",
                    "reuse_key": ad.get("visual_pattern_reuse_key") or "",
                },
                "visual_pattern": {
                    "id": visual_archetype["id"],
                    "label": visual_archetype["label"],
                    "selected_by_user": bool(forced_archetype),
                    "selection_mode": "reused" if ad.get("visual_pattern_reused_from_run_id") else ("manual" if forced_archetype else "auto_rotate"),
                    "reused_from_run_id": ad.get("visual_pattern_reused_from_run_id") or "",
                    "reuse_key": ad.get("visual_pattern_reuse_key") or "",
                },
                "background_decisions": {
                    "forced_background": bool(ad.get("background_slot") or ad.get("background_slot_id")),
                    "forced_seed": isinstance(ad.get("background_seed"), int) and ad.get("background_seed") > 0,
                    "reused_from_run_id": ad.get("background_reused_from_run_id") or "",
                    "reuse_key": ad.get("background_reuse_key") or "",
                    "shared_by_multiplier": creative_total > 1 and bool(background_group_key),
                    "shared_across_personas": bool(ad.get("share_background_across_personas")),
                    "visual_lock_applied": bool(visual_lock),
                    "aspect_ratio": aspect_ratio,
                    "assembler_seed": seed,
                },
            }
            out_path.with_suffix(".json").write_text(json.dumps(prompt_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            rendered[lang] = out_text

    print(f"Batch: {batch_name}")
    print(f"Seed: {seed}")
    print(f"Wrote: {batch_dir.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
