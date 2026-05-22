# Ad Creative System — Complete Handover Guide

## What This Project Is

This is an **Ad Creative Generation System** for **Obesity Killer Kit** (an Ayurvedic weight-loss product). It generates hundreds of ad creatives across 5 formats (HERO, BA, TEST, FEAT, UGC) in 3 languages (EN, HI, HINGLISH).

**Critical distinction:** This system does NOT generate images directly. It generates **structured text prompts** that are sent to image-generation tools (Gemini Web / ChatGPT). Each prompt is an "ad creative spec" — a 9-section document describing exactly what the image should look like, what text to render, what products to show, and what rules to follow.

---

## Directory Map

```
info/
├── AD_CREATIVE_SYSTEM_PLAYBOOK.md       # The Bible — all rules live here (1455 lines, 20 sections)
├── AD_GENERATION_REGISTRY.JSON          # Append-only log of everything ever generated
├── background_variant.json              # 500 background slot definitions (BG-001 to BG-500)
├── persona_seeds.json                   # 27 buyer personas (numbered 1-27)
├── AGENTS.md                            # Graphify knowledge-graph instructions (ignore for dev)
├── input/
│   ├── docs/product master doc.txt      # Single source of truth — approved product claims (685 lines)
│   ├── images/                          # Product packshot reference images (PNG/JPG/WebP)
│   ├── startingprompt.txt               # Prepended to every prompt sent to image-gen API
│   └── prompt_916_from_45.txt           # Additional instructions for 9:16 conversion
├── scripts/
│   ├── generate_ads.py                  # MAIN assembler (1623 lines)
│   ├── assemble_from_xlsx.py            # Alternate entry from xlsx export (821 lines)
│   ├── extract_format_rules.py          # Utility: pull format rules from playbook
│   ├── registry_banlist.py              # Export "do not repeat" list from registry
│   ├── gemini_web_automation.py         # Playwright-based Gemini Web image gen
│   ├── chatgpt_web_sutomation.py        # Playwright-based ChatGPT image gen
│   ├── bootstrap_stack.{sh,ps1}         # Server setup scripts
│   ├── start_dashboard_stack.{sh,ps1}   # Dashboard launch
│   └── stop_dashboard_stack.{sh,ps1}    # Dashboard stop
├── output/
│   └── v1..v{latest}/
│       ├── 45/                          # 4:5 ratio prompts
│       │   └── OUTPUT_{FORMAT}_P{NN}_{EN|HI}.txt  (+ sidecar .json per file)
│       └── 96/                          # 9:16 ratio prompts
│           └── OUTPUT_{FORMAT}_P{NN}_{EN|HI}.txt
├── runtime/
│   ├── context_canonical.json           # Extracted product context (placeholder/broken currently)
│   ├── product_context_cache.json       # Cache of same
│   ├── generation_logs/                 # Text log files from runs
│   ├── opencode_queue/                  # Queue for batch operations
│   ├── chatgpt_selected_prompts/        # Prompts copied for ChatGPT consumption
│   └── conversion_916_prompts/          # Prompts for 9:16 conversion runs
├── dashboard_storage/
│   └── runs/                            # Dashboard run manifests + copy batch JSONs
├── generated_images/                    # Actual generated images (not prompts)
│   └── v{N}/
│       └── GEMINI_4_5/ or GEMINI_9_16/
├── dashboard/                           # Flask web UI
│   ├── backend/                         # Flask routes + API (includes prompt builder)
│   ├── frontend/                        # HTML/CSS/JS
│   └── test_outputs/                    # Sample copy JSON for acceptance checks
├── graphify-out/                        # Knowledge graph output (not code)
└── docs/
    ├── AB_TESTING_PLAYBOOK.md           # A/B testing framework doc
    ├── end_to_end_flow.excalidraw.json  # Excalidraw diagram
    └── HANDOVER.md                      # This file
```

---

## The 7 Key Data Files — What's Inside Each

### 1. `persona_seeds.json`
27 objects, one per persona. Each contains:
- `persona_number` — unique ID (1-27)
- `persona_name` — e.g., "Post-Failure Re-starter", "Emotional Eater / Stress Snacker"
- `pain` — what hurts the person right now
- `desire` — dream outcome they want
- `friction` — why past attempts failed
- `proof` — what would make them believe
- `tone` — emotional tone for the copy (e.g., "hopeful and non-judgmental")
- `awareness_stage` — default awareness: unaware / problem_aware / solution_aware / product_aware

All fields are in English only. During prompt assembly, the Hindi/Hinglish versions are expected to be provided in the copy JSON (the `generate_ads.py` script does NOT translate).

### 2. `dashboard/backend/copy_architecture.json`
Creative direction metadata that controls LLM copy output. **No sentence templates or final-copy examples** — converted to intent-level guidance in May 2026 refactor.

Each entry under `headline_architectures` now has:
- `intent` — what the concept should feel like (not a sentence template)
- `headline_role` — what the headline should do in human terms
- `support_role` — what the support line should do
- `route_bias` — which creative routes fit this concept
- `avoid_skeletons` — literal sentence shapes the LLM must NOT produce

Groups: `concept_structure` (pas/bab/fab/four_us/pab), `hook_structure` (question/proof/contrast/confession/command), `concept_angle` (pain_point/desired_outcome/authority etc.), `awareness_stage` (unaware/problem_aware/solution_aware/product_aware).

Also includes `support_line_architectures` (rotation-order of support-line intents) and `non_headline_hypotheses` (proof_style and cta_voice preferences).

### 3. `dashboard/backend/copy_prompt_templates.json`
All prompt text templates the LLM sees. Controls:
- `system_prompt_base_rules` — creative-first rules (refactored to remove template-binding)
- `system_prompt_format_rules` — per-format rules (HERO/UGC now avoid headline architecture templates)
- `creative_routes` — route options for silent exploration (default + by_persona_theme)
- `prompt_tail` — final constraints (cleaned and shortened in refactor)
- `strict_schema_note` — persona field requirements and JSON schema
- `copy_requirements` — must_mention, hierarchy_rule, format_specific rules
- `cta_variants` — CTA text options per format per language
- `template_copy_*` — fallback template strings for when LLM fails

### 4. `background_variant.json`
500 background variants (BG-001 through BG-500). Each has:
- `id` — e.g., "BG-001"
- `title` — e.g., "Warm studio cream"
- `base` — base scene description
- `lighting[]` — array of lighting options
- `surface[]` — array of surface options
- `environment[]` — array of environment options
- `mood[]` — array of mood options
- `camera[]` — array of camera framing options
- `color_tone[]` — array of color tone options
- `composition[]` — safe-zone composition instructions
- `layout_intent[]` — layout placement rules
- `cta_safe_space[]` — CTA zone protection rules
- `crop_safety[]` — crop resilience rules
- `formats[]` — which formats this background is eligible for (e.g., ["HERO", "FEAT"])

When a prompt is assembled, ONE option is chosen from each array deterministically using a seeded random number generator (same seed + same background = same sentence every time).

### 3. `AD_GENERATION_REGISTRY.JSON`
This is the **memory** of everything ever generated. Append-only — never delete entries.

Top-level structure:
```json
{
  "mode": {
    "phase": "production",
    "write_enabled": true,
    "last_updated": "2026-..."
  },
  "entries": [
    {
      "id": "entry_001",
      "timestamp": "2026-...",
      "format": "HERO",
      "persona_number": 7,
      "persona_name": "Post-Failure Re-starter",
      "headline_angle": "desired_outcome",
      "awareness_stage": "solution_aware",
      "concept_angle": "desired_outcome",
      "concept_structure": "four_us",
      "visual_archetype": "hero_center_stage",
      "headline_en": "...",
      "headline_hi": "...",
      "support_line_en": "...",
      "support_line_hi": "...",
      "cta_en": "...",
      "cta_hi": "...",
      "bullets_en": [...],
      "bullets_hi": [...],
      "background_slot": "BG-038",
      "background_name": "Sun patch table",
      "opening_pattern_4tok_en": "tired_of_failing_at",
      "opening_pattern_4tok_hi": "...",
      "copy_skeleton": "question_mechanism_cta",
      "hook_structure_class": "proof_lead",
      "proof_style_class": "authority_anchor",
      "cta_voice_class": "guided_next_step",
      "headline_word_count": 6,
      "support_line_word_count": 30,
      "has_protocol_mechanics": false,
      "has_social_proof_number": true,
      "background_scene_category": "lifestyle",
      "hypothesis_id": "hook_structure-question_lead",
      "test_group": "",
      "variant_variable": "hook_structure",
      "variant_value": "question_lead",
      "seed": 7071
    }
  ],
  "indexes": {
    "used_text": { "headline_en": [...], "headline_hi": [...], "cta_en": [...], ... },
    "slot_exhaustion_tracker": { "HERO": { "used": [...], "remaining": [...], "cycle_number": 1 } },
    "backgrounds_by_format": { "HERO": [{ "entry_id": "...", "timestamp": "...", "background_slot": "BG-001" }] },
    "concept_combos": { "recent": [{ "entry_id": "...", "format": "...", "awareness_stage": "...", ... }] },
    "copy_patterns": { ... }
  }
}
```

### 6. `AD_CREATIVE_SYSTEM_PLAYBOOK.md`
20 sections covering everything. The crucial ones:
- **§6A** — Awareness stages (4 levels, how to infer them)
- **§7** — Headline engine (8 concept angles, 4 concept structures, 4U writing lens, headline execution rules, support line rules, human editor pass)
- **§9** — Background variation engine (exhaustive rotation, deterministic seeding)
- **§10** — Registry system (dedup rules, diversity matrix, concept-combo matrix, 5 diversity tags)
- **§11** — Format specifications (per-format purpose, copy shape, text budgets, variation lanes)
- **§12** — Prompt assembly template (9 mandatory sections, per-section depth minimums)
- **§15** — Interactive ad request flow (step-by-step generation sequence, validation checklist with CHK-01 through CHK-29)

### 7. `input/docs/product master doc.txt`
The absolute source of truth. Contains:
- What the product is and what problem it solves
- Who it's for and who it's NOT for
- Kit contents (OK Liquid, OK Tablet, OK Powder, Dried Amla, Daily Tracker, Guide)
- Formulation details (31 unique ingredients, 44 total entries)
- Approved claims: 3-5 kg in 15 days, Ayurvedic, cravings control, 70K+ users, money-back
- Pricing: ₹5,800/15d, ₹10,000/30d, ₹17,000/60d
- Protocol details, allowed/restricted foods, support structure
- NEGATIVE claims: No "fat burner", no "boosts metabolism", no "burns fat fast", etc.

---

## The 5 Formats — What Each Requires

### HERO
- **Copy shape:** headline + 1 support line + CTA
- **Purpose:** broad conversion, strongest pain-led hook
- **Text budget:** 18-36 words
- **Visual archetypes:** hero_center_stage, hero_left_copy_right_product, hero_close_crop_pedestal, hero_soft_lifestyle_frame
- **Default concept structure:** pab

### BA (Before/After)
- **Copy shape:** headline + 2-3 bullets + CTA
- **Purpose:** show transition from pain to control
- **Text budget:** 22-36 words
- **Visual archetypes:** ba_classic_split, ba_soft_diagonal_transition, ba_desk_to_discipline, ba_emotion_to_control
- **Default concept structure:** bab
- **Special:** `split_ba_contrast_lines()` splits bullets into "left situation" and "right shift" panels

### TEST
- **Copy shape:** headline/quote + attribution + trust_line + CTA
- **Purpose:** trust and social proof
- **Text budget:** 24-40 words
- **Visual archetypes:** test_editorial_quote_card, test_portrait_overlay_card, test_minimal_review_poster, test_proof_strip_layout
- **Default concept structure:** pas
- **Special:** Never fabricate quotes; fallback to rating + user-count framing

### FEAT
- **Copy shape:** headline + 3-4 bullets + CTA
- **Purpose:** mechanism clarity
- **Text budget:** 26-42 words
- **Visual archetypes:** feat_bullet_panel, feat_modular_cards, feat_mechanism_steps, feat_callout_annotations
- **Default concept structure:** fab

### UGC
- **Copy shape:** headline + 1 support line + (optional context_line) + CTA
- **Purpose:** authenticity, creator-style
- **Text budget:** 16-26 words
- **Visual archetypes:** ugc_selfie_hold, ugc_desk_review, ugc_morning_routine, ugc_unboxing_reaction
- **Default concept structure:** pas
- **Special:** `build_ugc_subject_line()` auto-generates subject description with age/tone/attire/hair

---

## Complete Copy-Generation Pipeline

Here is every step, every file touched, every rule enforced — from nothing to a written prompt.

### Phase 0: LLM Copy Generation (Prompt Builder)

**`generate_ads.py` does NOT write copy.** It assembles prompts from pre-generated copy. The copy is generated upstream by an LLM, driven by `dashboard/backend/app.py` and two JSON control files.

**Key files controlling LLM copy output:**
- `dashboard/backend/copy_prompt_templates.json` — system prompt rules, format rules, prompt tail, creative routes
- `dashboard/backend/copy_architecture.json` — intent-level creative direction metadata (not sentence templates)
- `persona_seeds.json` — 27 buyer personas with pain/desire/friction/proof/tone/awareness_stage

**How the prompt is built (`app.py` functions):**

1. `build_copy_requirements()` at line 521 constructs a `copy_requirements` dict for each ad. It now sends **compact creative direction** instead of binding sentence templates:

```json
{
  "creative_direction": {
    "concept_structure": {
      "id": "pas",
      "intent": "Start from a problem the persona already feels...",
      "headline_role": "Name the tension in a human, specific way...",
      "support_role": "Show the consequence or difficulty...",
      "avoid_skeletons": ["{problem} keeps blocking weight loss", "weight loss stalls when {problem}", "diets fail because {problem}"]
    },
    "concept_angle": {"id": "pain_point", "intent": "...", "route_bias": ["pain_moment", "specific_lived_moment"]},
    "awareness_stage": {"id": "problem_aware", "intent": "..."}
  },
  "creative_routes_to_explore": [
    "one_bite_loss_of_control",
    "evening_cravings",
    "food_noise",
    "willpower_fatigue",
    "failed_appetite_suppression",
    "easier_eating_less_routine"
  ],
  "hard_rules": [
    "headline must be clear and complete",
    "headline must connect to weight loss or appetite/craving control",
    "support line adds new mechanism/proof/ease",
    "do not use price",
    "do not use banned claims"
  ]
}
```

2. The system prompt (`build_ad_copy_system_prompt()` at line 582) now uses **creative-first rules**:
   - "Write like a human ad editor, not like a framework"
   - "Before choosing the final copy, silently explore multiple different creative routes"
   - No instructions to "follow headline_architecture template" — those were removed

3. The prompt tail (`build_ad_prompt_tail()` at line 604) now says:
   - "Silently generate at least 6 different creative routes before selecting the final copy"
   - "Prefer a specific lived moment, objection, contrast, or persona tension over generic product claims"
   - Replaced repetitive/hostile wording with clean constraints

4. Hypothesis metadata is sent in compact form:
```json
"hypothesis": {
  "type": "concept_structure",
  "variant": "pas",
  "hypothesis_id": "concept_structure-pas",
  "intent": "Use problem-aware flow: headline names tension; support adds consequence and product-led relief.",
  "do_not_force_template": true
}
```

5. `build_generation_payload_for_llm()` at line 613 now injects `product_truth` — a compact summary of allowed proof/mechanism examples and hard bans (no fat burner, no cure claims, etc.)

**Creative route exploration:**
- `dashboard/backend/app.py` `_persona_theme()` at line 540 detects persona theme (cravings/digestion/event_deadline/busy_life) from seed text
- `_creative_routes_for_persona()` at line 562 merges default routes + persona-theme routes + route_bias from the selected concept
- These routes are sent as silent exploration instructions — the LLM outputs only the final JSON

**Two-layer contract:**
1. **Creative layer** — give persona, product truth, format rules, creative direction metadata, and route options. LLM silently explores multiple routes before choosing the final ad.
2. **Packaging layer** — strict JSON output matching the schema expected by the downstream assembler.

The copy comes in as a JSON file with this structure:

```json
{
  "ads": [
    {
      "format": "HERO",
      "aspect_ratio": "4:5",
      "persona": {
        "number": 7,
        "name": "Post-Failure Re-starter",
        "pain_en": "...",
        "desire_en": "...",
        "friction_en": "...",
        "proof_needed_en": "...",
        "tone_cue_en": "...",
        "pain_hi": "...",
        "desire_hi": "...",
        "friction_hi": "...",
        "proof_needed_hi": "...",
        "tone_cue_hi": "..."
      },
      "headline_angle": "pain",
      "awareness_stage": "problem_aware",
      "concept_angle": "pain_point",
      "concept_structure": "four_us",
      "copy": {
        "EN": {
          "headline": "Stress snacking ruining weight loss?",
          "support_line": "...",
          "cta": "Check If You're Eligible"
        },
        "HI": {
          "headline": "...",
          "support_line": "...",
          "cta": "..."
        }
      },
      "hypothesis": {
        "hypothesis_id": "hook_structure-question_lead",
        "test_group": "A",
        "type": "hook_structure",
        "variant": "question_lead"
      }
    }
  ]
}
```

**Key copy-writing rules enforced in the prompt (not in the assembler):**
- Headline must clearly signal weight loss, slimming, eating less, craving control, or visible progress
- No price in on-image copy, no currency symbols or price words
- No cure claims, fat-burner claims, metabolism-boosting claims, or medical-treatment claims
- No product component names in headline
- No protocol mechanics in headline
- Support line must add mechanism, proof, ease, or consequence (not repeat the headline)
- Do not start support line with "Yes:", "No:", "Because", "It can", or "You can"

**Semantic preflight rejection (`semantic_copy_rejection()` at line 1322 in app.py):**
Before accepting LLM output, the prompt builder checks:
- Headline matches any `avoid_skeletons` from the creative direction metadata
- Headline contains generic skeletons like "keeps blocking weight loss", "weight loss stalls when", "diets fail because"
- Headline opening pattern repeats too often (checked against recent registry and current batch)
- Support line starts with generic openers like "This doctor-formulated Ayurvedic kit supports..."
- Support line is too generic for the persona (doesn't reference persona-specific terms)
- CTA repeats too frequently in the same format

If rejection triggers, the LLM is retried with a revision instruction. After one retry, the output is accepted with a warning.

### Phase 1: Loading into `generate_ads.py`

```
generate_ads.py --copy-file copy_batch.json [--batch vN] [--seed N] [--language-mode BOTH|EN|HI] [--skip-uniqueness-check] [--no-registry-write] [--dry-run]
```

The script loads:
- `copy_batch.json` → `payload` (the ads array)
- `AD_GENERATION_REGISTRY.JSON` → `registry` (for dedup checks)
- `background_variant.json` → `backgrounds` (for background selection)
- Computes/uses `seed` (from `--seed` or random)

### Phase 2: Copy Validation

For each ad in the array, `parse_copy_block()` validates:

**Headline bans (lines 656-659 of `generate_ads.py`):**
- Cannot contain product component names: `ok liquid`, `ok tablet`, `ok powder`, `okp`
- Cannot contain protocol mechanics: `am`, `pm`, `4-hour`, `no solid`, `empty stomach`
- If either is found → `RuntimeError`, script aborts

**Format-specific requirements (lines 1329-1338):**
- HERO: `support_line` is required
- UGC: `support_line` is required
- BA: `bullets` must have >= 2 items
- FEAT: `bullets` must have >= 2 items
- TEST: `attribution` is required
- TEST: `trust_line` is required

If any requirement is missing → `RuntimeError`, script aborts.

### Phase 3: Uniqueness Checks

For each headline, CTA, support line, trust line, and bullet in every language:

1. **Cross-run uniqueness** (lines 720-735, `uniqueness_check()`):
   - Check against `registry.indexes.used_text` in the SAME bucket (e.g., headline_en)
   - Check against ALL text across ALL buckets globally
   - Collision → add to `collisions[]` list

2. **Within-run uniqueness** (lines 1313-1323):
   - No two ads in the same batch can share the same headline or CTA
   - No text can be duplicated within the same batch

3. **Gate** (line 1356):
   - If `collisions` is non-empty AND `--skip-uniqueness-check` is NOT set → `RuntimeError`, script aborts
   - The copy must be regenerated upstream and re-submitted

### Phase 4: Concept Field Resolution

`resolve_concept_fields()` at line 605 establishes the 3 concept axes:

**awareness_stage:**
1. If `ad["awareness_stage"]` is provided and valid → use it
2. If not → `infer_awareness_stage(persona)` at line 594:
   - Keywords "doctor", "trust", "proof", "safe", "natural", "guarantee", "budget", "value" → `product_aware`
   - Keywords "past", "failed", "plateau", "rebound", "stubborn", "skeptic", "strict" → `solution_aware`
   - Keywords "craving", "hunger", "snack", "stress", "busy", "schedule", "time", "routine" → `problem_aware`
   - Else → `unaware`

**concept_angle:**
1. If `ad["concept_angle"]` is provided and valid → use it
2. If not → check `ad["headline_angle"]` and map: pain→pain_point, objection→comparison, mechanism→curiosity, time→offer, proof→social_proof, sacrifice_reduction→comparison
3. If still nothing → `desired_outcome`

**concept_structure:**
1. If `ad["concept_structure"]` is provided and valid → use it
2. If not → format default: HERO→pab, BA→bab, TEST→pas, FEAT→fab, UGC→pas

### Phase 5: Background Slot Selection

`pick_background_slot()` at line 431:

1. Filter `background_variant.json` variants to only those where `formats[]` contains the current format
2. Check `registry.indexes.slot_exhaustion_tracker.<FORMAT>`:
   - If `remaining[]` has items → pop the first one
   - If `remaining[]` is empty → increment `cycle_number`, shuffle all eligible slots into a new `remaining[]`, pop the first one
   - If no tracker exists → create one: shuffle all eligible slots, pop the first one
3. If background_slot_id is forced in ad config → use `get_background_by_id()` instead

### Phase 6: Background Seed Sentence

`build_seeded_background_sentence()` at line 475:

Uses `random.Random(seed)` to pick ONE option deterministically from each array:
- `base` (the main scene description)
- `lighting[]`
- `surface[]`
- `environment[]`
- `mood[]`
- `camera[]`
- `color_tone[]`
- `composition[]`
- `layout_intent[]`
- `cta_safe_space[]`
- `crop_safety[]`
- `text_overlay_treatment[]`
- `edge_tone_control[]`

Plus a `format_clause` based on aspect ratio (4:5 or 9:16 safe-zone instructions).

Result is a single paragraph sentence like:
```
"Approved light palette background: matte warm white and #FEFAE0 cream sweep... on a matte white or #FEFAE0 cream platform..., with subtle background blur..., lit by soft natural side daylight..., conveying minimal clean lifestyle confidence; eye-level medium framing..., balanced feed composition inside the central safe field..., designed for 4:5 feed framing..."
```

### Phase 7: Visual Archetype Selection

`pick_visual_archetype()` at line 819:

1. If `forced_archetype` is specified in the ad config → use it directly
2. If previously used archetypes exist for this format in this run → prefer unused ones
3. Otherwise → deterministic random based on `stable_signature_seed(fmt, persona_number, seed, headline, cta, support_line, context_line, trust_line, bullets)`

The stable seed is a SHA-256 hash to ensure determinism: same inputs always produce the same archetype.

### Phase 8: Prompt Rendering

`render_prompt()` at line 855 builds the 9-section prompt. Each section has a specific purpose:

**Section 1 — PRODUCT LOCK BLOCK (lines 978-988):**
"Use the uploaded Obesity Killer product packshot images as absolute visual truth. Do not redesign, redraw, relabel, or alter any product..."

**Section 2 — OUTPUT SPEC (lines 991-1001):**
Canvas size (1080x1350 for 4:5, 1080x1920 for 9:16), style intent, visual archetype ID, full-bleed requirement, text policy, 5-products rule.

**Section 3 — FORMAT LAYOUT INSTRUCTIONS (lines 1004-1005):**
Format-specific layout rules + visual archetype's layout_lines (composition map, focal hierarchy, product zone, text zones, camera framing, lighting, spacing).

**Section 4 — PERSONA INPUT BLOCK (lines 1007-1021):**
Persona name, number, pain, desire, friction, proof, tone, awareness stage, concept angle, concept structure.

**Section 5 — EXACT ON-IMAGE COPY (lines 1023-1025):**
The exact copy block formatted per-format:
- HERO: Headline, Support line, CTA
- BA: Headline, Left situation 1/2, Right shift 1/2, CTA
- FEAT: Headline, Bullet 1-n, CTA
- TEST: Headline, Attribution, Trust line, CTA
- UGC: Headline, Context line (optional), Support line, CTA

**Section 6 — NEGATIVE CONSTRAINTS (lines 1027-1048):**
~13 rules: no redraw, no blur, no sale badges, no body transformations, no colors outside palette, no more than 2 font weights, no overcrowding, etc. UGC adds hand-anatomy and no-holding rules. BA adds no-literal-BEFORE/AFTER rule.

**Section 7 — QUALITY BAR (lines 1050-1061):**
7 conditions: all 5 products present, text sharp at 375px, labels accurate, layout calm, no clutter, single focal hierarchy, regenerate if any fails.

**Section 8 — VISUAL DIRECTION BLOCK (lines 1063-1098):**
Background slot ID + seed + seeded sentence + subject + action + camera + lighting + props + surfaces + mood + realism + archetype direction lines. Includes safe-zone fields, edge-color control, and full-bleed enforcement.

**Section 9 — TYPOGRAPHY SHARPNESS BLOCK (lines 1099-1118):**
Poppins Bold for headlines, Poppins Medium/Regular for support/CTA, sizes, placement rules, font-weight limits, crisp edge requirement.

Then appended:
- `safezone_enforcement_block()` — the safe zone rules (14-65% for 9:16, central safe field for 4:5)
- `outpaint_lock_block()` — only for 9:16, prevents distortion during canvas extension
- Global footer lines about minimal text, CTA button, typography sharpness

### Phase 9: Prompt Validation

`validate_prompt_text()` at line 1128 checks:
1. `Background seed:` tag exists in the text
2. `Seeded background direction (single sentence, exact):` label exists
3. `SAFE-ZONE ENFORCEMENT` block exists
4. At least 45 non-empty lines

If any check fails → `RuntimeError`, no file written.

### Phase 10: Write to Disk

File path: `output/v{N}/{45|96}/OUTPUT_{FORMAT}_P{NN}_{EN|HI}.txt`

Also writes a sidecar `.json` file at the same path with full metadata:
- prompt_type, format, persona, language, aspect_ratio, creative_index
- hypothesis info
- background details (slot, name, seed, seeded_direction, scene_category)
- visual_archetype info
- background_decisions tracking

### Phase 11: Registry Update

Unless `--no-registry-write` is set:

1. Create entry dict with all 50+ fields
2. Append to `registry.entries[]`
3. `append_background_index()` — log slot usage per format
4. `append_concept_combo_index()` — log the awareness+angle+structure combo
5. `add_used_text()` — add all headline/cta/support/bullet strings to `indexes.used_text`
6. Update `registry.mode.last_updated`
7. Write entire registry back to `AD_GENERATION_REGISTRY.JSON`

### Phase 12: Auto-Classification of Diversity Tags

For every entry, 5 tags are automatically computed:

**hook_structure_class** (`classify_hook_structure()` at line 1149):
- `question_led` — starts with Why/What/How/When or has `?` in first 30 chars
- `proof_led` — starts with Finally/Trusted/Proven or contains "70,000"/"doctor"
- `contrast_loop` — contains before/after/without/instead/but/yet/still
- `confession_led` — starts with "I "/"my " or contains "felt"/"struggled"
- `command_led` — starts with Stop/Start/Try/See
- Default: `proof_led`

**proof_style_class** (`classify_proof_style()` at line 1168):
- `authority_anchor` — doctor/ayurvedic/dr/formulated in headline or support
- `social_proof` — 70,000/user/people/review/trusted
- `objection_flip` — but/skeptical/doubt/worried/tried
- `routine_clarity` — simple/clear/5-minute/easy
- `mechanism_explainer` — step/routine/morning/night/ok liquid
- Default: `mechanism_explainer`

**cta_voice_class** (`classify_cta_voice()` at line 1184):
- `urgent_start` — today/now/start/act
- `reassurance_start` — fit/risk/try/safe
- `challenge_action` — test/challenge/15-day
- `discovery_action` — learn/how/works/discover
- `guided_next_step` — see/view/check/steps/details
- Default: `guided_next_step`

**opening_pattern_4tok** (`get_opening_pattern_4tok()` at line 1200):
First 4 normalized alphabetic tokens from the headline, joined by underscore (e.g., "tired_of_failing_at")

**copy_skeleton** (`get_copy_skeleton()` at line 1206):
Derived from headline+support+bullets+cta patterns:
- `question_mechanism_cta` — question + mechanism keywords
- `contrast_mechanism_cta` — contrast words + mechanism keywords
- `proof_time_cta` — proof + time keywords
- `pain_mechanism_time` — mechanism + time keywords
- `pain_agitate_solve` — contrast words
- `proof_then_routine` — proof keywords
- `micro_story_then_action` — time keywords
- `problem_reframe_then_next_step` — default

---

## The Hypothesis System

Hypothesis data is **pass-through metadata** — `generate_ads.py` never acts on it, just stores it.

The fields come from the copy JSON at `ad["hypothesis"]`:
```json
{
  "hypothesis": {
    "hypothesis_id": "hook_structure-question_lead",
    "test_group": "A",
    "type": "hook_structure",
    "variant": "question_lead"
  }
}
```

These are written verbatim into:
1. The registry entry (`hypothesis_id`, `test_group`, `variant_variable`, `variant_value`)
2. The sidecar `.json` prompt metadata file

The purpose is downstream A/B testing: query the registry for all entries with `hypothesis_id="hook_structure-question_lead"` and compare performance metrics.

---

## The "Assembly" Scripts in Detail

### `generate_ads.py` (1623 lines)
The main workhorse. Takes pre-made copy JSON and assembles into 9-section prompts.
- **Entry point:** `main()` at line 1261
- **Parses args:** `--copy-file` (required), `--batch`, `--seed`, `--language-mode`, `--skip-uniqueness-check`, `--no-registry-write`, `--dry-run`
- **Flow:** Load → validate copy → check uniqueness → resolve concepts → pick backgrounds → pick archetypes → render prompts → validate → write files → update registry
- **Key dataclass:** `CopyBlock` at line 350 (headline, cta, support_line, context_line, trust_line, attribution, bullets)
- **Key constants:**
  - `SUPPORTED_FORMATS` = {HERO, BA, TEST, FEAT, UGC}
  - `SUPPORTED_LANGS` = {EN, HI, HINGLISH}
  - `SUPPORTED_AWARENESS_STAGES` = {unaware, problem_aware, solution_aware, product_aware}
  - `SUPPORTED_CONCEPT_ANGLES` = 8 values
  - `SUPPORTED_CONCEPT_STRUCTURES` = {pas, bab, fab, four_us}
  - `HEADLINE_ANGLE_TO_CONCEPT` = mapping from headline_angle to concept_angle
  - `FORMAT_DEFAULT_STRUCTURE` = per-format structure default

### `assemble_from_xlsx.py` (821 lines)
Alternate entry point. Reads an xlsx export from the Dashboard's "extract on-image copy" feature.
- **Flow:** Parse xlsx rows → group by (format, persona) for multipliers → pick one background per format (shared across personas) → build seeded sentences → pick visual archetypes → render 9-section prompts → write files + sidecar JSON + run manifest
- **Key difference from `generate_ads.py`:** Uses a simpler background strategy (one background per format shared by all personas). Does NOT update the registry. Writes a run manifest to `dashboard_storage/runs/{run_id}/`.

### `registry_banlist.py` (72 lines)
Exports a banlist from the registry for external LLM copy generation.
- Reads `AD_GENERATION_REGISTRY.JSON` → `indexes.used_text`
- Outputs last N strings per bucket as a clean JSON
- Also exports `derived_recent` — lightweight skeleton/opening metadata from recent entries (opening_pattern_4tok, copy_skeleton, hook_structure_class, proof_style_class, cta_voice_class)
- Purpose: pass to an LLM when asking it to write new copy, so it knows what strings/patterns are forbidden

### `extract_format_rules.py` (53 lines)
Utility. Parses `AD_CREATIVE_SYSTEM_PLAYBOOK.md` and extracts a specific format section (HERO/BA/TEST/FEAT/UGC) as a standalone text or JSON block.

---

## Validation Gates (What Can Block a Run)

| Check | Location | What Fails It |
|-------|----------|---------------|
| Copy has all required fields | `parse_args()` + `require_str()` | Missing headline/cta/support/etc. |
| Headline has banned content | `parse_copy_block()` lines 656-659 | Product component names or protocol mechanics in headline |
| Format requires support/bullets | `main()` lines 1329-1338 | Missing required fields per format |
| Text uniqueness against registry | `uniqueness_check()` lines 720-735 | Any exact string match in `indexes.used_text` |
| Text uniqueness within batch | `check_run_text()` lines 1313-1323 | Duplicate text within same copy batch |
| Prompt has required tags | `validate_prompt_text()` lines 1128-1137 | Missing "Background seed:", "Seeded background direction", "SAFE-ZONE ENFORCEMENT" |
| Prompt minimum length | `validate_prompt_text()` line 1136 | Fewer than 45 non-empty lines |
| Background exists for format | `pick_background_slot()` line 440 | No variants for this format in `background_variant.json` |
| Visual archetype exists | `pick_visual_archetype()` line 828 | No archetypes configured for format |
| Forced background invalid | `get_background_by_id()` line 472 | ID not found or not allowed for format |
| Semantic copy rejection (upstream) | `semantic_copy_rejection()` in app.py at line 1322 | Headline matches avoid_skeleton, generic skeleton, repeated opening pattern, generic support opener, persona-generic support, or overused CTA |

---

## Batch Numbering

- Output dirs are `output/v1/`, `output/v2/`, etc.
- `next_batch_name()` scans existing dirs, finds max N, returns `v{N+1}`
- Within a batch dir, subdirs: `45/` for 4:5, `96/` for 9:16
- File naming: `OUTPUT_{FORMAT}_P{NN}_{LANG}.txt`
- Multipliers (same format+persona, multiple creative variations): `OUTPUT_{FORMAT}_P{NN}_{LANG}_A01.txt`, `_A02.txt`, etc.

---

## What NOT To Do

1. **Do NOT modify `generate_ads.py` to write headlines.** It is an assembler, not a copy writer. Headlines come from upstream LLM via the prompt builder.
2. **Do NOT modify `generate_ads.py` to fix copy quality.** Fix the prompt templates (`copy_prompt_templates.json`, `copy_architecture.json`) or the prompt builder (`app.py`) instead.
3. **Do NOT put sentence templates or final-copy examples back into `copy_architecture.json`.** The architecture file is now intent-level metadata only. Adding examples back will make the LLM imitate them instead of writing original copy.
4. **Do NOT delete entries from `AD_GENERATION_REGISTRY.JSON`.** It's append-only. If something is wrong, add a correction entry, don't delete history.
5. **Do NOT overwrite existing batch folders.** Always write to `v{max+1}`.
6. **Do NOT create alternate registry files.** Always update root `AD_GENERATION_REGISTRY.JSON`.
7. **Do NOT skip the validation gate.** If CHK-* fails, the prompt cannot be written. Fix upstream copy and re-run.
8. **Do NOT hardcode persona mappings.** Personas are selected randomly per format (from 1-27), not paired 1:1 with formats.
9. **Do NOT translate copy in `generate_ads.py`.** The Hindi/Hinglish copy must come pre-written in the copy JSON.

---

## How to Run

```bash
# Activate venv
source .venv/bin/activate

# Run assembler with copy JSON
python scripts/generate_ads.py --copy-file path/to/copy_batch.json

# With custom batch name and seed
python scripts/generate_ads.py --copy-file copy.json --batch v20 --seed 12345

# Dry run (validate without writing)
python scripts/generate_ads.py --copy-file copy.json --dry-run

# Assemble from xlsx
python scripts/assemble_from_xlsx.py --xlsx path/to/on-image-copy.xlsx

# Export registry banlist
python scripts/registry_banlist.py --last 200

# Extract format rules from playbook
python scripts/extract_format_rules.py --format HERO
```
