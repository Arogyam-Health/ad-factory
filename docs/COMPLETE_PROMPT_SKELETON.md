# Complete Prompt Skeleton — Structured & Reference, Copy & Image, Browser

This doc is the single source for **every prompt the system sends**. It shows the exact skeleton for each flow, which file supplies each block, which code assembles it, how to edit that file, and what breaks if you change keys. The live guide at `/guide` and `/docs/STRUCTURED_COPY_SYSTEM.md` are subsets of this file.

If you have never seen Ad Factory before, read top to bottom and you can edit any layer safely.

---

## 1. System map — where prompts live

| Prompt | Engine | Code that builds it | Template / config files | Transport |
|---|---|---|---|---|
| **Structured Copy LLM** | OpenCode / Google Gemini / Browser (ChatGPT/Gemini) | `dashboard/backend/services/render_structured_copy.py:590 assemble_copy_llm_request` + `dashboard/backend/services/copy_system.py` | `dashboard/backend/copy_system/*.json` (12 files), `persona_seeds.json`, `concept.json`, `product_master_doc`, `copy_starting_prompt` | API `POST {api_url}/chat/completions` via `provider_relay` (local agent), or `browser_copy.py` CDP |
| **Structured Image (4:5)** | Local Chrome (ChatGPT/Gemini tab) via `structured_browser.py` | `dashboard/backend/services/generate_ads.py:685 render_prompt` + `dashboard/backend/copy_system/prompt_assembler_templates.json` | `prompt_assembler_templates.json`, `background_variant.json`, `persona_seeds`, copy blocks from LLM, `starting_prompt` (global product rules), `conversion_916_prompt` (9:16 only) | `local_agent_runtime/structured_browser.py:680` → `LocalScriptBrowser` → `chatgpt_web_sutomation.py` / `gemini_web_automation.py` CDP |
| **Structured Image (9:16)** | Same local Chrome, derived from 4:5 | `local_agent_runtime/structured_browser.py:734` (conversion) | `conversion_916_prompt` + `starting_prompt` + `9:16` bounded context + same `prompt_assembler_templates` | Same as 4:5 but `prompt_content = starter + conversion_body + bounded_context` |
| **Reference Image (4:5)** | Same local Chrome | `local_agent_runtime/reference_workflow.py:255 _put_prompt` | `reference_starting_prompt`, `product_document`, `persona_seeds` (as JSON), `reference_images` + `comment`, `creative_concept` | `reference_workflow.py:680` → same browser scripts, one tab per `run_id` |
| **Reference Image (9:16)** | Same, derived | `reference_workflow.py:546` | `conversion_916_prompt` + `starting_prompt` already in 4:5 source | Same |

**Rule:** Copy LLM never sees image keys (`background_group_key`, `share_background_across_personas`, visual layout). Image prompt never sees copy LLM `product_truths`/`requirements` as separate keys — product truths stay inside `product_document` / `starting_prompt` text.

---

## 2. Structured Copy LLM — full skeleton (transparent)

This is what `render_structured_copy.py:590` sends to `opencode.ai/zen/v1/chat/completions` (or Gemini `v1beta/models/...:generateContent`, or Browser warmup+chunk). Every key below comes from a file — see §4 table. Empty string/list/dict blocks are kept if the file explicitly contains them (see §7).

```json
{
  "task": "Generate structured advertising copy as JSON", // ad_guardrails.json: task
  "starting_prompt": "copy_starting_prompt if non-empty", // config key copy_starting_prompt (separate from image Starting Prompt)
  "product_document": "product_master_doc if non-empty", // product_master_doc.txt
  "languages": [
    {
      "id": "EN", // from ad_languages.json key
      "label": "English", // ad_languages.json[EN].label — send every field in that object as-is
      "rules": ["Write fully English ad copy. ..."], // ad_languages.json[EN].rules — every rule string
      "persona_fallbacks": { "pain_en": "..." }, // every key under ad_languages[EN] is sent (not just label/rules)
      "persona_map": { "pain": "pain_en", ... } // every key under ad_languages[EN].persona_map
    }
  ],
  "guardrails": ["...every non-empty line from ad_guardrails.json always[]...", "NO HYPOTHESIS MODE... if hypothesis None"],
  "planned_ads": [
    {
      "format": {
        "id": "HERO", // from ad_formats.json key, uppercased, must match ^[A-Z][A-Z0-9_]{0,15}$
        "label": "Hero", // every field in ad_formats[HERO] as-is: description, skeleton, output_fields, plus any custom keys you add
        "description": "...from ad_formats...",
        "skeleton": "Headline / Support / optional trust / CTA",
        "output_fields": ["headline", "support_line", "trust_line", "cta"]
        // any extra keys you put in ad_formats[HERO] (e.g. "rules", "custom_instruction") are sent transparently — do not rename
      },
      "persona": {
        // TRANSPARENT: every key from persona_seeds.json entry as-is
        "persona_number": 3,
        "persona_name": "Stress Snacker",
        "core_pattern": "Stress, tiredness, irritation...",
        "primary_tags": ["hunger_craving_control", "..."],
        "common_indian_moments": "...",
        "failed_attempts": "...",
        "why_it_failed": "...",
        "relevant_ok_kit_role": "...",
        "guardrail": "...",
        "headline_anchor_rule": "...",
        // legacy aliases auto-added for backward compat (image prompt still expects them)
        "number": 3,
        "name": "Stress Snacker",
        "pain_en": "Stress, tiredness...",
        "desire_en": "Stress will still happen...",
        "friction_en": "Food becomes quick relief...",
        "proof_needed_en": "Do not claim cure...",
        "tone_cue_en": "Practical, empathetic..."
        // plus pain/desire/friction/proof/tone for generate_ads render_prompt
      },
      "hypothesis": {
        "type": "copy_framework", // from settings.hypothesis.type, must be one of HYPOTHESIS_FILES keys
        "style": "pas", // from settings.hypothesis.variant
        // every field from ad_frameworks[pas] as-is:
        "label": "PAS",
        "instruction": "...how to apply this test...",
        "definition": "...style writeup...",
        "skeleton": "Headline = ...",
        // plus every field from ad_frameworks._meta as-is: type_label, instruction, etc.
        "type_label": "Copy Framework",
        // any extra keys you add to ad_frameworks[pas] (e.g. "examples") are sent
      },
      "creative_concept": { "id": "Concept/iPhone_Notes", "label": "...", "description": "..." }, // from concept.json, appears once per planned_ad
      "format_pattern": { "id": "HERO_orange", "label": "Orange vibe" } // from copy_prompt_templates.json visual_archetypes[HERO][id] if visual_archetypes_by_format selected
    }
  ],
  "output_schema": { "ads": [{ "copy": { "EN": { "headline": "string", "support_line": "string", "trust_line": "string", "cta": "string" } } }] }
  // output_schema fields = format_layer(...).output_fields per planned_ad — any extra LLM keys are ignored, missing required (except trust_line) triggers one repair then fail
}
```

**Rules (enforced in `render_structured_copy.py:590` + `copy_system.py`):**
- Hypothesis `None` → omit `hypothesis` entirely. Do not send `concept_angle`/`desired_outcome` legacy keys.
- Format always `id` + every field in `ad_formats[id]` as-is (transparent). Unknown ids still send `{id}`.
- Creative concept once per `planned_ad`, not top-level.
- Persona: every key from `persona_seeds` entry as-is. Legacy `pain_en` etc. auto-derived from `core_pattern` etc. for `generate_ads` compatibility, but originals are kept.
- Languages: every field in `ad_languages[LANG]` as-is (not just `label`/`rules`).
- Hypothesis: every field in `ad_*` style entry + `_meta` as-is.

---

## 3. Structured Image (4:5) — full skeleton (what Chrome sees)

Built by `dashboard/backend/services/generate_ads.py:685 render_prompt` (cloud, for `4:5`) via `render_structured_copy.py:931 _prompts_from_copy_batch` which now prepends `image_starting_prompt = effective_config.get("starting_prompt")` at `1045`. Then `local_agent_runtime/structured_browser.py:680` writes `staging/structured-browser/<job_id>/<prompt_id>/45/<stem>.txt` via `_materialize:484` and spawns `chatgpt_web_sutomation.py` with `--run-id <run_id> --keep-run-tab` (per-run tab).

```
OBESITY KILLER KIT - GLOBAL PRODUCT RULES  ← starting_prompt (dashboard/backend/defaults/starting_prompt.txt / config starting_prompt) — MUST be first, every image
A0. OUTPUT COUNT - ABSOLUTE RULE
- Generate exactly one final image for each prompt. Do not create two options...
A1. PRODUCT LOCK - ABSOLUTE RULE
- Use the provided Obesity Killer product packshot images pixel-for-pixel...
...

PRODUCT LOCK BLOCK  ← prompt_assembler_templates.json: product_lock_block (every line as-is)
- Use the uploaded product packshot images as absolute visual truth.
...

PROOF BAR BLOCK
PROOF BAR / TRUST STRIP
- Add one proof bar near the lower safe field...
- Exact proof bar text: 70,000+ Users | 3-5 kg loss with 1 Kit | 100% Ayurvedic  ← prompt_assembler_templates.json: proof_bar_text (brand lock)
...

OUTPUT SPEC
- Canvas: 1080 x 1350 pixels. Portrait. 4:5 ratio.  ← output_spec_lines templated with canvas_spec, aspect_ratio, style_description, archetype_id/label
- Style: BA (before/after journey without body-shaming visuals).  ← style_descriptions[BA]
- Visual archetype: ba_classic_split (Classic vertical split contrast)  ← copy_prompt_templates.json visual_archetypes[BA][id]
- Full-bleed requirement: scene must reach all canvas edges; ...
...

FORMAT LAYOUT INSTRUCTIONS
- Archetype: strict left/right split with a clear vertical divider...  ← archetype layout_lines + direction_lines from prompt_assembler_templates / copy_prompt_templates
...

PERSONA INPUT BLOCK  ← prompt_assembler_templates.json: persona_lines (15 lines) filled via generate_ads.py:798 _fill with every persona field
- Persona: Stress Snacker (Persona 3)  ← persona_name + persona_number
- Core pattern: Stress, tiredness, irritation, or mental fatigue turns into food urges.  ← core_pattern
- Primary tags: hunger_craving_control, daily_tracker_data, coach_chat_call_support  ← primary_tags joined
- Common Indian moments: Ordering snacks after a bad workday...
- Failed attempts: Diet charts, calorie tracking...
- Why it failed: Food becomes quick relief, not just hunger...
- Relevant OK Kit role: Stress will still happen. OK Liquid helps...
- Guardrail: Do not claim cure for stress...
- Headline anchor rule: Must imply stress eating + weight loss.
- Pain: Stress, tiredness...
- Desire: Stress will still happen...
- Friction: Food becomes quick relief...
- Proof needed: Do not claim cure...
- Tone cue: Practical, empathetic...
- Concept angle: none
- Concept path is strategy only; do not render these labels on-image.  ← prompt_shell: concept_path_note

CONCEPT INPUT BLOCK  ← if creative_concept present
- Concept: iPhone Notes
- Description: ...

Create the ad in English.  ← prompt_shell: create_ad_line templated with language_labels[EN]

EXACT ON-IMAGE COPY - DO NOT ALTER ANYTHING
- Headline: From Fighting Cravings to Following a Clear Plan  ← copy block: headline/support_line/cta etc. per format (HERO: headline/support/cta, BA: headline + left 2 + right 2 + CTA, TEST: headline/trust/cta etc.)
- Left situation 1: Hunger and cravings...
- Left situation 2: ...
- Right shift 1: Stay on track...
- CTA: Start Your Guided Course
- Proof bar: 70,000+ Users | 3-5 kg loss with 1 Kit | 100% Ayurvedic
Render every character exactly as written. No paraphrasing...

NEGATIVE CONSTRAINTS
- Do not recreate or redraw any product.
...

QUALITY BAR - verify before accepting output
- All products present...

VISUAL DIRECTION BLOCK
- Background slot: BG-322 - Dining Table After Breakfast 8  ← background_variant.json: variants[slot].title + background_field_defaults
- Background seed: 1213104877
- Seeded background direction (single sentence, exact): Approved light palette background: a sunny dining table...  ← build_seeded_background_sentence: background + bg_seed + templates background_sentence
- Subject: Same adult subject identity appears in both panels...
- Action: Split action: BEFORE panel shows...
- Camera: Eye-level medium framing...
...

TYPOGRAPHY SHARPNESS BLOCK
- Headline: [Your Font] Bold...
...

SAFE-ZONE ENFORCEMENT (NON-NEGOTIABLE)
- Frame: 1080(length) x 1350(height) (4:5 aspect ratio).  ← safezone_45
- Restricted bands: top 10% (0-135px), bottom 15% (1148-1350px)...
...

[for 9:16 conversion only, appended after above]
[LOCAL BOUNDED CONVERSION CONTEXT]  ← structured_browser.py:817
prompt_id=prm_...
source_output_id=out_...
source_output_version=1
source_creative_sha256=...
conversion_prompt_resource_id=res_...
conversion_prompt_version=1
target_aspect_ratio=9:16
```

**File → prompt key map (image):**

| Prompt block | File | Code that adds it |
|---|---|---|
| `OBESITY KILLER...` global rules | `starting_prompt` (`dashboard/backend/defaults/starting_prompt.txt` / config `starting_prompt`) | `render_structured_copy.py:1045` prepend + `structured_browser.py:771` fallback for old prompts + `9:16` `844` before conversion |
| `PRODUCT LOCK BLOCK` | `prompt_assembler_templates.json: product_lock_block` (every line as-is) | `generate_ads.py:740` |
| `PROOF BAR` `proof_bar_text` | `prompt_assembler_templates.json: proof_bar_text` | `generate_ads.py:783` `proof_bar_text(T)` |
| `OUTPUT SPEC` | `prompt_assembler_templates.json: output_spec_lines` templated + `background_field_defaults` | `generate_ads.py:781` |
| `FORMAT LAYOUT` | `copy_prompt_templates.json: visual_archetypes[fmt][id].layout_lines/direction_lines` + `prompt_assembler_templates: ba_panel_anchors` | `generate_ads.py:706` |
| `PERSONA` | `persona_seeds.json` every key as-is + `prompt_assembler_templates.json: persona_lines` (15 lines) | `generate_ads.py:798` `_persona_fill` |
| `CONCEPT` | `concept.json` / `copy_prompt_templates.json` if `selected_concept` | `render_structured_copy.py:395` + `generate_ads.py:812` |
| `EXACT ON-IMAGE COPY` | Copy LLM `copy[LANG]` per `output_fields` | `generate_ads.py:710` `copy_lines` |
| `NEGATIVE` `QUALITY` `VISUAL` `TYPOGRAPHY` `SAFEZONE` | `prompt_assembler_templates.json` every block as-is | `generate_ads.py:825` |
| `Background sentence` | `background_variant.json` + `prompt_assembler_templates: background_sentence` | `generate_ads.py:626 build_seeded_background_sentence` |

---

## 4. Reference Image (4:5 & 9:16) — skeleton

Built by `local_agent_runtime/reference_workflow.py:255 _put_prompt` — **already transparent** (`json.dumps(persona)`). No copy LLM.

```
{starting_prompt}  ← reference_starting_prompt (config_file, not copy_starting_prompt)
TARGET PERSONA:
{ "persona_number": 3, "persona_name": "Stress Snacker", "core_pattern": "...", ...every key from persona_seeds entry as-is... }

TARGET CONCEPT:
{ "id": "Concept/...", "label": "...", "description": "..." }  ← if concept selected (_resolve_creative_concept:17)

REFERENCE INSTRUCTION:
{comment file text}  ← reference comment resource, if attached

PRODUCT DOCUMENT (SOURCE OF TRUTH):
{product_master_doc or reference product_document}  ← product_document resource

Create one exact 4:5 portrait ad using the first uploaded image as the visual reference and only the subsequently uploaded product resources.
Create the ad in English.

[for 9:16 only, built at reference_workflow.py:546]
{conversion_916_prompt text}
[LOCAL BOUNDED CONVERSION CONTEXT]
prompt_id=prm_...
source_output_id=out_...
...
target_aspect_ratio=9:16
```

Difference from Structured: reference packs persona as raw JSON, not persona_lines. Changing `persona_seeds.json` keys immediately appears in `TARGET PERSONA:` block — no code change.

---

## 5. Browser automation — image generation skeleton (what CDP tab receives)

`LocalScriptBrowser.generate:154` → `chatgpt_web_sutomation.py` / `gemini_web_automation.py` with `--run-id <run_id> --keep-run-tab` (per-run tab pool `browser.py:272 get_or_create_run_tab` file `browser/run_tabs/<run_id>.json`).

Per **image** (one subprocess per `prompt_path`):

1. `build_browser_context:720` → `connect_over_cdp:9222` → `mark_cdp_attached` (`browser.py:122`)
2. `get_or_create_run_tab(context, run_id)` → `Target.createTarget` (`newWindow=false` Windows) or reuse existing run tab (file `targetId`); `page._ad_factory_run_id=run_id`
3. `navigate_to_fresh_chat` → `about:blank` → `https://chatgpt.com/` → wait `manual_login_timeout`
4. `select_model_and_tool_if_requested` (Instant / Create image)
5. `upload_images(page, [C:\...\staging\...\0001.png, ...])` → CDP `DOM.setFileInputFiles` with Windows `C:\...` paths (resolved, `copy_to_windows_temp` for WSL)
6. `set_prompt_text` → `full_prompt` = `starting_prompt + "\n\n" + {image prompt above}` (chatgpt `full_prompt = prepend_starting_prompt` `3224`; gemini `prepend_starting_prompt` `3616`)
7. `click_send_and_confirm` → `wait_for_generated_image` → `download_generated_image` → `out_dir/.browser_downloads/<run_id>/`
8. Write `out_dir/result.json` `{"output_path": ".../HERO_..._4_5.png", "raw_output_path": "...raw.png"}` → `structured_browser.py:242` reads, `commit_output:580` stores `output_image` + `output_raw`.
9. **Keep run tab**: if `--keep-run-tab` then NOT added to `job_pages`, so `release_browser:340` leaves it open; parent `StructuredBrowserExecutor:990` finally `close_run_tab_via_cdp(run_id)` after all 20 images + `ensure_keepalive_window:308` keeps one empty `about:blank` (tagged `keepalive`) so Chrome never shows `no browser is open`.

**One tab per `run_id`**: first image of `run_A` creates `TargetId_A`, next 19 images of `run_A` (even though each is a new subprocess) find `browser/run_tabs/<run_A>.json` → `targetId_A` → `_known_pages` → reuse same Page. Concurrent `run_B` (second job thread `local_agent.py:88 MAX 5`) creates `TargetId_B` — Chrome now has `TargetId_A` (run_A), `TargetId_B` (run_B), plus keepalive = 3 tabs in one window (Windows) or 3 windows (Linux `newWindow=true`). 6th run stays `pending` in `agent/service.py:871 limit(5)` and `local_agent.py:124 leaving queued` until a slot frees.

---

## 6. How to edit each file — what to keep, what breaks if you rename

### `persona_seeds.json` (transparent now)
- **Location:** `persona_seeds.json` (bundled) or Studio → Config `persona_seeds` (Mongo). Read by `render_structured_copy.py:125 _persona_map` and `reference_workflow.py:229 _personas` and `generate_ads.py:642`.
- **Edit:** Add/remove any top-level key in a persona object — e.g. new `"seasonal_trigger": "Diwali..."` — it will appear in both LLM `planned_ads[].persona` and image `PERSONA INPUT BLOCK` automatically. **Do not rename `persona_number`/`persona_name`** — still used as `number`/`name` for `reserve_run_number` and `prompt_filename`. Numeric `persona_number` must stay `1..28` unique. `primary_tags` stays list of strings used only for display, not validation.
- **If you rename `core_pattern` → `pain_point`:** then `persona_prompt_values` legacy alias `pain<-core_pattern` will break unless you also add `pain_point` to `FALLBACK_PERSONA_SOURCE_MAP` or keep both keys for a migration. Safer to **add new key, keep old**.

### `dashboard/backend/copy_system/ad_forms.json` → `ad_formats`
- **Keys:** `description`, `skeleton`, `output_fields` (`["headline","support_line",...]`), `label` — but now **every key** you add (e.g. `"rules": "..."`) is sent in `format_layer` transparent. Changing `output_fields` changes `output_schema` and image `copy_labels` required fields — missing `trust_line` is optional, others required or one repair then fail.
- **Add format:** `{"NEW": {"label":"New","description":"...","skeleton":"...","output_fields":["headline","cta"]}}` → Studio chips appear, default visual archetypes stubbed, edit in `copy_prompt_templates.json`.

### `ad_languages.json` → `ad_languages`
- **Transparent:** every field under `EN`/`HI`/`HINGLISH` (including `_modes`, `_persona_source_map`, `persona_fallbacks`, `persona_map`, `rules`, `label`) is sent in `language_layers` per `copy_system.py:310`. Changing `rules` does not fail a run. Adding new language id needs matching `ad_languages._modes` entry and `persona_seeds` language-suffixed keys if you want localized persona.

### `ad_hooks.json` ... `ad_support_shapes.json` (11 hypothesis files)
- **Each style:** `{"label","instruction","definition","skeleton"}` plus any extra keys you add — all sent via `hypothesis_layer:459` transparent. `instruction/definition/skeleton` only attached when style file has them; missing keys omitted without error. Deleting a style id → Studio dropdown loses it; old runs with that id still send `type+style` only.

### `ad_guardrails.json`
- **Keys:** `task`, `repair_task`, `always` (list), `no_hypothesis`, `no_hypothesis_label/catalog` — `guardrails:415` sends `always` + `no_hypothesis` when `hypothesis None`; `copy_task:326` sends `task`. Adding new top-level key won't appear unless you also handle it in `guardrails()`.

### `concept.json` / `persona_seeds` are the only catalogs that attach as `creative_concept` / `persona` — concept appears **once** per `planned_ad` not top-level.

### `background_variant.json`
- **Structure:** `{"variants": [{"id":"bg-322","title":"Dining Table...","base":"...","formats":["HERO","BA"], "surface":...}]}`. Image prompt `background_sentence` built from `pick_background_slot` + `build_seeded_background_sentence:626`. Adding new `id` → Studio may not list it until `generate_ads.py:79` reloads; invalid `id` for a format falls back to bundled `BG-322`.

### `dashboard/backend/copy_system/prompt_assembler_templates.json` (image prompt shell)
- **Business lock:** `proof_bar_text`, `headline_bans` — keep wording, change only for new brand. Other keys are system layout — edit at most `proof_bar_text` `headline_bans` per `docs/STRUCTURED_COPY_SYSTEM.md:89`. `persona_lines` now 15 lines (core_pattern ... tone) — adding new line like `"- Seasonal trigger: {seasonal_trigger}"` requires `generate_ads.py:798` `_persona_fill` to have that key in persona (it does, transparent) and `persona_prompt_values` to pass it — already does. Changing `output_spec_lines`, `negative_constraints`, `safezone_45/916` directly changes image prompt text.

### `starting_prompt` vs `copy_starting_prompt`
- **`starting_prompt`** (`dashboard/backend/defaults/starting_prompt.txt`): **image only** — prepended to every image prompt (`render_structured_copy.py:1045` cloud `4:5` + `structured_browser.py:771` local `4:5` fallback + `844` `9:16` before conversion). Edit in Config `starting_prompt`.
- **`copy_starting_prompt`**: copy LLM only (`render_structured_copy.py:1127` `starting_prompt` for LLM). Do not swap.

### `copy_prompt_templates.json` visual_archetypes
- **Not sent to copy LLM.** Only after copy exists for image `visual_archetype_llm_prompt`. Changing `visual_archetypes[HERO][0].id` etc. needs matching `render_structured_copy.py:501` `format_visual_archetypes` else image uses default.

### `conversion_916_prompt` (config `conversion_916_prompt`)
- **9:16 only:** `structured_browser.py:810` `conversion_body` + `reference_workflow.py:546`. Changing it affects only `9:16` derived images. Keep short, no product rules (those are in `starting_prompt`).

---

## 7. What to take care of when changing files

- **Never rename `id`/`type`/`style` keys** in `ad_*` files — Studio dropdowns use `label`, but LLM uses `id`. Renaming breaks `output_schema` validation and `hypothesis_layer` `style` lookup.
- **Keep `output_fields` strings exactly** `headline/support_line/cta/trust_line/bullets/context_line/attribution` — image `copy_labels` and `parse_copy_block:533` only handle those; extra field needs new `copy_labels` entry and `format_output_fields` handling.
- **Do not add `null`** — `compact` was removed for persona/format/hypothesis but some callers still `compact` top-level (`assemble_copy_llm_request:603`, `format_layer` old). Empty `""` is now kept, but `null` still omitted. Prefer `""` to clear.
- **Clear a whole layer:** store `{}` inherits bundled; to send no styles store `{"_meta":{"label":"Hook Structure"}}` (non-empty, no styles).
- **Changing `persona_seeds` keys requires updating `prompt_assembler_templates.json: persona_lines` if you want the new key visible in image prompt** — otherwise it is still sent to LLM (transparent) but not rendered in image persona block. Add line `"- New key: {new_key}"` and ensure `generate_ads.py:798` `_persona_fill` has it (it does, via `for k,v in persona.items()`).
- **Changing `background_variant` `id`** needs matching `prompt_assembler_templates: background_sentence` pools or image falls back to `BG-322`.
- **Changing `proof_bar_text`** must stay `70,000+ Users | 3-5 kg loss with 1 Kit | 100% Ayurvedic` exactly for brand lock — image prompt `proof_bar_block` is templated.
- **Cross-file:** Adding new format `NEW` needs `ad_formats[NEW]` + `prompt_assembler_templates: style_descriptions[NEW]` + `copy_prompt_templates: visual_archetypes[NEW]` or images use `default_NEW`.

---

## 8. Tests that lock the skeletons

```bash
python -m unittest tests.test_copy_system_request tests.test_render_structured_pipeline tests.test_concept_catalog
# and
python -m unittest tests.test_browser_copy tests.test_frontend_control_plane_contract
# and Windows
python scripts/e2e_windows_playwright.py
```

What they lock: `HERO` vs `BA` description/skeleton/fields, hypothesis `None` no `hypothesis` key, `core_pattern` in persona still maps to `pain` for image fallback, `starting_prompt` prepended to image `4:5` and `9:16`, `get_or_create_run_tab` reuse, keepalive never zero.

---

## 9. Browser automation image skeleton (per run tab)

Same as §3 image skeleton, but now per `run_id` tab: `LocalScriptBrowser:154 --run-id <run_id> --keep-run-tab` → `chatgpt_web_sutomation.py:195 --run-id` → `get_or_create_run_tab` → same Chrome `TargetId` reused for 20 images, `navigate_to_fresh_chat` per image in same tab, `close_run_tab_via_cdp` only after `StructuredBrowserExecutor:990` `shutil.rmtree(job_staging)` + `reference_workflow:714` `close_run_tab_via_cdp`.

---

## 10. Out of scope

- Binding default framework to format, ranking product truths, auto-creating background slots for new format, changing local image agent beyond reading a locked pattern id — not handled here.

