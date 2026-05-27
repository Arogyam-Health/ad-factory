#!/usr/bin/env python3
"""
Assembler-only ad prompt generator.

What this script does:
  - Reads externally-generated ad copy from a JSON file (no copy generation here).
  - Selects catalog background slots with exhaustive per-format rotation.
  - Builds seeded background sentence from `background_variant.json`.
  - Assembles full 9-section prompts per playbook and writes `output/vN/<FORMAT>_<persona>_<lang>.txt`.
  - Enforces safe-zone rules by embedding an explicit SAFE-ZONE ENFORCEMENT block.
  - Appends entries to `AD_GENERATION_REGISTRY.JSON` and updates indexes (background rotation + used_text).

What this script explicitly does NOT do:
  - It does not call any LLM.
  - It does not invent persona fields or ad copy.
  - It does not “freshen” text; freshness is enforced by strict uniqueness checks against registry.
"""

from __future__ import annotations

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
REGISTRY_PATH = ROOT / "AD_GENERATION_REGISTRY.JSON"
BACKGROUNDS_PATH = ROOT / "background_variant.json"
COPY_PROMPTS_PATH = ROOT / "dashboard" / "backend" / "copy_prompt_templates.json"
OUTPUT_DIR = ROOT / "output"

SUPPORTED_FORMATS = {"HERO", "BA", "TEST", "FEAT", "UGC"}
SUPPORTED_LANGS = {"EN", "HI", "HINGLISH"}
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
SUPPORTED_CONCEPT_STRUCTURES = {"pas", "bab", "fab", "four_us", "pab"}
HEADLINE_ANGLE_TO_CONCEPT = {
    "pain": "pain_point",
    "objection": "comparison",
    "mechanism": "curiosity",
    "time": "offer",
    "proof": "social_proof",
    "sacrifice_reduction": "comparison",
}
FORMAT_DEFAULT_STRUCTURE = {
    "HERO": "pab",
    "BA": "bab",
    "TEST": "pas",
    "FEAT": "fab",
    "UGC": "pas",
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
    parser.add_argument("--no-registry-write", action="store_true", help="Skip writing AD_GENERATION_REGISTRY.JSON updates")
    parser.add_argument("--skip-uniqueness-check", action="store_true", help="Allow duplicate copy values against registry")
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


def stable_fmt_seed(seed: int, fmt: str) -> int:
    return (seed * 31 + sum(ord(c) for c in fmt)) & 0x7FFFFFFF


def ensure_slot_tracker(registry: dict[str, Any], fmt: str, pool_ids: list[str], seed: int) -> dict[str, Any]:
    idx = registry.setdefault("indexes", {})
    tracker = idx.setdefault("slot_exhaustion_tracker", {}).setdefault(fmt, {})
    used = tracker.get("used") or []
    remaining = tracker.get("remaining") or []
    cycle = int(tracker.get("cycle_number") or 1)

    if remaining:
        tracker["used"] = used
        tracker["remaining"] = remaining
        tracker["cycle_number"] = cycle
        return tracker

    # Start (or restart) a cycle: refill remaining with a deterministic shuffle.
    if used:
        cycle += 1
    order = list(pool_ids)
    rng = random.Random(stable_fmt_seed(seed, fmt))
    rng.shuffle(order)

    tracker["cycle_number"] = cycle
    tracker["used"] = []
    tracker["remaining"] = order
    return tracker


def pick_background_slot(
    registry: dict[str, Any],
    backgrounds: dict[str, Any],
    fmt: str,
    seed: int,
) -> dict[str, Any]:
    variants: list[dict[str, Any]] = backgrounds.get("variants", [])
    pool = [v for v in variants if fmt in (v.get("formats") or [])]
    if not pool:
        raise RuntimeError(f"No background variants found for format {fmt}")
    pool_ids = [v["id"] for v in pool]
    tracker = ensure_slot_tracker(registry, fmt, pool_ids, seed)

    remaining: list[str] = tracker["remaining"]
    chosen_id = remaining.pop(0)
    tracker["used"].append(chosen_id)

    chosen = next((v for v in pool if v.get("id") == chosen_id), None)
    if not chosen:
        chosen = pool[0]
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


def build_seeded_background_sentence(bg: dict[str, Any], seed: int, aspect_ratio: str) -> str:
    rng = random.Random(seed)
    base = bg["base"]
    lighting = rng.choice(bg["lighting"])
    surface = rng.choice(bg["surface"])
    environment = rng.choice(bg["environment"])
    mood = rng.choice(bg["mood"])
    camera = rng.choice(bg["camera"])
    color_tone = rng.choice(bg["color_tone"])
    composition = rng.choice(bg.get("composition") or ["balanced feed composition inside the central safe field"])
    layout_intent = rng.choice(bg.get("layout_intent") or ["preserve a stable center-of-interest corridor with consistent margin protection on every side"])
    cta_safe_space = rng.choice(bg.get("cta_safe_space") or ["maintain subtle low-contrast space near the lower edge to protect feed overlay readability"])
    crop_safety = rng.choice(bg.get("crop_safety") or ["maintain protected margin buffers so alternate crops do not clip meaningful scene structure"])
    text_overlay_treatment = rng.choice(
        bg.get("text_overlay_treatment")
        or [
            "if a text readability panel is used, keep it in the upper text zone only as a soft vertical fade (high opacity near top, fading to transparent before the product cluster), never behind or below products"
        ]
    )
    edge_tone_control = rng.choice(
        bg.get("edge_tone_control")
        or [
            "keep all frame edges tonally neutral with no orange, amber, or sepia cast; no border glow and no vignette halo"
        ]
    )

    if aspect_ratio == "9:16":
        format_clause = (
            "designed for 9:16 vertical placement with key subject content constrained to the 14-65 percent safe band, positioned slightly above center, and with the lower 35 percent kept visually quiet for overlays; avoid edge glow frames and tinted border gradients"
        )
    else:
        format_clause = (
            "designed for 4:5 feed framing with key content held inside the central safe field, centered to slightly above center, while top 10 percent, bottom 15 percent, and side edge zones remain low-priority; avoid edge glow frames and tinted border gradients"
        )

    return (
        f"{base} on a {surface}, with {environment}, lit by {lighting}, conveying {mood}; "
        f"{camera}, {composition}, {layout_intent}, {cta_safe_space}, {crop_safety}, {text_overlay_treatment}, {edge_tone_control}, {color_tone}, "
        f"{format_clause}, clean premium studio ad photography, ultra-detailed, flawless commercial finish."
    )


def require_str(obj: dict[str, Any], key: str, ctx: str) -> str:
    val = obj.get(key)
    if not isinstance(val, str) or not val.strip():
        raise RuntimeError(f"Missing or empty string '{key}' in {ctx}")
    return val.strip()


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
    structure = clean_id(ad.get("concept_structure"))
    explicit = bool(angle or structure)

    if angle not in SUPPORTED_CONCEPT_ANGLES:
        headline_angle = clean_id(ad.get("headline_angle"))
        angle = HEADLINE_ANGLE_TO_CONCEPT.get(headline_angle, "desired_outcome")

    if structure not in SUPPORTED_CONCEPT_STRUCTURES:
        structure = FORMAT_DEFAULT_STRUCTURE.get(fmt, "four_us")

    return {
        "concept_angle": angle,
        "concept_structure": structure,
        "explicit": explicit,
    }


def append_concept_combo_index(
    registry: dict[str, Any],
    entry_id: str,
    timestamp: str,
    fmt: str,
    persona_number: int,
    concept: dict[str, Any],
) -> None:
    recent = registry.setdefault("indexes", {}).setdefault("concept_combos", {}).setdefault("recent", [])
    recent.append(
        {
            "entry_id": entry_id,
            "timestamp": timestamp,
            "format": fmt,
            "persona_number": persona_number,
            "concept_angle": concept["concept_angle"],
            "concept_structure": concept["concept_structure"],
        }
    )
    if len(recent) > 500:
        del recent[:-500]


def parse_copy_block(fmt: str, lang: str, raw: dict[str, Any]) -> CopyBlock:
    ctx = f"ads[].copy.{lang} for format={fmt}"
    headline = require_str(raw, "headline", ctx)
    if re.search(r"\b(ok\s*liquid|ok\s*tablet|ok\s*powder|okp)\b", headline, flags=re.IGNORECASE):
        raise RuntimeError(f"{ctx}.headline contains product component name; move it to support/bullets")
    if re.search(r"\b(am|pm)\b|\b4\s*-?\s*hour\b|\bno\s*solid\b|\bempty\s*stomach\b", headline, flags=re.IGNORECASE):
        raise RuntimeError(f"{ctx}.headline contains protocol mechanics; move to support/bullets")
    cta = require_str(raw, "cta", ctx)
    sub_val = raw.get("subheadline") or raw.get("support_line")
    support_line = (sub_val or "").strip() if isinstance(sub_val, str) else ""
    context_line = (raw.get("context_line") or "").strip() if isinstance(raw.get("context_line"), str) else ""
    trust_line = (raw.get("trust_line") or "").strip() if isinstance(raw.get("trust_line"), str) else ""
    attribution = (raw.get("attribution") or "").strip() if isinstance(raw.get("attribution"), str) else ""
    bullets_val = raw.get("bullets")
    bullets: list[str] | None = None
    if bullets_val is not None:
        if not isinstance(bullets_val, list) or not all(isinstance(x, str) and x.strip() for x in bullets_val):
            raise RuntimeError(f"'bullets' must be a non-empty string list when present in {ctx}")
        bullets = [x.strip() for x in bullets_val]
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


def registry_used_text(registry: dict[str, Any]) -> dict[str, set[str]]:
    buckets = (registry.get("indexes", {}) or {}).get("used_text", {}) or {}
    out: dict[str, set[str]] = {}
    for bucket, arr in buckets.items():
        if not isinstance(arr, list):
            continue
        out[bucket] = {s.strip() for s in arr if isinstance(s, str) and s.strip()}
    return out


def registry_all_used_text(used: dict[str, set[str]]) -> set[str]:
    all_text: set[str] = set()
    for values in used.values():
        all_text.update(v for v in values if v.strip())
    return all_text


def uniqueness_check(
    used: dict[str, set[str]],
    all_used: set[str],
    bucket: str,
    value: str,
    collisions: list[str],
    ctx: str,
) -> None:
    clean = value.strip()
    if not clean:
        return
    if clean in all_used:
        collisions.append(f"{ctx} collides with registry used_text across all buckets: {value!r}")
    elif clean in used.get(bucket, set()):
        collisions.append(f"{ctx} collides with registry used_text.{bucket}: {value!r}")


def add_used_text(registry: dict[str, Any], bucket: str, values: list[str]) -> None:
    idx = registry.setdefault("indexes", {}).setdefault("used_text", {}).setdefault(bucket, [])
    for v in values:
        s = (v or "").strip()
        if s:
            idx.append(s)


def next_entry_id(registry: dict[str, Any]) -> str:
    entries = registry.get("entries") or []
    if not entries:
        return "entry_001"
    last = entries[-1].get("id", "")
    m = re.match(r"^entry_(\d+)$", str(last))
    if not m:
        return f"entry_{len(entries) + 1:03d}"
    return f"entry_{int(m.group(1)) + 1:03d}"


def append_background_index(registry: dict[str, Any], fmt: str, entry_id: str, timestamp: str, bg_id: str) -> None:
    idx = registry.setdefault("indexes", {}).setdefault("backgrounds_by_format", {}).setdefault(fmt, [])
    idx.append({"entry_id": entry_id, "timestamp": timestamp, "background_slot": bg_id, "background_source": "catalog"})


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
        raise RuntimeError(f"No visual archetypes configured for format {fmt}")

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
) -> str:
    if fmt == "HERO":
        style = "HERO, polished enough for paid ad deployment."
    elif fmt == "BA":
        style = "BA (before/after journey without body-shaming visuals)."
    elif fmt == "TEST":
        style = "TEST (trust-first testimonial/review framing)."
    elif fmt == "FEAT":
        style = "FEAT (features and mechanism clarity)."
    elif fmt == "UGC":
        style = "UGC (creator-style authenticity, premium and clean)."
    else:
        raise RuntimeError(f"Unsupported format: {fmt}")

    if lang == "EN":
        persona_name = require_str(persona, "name", "ads[].persona")
        pain = require_str(persona, "pain_en", "ads[].persona")
        desire = require_str(persona, "desire_en", "ads[].persona")
        friction = require_str(persona, "friction_en", "ads[].persona")
        proof = require_str(persona, "proof_needed_en", "ads[].persona")
        tone = require_str(persona, "tone_cue_en", "ads[].persona")
    elif lang == "HINGLISH":
        persona_name = require_str(persona, "name", "ads[].persona")
        pain = str(persona.get("pain_hinglish") or persona.get("pain_hi") or "")
        desire = str(persona.get("desire_hinglish") or persona.get("desire_hi") or "")
        friction = str(persona.get("friction_hinglish") or persona.get("friction_hi") or "")
        proof = str(persona.get("proof_needed_hinglish") or persona.get("proof_needed_hi") or "")
        tone = str(persona.get("tone_cue_hinglish") or persona.get("tone_cue_hi") or "")
    else:
        persona_name = require_str(persona, "name", "ads[].persona")
        pain = require_str(persona, "pain_hi", "ads[].persona")
        desire = require_str(persona, "desire_hi", "ads[].persona")
        friction = require_str(persona, "friction_hi", "ads[].persona")
        proof = require_str(persona, "proof_needed_hi", "ads[].persona")
        tone = require_str(persona, "tone_cue_hi", "ads[].persona")

    persona_number = require_int(persona, "number", "ads[].persona")

    T = PROMPT_ASSEMBLER_TEMPLATES

    layout_lines = list(visual_archetype.get("layout_lines") or [])
    archetype_direction_lines = [str(line) for line in (visual_archetype.get("direction_lines") or []) if isinstance(line, str) and line.strip()]

    copy_lines: list[str] = []
    if fmt == "HERO":
        copy_lines = [
            f"- Headline: {copy.headline}",
            f"- Support line: {copy.support_line}",
            f"- CTA: {copy.cta}",
        ]
    elif fmt == "UGC":
        copy_lines = [
            f"- Headline: {copy.headline}",
            f"- Support line: {copy.support_line}",
            f"- CTA: {copy.cta}",
        ]
        if copy.context_line:
            copy_lines.insert(2, f"- Context line: {copy.context_line}")
    elif fmt == "BA":
        bullets = copy.bullets or []
        left_lines, right_lines = split_ba_contrast_lines(bullets)
        copy_lines = [f"- Headline: {copy.headline}"]
        for i, line in enumerate(left_lines, start=1):
            copy_lines.append(f"- Left situation {i}: {line}")
        for i, line in enumerate(right_lines, start=1):
            copy_lines.append(f"- Right shift {i}: {line}")
        copy_lines.append(f"- CTA: {copy.cta}")
    elif fmt == "FEAT":
        bullets = copy.bullets or []
        copy_lines = [f"- Headline: {copy.headline}"]
        for i, b in enumerate(bullets, start=1):
            copy_lines.append(f"- Bullet {i}: {b}")
        copy_lines.append(f"- CTA: {copy.cta}")
    else:  # TEST
        copy_lines = [
            f"- Headline: {copy.headline}",
            f"- Trust line: {copy.trust_line}",
            f"- CTA: {copy.cta}",
        ]

    copy_lines.append(f"- Proof bar: 70,000+ Users | 3-5 kg loss with 1 Kit | 100% Ayurvedic")

    lock = visual_lock if isinstance(visual_lock, dict) else {}
    subject_line = (lock.get("subject") or "").strip() if isinstance(lock.get("subject"), str) else ""
    if not subject_line:
        subject_line = T["subject_lines"].get(fmt) or T["subject_lines"]["default"]
    action_line = (lock.get("action") or "").strip() if isinstance(lock.get("action"), str) and lock.get("action").strip() else (T["action_lines"].get(fmt) or T["action_lines"]["default"])
    camera_line = (lock.get("camera") or "").strip() if isinstance(lock.get("camera"), str) and lock.get("camera").strip() else (T["camera_lines"].get(fmt) or T["camera_lines"]["default"])
    realism_line = (lock.get("realism") or "").strip() if isinstance(lock.get("realism"), str) and lock.get("realism").strip() else (T["realism_lines"].get(fmt) or T["realism_lines"]["default"])

    lines: list[str] = []
    canvas_spec = "1080 x 1920" if aspect_ratio == "9:16" else "1080 x 1350"
    lines.append("PRODUCT LOCK BLOCK")
    lines.extend(T["product_lock_block"])
    lines.append("")
    lines.append("PROOF BAR BLOCK")
    lines.extend(T["proof_bar_block"])
    lines.append("")
    lines.append("OUTPUT SPEC")
    style_description = T["style_descriptions"].get(fmt) or ""
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
    lines.append("FORMAT LAYOUT INSTRUCTIONS")
    lines.extend(layout_lines)
    lines.append("")
    lines.append("PERSONA INPUT BLOCK")
    lines.extend(
        [
            f"- Persona: {persona_name} (Persona {persona_number})",
            f"- Pain: {pain}",
            f"- Desire: {desire}",
            f"- Friction: {friction}",
            f"- Proof needed: {proof}",
            f"- Tone cue: {tone}",
            f"- Concept angle: {concept['concept_angle']}",
            f"- Concept structure: {concept['concept_structure']}",
            "- Concept path is strategy only; do not render these labels on-image.",
        ]
    )
    lines.append("")
    lines.append("EXACT ON-IMAGE COPY - DO NOT ALTER ANYTHING")
    lines.extend(copy_lines)
    lines.append("Render every character exactly as written. No paraphrasing, no punctuation changes, no autocorrection.")
    lines.append("- Proof bar is present exactly once, fully readable, horizontally centered, and does not enter the bottom restricted band.")
    lines.append("")
    lines.append("NEGATIVE CONSTRAINTS")
    negative = list(T["negative_constraints"])
    if fmt == "UGC":
        negative[8:8] = T["ugc_extra_constraints"]
    if fmt == "BA":
        negative.extend(T["ba_extra_constraint"])
    lines.extend(negative)
    lines.append("")
    lines.append("QUALITY BAR - verify before accepting output")
    lines.extend(T["quality_bar_lines"])
    lines.append("")
    lines.append("VISUAL DIRECTION BLOCK")
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
    lines.append("TYPOGRAPHY SHARPNESS BLOCK")
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


def prompt_filename(fmt: str, persona_number: int, lang: str, concept_angle: str = "", creative_index: int = 1, creative_total: int = 1) -> str:
    suffix = f"_A{creative_index:02d}" if creative_total > 1 else ""
    angle_part = f"_{concept_angle}" if concept_angle else ""
    return f"{fmt}_P{persona_number:02d}_{lang}{suffix}{angle_part}.txt"


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

    registry = load_json(REGISTRY_PATH)
    backgrounds = load_json(BACKGROUNDS_PATH)

    seed = args.seed if args.seed is not None else random.SystemRandom().randint(10_000_000, 2_147_483_647)
    used = registry_used_text(registry)
    all_used = registry_all_used_text(used)
    render_langs = ["EN", "HI", "HINGLISH"] if args.language_mode == "BOTH" else [args.language_mode]

    # Validate copy payload + uniqueness against registry BEFORE consuming background slots.
    collisions: list[str] = []
    run_used_text: dict[str, set[str]] = {}
    run_all_text: set[str] = set()
    for i, ad in enumerate(ads):
        ctx = f"ads[{i}]"
        if not isinstance(ad, dict):
            raise RuntimeError(f"{ctx} must be an object")

        fmt = require_str(ad, "format", ctx).upper()
        if fmt not in SUPPORTED_FORMATS:
            raise RuntimeError(f"{ctx}.format must be one of {sorted(SUPPORTED_FORMATS)}")

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

            def check_run_text(bucket: str, value: str, text_ctx: str) -> None:
                clean = (value or "").strip()
                if not clean:
                    return
                if clean in run_all_text:
                    collisions.append(f"{text_ctx} duplicates another text string in this copy batch: {clean!r}")
                run_all_text.add(clean)
                seen = run_used_text.setdefault(bucket, set())
                if clean in seen:
                    collisions.append(f"{text_ctx} duplicates another item in this copy batch: {clean!r}")
                seen.add(clean)

            check_run_text("headline_en" if lang == "EN" else "headline_hi", cb.headline, f"{ctx}.copy.{lang}.headline")
            check_run_text("cta_en" if lang == "EN" else "cta_hi", cb.cta, f"{ctx}.copy.{lang}.cta")

            # format-specific required fields (do not invent)
            if fmt in {"HERO", "UGC"} and not cb.support_line:
                raise RuntimeError(f"{ctx}.copy.{lang}.support_line required for {fmt}")
            if fmt in {"BA", "FEAT"}:
                if not cb.bullets or len(cb.bullets) < 2:
                    raise RuntimeError(f"{ctx}.copy.{lang}.bullets must have >=2 items for {fmt}")
            if fmt == "TEST":
                if not cb.trust_line:
                    raise RuntimeError(f"{ctx}.copy.{lang}.trust_line required for TEST")

            # Registry uniqueness checks (exact string match).
            uniqueness_check(used, all_used, "headline_en" if lang == "EN" else "headline_hi", cb.headline, collisions, f"{ctx}.copy.{lang}.headline")
            uniqueness_check(used, all_used, "cta_en" if lang == "EN" else "cta_hi", cb.cta, collisions, f"{ctx}.copy.{lang}.cta")

            if fmt in {"HERO", "UGC"}:
                check_run_text("support_line_en" if lang == "EN" else "support_line_hi", cb.support_line, f"{ctx}.copy.{lang}.support_line")
                uniqueness_check(used, all_used, "support_line_en" if lang == "EN" else "support_line_hi", cb.support_line, collisions, f"{ctx}.copy.{lang}.support_line")
            if fmt in {"BA", "FEAT"}:
                bucket = "bullets_en" if lang == "EN" else "bullets_hi"
                for b in cb.bullets or []:
                    check_run_text(bucket, b, f"{ctx}.copy.{lang}.bullets")
                    uniqueness_check(used, all_used, bucket, b, collisions, f"{ctx}.copy.{lang}.bullets")
            if fmt == "TEST":
                check_run_text("support_line_en" if lang == "EN" else "support_line_hi", cb.trust_line, f"{ctx}.copy.{lang}.trust_line")
                uniqueness_check(used, all_used, "support_line_en" if lang == "EN" else "support_line_hi", cb.trust_line, collisions, f"{ctx}.copy.{lang}.trust_line")

    if collisions and not args.skip_uniqueness_check:
        msg = "Copy batch failed uniqueness checks against registry (regenerate via your LLM step):\n- " + "\n- ".join(collisions[:50])
        if len(collisions) > 50:
            msg += f"\n... and {len(collisions)-50} more collisions"
        raise RuntimeError(msg)

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
            for stale_path in ratio_dir.glob(f"{fmt}_P{persona_number:02d}_{stale_lang}*.txt"):
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
                bg = pick_background_slot(registry, backgrounds, fmt, seed)

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
            out_path = ratio_dir / prompt_filename(fmt, persona_number, lang, concept.get("concept_angle", ""), creative_index, creative_total)
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
                "concept_structure": concept.get("concept_structure", ""),
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

        if args.no_registry_write:
            continue

        entry_id = next_entry_id(registry)
        headline_en = ad["copy"]["EN"]["headline"]
        headline_hi = ad["copy"]["HI"]["headline"]
        support_en = ad["copy"]["EN"].get("support_line") or ad["copy"]["EN"].get("trust_line") or ""
        support_hi = ad["copy"]["HI"].get("support_line") or ad["copy"]["HI"].get("trust_line") or ""
        cta_en = ad["copy"]["EN"]["cta"]
        cta_hi = ad["copy"]["HI"]["cta"]
        bullets_en = ad["copy"]["EN"].get("bullets") or []
        bullets_hi = ad["copy"]["HI"].get("bullets") or []

        # Hypothesis metadata from ad payload (injected by backend when testing)
        hyp_meta = ad.get("hypothesis") or {}

        entry = {
            "id": entry_id,
            "timestamp": timestamp,
            "format": fmt,
            "persona_number": persona["number"],
            "persona_name": persona["name"],
            "headline_angle": angle or None,
            "concept_angle": concept["concept_angle"],
            "concept_structure": concept["concept_structure"],
            "visual_archetype": visual_archetype["id"],
            "headline_en": headline_en,
            "headline_hi": headline_hi,
            "support_line_en": support_en,
            "support_line_hi": support_hi,
            "cta_en": cta_en,
            "cta_hi": cta_hi,
            "disclaimer_en": "",
            "disclaimer_hi": "",
            "caption_en": "",
            "caption_hi": "",
            "bullets_en": bullets_en,
            "bullets_hi": bullets_hi,
            "background_slot": bg["id"],
            "background_name": bg.get("title", ""),
            "background_source": "catalog",
            "fresh_background_signature": None,
            "language": "BOTH",
            "output_quality": "pending",
            "notes": f"assembled_from={copy_path.name}; batch={batch_name}; aspect_ratio={aspect_ratio}; seed={seed}; visual_archetype={visual_archetype['id']}",
            # Copy diversity fields (now populated automatically)
            "opening_pattern_4tok_en": get_opening_pattern_4tok(headline_en),
            "opening_pattern_4tok_hi": get_opening_pattern_4tok(headline_hi),
            "copy_skeleton": get_copy_skeleton(fmt, headline_en, support_en, bullets_en, cta_en),
            "hook_structure_class": classify_hook_structure(headline_en),
            "proof_style_class": classify_proof_style(headline_en, support_en),
            "cta_voice_class": classify_cta_voice(cta_en),
            # New analytics fields
            "headline_word_count": len((headline_en or "").split()),
            "support_line_word_count": len((support_en or "").split()),
            "has_protocol_mechanics": has_protocol_mechanics(support_en) or has_protocol_mechanics(" ".join(bullets_en)),
            "has_social_proof_number": has_social_proof_number(headline_en) or has_social_proof_number(support_en),
            "background_scene_category": get_background_scene_category(bg),
            # Hypothesis testing fields
            "hypothesis_id": hyp_meta.get("hypothesis_id") or "",
            "test_group": hyp_meta.get("test_group") or "",
            "variant_variable": hyp_meta.get("type") or "",
            "variant_value": hyp_meta.get("variant") or "",
        }

        registry.setdefault("entries", []).append(entry)
        append_background_index(registry, fmt, entry_id, timestamp, bg["id"])
        append_concept_combo_index(registry, entry_id, timestamp, fmt, int(persona["number"]), concept)

        # used_text updates
        if "EN" in render_langs:
            add_used_text(registry, "headline_en", [ad["copy"]["EN"]["headline"]])
            add_used_text(registry, "cta_en", [ad["copy"]["EN"]["cta"]])
        if "HI" in render_langs:
            add_used_text(registry, "headline_hi", [ad["copy"]["HI"]["headline"]])
            add_used_text(registry, "cta_hi", [ad["copy"]["HI"]["cta"]])

        if fmt in {"HERO", "UGC"}:
            if "EN" in render_langs:
                add_used_text(registry, "support_line_en", [ad["copy"]["EN"]["support_line"]])
            if "HI" in render_langs:
                add_used_text(registry, "support_line_hi", [ad["copy"]["HI"]["support_line"]])
        elif fmt in {"BA", "FEAT"}:
            if "EN" in render_langs:
                add_used_text(registry, "bullets_en", ad["copy"]["EN"]["bullets"])
            if "HI" in render_langs:
                add_used_text(registry, "bullets_hi", ad["copy"]["HI"]["bullets"])
        else:  # TEST trust_line stored in support_line_* buckets for dedupe parity
            if "EN" in render_langs:
                add_used_text(registry, "support_line_en", [ad["copy"]["EN"]["trust_line"]])
            if "HI" in render_langs:
                add_used_text(registry, "support_line_hi", [ad["copy"]["HI"]["trust_line"]])

        if isinstance(registry.get("mode"), dict):
            registry["mode"]["last_updated"] = timestamp

    if not args.no_registry_write:
        write_json(REGISTRY_PATH, registry)

    print(f"Batch: {batch_name}")
    print(f"Seed: {seed}")
    print(f"Wrote: {batch_dir.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
