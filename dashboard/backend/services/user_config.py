from __future__ import annotations

import time
from typing import Any, Optional

from dashboard.backend.db.client import get_sync_db
from dashboard.backend.db.collections import COLL_USER_CONFIGS


CONFIG_KEYS = [
    "product_master_doc",
    "starting_prompt",
    "copy_prompt_templates",
    "persona_seeds",
    "copy_architecture",
    "background_variant",
]


def _get_doc(user_id: str) -> Optional[dict[str, Any]]:
    return get_sync_db()[COLL_USER_CONFIGS].find_one({"user_id": user_id})


def get_user_config(user_id: str) -> dict[str, Any]:
    doc = _get_doc(user_id)
    if doc is None:
        return get_generic_config()
    return {k: doc.get(k, "") for k in CONFIG_KEYS}


def set_user_config(user_id: str, config: dict[str, Any]) -> dict[str, Any]:
    update = {}
    for k in CONFIG_KEYS:
        if k in config:
            update[k] = config[k]
    if not update:
        return get_user_config(user_id)
    update["updated_at"] = time.time()
    get_sync_db()[COLL_USER_CONFIGS].update_one(
        {"user_id": user_id},
        {"$set": update},
        upsert=True,
    )
    return get_user_config(user_id)


def delete_user_config(user_id: str) -> None:
    get_sync_db()[COLL_USER_CONFIGS].delete_one({"user_id": user_id})


def has_custom_config(user_id: str) -> bool:
    return _get_doc(user_id) is not None


def push_vinaysaini_config() -> None:
    from dashboard.backend.auth.service import find_user_by_email
    user = find_user_by_email("vinaysaini@arogyamhealth.in")
    if user is None:
        return
    vinay_id = user["user_id"]
    if has_custom_config(vinay_id):
        return

    product_master = """Single Source of Truth: Product Foundation Document
PRODUCT TRUTH LIBRARY

1. allowed_food_guidance
Category: Structure
Product Truth: The course includes clear allowed and not-allowed food guidance, so customers know what to eat.
Use When: Confused beginners, failed dieters, support seekers.
Avoid Saying: Eat freely without rules.

2. arogyam_hospital_backing
Category: Authority
Product Truth: Obesity Killer Kit is linked to Arogyam Hospital & Research Center in Jodhpur.
Use When: Trust-first buyer, authority angle, skeptical buyer, tried everything else.
Avoid Saying: Hospital cure or disease treatment.

3. caloric_deficit_support
Category: Mechanism
Product Truth: The course helps make calorie deficit easier by reducing hunger and improving consistency.
Use When: Educated buyer, feature-led ads, skeptical users.
Avoid Saying: Burns fat without eating less.

4. clear_15_day_structure
Category: Structure
Product Truth: Obesity Killer Kit gives a clear 15-day routine. The kit includes 15 pre-packed pouches, one for each day, along with the correct quantity of tablet and powder. The course rules are simple and reduce guesswork.
Use When: Beginners, failed dieters, busy users, event deadline, PCOD, thyroid.
Avoid Saying: Results without following the course.

5. coach_chat_call_support
Category: Support
Product Truth: Expert coaches guide customers through chat and calls, helping with course-related doubts, tracker sharing, and consistency.
Use When: Accountability seekers, beginners, PCOD, support-dependent users.
Avoid Saying: Medical treatment, therapy, doctor consultation for every issue.

6. customer_scale_70k
Category: Social Proof
Product Truth: Obesity Killer Kit is trusted by 70,000+ customers in India.
Use When: Skeptical buyer, social proof angle, broad trust.
Avoid Saying: Everyone gets the same result.

7. daily_tracker_data
Category: Support
Product Truth: Course comes with a tracker that records weight, meals, water, sleep, and steps, helping customers see progress, notice patterns, and share data with coaches for feedback.
Use When: Office snackers, stress snackers, plateau users, accountability seekers.
Avoid Saying: Tracker itself causes weight loss.

8. daywise_pouches
Category: Structure
Product Truth: Each pouch is marked day-wise, so the customer follows a fixed course instead of figuring it out alone.
Use When: Confused beginners, support-dependent users, structured-course ads.
Avoid Saying: Magic routine or effortless transformation.

9. developed_over_decades
Category: Authority
Product Truth: OK Kit was developed through decades of obesity-focused Ayurvedic practice, patient learning, and refinement. This made the course effective, safe, and practical to follow.
Use When: Trust-first buyer, authority angle, skeptical buyer, natural seekers, PCOD, thyroid, tried everything else.
Avoid Saying: Ayurveda guarantees cure.

10. doctor_34_years
Category: Authority
Product Truth: Obesity Killer Kit was created by Dr. Arun Tyagi, an Ayurvedic doctor with 34 years of practice.
Use When: Trust-first buyer, authority ads, PCOD, thyroid, natural seekers, tried everything else.
Avoid Saying: Doctor guarantees cure.

11. eat_only_when_hungry_rule
Category: Behaviour
Product Truth: The course encourages customers to eat only when genuinely hungry, helping them notice unnecessary eating.
Use When: Office snacker, night snacker, stress snacker, homemaker.
Avoid Saying: Cures emotional eating.

12. eligibility_exclusions
Category: Guardrail
Product Truth: Not for pregnant women, women within 6 months postpartum, people actively taking insulin, and doctor-review cases like multiple psychiatric medicines or multiple surgeries.
Use When: Safety check only.
Avoid Saying: Suitable for everyone.

13. five_minute_routine
Category: Ease
Product Truth: The daily product routine is simple: OK Liquid in the morning and OK Tablet plus OK Powder at night.
Use When: Busy professionals, parents, homemakers, gym-averse users.
Avoid Saying: Zero effort or no discipline needed.

14. guided_system_not_product
Category: Positioning
Product Truth: Obesity Killer Kit is a guided course, not just a loose supplement or random pill.
Use When: Tried everything else, comparison angle, failed product users.
Avoid Saying: Other brands are fake or useless.

15. harder_weight_loss_context
Category: Relevance
Product Truth: Obesity Killer Kit is relevant for people whose weight-loss journey feels harder due to PCOD, thyroid, menopause, genetics, medication, or chronic conditions.
Use When: PCOD, thyroid, menopause, 35+ slow progress.
Avoid Saying: Treats conditions, fixes hormones, replaces medicine.

16. hero_3_5kg_15days
Category: Result
Product Truth: Obesity Killer Kit is designed to help users lose 3-5 kg in 15 days when followed properly.
Use When: Result-led, offer, event deadline, skeptical buyer, hero ads.
Avoid Saying: Guaranteed result for everyone.

17. homemade_food_guidance
Category: Food Guidance
Product Truth: The course works with homemade vegetables and salads within course food guidance. No complex meal prep is required. Users can choose from allowed foods based on their preferences.
Use When: Busy users, homemakers, parents, beginners.
Avoid Saying: Normal chapati/rice freely, eat anything and lose weight.

18. hunger_craving_control
Category: Mechanism
Product Truth: OK Liquid helps reduce hunger and cravings, making it easier to eat less and stay in a calorie deficit without constantly fighting food urges. This means the customer is not relying only on willpower to follow the plan.
Use When: Hungry all the time, stress snacking, late-night eating, office snacking, PCOD, failed diets.
Avoid Saying: Cures binge eating, stress, PCOD, thyroid, or hormones.

19. Brand design system
Typography:
- Headline: Poppins Bold
- Body: Poppins Regular

Palette:
- #F5821F
- #000000
- #560E06
- #008C44
- #8DA738
- #FFCC29
- #FEFAE0"""

    starting_prompt = """OBESITY KILLER KIT - GLOBAL PRODUCT RULES

A0. OUTPUT COUNT - ABSOLUTE RULE
- Generate exactly one final image for each prompt. Do not create two options, variations, comparisons, grids, or ask which one is better.
- Do not show alternatives. Do not present a choice. Output one finished ad image only.

A1. PRODUCT LOCK - ABSOLUTE RULE
- Use the provided Obesity Killer product packshot images pixel-for-pixel as visual truth.
- Do not redraw, recreate, regenerate, relabel, recolor, blur, "fix", or reinterpret any product or packaging.
- Do not change Hindi or English label text, logos printed on packaging, illustrations, colors, proportions, or label layouts.
- If any label text is unclear, preserve the uploaded image exactly as-is. Do not guess or rewrite it.
- Only permitted product-image operations: placement, scaling, subtle drop shadows, and mild warm lighting correction.

A2. EXACT LABEL TEXT
- Every character on every product must be razor-sharp and faithful to the uploaded references.
- Kit Box top strip: "Panacea for weight loss and obesity related conditions"
- Kit Box logo block: "Dr. ARUN TYAGI'S" in red + "OBESITY KILLER KIT" in black
- Kit Box badge: "ISO 9001:2008 Certified"
- Dried Amla bottle: "Dried Amla"; keep the amla fruit illustration sharp and unmodified.
- OK Tablets bottle: "OK TABLETS"
- OKP bottle: "OKP"; keep the light pattern on label exactly as provided.
- OK Liquid sachet: "OK LIQUID"; keep the lotus mark above text sharp and unmodified.
- Never blur, approximate, paraphrase, rewrite, change casing, change spacing, or grammatically alter packaging text.

A3. PRODUCT DIMENSIONS AND COUNT
- OK Kit Box: cuboid, 20.3 x 10.1 x 14.8 cm.
- OK Powder Bottle: cylinder, diameter 5 cm, height 10 cm.
- OK Tablet Bottle: cylinder, diameter 3 cm, height 5 cm.
- Dried Amla Bottle: cylinder, diameter 3 cm, height 5 cm.
- OK Liquid Sachet: vertical rectangle, width 10 cm, height 15.3 cm.
- Every output must contain exactly these five distinct products: Kit Box, Dried Amla bottle, OK Tablets bottle, OKP bottle, and OK Liquid sachet.
- Size products in correct relative proportion. Kit Box is the largest anchor/backdrop; bottles are notably smaller; sachet is more taller than wider.
- Never duplicate OKP. Never omit Dried Amla. If two OKP bottles appear, Dried Amla is missing, or only four distinct products appear, reject and regenerate.

A4. COLOR AND LIGHTING
- Primary palette: #FFFBED, #FEEFD6, #FCDBAC, #F79040, #E66410, #973015, #421808.
- Avoid neon, random accent colors, harsh gradients, orange edge glows, or decorative vignette frames.
- Use warm, soft, directional light from top-left.
- Keep clean highlights on product labels and caps.
- Use subtle realistic shadows only; avoid harsh shadows, flat lighting, studio flash, or ring-light glow.

A5. TYPOGRAPHY AND COPY
- Use Poppins only for on-image ad copy.
- Use maximum two weights: Medium/Regular and Bold. Avoid thin/light weights.
- Do not use decorative fonts, serif fonts, outlines, glows, or drop shadows on ad copy text.

A6. MASTER FORBIDDEN LIST
- Do not create AI-stylized versions of the products.
- Do not use sale badges, burst graphics, stickers, emoji, or visual clutter.
- Do not show body transformations or weight-loss visuals.
- Do not use laptop, keyboard, mouse, monitor, or workstation-heavy props unless explicitly requested.
- Do not add disclaimer copy on-image unless explicitly requested.
- Do not add any standalone brand logo to the ad. Only logos already printed on the product packaging should appear.

A7. QUALITY CHECK
- All 5 products are present, correctly proportioned, fully visible, and completely unmodified.
- Product labels are sharp, accurate, and faithful to the uploaded images.
- Layout is calm, balanced, premium, and not overcrowded.
- No forbidden elements are present.
- If product count, product identity, label accuracy, or requested background fails, silently regenerate until the output passes. Only the final image output is acceptable.

Important !! Dont fuck  PRODUCT DIMENSIONS AND COUNT , the box should not neither too big in height nor too small , in the box , dont put any extra space below the apple.
Important take a note of these :
        For the box:
            The main product box must be landscape-oriented rectangle, significantly wider than it is tall (roughly 3:1 width-to-height ratio). It should appear as a long flat box standing upright on its back edge, not a cube or square box.

        For the sachet:
            The OK Liquid sachet on the left must be a tall, narrow foil pouch — portrait orientation with height clearly greater than width (approx 2:1 height-to-width ratio). It should stand upright like a flat rectangular packet, not spread wide.

        For overall proportions:
            Maintain consistent scale between all products as if photographed together on a flat surface. The OKP bottle on the right is the tallest item. The box behind is wide but lower than the bottle height. The sachet is about 80% the height of the OKP bottle."""

    import json
    copy_templates_path = __import__("pathlib").Path(__file__).resolve().parent.parent / "copy_prompt_templates.json"
    copy_templates = copy_templates_path.read_text(encoding="utf-8") if copy_templates_path.exists() else "{}"

    persona_seeds_path = __import__("pathlib").Path(__file__).resolve().parent.parent.parent.parent / "persona_seeds.json"
    persona_seeds = persona_seeds_path.read_text(encoding="utf-8") if persona_seeds_path.exists() else "[]"

    set_user_config(vinay_id, {
        "product_master_doc": product_master,
        "starting_prompt": starting_prompt,
        "copy_prompt_templates": copy_templates,
        "persona_seeds": persona_seeds,
    })


def get_generic_config() -> dict[str, Any]:
    """Read actual filesystem config files as global defaults."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent.parent

    product_master_path = root / "input" / "docs" / "product master doc.txt"
    starting_prompt_path = root / "input" / "startingprompt.txt"
    copy_templates_path = root / "dashboard" / "backend" / "copy_prompt_templates.json"
    persona_seeds_path = root / "persona_seeds.json"
    copy_arch_path = root / "dashboard" / "backend" / "copy_architecture.json"
    background_variant_path = root / "background_variant.json"

    def _read(p: Path) -> str:
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            return ""

    return {
        "product_master_doc": _read(product_master_path),
        "starting_prompt": _read(starting_prompt_path),
        "copy_prompt_templates": _read(copy_templates_path) or "{}",
        "persona_seeds": _read(persona_seeds_path) or "[]",
        "copy_architecture": _read(copy_arch_path) or "{}",
        "background_variant": _read(background_variant_path) or "{}",
    }
