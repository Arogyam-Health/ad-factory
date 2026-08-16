from __future__ import annotations

import json
from typing import Any


def normalize_aspect_ratio(value: str) -> str:
    raw = str(value or "").strip().lower().replace("_", ":").replace("x", ":")
    if raw in {"9:16", "9/16", "916", "96"}:
        return "9:16"
    return "4:5"


def parse_assembler_templates(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def build_output_revision_prompt(
    *,
    comment: str,
    aspect_ratio: str,
    original_prompt: str = "",
    assembler_templates: Any = None,
    conversion_916_prompt: str = "",
) -> str:
    """Build a revision prompt using the existing user-editable 4:5 / 9:16 rules.

    4:5 uses `safezone_45` from prompt assembler templates plus the original
    generation prompt. 9:16 uses `conversion_916_prompt` and `safezone_916`
    instead of the 4:5 generation prompt, so commented 9:16 images are not
    revised against 4:5 canvas rules.
    """
    comment_text = str(comment or "").strip()
    aspect = normalize_aspect_ratio(aspect_ratio)
    templates = parse_assembler_templates(assembler_templates)
    original = str(original_prompt or "").strip()
    conversion = str(conversion_916_prompt or "").strip()
    parts = [
        "Edit the current ad image. Apply the requested revision exactly while preserving "
        "everything not requested.",
        "",
        "REVISION REQUEST:",
        comment_text,
    ]
    if aspect == "9:16":
        parts.extend(
            [
                "",
                "The attached image is already 9:16. Keep this 9:16 canvas. Do not treat it "
                "as a 4:5 generation or convert it from 4:5.",
            ]
        )
        if conversion:
            parts.extend(["", "9:16 DIMENSION AND SAFE-ZONE RULES:", conversion])
        safezone = str(templates.get("safezone_916") or "").strip()
        if safezone:
            parts.extend(["", safezone])
    else:
        if original:
            parts.extend(["", "ORIGINAL GENERATION INSTRUCTIONS:", original])
        safezone = str(templates.get("safezone_45") or "").strip()
        if safezone:
            parts.extend(["", safezone])
    parts.extend(["", "Return only the revised image."])
    return "\n".join(parts).strip() + "\n"
