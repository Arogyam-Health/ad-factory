# Obesity Killer Ad Creative System Playbook
# Version 2.0 — Revised with full prompt system, background variation engine, and live registry logic

---

## 0) Quick Start (Read First)

How to use this playbook in chat:
- If user gives no specific inputs (persona/headline/format), generate a default starter batch immediately.
- If user gives specific inputs (persona and/or headline and/or format), use those inputs first.
- If user gives partial inputs, use what they gave and fill missing fields with defaults from this playbook.
- Always keep claims restricted to `input/docs/product master doc.txt` and preserve product fidelity rules.

Execution mode lock (important):
- This repo uses the custom in-repo playbook + scripts workflow as primary execution path.
- Do not switch to the generic `ad-factory` skill flow unless the user explicitly asks to use that skill.
- Treat "refer to playbook md and create ads" as the same as "create ads".
- For `create ads` requests, execute directly and return outputs; do not run exploratory commentary.

Accepted user input styles (examples):
- "Create ads" -> generate default starter batch (all 5 formats, 1 variation each)
- "Persona 9, HERO + TEST" -> use persona 9 for those formats
- "Use this headline: ... for FEAT" -> keep that headline, generate rest as needed
- "UGC only" -> generate only UGC with default persona/headline mode unless user specifies
- "Create 9:16 ads" / "Stories ads" / "Reels ads" -> switch output sizing and text placement to 9:16 safe-zone rules before prompt finalization

Default starter batch profile (use when no specific input is given):
- Persona selection: random from 1-26 per format (do not use a fixed mapping)
- Prefer unique personas across selected formats in the same batch when possible
- If user provides persona for only some formats, keep those fixed and randomize remaining formats
- Headline mode: AI-generated fresh headlines
- Default canvas mode when user gives no placement/ratio instruction: 4:5
- Formats: HERO, BA, TEST, FEAT, UGC

---

## 1) Objective

Generate high-converting static ads for Obesity Killer Kit across 5 formats:
- HERO
- BA (Before/After journey style)
- TEST (Testimonial)
- FEAT (Features/benefits)
- UGC (Creator-style)

Current production mode:
- Generate 1 variation per format.
- Log every generation to registry immediately after output finalization.

---

## 2) Source of Truth (Do not invent)

Use only approved claims and context from `input/docs/product master doc.txt`.
Use mechanism details only when they are present in `input/docs/product master doc.txt`.

Persona language source:
- Use the persona library in this playbook as the source of persona identity and intent.
- For each selected persona, define copy inputs using the 5-field model: pain, desire, friction, proof needed, tone cue.
- Compose fresh copy each run from those fields and product/mechanism constraints.
- Do not depend on legacy persona artifacts outside this playbook during runtime.

Foundation doc handling rules:
- Treat `productinfomain.txt` as the Single Source of Truth product foundation document.
- Do not summarize, reconcile, reinterpret, or re-prioritize that document at runtime.
- Split it into meaningful verbatim section chunks and pass only the needed chunks to the model.
- Preserve the product's own benefit hierarchy when writing copy.
- Sections 9 (`What benefits do users see?`) and 10 (`What makes it different?`) outrank recurring ad themes seen in prior outputs, spreadsheets, or registries.
- Do not let supporting themes like `AM/PM routine`, `cravings down`, `support`, `tracker`, or `structured system` become the lead headline/subheadline angle unless the selected persona explicitly needs that framing.
- Default headline/subheadline priority order: fast visible weight loss first, then reduced hunger/cravings and easier adherence, then safe-feeling/no-side-effects trust, then practical ease and guided support.
- Use routine/timing/support language as supporting explanation, not as the main promise, unless format purpose specifically requires mechanism breakdown.

Mechanism grounding rules:
- Use `input/docs/product master doc.txt` as the single product source.
- `input/docs/product master doc.txt` is the source for approved claims, mechanism boundaries, support details, and offer details.
- Protocol details, Q&A handling, usage caveats, and mechanism framing must come from `input/docs/product master doc.txt`.
- Do not invent new benefit logic outside `input/docs/product master doc.txt`.
- Do not frame the product as a fat burner.
- Do not use claims like `boosts metabolism`, `burns fat fast`, `accelerates fat loss`, or similar shortcuts.
- Product logic must stay grounded in: reduced hunger, reduced cravings, reduced random eating, reduced intake, and digestion support.
- Support lines must not stop at generic routine language; they must connect the approved mechanism to the product's weight-management purpose.
- Every support line should ladder up from mechanism to outcome using compliant phrasing such as: supports weight loss efforts, helps reduce overeating that drives weight gain, supports reducing excess weight, supports obesity reduction efforts, or helps make weight reduction more manageable.
- Do not write support lines that only mention routine simplicity, clarity, consistency, mindset, or guidance unless they also make clear that the product is for reducing excess weight / obesity.

Approved anchors:
- 3-5 kg average in 15 days
- Ayurvedic kit, natural ingredients
- Cravings/hunger control support
- Digestion/metabolism support
- No crash diets, no heavy workouts
- Trusted by 70,000+ users
- Money-back terms apply

Never add unsupported medical claims, disease cures, or made-up mechanisms.

---

## 3) Product Fidelity Lock (Mandatory — applies to every single output)

Image source:
- Product reference images are stored in `input/images/` (PNG/JPG/WebP). Uploaded via Dashboard or placed manually.
- Images are uploaded to Gemini as visual truth references before every generation run.
- Prompt requirement (always include): "Use the uploaded Obesity Killer product packshot images as absolute visual truth."
- Never ask the user to re-describe products — the images are the reference.

Product dimensions (use for correct relative sizing in every image):

- OK Kit Box — Cuboid — 20.3 x 10.1 x 14.8 cm (largest element, acts as anchor)
- OK Powder Bottle — Cylinder — Diameter 5 cm, Height 10 cm
- OK Tablet Bottle — Cylinder — Diameter 3 cm, Height 5 cm
- Amla Bottle — Cylinder — Diameter 3 cm, Height 5 cm
- OK Liquid Sachet — Vertical Rectangle — Width 10 cm, Height 15.3 cm (tall and narrow)

All 5 products must appear in every output. Size them in correct proportion based on dimensions above.

Exact label text — render these perfectly on every product:

- Kit Box: Top strip — "Panacea for weight loss and obesity related conditions" / Logo — "Dr. ARUN TYAGI'S" in red + "OBESITY KILLER KIT" in black / Badge — "ISO 9001:2008 Certified"
- Amla Bottle (red cap, far left): "Dried Amla" — retain amla fruit illustration sharp
- OK Tablets Bottle: "OK TABLETS"
- OKP Bottle: "OKP" — retain light pattern on label
- OK Liquid Sachet (green, far right): "OK LIQUID" — retain sharp lotus logo above text

Product fidelity rules:
- Do not redesign, redraw, relabel, or recreate any product or packaging
- Do not change brand name, label colors, illustrations, or proportions
- Do not blur, approximate, paraphrase, or "fix" any text (Hindi or English)
- Do not replace unclear text with guesses — preserve the original image as-is
- Only permitted: placement, scaling, subtle drop shadows, mild lighting correction
- If uncertain about any detail — simplify the background, keep the product unchanged

---

## 4) Brand and Writing Rules

From `input/docs/product master doc.txt`:
- Tone: empathetic coach, trustworthy, uplifting
- Style: simple language, active voice, short sentences, no filler
- Credibility: specifics over vague adjectives
- Function over form
- Correctness over hype

Headline and caption must be clear, readable, and useful.

On-image copy realism rules (anti-AI style):
- Write headlines like real Indian performance ads: plain language, sentence case, natural rhythm.
- Do not use decorative symbols in headline/support/CTA: `*`, `-`, `_`, `|`, `~`, `#`, `@`, `/`, `\\`.
- Allowed punctuation on-image is minimal: `.`, `,`, `?`, apostrophe. Use only when naturally needed.
- No emoji, no hashtag-style copy, no forced separators, no bracketed gimmicks.
- Avoid all-caps headlines unless acronym is part of product label.
- Keep headline human-sounding, not template-looking: avoid stacked fragments and slogan-like punctuation tricks.
- No disclaimer line on-image by default. Do not add disclaimer text blocks in headline/support/CTA area.

---

## 5) Color Palette — Strict, No Exceptions

Background: #FFFBED to #FEEFD6 to #FCDBAC — smooth top-to-bottom gradient only
Accents and CTA: #F79040 to #E66410
Headlines: #973015
Body text and bullets: #421808
No colors outside this palette. No neon. No harsh gradients. No random accent colors anywhere.

---

## 6A) Awareness-Stage Layer

Every production ad must declare one reader awareness stage before copy is written. This is separate from persona.

Persona answers who is being addressed. Awareness stage answers how much persuasion context they already have.

Allowed awareness stages:
- `unaware`: reader does not yet realize the hidden weight-management friction. Open by naming an ignored cause such as random eating, hunger spikes, or routine breakdown.
- `problem_aware`: reader knows the problem but not the fix. Validate the pain, then introduce the approved product mechanism.
- `solution_aware`: reader knows fixes exist but not why this kit is different. Compare against stricter, messier, or less guided alternatives.
- `product_aware`: reader already knows the kit or brand. Use proof, urgency, trust, guarantee terms, or simplicity to create the final push.

Awareness-stage selection rules:
- If user provides an awareness stage, use it exactly unless it conflicts with format or compliance rules.
- If missing, infer from persona pain, friction, and proof-needed fields.
- Trust-first, doctor-backed, natural-solution, guarantee, or budget/value personas usually map to `product_aware` or `solution_aware`.
- Craving, snacking, schedule, routine, stress, or beginner personas usually map to `problem_aware`.
- Broad educational concepts that expose an overlooked behavior loop may use `unaware`.

Awareness-stage writing map:
- `unaware`: headline names the hidden issue; support line connects it to weight-management purpose.
- `problem_aware`: headline validates the known pain; support line introduces the kit mechanism.
- `solution_aware`: headline contrasts old fixes with this system; support line explains why the routine is easier, safer-feeling, or more guided.
- `product_aware`: headline opens with proof, deadline, offer, or trust; support line reduces the final buying hesitation.

Concept path lock:
- Each ad must choose exactly one concept path: `awareness_stage + concept_angle + concept_structure`.
- Do not mix multiple awareness stages or multiple concept angles inside one headline.
- The chosen concept path is direction only. Do not render framework labels on-image.

---

## 7) Headline Engine

Each headline must do 2 jobs:
1. Scroll stop with persona pain
2. Show how Obesity Killer solves that pain using an approved mechanism

Formula: Persona pain + Awareness stage + Concept angle + Copy structure + Approved mechanism/outcome

Headline construction sequence:
1. Select persona and define pain/desire/friction/proof needed/tone cue.
2. Select or infer `awareness_stage`.
3. Select `concept_angle` from the 8-angle menu.
4. Select `concept_structure` from PAS / BAB / FAB / 4U's.
5. Compose headline and support line as one hierarchy: headline earns attention, support line explains the product reason to believe.

Concept angle menu:
- `pain_point`: open with the specific problem.
- `desired_outcome`: open with the result or felt outcome.
- `customer_scale_70k`: open with others' trust, user scale, or review logic.
- `authority`: open with doctor formulation, Ayurveda credibility, or expertise.
- `story`: open with a real-life routine moment or first-person situation.
- `curiosity`: open with an information gap or mechanism question.
- `comparison`: open by contrasting the kit with harder alternatives.
- `offer`: open with result window, guarantee terms, kit completeness, or practical reason to act.

Concept structure menu:
- `pas`: Problem -> Agitate -> Solve. Name the pain, sharpen why it keeps happening, then introduce approved kit mechanism.
- `bab`: Before -> After -> Bridge. Show current friction, desired progress state, then the kit/routine bridge.
- `fab`: Feature -> Advantage -> Benefit. State concrete product feature, why it helps, and weight-management benefit.
- `four_us`: Useful, Urgent, Unique, Ultra-specific. Make the headline immediately relevant, honestly time-sensitive, differentiated, and concrete.

Freshness rule:
- Do not use a fixed headline bank as final output.
- Generate new headlines every request from persona pain + mechanism + outcome.
- Avoid repeating opening patterns across consecutive ads.
- Rotate angle each time: pain, objection, mechanism, time, proof, sacrifice reduction.

4U writing lens:
- Useful: reader understands why the line matters right now.
- Urgent: the line gives a real reason to act now or start soon. No fake scarcity.
- Unique: a generic competitor could not say the exact same line unchanged.
- Ultra-specific: line uses concrete detail such as 15 days, cravings, routine step, kit system, doctor-formulated proof, or user scale.
- Use these as instructions while writing. Do not run a hard vocabulary scorer or block prompt assembly only because wording is new.

### Headline Execution Layer (mandatory)

The concept framework chooses the strategic route. The execution layer decides whether the final headline sounds like a real paid ad.

Headline role:
- The headline should carry one strong idea only: tension, proof, identity, deadline, outcome, or sacrifice reduction.
- The headline should sound edited by a human, not like a planning label or SEO phrase.
- Do not force the full mechanism into the headline. Move mechanism, proof, and timing details into the support line.
- Prefer plain sentence rhythm over cleverness, slogans, or stacked keyword phrases.
- Use sentence case by default. Title case is allowed only when it intentionally reads like a poster headline.
- Do not start with instructional verbs (Start, Begin, Kickstart, Follow). Headlines should read like statements or questions, not how-to steps.
- Do not place AM/PM timing, 4-hour windows, or protocol mechanics in the headline. Those belong in support lines or bullets.
- Do not put product component names (OK Liquid / OK Tablet / OK Powder / OKP) in headlines. Keep those in support lines or bullets.
- Never surface internal persona notes, proof-needed notes, or any planning text as on-image copy.

Support-line role:
- The support line explains why the headline is believable.
- It must add one second-lane reason to believe: simple routine, doctor credibility, 70,000+ users, 15-day progress, reduced cravings, digestion support, low-guesswork protocol, or less sacrifice.
- Do not paraphrase the headline.
- Do not use support lines as generic wellness copy. Keep weight loss, obesity reduction, excess-weight reduction, or 15-day progress obvious.

Human headline pattern families (direction only, do not copy exactly):
- Authority stamp: `Doctor-formulated. Ayurvedic. Proven at scale.` Pattern: credibility + category + proof.
- Protocol identity: `Doctor-formulated, protocol-led system.` Pattern: expert source + structured method.
- Pain question: `Too tired to track everything for weight loss?` Pattern: too tired/too busy/too overwhelmed + hard behavior.
- Stubborn-weight proof: `When weight feels stubborn, you need something proven.` Pattern: when problem feels hard + trusted solution.
- Deadline direct: `Need visible weight loss in 15 days?` Pattern: need + concrete outcome + timeframe.
- Sacrifice reduction: `3 to 5 kg with 1 Kit, without turning life upside down.` Pattern: specific result + simple vehicle + avoided sacrifice.
- Emotional outcome: `Feel lighter. Look more like yourself again.` Pattern: felt result + identity restoration.
- Messy-life mechanism: `Why this works even when life stays messy.` Pattern: curiosity + real-life friction.

Support-line pattern families (direction only, do not copy exactly):
- Product identity + ease: `A guided Ayurvedic weight-loss system with clear morning and evening steps.`
- Routine simplicity: `A simple 2-step routine that takes about 5 minutes a day.`
- Authority + proof + time: `Created by Dr. Arun Tyagi and trusted by 70,000+ Indians for visible progress in 15 days.`
- Mechanism + practical benefit: `Morning support helps fullness; night support helps digestion and next-day follow-through.`
- Result + sacrifice reduction: `Designed for 3 to 5 kg weight-loss support without feeling weak, drained, or overwhelmed.`
- Event/deadline support: `Designed for people who want guided progress before a wedding, trip, or important event.`

Human editor pass (mandatory before returning copy JSON):
- Rewrite every headline once as a performance-ad editor.
- Remove generic AI phrases, abstract benefit language, and planning labels.
- Make the headline shorter and more spoken.
- Keep one central idea in the headline.
- Move extra explanation into support line or bullets.
- If headline and support line say the same thing, rewrite the support line with proof, mechanism, or ease.

Bad-to-better rewrite guidance (direction only):
- Weak: `Start mornings with a clearer 15-day weight-loss routine.` Better pattern: `Too tired to track every weight-loss step?`
- Weak: `Ayurvedic support for daily appetite control.` Better pattern: `When cravings run the day, weight loss feels impossible.`
- Weak: `Guided support for practical progress.` Better pattern: `Clear steps when dieting feels like too much work.`
- Weak: `Fast visible progress for busy people.` Better pattern: `Need visible progress in 15 days?`
- Weak: `A simple routine for your lifestyle.` Better pattern: `A 5-minute routine for stubborn weight days.`

Preferred CTA family:
- Prefer direct, low-friction CTAs: `See How It Works`, `See The Steps`, `View Details`, `See The Routine`, `Start Today`.
- Vary CTAs enough to avoid registry collisions, but keep the style simple and non-salesy.

Examples (direction only, do not copy-paste):
- "Cravings control nahi ho rahe? 15 din mein routine palat sakta hai."
- "Time nahi hai gym ka? Weight routine fir bhi possible hai."
- "Weight loss chahiye, weakness nahi."

---

## 8) Caption Engine (Value Equation)

Use this equation in every caption:
Value = (Dream Outcome x Likelihood of Achievement) / (Time Delay x Sacrifice)

Move each component:
- Dream Outcome: visible loss + lighter body + confidence
- Likelihood: doctor credibility, 70,000+ users, clear daily protocol, support
- Time Delay: 15-day milestone language
- Sacrifice: no crash diet, no heavy workout, practical routine

Caption checklist:
- 1-2 short lines
- Concrete benefit
- Concrete mechanism
- Low-sacrifice framing
- No disclaimer sentence in on-image copy

Fresh caption rule: generate fresh each run, keep meaning stable, vary phrasing and angle.

---

## 9) Background Variation Engine (NEW)

Goal: Every ad generation session must use a distinctly different background/scene setting so no two outputs look the same.

Background files and purpose:
- `background_variant.json` = structured background catalog (ID, title, format eligibility + safe-zone descriptors) used for seeded scene sentence generation

Background hygiene rule (mandatory):
- Avoid workstation-heavy props by default (keyboard, laptop, monitor, mouse, dense office clutter), unless user explicitly requests office-device context.
- Prefer clean lifestyle props (cup, book, towel, tray, plant) with low visual noise.
- FEAT default pool preference: avoid desk/workstation scenes entirely; prioritize clean studio, shelf, tray, kitchen, or clinical-minimal contexts.

### How it works:

Before generating any prompt, the assistant must:
1. Check `AD_GENERATION_REGISTRY.JSON` slot usage for the selected format.
2. Build allowed catalog pool from `background_variant.json` where `formats` contains the selected format.
3. Select only from slots not yet used in current cycle for that format (`indexes.slot_exhaustion_tracker.<FORMAT>.remaining_slots_current_cycle`).
4. Generate seeded scene sentence via the in-pipeline background sentence builder using the same slot ID and seed.
5. Include both slot ID and seeded sentence in Section 8 (VISUAL DIRECTION BLOCK), then log slot + seed in registry.
6. When remaining pool becomes empty, reset cycle for that format and continue.

### Selection rules:

- Exhaustive rotation is mandatory for catalog slots: no slot may repeat for a format until all allowed slots for that format are used once.
- If registry is empty or tracker missing: initialize cycle with full allowed pool for that format and pick one.
- ID format is three digits (`BG-001` ... `BG-500`).

### Background selection algorithm (mandatory)

- Use catalog-first selection from `background_variant.json` and enforce exhaustive rotation using `indexes.slot_exhaustion_tracker`.
- Select from `remaining_slots_current_cycle`, then move selected slot to `used_slots_current_cycle`.
- If `remaining_slots_current_cycle` is empty, increment cycle number and repopulate from current allowed pool.
- Safe-zone sentence generation must be deterministic from slot + seed and produced by the active prompt assembly pipeline.
- SEED rule: `SEED = (BATCH_NUMBER * 1000) + (PERSONA_NUMBER * 10) + VARIATION`
- Include in Section 8:
  - `Background slot: BG-XXX`
  - `Seed: <SEED>`
  - Full seeded sentence output
  - Safe-zone fields: `composition`, `layout_intent`, `cta_safe_space`, `crop_safety`

---

## 10) Registry System

Registry file: `AD_GENERATION_REGISTRY.JSON`

### Current mode: PRODUCTION — registry read enabled, `mode.write_enabled: true`.

In production mode:
- Read registry to check what has been used recently (avoid repeating).
- Write one entry per generation immediately after final output.
- Never overwrite history; append-only logging.

### Registry schema (current production structure):

```json
{
  "mode": {
    "phase": "production",
    "write_enabled": true,
    "last_updated": "2026-04-18T09:51:00Z"
  },
  "entries": [
    {
      "id": "entry_001",
      "timestamp": "2025-01-01T00:00:00Z",
      "format": "HERO",
      "persona_number": 2,
      "persona_name": "Busy Professional",
      "headline_angle": "sacrifice_reduction",
      "awareness_stage": "problem_aware",
      "concept_angle": "desired_outcome",
      "concept_structure": "four_us",
      "headline_en": "Weight loss without life disruption.",
      "headline_hi": "जीवन बिगाड़े बिना वज़न घटाएं।",
      "support_line_en": "Control cravings, support metabolism daily.",
      "support_line_hi": "रोज cravings control करें, metabolism support पाएं।",
      "cta_en": "Start Now",
      "cta_hi": "आज शुरू करें",
      "caption_en": "Ayurvedic support for consistent fat-loss habits without crash diets.",
      "caption_hi": "बिना crash diet के fat-loss habit बनाने में ayurvedic support।",
      "bullets_en": [
        "Helps reduce frequent hunger spikes",
        "Supports digestion and daily metabolism"
      ],
      "bullets_hi": [
        "बार-बार लगने वाली भूख को कम करने में सहायक",
        "पाचन और रोज़ाना metabolism को support"
      ],
      "background_slot": "BG-001",
      "background_name": "Clean warm studio",
      "background_source": "catalog",
      "fresh_background_signature": null,
      "opening_pattern_4tok_en": "your_day_goes_right",
      "opening_pattern_4tok_hi": "दिन_ठीक_चलता_है",
      "copy_skeleton": "pain_mechanism_time",
      "hook_structure_class": "contrast_loop",
      "proof_style_class": "mechanism_explainer",
      "cta_voice_class": "urgent_start",
      "language": "EN",
      "output_quality": "approved",
      "notes": "First test generation",
      "seed": 7071
    }
  ],
  "indexes": {
    "backgrounds_by_format": {},
    "slot_exhaustion_tracker": {},
      "used_text": {},
      "concept_combos": {
        "recent": []
      },
      "copy_patterns": {
      "by_format_language": {},
      "recent_opening_4tok": {},
      "recent_skeletons": {},
      "recent_cta_voice": {},
      "recent_hook_structure": {},
      "recent_proof_style": {}
    }
  }
}
```

### Fields to log for every entry:

- id: sequential entry ID (entry_001, entry_002, etc.)
- timestamp: ISO 8601 format
- format: HERO / BA / TEST / FEAT / UGC
- persona_number: 1-26
- persona_name: readable name
- headline_angle: pain / objection / mechanism / time / proof / sacrifice_reduction
- awareness_stage: unaware / problem_aware / solution_aware / product_aware
- concept_angle: pain_point / desired_outcome / customer_scale_70k / authority / story / curiosity / comparison / offer
- concept_structure: pas / bab / fab / four_us
- headline_en: exact English headline used
- headline_hi: exact Hindi headline used
- support_line_en/support_line_hi: exact support line used on image
- cta_en/cta_hi: exact CTA used on image
- caption_en/caption_hi: exact long-form caption used (if any)
- bullets_en/bullets_hi: exact bullet text array used (if any)
- background_slot: BG-001 through BG-500 (or fresh-generated background signature)
- background_name: readable name of slot
- background_source: catalog / fresh
- fresh_background_signature: required when source is fresh, else null
- opening_pattern_4tok_en/opening_pattern_4tok_hi: first 4-token normalized opening pattern of headline
- copy_skeleton: high-level copy structure tag (for example: pain_mechanism_time)
- hook_structure_class: hook composition class (question_led / contrast_loop / command_led / confession_led / proof_led)
- proof_style_class: trust framing class (customer_scale_70k / mechanism_explainer / authority_anchor / routine_clarity / objection_flip)
- cta_voice_class: CTA intent class (urgent_start / guided_next_step / reassurance_start / challenge_action / discovery_action)
- language: EN / HI / BOTH
- output_quality: approved / rejected / pending
- notes: optional free text

### Deduplication rules (apply in production):

- Same persona + same format = flag if used in last 3 entries for that format. Suggest switching persona or angle.
- Catalog background dedupe = exhaustive rotation hard block by format until cycle exhaustion reset.
- Same fresh_background_signature + same format = hard block if used in last 20 entries for that format.
- Same headline_angle + same format = flag if used in last 3 entries. Rotate angle.
- Same persona + same headline_angle = hard block regardless of format. Always change at least one.
- Any exact text reuse is forbidden across all history: headline, support line, CTA, caption, and bullets in both EN and HI must be new every time.
- Same opening_pattern_4tok + same format/language = hard block in recent window (last 10 entries).
- Same copy_skeleton + same format/language = hard block if repeated in last 5 entries.
- Same hook_structure_class + proof_style_class + cta_voice_class trio = hard block in last 12 entries (prevents "same ad with new words" effect).
- Same `awareness_stage + concept_angle + concept_structure + format` = creative warning only. Prefer rotating the idea path, but do not block prompt assembly if generated copy is otherwise valid and unique.

### Copy diversity matrix (mandatory for scaled production)

Every ad must be tagged before finalization using these 4 axes and rotated intentionally across a batch.

- hook_structure_class:
  - `question_led`
  - `contrast_loop`
  - `command_led`
  - `confession_led`
  - `proof_led`
- proof_style_class:
  - `customer_scale_70k`
  - `mechanism_explainer`
  - `authority_anchor`
  - `routine_clarity`
  - `objection_flip`
- cta_voice_class:
  - `urgent_start`
  - `guided_next_step`
  - `reassurance_start`
  - `challenge_action`
  - `discovery_action`
- copy_skeleton (examples):
  - `pain_mechanism_time`
  - `pain_agitate_solve`
  - `objection_flip_mechanism`
  - `proof_then_routine`
  - `micro_story_then_action`
  - `problem_reframe_then_next_step`

Concept-combo matrix (mandatory for headline ideation):
- awareness_stage:
  - `unaware`
  - `problem_aware`
  - `solution_aware`
  - `product_aware`
- concept_angle:
  - `pain_point`
  - `desired_outcome`
  - `customer_scale_70k`
  - `authority`
  - `story`
  - `curiosity`
  - `comparison`
  - `offer`
- concept_structure:
  - `pas`
  - `bab`
  - `fab`
  - `four_us`

Concept-combo rule:
- Treat `awareness_stage + concept_angle + concept_structure` as the creative idea ID.
- Rotate concept combos intentionally across a batch.
- If the same format is repeated in one run, prefer a different concept combo unless user explicitly requests a controlled duplicate test. This is an instruction to the AI/operator, not a machine-blocking validation gate.

Batch diversity minimum:
- In any 5-ad batch, at least 4 unique `hook_structure_class` values.
- In any 5-ad batch, at least 3 unique `cta_voice_class` values.
- No repeated `copy_skeleton` within the same batch.

### Heading uniqueness rules (mandatory)

1) Fresh composition source
- Generate all headline/support/CTA copy from the selected persona's 5-field model plus approved product/mechanism chunks only.
- Do not reuse any prior output sentence.
- Do not paraphrase old lines from previous batches.

2) Same-persona multi-format separation
- If one persona is used across HERO, BA, TEST, FEAT, UGC in the same batch, each format must use a different sub-angle:
  - HERO: strongest pain contrast
  - BA: before to after transition from one specific trigger
  - TEST: objection to belief shift
  - FEAT: mechanism breakdown on a different trigger
  - UGC: first-person micro-moment

3) Motif non-repetition (hard block)
- Do not repeat the same 2-4 word motif across formats in the same batch.
- Examples of banned repeats in one batch: `night loop`, `undoes your day`, `late-night cravings`.
- If overlap appears, regenerate lower-priority format copy.

4) Structural uniqueness
- Opening pattern must differ across formats.
- Reject if first 4-token pattern repeats in same persona batch.
- Reject if same copy skeleton repeats (example: question to mechanism to CTA).

5) Headline angle rotation
- Rotate headline angle each time: pain, objection, mechanism, proof, time, sacrifice reduction.
- Same persona plus same angle in adjacent runs is not allowed.

6) Lexical distance check
- Headline must use substantially different core nouns/verbs from other formats in same persona batch.
- Synonym swaps alone are not accepted as new.

7) Language purity
- EN files: fully English on-image copy.
- HI files: Hindi-first unless Hinglish is explicitly requested.
- Reject mixed-language accident in EN headings.

8) Validation gate before write (mandatory)
- If any uniqueness check fails, do not write files.
- Regenerate until all formats pass.
- Return pass/fail report per file with failed checks.

Same-persona multi-format diversification (mandatory):
- If one persona is used across multiple formats in the same batch, do not reuse the same core pain sentence across those formats.
- Build a per-format sub-angle map from the 5-field persona model before writing copy, and lock one unique sub-angle per format.
- Allowed sub-angle sources:
  - Pain
  - Desire
  - Friction
  - Proof needed
  - Tone cue expressed through a distinct trust or framing angle
- Sub-angle assignment rule for a 5-format batch using one persona:
  - HERO: primary pain contrast
  - BA: transition from one concrete trigger to one concrete recovery behavior
  - TEST: objection-to-belief shift (quote-style trust framing)
  - FEAT: mechanism breakdown mapped to a different trigger than HERO/BA
  - UGC: first-person micro-moment using a language-bank phrase not used in other formats
- Repetition block: do not reuse the same 2-4 word motif across more than one format in the batch (for example: `night loop`, `undoes your day`, `late-night cravings`).
- If motif overlap appears, regenerate the lower-priority format copy until all five formats are meaningfully distinct in angle, not just wording.

### Production write instruction:

- Keep `mode.write_enabled = true` in production.
- Update `mode.last_updated` on every successful append.
- Generate 1 variation per requested format unless user explicitly asks for more.
- Append entry before final handoff.

---

## 11) Format Specifications

Keep format structure fixed. Swap persona-specific headline/caption blocks.

Variation operating mode:
- Default production remains 1 variation per format unless user asks for more.
- Scale mode: prepare up to 8 variation lanes per format using the variation matrices below.
- Across multi-variation runs, rotate headline angle, CTA wording, scene/background slot, and composition map. Do not only swap one word.

### HERO
- Purpose: broad conversion
- Persona use: strongest pain-led hook + fastest believable outcome
- Copy shape: strong headline + support line + CTA + disclaimer
- Text budget: 18-36 words + disclaimer
- Minimum copy units: headline + 1 support line + CTA + disclaimer
- Prompt-detail baseline (Hero must be highest-clarity format):
  - Use uploaded product references as EXACT visual truth (shape, color, label, logo placement unchanged).
  - Product cluster remains dominant focal element; background and props are secondary.
  - Headline lives in top text band only; CTA button in lower safe zone only.
  - Never render price text, price badges, or currency symbols on-image.
  - Keep style clean, minimal, conversion-led, Meta-ready, and text fully legible.
- Hero headline variation lanes (rotate for up to 8 variants):
  - Benefit-led: "[Top benefit] in [timeframe]"
  - Value-led: "Simple routine. Clear daily progress."
  - Social-proof-led: "[X,000+] customers trust [brand]"
  - Problem-solution: "Stop [pain point]. Start [benefit]."
  - Curiosity-led: "Why [audience] are switching to [product]"
  - Urgency-led: "[Offer] ends soon. [Benefit] starts now."
  - Comparison-led: "[Product] vs [old way]. No contest."
  - Direct offer: "[Product]. [One-line benefit]."
- Hero background variation lanes:
  - Clean studio gradient
  - Lifestyle countertop scene
  - Trust/clinical desk context
  - Home practical routine context
  - Minimal editorial dark-premium context

### BA
- Purpose: show transition from pain to control
- Persona use: "before" = persona struggle, "after" = first meaningful win
- Copy shape: pain-to-progress headline + 3 outcome bullets
- Text budget: 22-36 words + disclaimer
- Minimum copy units: headline + 2-3 short bullets + CTA + disclaimer
- Prompt-detail baseline:
  - Split-screen structure with clear divider and explicit BEFORE/AFTER labels.
  - BEFORE side: muted/desaturated tone, friction/pain context, lower confidence energy.
  - AFTER side: vibrant/clean tone, confident outcome, product visibly integrated.
  - Keep product fidelity exact on both sides; never redraw packaging.
- BA variation lanes (do not rely only on facial mood changes):
  - Context-shift lane: messy routine vs structured routine.
  - Time-of-day lane: late-night struggle vs morning control.
  - Choice-architecture lane: impulse-snack desk vs planned-kit setup.
  - Energy-state lane: low-energy posture vs active upright posture.
  - Environment lane: kitchen chaos vs clean meal-prep corner.
  - Decision lane: confusion board/screens vs simple step-by-step kit use.
  - Social lane: isolated/stressed frame vs supported/accountable frame.
  - Progress lane: stagnation cues vs measurable-win cues (without fake body morphing).
- BA hard constraints:
  - No body-shaming visuals, no unrealistic body transformation edits, no fake medical visuals.
  - Transformation must be shown via routine/behavior/outcome cues, not extreme physique manipulation.

### TEST
- Purpose: trust
- Persona use: testimonial language mirrors persona words (cravings, stuck, no time)
- Copy shape: quote + attribution + trust line + CTA
- Text budget: 24-40 words + disclaimer
- Minimum copy units: headline/quote + attribution + trust line + CTA + disclaimer
- Testimonial integrity: never fabricate customer quotes; if real quote is unavailable, use rating + user-count + mechanism proof framing instead
- Prompt-detail baseline:
  - Quote card as primary visual block, product as secondary support element.
  - Large quote marks, clear attribution area, trust strip under quote.
  - If real testimonial exists in source: use exact quote + real attribution.
  - If no real testimonial: switch to rating-and-benefits card (no fabricated human quote).
- TEST variation lanes:
  - Real-quote lane (when available)
  - Star-rating + review-count lane
  - Outcome-proof lane (specific measurable benefit line)
  - Objection-resolution lane (price/safety/adherence concern)
  - Doctor/trust-anchor lane
  - Community/social-proof lane
  - Simplicity/usability lane
  - Consistency/maintenance lane

### FEAT
- Purpose: mechanism clarity
- Persona use: map each feature to persona friction
- Copy shape: "what makes it work" + 3-4 benefit bullets + CTA
- Text budget: 26-42 words + disclaimer
- Minimum copy units: headline + 3 feature-benefit bullets + CTA + disclaimer
- Prompt-detail baseline:
  - Center product remains exact reference; use callout pointers with clean labels.
  - Each callout must connect feature/mechanism to practical benefit.
  - Keep callout arrows clean and non-overlapping; no diagram clutter.
- FEAT variation lanes:
  - Component lane: Liquid / Powder / Tablet role clarity
  - Timing lane: morning step vs night step protocol
  - Benefit lane: appetite / digestion / adherence mapping
  - Objection lane: no-crash-diet / practical-routine framing
  - Trust lane: doctor-formulated + user-scale proof
  - Safety lane: boundaries + exclusions clarity
  - Lifestyle lane: busy-day compatibility mapping
  - Maintenance lane: continuation + post-goal sustainability

### UGC
- Purpose: authenticity
- Persona use: first-person micro-story from persona POV
- Copy shape: my routine support + 3 practical wins + CTA
- Text budget: 16-26 words + disclaimer
- Default text policy: headline + support line + CTA + disclaimer only
- Minimum copy units: headline + 1 short support line + CTA + disclaimer
- No bullets or long paragraph quote unless explicitly requested
- Prompt-detail baseline:
  - Casual phone-captured feel with natural framing, not polished studio symmetry.
  - Product remains clearly visible and exactly matched to reference.
  - Human subject must never hold, grab, lift, open, pinch, or touch any product/package; products stay independently arranged on a table/counter at correct scale.
  - Authentic setting aligned to persona (kitchen, desk, bedroom, commute, etc.).
  - Overlay text stays punchy and legible; avoid dense copy in UGC.
- UGC variation lanes:
  - Morning-rush routine
  - Office-break habit reset
  - Night-craving interruption
  - Parent-schedule compliance
  - Travel-day adaptation
  - Weekend consistency
  - First-week progress reaction
  - Maintenance confidence update

Cross-format variation rule (mandatory for 100+ ad libraries):
- Rotate at least 3 axes per new creative: copy angle, scene context, composition map, trust framing, CTA voice.
- Do not repeat the same format skeleton more than 2 times consecutively for one persona.
- Maintain product fidelity and compliance constant while varying narrative frame and visual context.

Pricing visibility rule (global):
- Do not show price anywhere on-image in any format.
- Do not use currency symbols (`₹`, `$`) or text like INR, price, only, discount, off, MRP on-image.

---

## 12) Prompt Assembly Template

Use this 9-section structure every time. All 9 sections are mandatory. No section may be dropped or collapsed to a placeholder.

```
Section 1 — PRODUCT LOCK BLOCK
Section 2 — OUTPUT SPEC (1080x1350)
Section 3 — FORMAT LAYOUT INSTRUCTIONS
Section 4 — PERSONA INPUT BLOCK
Section 5 — EXACT ON-IMAGE COPY
Section 6 — NEGATIVE CONSTRAINTS
Section 7 — QUALITY BAR
Section 8 — VISUAL DIRECTION BLOCK (includes background slot)
Section 9 — TYPOGRAPHY SHARPNESS BLOCK
```

Add this mandatory line in every prompt:
- "Keep on-image text minimal and mobile-readable. Avoid dense copy blocks."

Add this mandatory line in every prompt:
- "Typography must be pin-sharp. If any text appears soft, blurry, or smeared, regenerate."

### Per-section depth minimums (mandatory — do not write placeholder sections):

Section 1 — Product Lock: minimum 4 bullets
- Include: uploaded image reference rule, no redesign rule, no relabel rule, fallback behavior

Section 2 — Output Spec: minimum 5 bullets
- Include: canvas size, aspect ratio, style intent, text readability requirement, delivery expectation

Section 3 — Format Layout Instructions: minimum 8 bullets
- Include: composition map, focal hierarchy, product zone, text zones, background behavior, camera framing, lighting, spacing

Section 4 — Persona Input: minimum 5 bullets
- Include: pain, desire, friction, proof needed, tone cue

Section 5 — Exact On-Image Copy: minimum 3 copy lines
- Include: headline, support or quote line, CTA — render exactly as written, no alterations

Section 6 — Negative Constraints: minimum 8 bullets
- Include: visual and claim-level "do not" constraints

Section 7 — Quality Bar: minimum 6 bullets
- Include: readability, fidelity, anatomy, clutter, hierarchy, regeneration trigger

Section 8 — Visual Direction Block: minimum 8 bullets
- Include: scene (reference background slot ID and name), subject, props, camera, light, texture, depth, realism

Section 9 — Typography Sharpness Block: minimum 6 bullets
- Include: contrast, font weight, placement, anti-blur checks, mobile legibility, regen trigger

### Length floor:
- Full production prompt: 45-75 lines minimum for one format.
- If below 35 lines: treat as compressed and rewrite with full depth before sending.

### Prompt length hardline check (mandatory):
- Never collapse to ultra-short prompts after follow-ups.
- Final image prompt must retain full structure with all sections intact.
- Minimum required sections in every final prompt:
  1) Product Lock
  2) Output Spec
  3) Format Layout Instructions
  4) Persona Input
  5) Exact On-Image Copy
  6) Negative Constraints
  7) Quality Bar
  8) Visual Direction Block
  9) Typography Sharpness Block
- If any section is missing, treat as invalid and regenerate the prompt text before sending.
- Brevity is allowed only inside lines, not by dropping sections.

### Prompt compression prevention:
- Do not summarize or shorten the final generation prompt after follow-ups.
- On revisions: edit only the requested part, keep all other sections intact.
- Preserve all guardrail lines during rewrites.

### Language output mode:
- Always generate two final prompts together and save both in the batch folder:
  - English prompt
  - Hindi prompt
- Keep the same layout instructions in both, only language/copy changes.
- Language purity rule:
  - English output must be fully English (no Hinglish transliteration).
  - Hindi output must be fully Hindi in Devanagari script.
  - Do not mix scripts in one line unless explicitly requested.
- Image-generation default rule:
  - Generate EN images by default.
  - Do not generate HI images unless user explicitly asks.

### Text clarity and sharpness rule (mandatory):
- Prioritize crisp typography over decorative styling.
- Use high contrast text/background pairs.
- Keep text on flat areas; avoid curved, noisy, or low-contrast surfaces.
- Avoid perspective-warped text and ultra-thin font styles.
- Keep all text edge-sharp and mobile-legible at 100% view.

### Ad-factory prompt upgrades (adopted)
- Keep this explicit reference line in every final prompt: "Use provided product references as exact appearance truth for pack shape, label, and color."
- Enforce readability target: all visible copy must meet high contrast intent equivalent to WCAG AA readability.
- For testimonial creatives, never invent quotes; fallback to star rating + user count + concrete benefit when quote proof is unavailable.
- For scaled variants, vary one main axis at a time (headline angle, CTA, layout, or visual mood) to preserve test clarity.
- Keep output-ready sizing explicit per placement when requested: 1080x1350 (portrait default), 1080x1080 (feed square), 1080x1920 (stories).
- When generating batches, keep product fidelity constraints identical across all variants; only concept variables should change.

---

## 13) Full Production Prompt Template (filled example — copy and adapt)

PRODUCT LOCK BLOCK
- Use the uploaded Obesity Killer product packshot images as absolute visual truth.
- Do not redesign, redraw, relabel, or alter any product or packaging in any way.
- Do not change brand name, label colors, illustrations, proportions, or any text (Hindi or English).
- If any label text is unclear — preserve the original image as-is. Do not reinterpret it.
- Only permitted: placement, scaling, subtle drop shadows, mild warm lighting correction.

OUTPUT SPEC
- Canvas: 1080 x 1350 pixels. Portrait. 4:5 ratio.
- Style: [HERO / BA / TEST / FEAT / UGC] — polished enough for paid ad deployment.
- Text policy: Low-text by default. All copy minimal and mobile-readable at 375px width.
- Rendering: No compression artifacts. No soft edges on text or product label.
- All 5 products present, proportionally sized per reference dimensions, unmodified.
- Readability: maintain high-contrast foreground/background treatment for ad-platform legibility.
- Placement safety: before finalizing any prompt, lock all critical text, logos, CTA, and product-signaling elements inside the format-safe protected area.
- Default canvas mode when not specified by user: 4:5 portrait at 1080 x 1350.
- 4:5 safe-zone rule for mobile-first delivery: keep critical elements away from the top 14%, bottom 35%, and outer 6% side margins.
- 4:5 pixel guardrail at 1080 x 1350: avoid critical placements in the top 189px, bottom 473px, and outer 65px on left/right.
- 9:16 safe-zone rule for Stories/Reels delivery: keep critical text, logos, and CTA out of the top and bottom UI-risk bands and away from side edges.
- 9:16 pixel guardrail at 1080 x 1920: keep critical elements out of the top 250px, bottom 340px, and outer 65px on left/right unless the platform explicitly guarantees safe visibility.
- Size override rule: if channel-specific export is requested, output exact dimensions explicitly in prompt.

FORMAT LAYOUT INSTRUCTIONS
- [Fill based on selected format from Section 11]
- Composition: [describe zone map for selected format]
- Focal hierarchy: product dominant, text secondary, background tertiary.
- Product zone: [center / center-right / foreground-lower — specify per format]
- Text zones: flat uncluttered areas only — never over busy background detail.
- Text stack map (mandatory):
  - Headline zone = upper band only (top 15-32% of canvas).
  - Support line zone = directly below headline (top 32-45%).
  - CTA zone = lower safe band (bottom 70-85%), never above headline/support.
  - Forbidden placement = headline in lower-third or below product midline.
- 4:5 safe placement override:
  - Keep all essential text, CTA, logos, and product-signaling elements inside the center-safe region.
  - Do not place critical elements in the top 14%, bottom 35%, or outer 6% side margins.
  - Treat the lower 35-40% as a danger zone for CTA and key copy when the ad may run in reels/story-like surfaces with UI overlays.
- 9:16 safe placement override:
  - Keep all essential text, CTA, logos, and product-signaling elements inside the central protected zone only.
  - Do not place key elements in the top 250px, bottom 340px, or outer 65px on left/right of a 1080 x 1920 canvas.
  - Keep the bottom interaction zone visually quiet so platform UI does not collide with message-critical copy.
- Background: [reference selected background slot — see Section 9 below]
- Camera: [close-medium / medium / overhead — specify per format]
- Lighting: warm soft directional — source from top-left. Subtle drop shadows beneath products.
- Spacing: strong whitespace between zones. Grid-based alignment. Nothing floats randomly.

PERSONA INPUT BLOCK
- Persona: [Name from persona library]
- Pain: [what hurts now]
- Desire: [dream outcome]
- Friction: [why they failed before]
- Proof needed: [what makes them believe]
- Tone cue: [sincere / hopeful / authoritative / relatable]

EXACT ON-IMAGE COPY — DO NOT ALTER ANYTHING
- Headline: [exact text]
- Support line: [exact text]
- CTA: [exact text]
- Disclaimer: not allowed on-image unless user explicitly asks for disclaimer mode.
Render every character exactly as written. No paraphrasing, no punctuation changes, no autocorrection.

NEGATIVE CONSTRAINTS
- Do not recreate or redraw any product
- Do not blur, approximate, or rewrite any label text
- Do not use sale badges, burst graphics, or stickers
- Do not show body transformations or weight-loss visuals
- Do not use colors outside the defined palette
- Do not use more than 2 font weights
- Do not overcrowd the layout
- Do not make medical cure claims of any kind
- Do not render unnatural or anatomically incorrect hands (for UGC)
- Do not show a human hand holding, grabbing, lifting, opening, pinching, or touching any product/package in UGC; use pointing/open-palm gestures near surface-placed products only.
- Do not use ring light, studio flash, or overproduced lighting

QUALITY BAR — verify before accepting output:
- All 5 products present, correctly proportioned, completely unmodified
- All on-image text sharp and readable at 375px mobile size
- Product label accurate, unmodified, and fully readable
- Layout is calm, balanced, and premium
- No clutter, no hype, no forbidden elements
- Single clear focal hierarchy — product dominant throughout
- Section 8 contains full seeded background sentence from script execution (not just seed number reference) — if missing, regenerate immediately
- Section 8 contains all 4 safe-zone fields from background_variant.json: composition, layout_intent, cta_safe_space, crop_safety — if any missing, regenerate immediately
- If any item above fails — regenerate immediately without compromise

VISUAL DIRECTION BLOCK
- Background slot: [BG-XXX — slot name] (selected from background variation engine using exhaustive format-wise rotation)
- Scene: [describe scene based on selected slot]
- Subject: [for UGC — Indian woman 27-35, natural unposed expression. For product-only formats — no subject]
- Action: [for UGC — products arranged on surface while creator points/gestures nearby; never holding/grabbing product. For product-only formats — products arranged on surface]
- Camera: [handheld close-medium / editorial medium / overhead — specify]
- Lighting: [warm desk lamp + soft ambient fill / soft natural daylight from left / etc. — match slot]
- Props: [per slot — minimal, non-competing, background zone only]
- Surfaces: [per slot — wood / marble / white matte / etc.]
- Mood: [per persona — emotional relief / confidence / trust / urgency-free / etc.]
- Realism: natural skin, correct hand anatomy, true-to-life proportions, no stock-template look

TYPOGRAPHY SHARPNESS BLOCK
- Headline: Poppins Bold — high contrast against background zone
- Support and CTA: Poppins Medium/Regular — same typeface family
- Size: large enough to read on 375px mobile screen without zooming
- Placement lock:
  - Headline must sit in the upper text band (top 15-32%).
  - Support line must sit directly under headline with consistent left alignment.
  - CTA must sit in lower safe band only (bottom 70-85%).
  - Never place headline below support or in lower-third.
- Forbidden: thin fonts, script fonts, decorative typefaces, glow effects, outlined text, drop shadows on copy
- Mandatory: crisp hard text edges — zero softness — zero anti-alias blur on any character
- If any text is soft, blurry, or illegible — discard and regenerate immediately

---

## 14) Language Output Rules

Always produce two final prompts together:
- English prompt (fully English — no Hinglish transliteration)
- Hindi prompt (fully Hindi in Devanagari script — no mixed scripts in one line unless explicitly requested)

Keep the same layout instructions in both. Only the copy block changes language.
For image generation, submit EN prompt only by default. Submit HI only on explicit request.

---

## 15) Interactive Ad Request Flow (assistant behavior)

Decision policy (mandatory):
- If user gives no specific inputs in first ad request, generate default starter batch immediately (no clarification round).
- Single-command behavior: when user says "create ads", run end-to-end in one flow: generate prompts -> save `output/vN/` -> submit API jobs (EN default) -> save images in `generated_image/vN/`.
- If user gives specific inputs (persona/headline/format), apply them directly.
- If user gives partial inputs, apply provided values and fill missing values with defaults.
- Ask follow-up questions only when user intent is conflicting (for example, two different persona instructions for same format).
- Never ask pre-flight questions like "text prompts or images?" for standard create-ad requests; default to end-to-end flow.
- If runtime blockers exist (missing API key, missing image URLs, API failure), still complete all non-blocked steps first (generate + save prompts), then report the exact blocker and stop only blocked steps.
- Do not narrate internal workflow steps (for example: "reading files", "extracting sections", "checking patterns"). Return only final outputs and blockers.

Read-scope rule for `create ads` (mandatory):
- Read only required sources: this playbook, `input/docs/product master doc.txt`, `background_variant.json`, `AD_GENERATION_REGISTRY.JSON`.
- Do not mine old `output/v*` prompt files for style or structure unless user explicitly asks to replicate a specific past batch.
- Historical checks must use registry indexes first; do not crawl old output folders by default.
- Forbidden sources for normal create-ad runs: `generated_image/v*/prompt_task_*.txt`, `generated_image/v*/task_*.json`, `generated_image/v*/batch_run_summary.json`.
- Forbidden behavior for normal create-ad runs: progress narration blocks like "Explored X files", "Read ...", and step-by-step planning chatter.
- Output contract: return only (a) files created/updated, (b) execution result, (c) blockers/errors with exact fix needed.

Input parsing rules:
- Persona input accepted as one value for all formats, or format-wise values.
- Headline input accepted as custom per format or one master line.
- Format input accepted as single or multiple from: HERO, BA, TEST, FEAT, UGC.
- If format is not provided, default to all 5 formats.

Defaults when missing:
- Headline mode default: AI-generated fresh headlines.
- Persona default: random selection from persona library (1-26), format-wise.
- Randomization rule: avoid repeating the same persona across formats in the same batch when possible.
- If registry history exists, avoid persona+format combinations that appear in the recent dedupe window.

Generation sequence:
Step 1 - Parse user-provided persona/headline/format values.
Step 2 - Fill missing fields from defaults.
Step 3 - Generate copy for requested formats.
Step 4 - Background slot selection.

Step 4 — Background slot selection
- Check registry for recently used slots per selected format.
- Select an unused slot automatically and state which one was selected.
- User may override by requesting a specific slot number.
- For catalog selection, read/update `indexes.slot_exhaustion_tracker.<FORMAT>` (used/remaining/cycle_number).

Step 4.25 — Safe-zone enforcement (mandatory)
- Every background must be treated as a *safe-zone controlled* scene: keep key subject and all important copy away from edge risk bands.
- Use the structured background catalog in `background_variant.json` because it includes safe-zone control fields:
  - `composition`, `layout_intent`, `cta_safe_space`, `crop_safety`
- When you pick a background slot `BG-XXX`, generate a *seeded* background prompt so the safe-zone phrasing is deterministic per batch.
  - Selection order (mandatory):
    1) Pick an *unused* background slot for the requested format from `AD_GENERATION_REGISTRY.JSON` → `indexes.slot_exhaustion_tracker.<FORMAT>.remaining_slots_current_cycle` when present.
    2) If that list is empty/missing, pick a slot not in `indexes.backgrounds_by_format.<FORMAT>` recent window; if still not possible, fall back to any valid slot but state it explicitly.
  - Seed rule (mandatory, deterministic): `SEED = (BATCH_NUMBER * 1000) + (PERSONA_NUMBER * 10) + VARIATION`
    - Example: batch `v7`, persona `7`, variation `1` → seed `7071`
  - Use the active prompt assembly pipeline to build the seeded background sentence from slot + seed.
  - Paste the resulting *seeded* sentence into Section 8 (VISUAL DIRECTION BLOCK) and keep the selected `BG-XXX`.
  - Add a line in Section 8: `Seed: <SEED>` so reviewers can verify deterministic seeding was applied.
  - MANDATORY CHECKPOINT: Section 8 must contain the full seeded background sentence, not just the seed number. If your prompt only says "Seed: <SEED>" without the seeded background sentence, it is INVALID — regenerate immediately.
  - ALSO: Extract and include these 4 safe-zone fields from background_variant.json into Section 8: composition, layout_intent, cta_safe_space, crop_safety. All 4 must be present in the final prompt.
  - Store both `BG-XXX` and `<SEED>` in the registry notes (or dedicated fields) so the background prompt can be reproduced later.
- Never “fix safe-zones after the fact” by rewriting the prompt mid-run; safe-zone rules must be present in the prompt *before* the API call.

Step 4.5 - Text dedupe gate (mandatory)
- Before finalizing output text, check registry `indexes.used_text` for headline/support/CTA/caption/bullets in both EN and HI.
- If any text string already exists in index, regenerate that text until unique.
- Never reuse any previously used text string.
- Near-duplicate guard (mandatory): reject copy that is structurally too similar to recent outputs even if exact words changed.
  - Reject if same first 4-token opening pattern appears in recent outputs for the same format/language.
  - Reject if headline/support skeleton matches a recent pattern with minimal lexical change.
  - Reject if 2 or more bullets reuse the same verb-led template as recent outputs in that format.
  - Reject if same-persona same-batch outputs reuse the same trigger scenario or objection source line across formats.
  - Reject if same-persona same-batch outputs share the same 2-4 word motif in headline/support across formats.
- Diversity tag gate (mandatory): assign and validate tags before finalizing text:
  - `opening_pattern_4tok_en` / `opening_pattern_4tok_hi`
  - `copy_skeleton`
  - `hook_structure_class`
  - `proof_style_class`
  - `cta_voice_class`
- If tag combination violates matrix or recent-window rules, regenerate copy and re-tag before proceeding.

Step 4.75 - Validation checklist gate (mandatory before writing `output/vN/*`)
- The generator must run this checklist and pass all checks before writing any `OUTPUT_<FORMAT>_<LANG>.txt` file.
- If any check fails, block write, report failed check IDs, regenerate/fix, then re-run checklist.
- Hard fail policy: if any `CHK-*` fails, do not write partial batch files. Regenerate until all prompts in the requested batch pass.

Validation checklist (machine-checkable):
- `CHK-01` sections_present: all 9 required sections exist (Section 1 through Section 9).
- `CHK-02` section_depth: each section meets minimum bullet/line depth defined in Section 12.
- `CHK-03` copy_units: required copy units exist for the format (headline/support-or-quote/CTA/bullets rules).
- `CHK-04` text_budget: on-image copy word count falls within format budget.
- `CHK-05` exact_text_uniqueness: no exact string reuse against `indexes.used_text`.
- `CHK-06` structural_uniqueness: near-duplicate guards pass (opening pattern, skeleton, bullet-template checks).
- `CHK-07` diversity_tags_present: all 5 diversity tags are assigned and valid.
- `CHK-08` diversity_matrix_pass: batch-level diversity constraints are still satisfied after this ad is added.
- `CHK-09` background_traceability: prompt contains `Background slot: BG-XXX`, `Seed: <SEED>`, seeded sentence, and all 4 safe-zone fields.
- `CHK-10` product_lock_lines: product lock block includes required fidelity constraints and absolute visual truth line.
- `CHK-11` language_purity: EN is fully English and HI is Devanagari-only unless mixed script is explicitly requested.
- `CHK-12` script_execution_evidence: when script-based steps are required, command output evidence exists (no simulated results).
- `CHK-13` symbol_hygiene: headline/support/CTA contain no banned decorative symbols (`*`, `-`, `_`, `|`, `~`, `#`, `@`, `/`, `\\`).
- `CHK-14` version_progression: write target is next available `vN` (max existing + 1), never overwrite existing batch folder.
- `CHK-15` registry_target_file: updates are applied to root `AD_GENERATION_REGISTRY.JSON` only; no alternate/new registry files created.
- `CHK-16` text_placement_hierarchy: headline in top band, support below headline, CTA in lower safe band; headline never in lower-third.
- `CHK-17` no_disclaimer_default: no Disclaimer line in on-image copy unless user explicitly requests disclaimer mode.
- `CHK-18` no_forbidden_source_scan: normal create-ad run does not read old generated-image task artifacts (`generated_image/v*/prompt_task_*.txt`, `task_*.json`, `batch_run_summary.json`).
- `CHK-19` no_progress_chatter: final response contains no internal exploration narration ("Explored", "Read", "I will now...").
- `CHK-20` fresh_composition_only: final copy is freshly composed from the persona 5-field model plus approved product/mechanism chunks and passes structural uniqueness checks.
- `CHK-21` same_persona_batch_angle_separation: when one persona appears in multiple formats in a batch, each format uses a distinct sub-angle source and passes motif non-repetition checks.
- `CHK-22` english_copy_no_hinglish: for `OUTPUT_*_EN.txt`, on-image copy fields (headline/support/quote/bullets/CTA/trust line/attribution) must not contain Hindi/romanized-Hindi phrasing.
- `CHK-23` no_workstation_props_default: unless explicitly requested, do not include workstation-heavy props (keyboard, laptop, monitor, mouse) in visual direction blocks.
- `CHK-24` support_line_outcome_anchor: support lines must not be generic routine-only language; they must connect approved mechanism logic to weight-loss / excess-weight / obesity-reduction intent using compliant wording.
- `CHK-25` safe_zone_rules_present: prompt must include the correct safe-zone rule set for the selected canvas ratio before write.
- `CHK-26` critical_elements_inside_safe_zone: headline, support line, CTA, logos, and product-signaling elements must stay out of declared top/bottom/side risk bands for 4:5 and 9:16 outputs.
- `CHK-27` concept_fields_present: each ad declares `awareness_stage`, `concept_angle`, and `concept_structure` or has them inferred from `copy_requirements.concept_variation` before assembly.
- `CHK-28` reserved: concept-combo rotation is a writing/operation instruction, not a machine-blocking validation gate.
- `CHK-29` reserved: 4U is a writing instruction for the AI/operator, not a machine-blocking validation gate. Do not fail prompt assembly solely because a generated headline uses words outside a scorer vocabulary.

Operational examples of CHK-22 failure (reject and regenerate):
- `Raat ko control nahi ho raha?`
- `Subah wale regret se mukt`
- `Try Karein`

Checklist result contract:
- Emit a compact status object per prompt: `{"status":"pass|fail","failed_checks":[...],"format":"...","language":"..."}`.
- Only `status=pass` prompts may be written to `output/vN/*`.

Step 5 — Final output and storage
- Deliver final prompts in both EN and HI for selected format(s).
- Compute next version before writing: scan `output/` for existing `vN` folders and set `N = max(existing) + 1`.
- Save prompts under `output/vN/` using that computed `N`.
- Never rewrite an older version folder when user asks "create ads".
- If user explicitly asks to reuse an existing `vN`, keep folder but write new files with `_V2`, `_V3`, etc.
- File naming per format/language/variation:
  - `OUTPUT_<FORMAT>_EN.txt`
  - `OUTPUT_<FORMAT>_HI.txt`
  - For multiple variations: `OUTPUT_<FORMAT>_EN_V2.txt` (and same pattern for HI)
- State the background slot used and log it to registry (`mode.write_enabled: true`).

Step 5.5 — Prompt assembly for API
- For each API job, build one complete prompt only:
  - Start with `input/startingprompt.txt`
  - Then append exactly one generated prompt file body
- Never merge multiple format prompts into one API call.
- Traceability: save the *composed* prompt (startingprompt + generated prompt) under `generated_image/vN/<format>-<language>/prompt_task_<taskId>.txt`.

Step 5.6 — Image generation (Gemini Web automation)
- Triggered from Dashboard:
  - `/api/runs/{id}/generate-images-45` — generates 4:5 images in Gemini.
  - `/api/runs/{id}/generate-images-916-from-45` — generates 9:16 from 4:5 references.
- Images are generated via `scripts/gemini_web_automation.py` using Playwright + Gemini Web.
- Reference images are uploaded from `input/images/` to each Gemini chat session.
- For 9:16 conversion, the 4:5 output image is used as the single visual reference.
- Prompt integrity is verified (98%+ character match) before sending.
- Generated images are saved under `generated_images/v{N}/GEMINI_4_5/` and `generated_images/v{N}/GEMINI_9_16/`.
- Mandatory execution note:
  - Never claim generation success without confirmed saved file paths from the automation run.

Step 5.7 — Generated image storage
- Save results in `generated_image/vN/<format>-<language>/`
- Examples:
  - `generated_image/v1/feat-en/`
  - `generated_image/v2/hero-en/`
- Keep task metadata JSON in same folder for traceability (`task_<taskId>.json`).
- Also keep the composed prompt file in the same folder (`prompt_task_<taskId>.txt`).

Step 6 - Registry write (mandatory in production)
- Registry file handling rule (mandatory):
  - Always read and update root `AD_GENERATION_REGISTRY.JSON`.
  - "Create registry" means initialize missing keys in this file, not creating a new registry file.
  - Do not create alternate names like `AD_GENERATION_REGISTRY_V2.JSON`, `registry.json`, or per-run registry files.
  - If file exists, perform read-modify-append only; never reset `entries`.
- Append full entry to `entries`.
- Append `{entry_id, timestamp, background_slot, background_source}` to `indexes.backgrounds_by_format.<FORMAT>`.
- If background_source is `catalog`, update `indexes.slot_exhaustion_tracker.<FORMAT>` for cycle progression.
- Append every used text string to `indexes.used_text` buckets.
- Append diversity tags to `indexes.copy_patterns` buckets (`recent_opening_4tok`, `recent_skeletons`, `recent_cta_voice`, `recent_hook_structure`, `recent_proof_style`).
- Keep append-only behavior; do not delete or rewrite past records.

Generated image task logging (mandatory):
- Every `task_<taskId>.json` and `batch_run_summary.json` job record must include prompt metadata:
  - `persona_name`
  - `persona_number`
  - `background_slot`
  - `background_title` (if available)
  - `seed`
  - `seeded_background_prompt`
  - `headline`
  - `support_line`
  - `cta`
- Missing any of the above should be treated as logging failure and fixed before final handoff.

Default behavior if user gives minimal input:
- Generate default starter batch immediately using Section 0 defaults.
- Use AI-generated fresh headlines.
- Use all 5 formats unless user narrowed scope.

Prompt quality upgrade block (add by default):
- "Typography must be pin-sharp. If any text appears soft, blurry, or smeared, regenerate."
- "Keep text count minimal and increase font size rather than packing more copy."
- "Use clean sans typography with strong stroke clarity; no thin/light weights for body text."
- "Use Poppins only for on-image text: Headline in Poppins Bold, support/CTA in Poppins Medium or Regular."

Mandatory visual-direction block (add by default):
- "Describe scene, subject, and props explicitly (what is visible in frame)."
- "Define camera framing (close/medium/wide), angle, and product placement zone."
- "Define lighting style (soft daylight/warm indoor), shadow direction, and contrast level."
- "Define background behavior (clean, low-noise, non-distracting, premium texture)."
- "Define realism constraints (natural skin, correct hand anatomy, true-to-life proportions)."
- "Define what to avoid visually (stock-template look, clutter, random stickers, noisy overlays)."

---

## 15A) Quick Ideation Mode

Purpose:
- Use this mode only when the user asks for headline ideas, concept routes, hooks, or brainstorming before production.
- This mode generates creative options without assembling image prompts.

Input model:
- Pick 1 `awareness_stage` from Section 6A.
- Pick 1 `concept_angle` from Section 7.
- Pick 1 `concept_structure` from Section 7.
- Optional: pick persona and format.

Output model:
- Generate 5-10 headline/support-line pairs.
- Each option must include the chosen awareness stage, concept angle, concept structure, and one sentence explaining the test hypothesis.
- Do not write to `output/vN/`.
- Do not select backgrounds.
- Do not call image APIs.
- Do not append to registry unless the user promotes an option to production.

Promotion to production:
- When the user selects an ideation option, convert it into the normal production copy payload.
- Carry forward `awareness_stage`, `concept_angle`, and `concept_structure`.
- Run all production dedupe and registry checks before prompt assembly.

---

## 16) Quality Guardrails

Reject and regenerate if any one fails:
- Text not fully readable
- Text appears blurry, soft, or smeared
- Product label modified in any way
- Generic template look
- Anatomy issues (for UGC hands)
- Cluttered layout or low hierarchy
- High text density on canvas

## 16A) Text budget guardrails (to prevent clutter)

Use strict on-canvas text budgets.

Balance rule:
- Text-light is required. Text-empty is not acceptable.
- Every ad must communicate offer + mechanism/proof + action, not just a headline.

Global rules:
- Never place paragraph captions on-image unless user explicitly asks.
- Prefer short headline + short support line + CTA.

- If bilingual is needed, generate separate EN and HI creatives (no mixed-language text in one creative).

Recommended on-image text limits by format:
- HERO: 18-32 words
- BA: 22-36 words
- TEST: 24-40 words
- FEAT: 26-42 words
- UGC: 16-26 words

Minimum required on-image copy units by format:
- HERO: headline + 1 support line + CTA
- BA: headline + 2-3 short bullets + CTA
- TEST: headline/quote + attribution + trust line + CTA
- FEAT: headline + 3 feature-benefit bullets + CTA
- UGC: headline + 1 short support line + CTA

UGC default text policy:
- One headline
- One short support line
- One CTA
- No bullets or long paragraph quote unless requested

Text budget repair commands:

If output is too empty:
- "Keep same visual concept, add one concise support line and one trust/proof line while preserving clean spacing and readability."

If output is copy-heavy:
- "Regenerate same concept with minimal text: remove paragraphs and bullets; keep only headline and CTA. Maintain product fidelity and composition."

Visual quality repair commands:

- "Keep same layout, improve text sharpness and hierarchy only."
- "Keep same composition, restore exact product packaging from uploaded references."
- "Keep concept, reduce clutter and increase premium spacing."
- "Regenerate with crisp text edges, higher contrast, larger font sizes, and fewer words on canvas."

## 16B) Visual specificity guardrails (to improve image quality)

If image quality feels generic or under-directed, increase visual specificity in prompts.

Always specify these fields:
- Scene context: room type, time of day, mood
- Subject profile: age band, role context, expression, pose
- Product placement: hand-held / table-top / foreground-right / centered-lower
- Camera: lens feel (phone-like vs editorial), crop, perspective
- Lighting: source direction, softness, color temperature
- Material cues: tabletop texture, wall texture, fabric realism
- Depth: foreground-midground-background separation
- Brand palette usage: where accent colors appear and where they should not

Hard reject conditions:
- Subject looks stock-photo generic
- Product visually competes with busy background
- Scene lacks narrative cue for selected persona
- Composition has no clear focal hierarchy

## 16C) Prompt compression prevention

Goal: stop quality drift from prompt shortening across turns.

Rules:
- Do not summarize the final generation prompt unless user explicitly asks for a short version.
- Keep technical constraints explicit even in later iterations.
- On revisions, edit only the requested part and keep the rest of the full prompt unchanged.
- Preserve all guardrail lines during rewrites (product fidelity, text budget, sharpness, negative constraints).
- Preserve per-section depth minimums during rewrites.

Optional delivery mode:
- Provide two blocks when needed:
  - Full Production Prompt (default)
  - Short Operator Note (optional)
- The image model should always receive the Full Production Prompt.

Quick repair prompts:
- "Keep same layout, improve text sharpness and hierarchy only."
- "Keep same composition, restore exact product packaging from references."
- "Keep concept, reduce clutter and increase premium spacing."
- "Regenerate with crisp text edges, higher contrast, larger font sizes, and fewer words on canvas."

---

## 17) Compliance and Trust Notes

- Avoid fear tactics and fake urgency.
- Losing a sale is better than losing trust.

---

## 18) New Chat Setup (copy-paste starter)

Paste this in every fresh chat:

```
You are generating static ad creatives for Obesity Killer Kit.
Follow these rules strictly:

- Use only claims from my knowledge base (productinfo).
- Never invent mechanisms, results, or medical claims.
- Use uploaded Obesity Killer product packshot images as absolute visual truth. I will upload 6 images each session.
- Create ads in 5 formats: HERO, BA, TEST, FEAT, UGC.
- For each ad, use one persona input block (pain, desire, friction, proof needed, tone cue).
- Build persona inputs from the selected persona and the 5-field model (pain, desire, friction, proof needed, tone cue).
- Headline must do two things: scroll stop + pain-solution fit.
- Every ad must use one concept path: awareness_stage + concept_angle + concept_structure.
- Awareness stages are unaware, problem_aware, solution_aware, and product_aware. If missing, infer from persona pain/friction/proof needed.
- Concept angles are pain_point, desired_outcome, customer_scale_70k, authority, story, curiosity, comparison, and offer.
- Concept structures are pas, bab, fab, and four_us.
- Use the 4U lens while writing headlines: Useful, honestly Urgent, Unique, and Ultra-specific. This is an AI/operator writing guideline, not a machine-scored blocker.
- Caption must increase Value = (Dream Outcome x Likelihood) / (Time Delay x Sacrifice).
- Keep language simple, active, specific, and short.
- Reject generic templates; prioritize clear hierarchy and premium composition.
- Default production output: 1 variation per format.
- Always provide English + Hindi versions of final prompts and save them in `output/vN/`.
- Use plain conversational punctuation in on-image copy. Do not use decorative symbols like `*`, `-`, `_`, `|`, `~`, `#`, `@`, `/`, `\\` in headline/support/CTA.
- Do not add disclaimer lines on-image unless I explicitly ask for disclaimer mode.
- Never rely on fixed headline banks; generate fresh headlines each request.
- Keep typography pin-sharp; regenerate if any on-image text is blurry.
- Use Poppins font family for all on-image text (Headline: Poppins Bold; body/support/CTA: Poppins Medium or Regular).
- Select a background slot from the Background Variation Engine (Section 9 of playbook) using exhaustive format-wise rotation (no repeat until all allowed slots for that format are used once). State which slot you selected.
- Enforce strict text uniqueness: headline/support/CTA/caption/bullets in EN and HI must never repeat any previously used string.
- Check the registry at `AD_GENERATION_REGISTRY.JSON` before generation to avoid persona, angle, and background repetition.
- Track concept combos in the registry; use them as creative guidance to avoid repeating the same awareness_stage + concept_angle + concept_structure + format in the recent window.
- Registry is in production mode. Write one entry after each generation.
- When user says "create ads", write to next available `output/vN/` (max existing + 1). Never overwrite old version folders.
- "Create registry" or "update registry" must target existing root `AD_GENERATION_REGISTRY.JSON` only.
```

Optional add-on line:
```
When I ask for ad creation with no specific inputs, generate the default starter batch directly (no clarification round). If I provide persona/headline/format, apply those inputs directly.
```

---

## 19) Practical Day-to-Day Usage

1. Open this playbook and copy the New Chat Setup block (Section 18).
2. Paste it in a fresh Gemini or assistant chat.
3. Upload all 6 product packshot images to Gemini Web.
4. Start with: "Create ad."
5. If no inputs are provided, generate default starter batch immediately.
6. If partial/specific inputs are provided, apply them and fill missing fields from defaults.
7. Run Gemini image generation via Dashboard for 4:5 and/or 9:16 outputs.
8. Store generated images in `generated_image/vN/<format>-en/` (or `-hi` only when requested).
9. Use repair commands (Section 16) if quality fails.
10. In production (`mode.write_enabled: true`), append each generation to `entries` and update indexes.

How to avoid repetition at scale:
- Never reuse old text directly (headline, support line, CTA, caption, bullets).
- Recompute all copy every generation from persona fields and approved claims.
- Use registry for both angle-level dedupe and strict text-level dedupe.
- Use registry for concept-combo awareness and creative guidance: awareness_stage + concept_angle + concept_structure + format.
- Rotate catalog background slots with exhaustive format-wise cycles (no repeat until pool exhaustion, then reset cycle).

---

## 20) Registry File — Current State

File path: `AD_GENERATION_REGISTRY.JSON`

Current mode: production (`mode.write_enabled: true`)

Starting state example (append entries in live production):

```json
{
  "mode": {
    "phase": "production",
    "write_enabled": true,
    "last_updated": null
  },
  "entries": [],
  "indexes": {
    "backgrounds_by_format": {},
    "slot_exhaustion_tracker": {},
    "used_text": {},
    "concept_combos": {
      "recent": []
    },
    "copy_patterns": {
      "by_format_language": {},
      "recent_opening_4tok": {},
      "recent_skeletons": {},
      "recent_cta_voice": {},
      "recent_hook_structure": {},
      "recent_proof_style": {}
    }
  }
}
```

Registry indexing requirement (production):
- Maintain `indexes.backgrounds_by_format` for per-format background tracking.
- Maintain `indexes.slot_exhaustion_tracker` for catalog cycle state per format.
- Maintain `indexes.used_text` for global text uniqueness tracking.
- Maintain `indexes.concept_combos.recent` for recent concept path tracking.
- Maintain `indexes.copy_patterns` for structural diversity tracking (opening patterns, skeletons, hook/proof/CTA classes).
- Treat every string in `indexes.used_text` as permanently blocked from reuse.

Production live rules:
- Keep `mode.write_enabled` true.
- Set `mode.last_updated` to current timestamp on every write.
- Append one entry per generation to `entries`.
- Append one concept-combo record per generation to `indexes.concept_combos.recent`.
- Never overwrite existing entries — append only.
- Read registry before every generation to check deduplication rules.
