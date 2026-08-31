# Config Edit Guide — Every File, Every Button, Every Flow

Use this when you want to change **any plate file** and be sure what travels, what breaks, and what to click in the editor. The short operator intro stays at `/guide` ( `docs/OPERATOR_PLATE_GUIDE.md` ); the full prompt skeletons live at `/docs/COMPLETE_PROMPT_SKELETON.md`. This file is the **how-to-edit** companion.

---

## 0. If you want to give the entire structure to an AI

Give it **these 5 things, order matters**:

1. **This guide** (`docs/CONFIG_EDIT_GUIDE.md`) + **`docs/COMPLETE_PROMPT_SKELETON.md`** — the skeleton tells *what* is sent, this guide tells *how* to change it.
2. **`dashboard/backend/copy_system/` (12 files) + `persona_seeds.json` + `concept.json` + `background_variant.json`** — the live plate. Prefer the **Mongo plate** the Studio shows (Personal `user:<id>` or Org `org_shared:<id>`), not just the bundled files on disk. Export via Config → History if you need the JSON.
3. **`dashboard/backend/copy_system/prompt_assembler_templates.json` + `dashboard/backend/copy_system/ad_formats.json` + `copy_prompt_templates.json`** — the image shell, format ids, and visual archetypes. Without them the AI will not know why `output_fields` or `persona_lines` are what they are.
4. **`dashboard/backend/services/copy_system.py` + `render_structured_copy.py:125` (`_persona_map`, `_hypothesis_from_settings`, `_planned_ads`) + `generate_ads.py:642` (`persona_prompt_values`/`render_prompt`)** — the only place that decides *transparent* vs filtered. After `ccc764f` persona is transparent (every key as-is) with legacy `pain_en` aliases.
5. **`local_agent_runtime/browser.py:272` (per-run tab pool) + `structured_browser.py:680`/`reference_workflow.py:255`** — how a run becomes a Chrome tab. Without this the AI will assume one image = one new Chrome window.

Do **not** give only `persona_seeds.json` and ask “fix persona”. The persona `core_pattern` → `pain` mapping lives in `copy_system.py:53` and `generate_ads.py:642`; the image `PERSONA INPUT BLOCK` lives in `prompt_assembler_templates.json:32`; the copy LLM `planned_ads[].persona` lives in `render_structured_copy.py:136`. Give all three or the AI will patch one and break the other.

---

## 1. How a config travels from one user to another

Ad Factory never copies bytes through Render. Only **metadata** lives in Mongo; bytes stay on the paired device.

### The 3 scopes (one Mongo document per scope)

| Scope | Mongo key | Who sees it | Where it is edited |
|---|---|---|---|
| **My Config** `personal` | `user:<your user_id>` | Only you | Studio chip “My Config” / Config when “My Config” selected |
| **Org individual** `org_individual` | `org:<org_id>:<your user_id>` | Only you inside that org | Studio chip “<Org> — Personal” |
| **Org shared** `org_shared` | `org_shared:<org_id>` | Every active org member | Studio chip “<Org>” / Config when org chip selected |

Switching the chip at the top of Studio/Config **switches the Mongo document**. There is no local overlay.

### 4 ways a config moves

1. **Copy to my config** (always allowed, any org member on any team plate). Config toolbar → `Copy to my config` modal → pick team plate → pulls that team’s `org_shared` document onto your `user:<id>` doc. Snapshots your personal plate first. Does not change the team plate.
2. **Copy to org** (needs `config-admin` on destination org). Config toolbar when viewing a plate → `Copy to org` → pick org → overwrites that org’s `org_shared` doc. Other members see it on next `/api/defaults?org_id=`.
3. **Share via org membership** — you don’t copy, you just invite the other user to the org (`Teams` → add member). Their Studio chip for that org then reads the same `org_shared` doc.
4. **Device-to-device replication** (when Org shared plate’s bytes live on another laptop). The *authority* device holds the only CMS bytes; a replica device must import an encrypted export: `POST /v1/configs/{logical_key}/replicas/export` on authority → transfer package + 32-byte secret on separate channel → `POST .../import` on replica → `PUT /api/local-config-references/{logical_key}`. Mongo only stores authority/replica metadata, not bytes. If no authority/replica is online, Studio shows *unavailable* rather than cloud bytes.

**What does NOT travel as a file:** `persona_seeds`, `ad_formats`, etc. are **fields inside one Mongo document** (`ad_formats` key holds the whole `ad_formats.json` object), not separate files. History → snapshots let you roll back.

---

## 2. Permissions — who can do what

| Role | How it is granted | Plate edit | Copy to org | Members | Org config | Super admin |
|---|---|---|---|---|---|---|
| **Guest** (not signed in) | — | read generic bundled plate only (`/guide` + `persona_seeds` bundled) | — | — | — | — |
| **Member** `org_members.status=active` | Teams → Add member (any config-admin or owner) | Edit **My Config** always; edit `org_shared` only if `config-admin` | No | Add members if config-admin | No | No |
| **Config-admin** | Teams → gear on member → toggle config-admin (org owner) | As member + overwrite `org_shared` via Save/ Copy to org | Yes | Yes | Yes | No |
| **Org owner** (creator) | First member | All of config-admin + remove org | Yes | Yes | Yes | No |
| **Super admin** | `SUPER_ADMIN_EMAILS` env on Render + `dashboard/backend/admin/admin_routes.py` | All plates, plus `/admin` → users/orgs/configs/audit/exports/readiness | Yes | Yes | Yes | Yes |
| **Personal only** | — | A user’s org_individual plate is private even inside the org; no other member sees it. |

Assign via `Teams` → member row → `config-admin` toggle. Removing a member does not delete the `org_shared` plate, only their `org_individual`.

Other uses of **Teams** besides config copy: shared `prompt_assembler_templates` background catalog, shared `concept` catalog, shared run numbering (`reserve_run_number` per `owner_type:org`), and `org_id` on `local-config-references` for device replication.

---

## 3. JSON editor — Field, List, Group, _meta

Every Config/Studio file modal has **Form** (field rows) and **JSON** (raw). `Form` disables if JSON is invalid — fix in JSON.

At the bottom of a group: `+ Add field` `+ Add list` `+ Add group`.

### Field — one name, one value
Rename the name, edit the value, or Delete the row.
- Used for: `ad_guardrails.task` (`Generate structured...`), `prompt_assembler_templates.proof_bar_text`, `concept[id].label`, any persona scalar (`core_pattern`, `guardrail`, `headline_anchor_rule`).
- Example: `ad_formats.HERO.label` → Field `label` = `Hero`.

### List — one name, many values
Short string lists show as **chips**; longer items as numbered rows. `+ Add value` / `+ Add item`.
- Used for: `ad_formats.HERO.output_fields` `["headline","support_line","cta"]`, `ad_languages.EN.rules` `["Write fully English..."]`, `persona_seeds` (top-level list), `ad_hooks.*` labels only, `background_variant.variants` (list of objects).
- Where you see chips in Studio (Format chips `HERO/BA/TEST`, Language chips `EN`), those lists are `output_fields` / `rules` filtered through `format_catalog` / `language_layers`.

### Group — a named folder of more fields
Open with **Show**, then add fields/lists/groups inside.
- Used for: `ad_formats` (group `HERO` → fields `description`/`skeleton`/`output_fields`), `visual_archetypes` (`copy_prompt_templates.visual_archetypes.HERO` → list of `{id,label,layout_lines}`), `ad_languages.EN` (group with `label`/`rules`/`persona_map`).

### `_meta` and other `_` keys
`_meta` and any key starting with `_` is a **file header, not a style**. Live copy skips underscore keys when it builds Studio menus (`copy_system.py:170 _styles` `if key.startswith("_"): continue`, `191 _format_entries`, `240 _language_entries`). `ad_hooks` `_meta.label = "Hook Structure"` and `_meta.instruction` are the Studio layer name and the default copy-LLM rule for that layer when no style is picked. `_meta.type = "hook_structure"` is docs. **If a layer file is `{}` (empty), it inherits the bundled file; to send no styles, save `{ "_meta": {"label":"Hook Structure"} }` (non-empty object, no styles) — otherwise `bundled_copy_system().get(key)` would inject the generic file.

**When to use what:**
- Add a **Field** when the file expects a scalar (e.g. `ad_guardrails.task`).
- Add a **List** when the file expects an array (e `ad_formats.HERO.output_fields`, `persona_seeds`).
- Add a **Group** when you need a new id (e.g. new format `STORY` → Group `STORY` inside `ad_formats`).

Changing `persona_seeds.json` → edit via **List** (each persona is an item) → each item is a **Group** with Fields `core_pattern`, `guardrail` etc. and Lists `primary_tags`.

---

## 4. Ad Languages — the `ad_languages` plate file

This is the most misread plate. It controls **copy language, Studio language chips, and persona field mapping**.

Bundled `ad_languages.json` shape:

```json
{
  "_modes": {
    "EN": {"label":"English","languages":["EN"]},
    "HI": {"label":"Hindi","languages":["HI"]},
    "HINGLISH": {"label":"Hinglish","languages":["HINGLISH"]},
    "ALL": {"label":"All","languages":["EN","HI","HINGLISH"]}
  },
  "EN": {
    "label": "English",
    "rules": ["Write fully English ad copy. No Hindi words."],
    "persona_map": {"pain":"pain_en", "desire":"desire_en", "friction":"friction_en", "proof_needed":"proof_needed_en", "tone_cue":"tone_cue_en"},
    "persona_fallbacks": {"pain_en":"The current routine is difficult...", "desire_en":"...", "friction_en":"...", "proof_needed_en":"...", "tone_cue_en":"..."},
    "_persona_source_map": {"name":["persona_name","name"], "pain_en":["core_pattern","pain_en"], "desire_en":["relevant_ok_kit_role","desire_en"], ...}
  },
  "HI": { "label":"Hindi","rules":["...Devanagari..."], "persona_map":{...}, "persona_fallbacks":{...} },
  "HINGLISH": { "label":"Hinglish", ... }
}
```

### What each key does
- **`_modes`**: Studio language chips. `ALL` expands to every language in `ad_languages`. Changing `_modes` does not fail a run. Copy request `languages[]` comes from here via `copy_system.py:251 _language_modes`.
- **`EN`/`HI`/`HINGLISH` group**: 
  - `label` → shown in language object `language_layers:326` (`"English"`).
  - `rules` → every string sent in `languages[].rules` for that id (`language_layers:310` now transparent — every field under `EN` as-is, but `rules` is the one the LLM uses).
  - `persona_map` → which persona keys are copied for that language (`copy_system.py:409 language_persona_map`). After `ccc764f` persona is transparent (every `persona_seeds` key as-is), but image `persona_lines` still uses this to decide which language-suffixed persona fields to show. **Do not invent** `pain_hi` when mode is `EN`; the assembler (`render_structured_copy.py:176 _persona_for_llm`) now sends whole persona, and `copy_system` still checks `if "HI" not in language_ids and key.endswith("_hi") → error`.
  - `persona_fallbacks` → when a persona has empty `core_pattern` etc., this English fallback is used for image `persona_prompt_values:642` (now transparent but still provides legacy `pain` etc. for old templates).
  - `_persona_source_map` → legacy alias map (`core_pattern → pain_en` etc.) kept for backward `FALLBACK_PERSONA_SOURCE_MAP:53`. Since `ccc764f` persona is transparent, this map is only for image legacy `pain` derivation.

### How to configure languages
1. To **add a new language** e.g. `MARATHI`: add group `MARATHI` with `label`/`rules`/`persona_map`/`persona_fallbacks` mirroring `HI`, then add `MARATHI` to `_modes.EN.languages` or create new mode `MARATHI` `{"label":"Marathi","languages":["MARATHI"]}`. Studio chips refresh from `/api/defaults?org_id=`.
2. To **change writing rules** (e.g. “English must be Hinglish”): edit `EN.rules` strings. Changing rules never 400s a run.
3. To **change persona field mapping** for a language (e.g. want `core_pattern` to map to `pain` in Marathi): edit `EN.persona_map.pain = "core_pattern"` already, for new language add `MARATHI.persona_map`.
4. After save, Studio `language_mode` dropdown reloads. Pick the mode; `resolve_language_ids:304` expands it to `["MARATHI"]` and copy/image both see it.

---

## 5. Global formats

`Global formats` = the row of format chips at the top of Studio’s Structured picker (`HERO` `BA` `TEST` `FEAT` `UGC`). They **apply to every selected persona that has no per-persona override**.

- **Where they come from:** `ad_formats.json` keys `HERO` etc. via `copy_system.py:191 _format_entries` → Studio `format_catalog`.
- **What they do:** In `_planned_ads:323`:
  ```python
  formats = per_persona.get(str(number))  # formats_by_persona["3"] if that persona card has its own chips
  formats = formats if list and non-empty else global_formats
  ```
  So if persona 3’s card has no chips, it inherits `global_formats`. If it has `["HERO","FEAT"]`, it uses those, not globals. Unselected personas inherit nothing.
- **Example:** Select personas `1,3`, set Global `["HERO","BA"]`, then on persona 3’s card set `["TEST"]` → persona 1 gets `HERO,BA` (2 ads), persona 3 gets `TEST` (1 ad). Before: 3 personas × 2 formats = 6 ads; after per-persona override the count is per-card.
- **Adding a format:** add `ad_formats.STORY` + its `visual_archetypes[STORY]` + background `variants` entry or it falls back to full pool.

---

## 6. Prompt assembling — inputs for each flow

All flows start from the same 8 plate files + `copy_system` + `prompt_assembler_templates.json`, but assemble differently. See `docs/COMPLETE_PROMPT_SKELETON.md:105` for the literal skeletons.

### Structured flow — copy generation (LLM)
**Input:** `product_master_doc` + `copy_starting_prompt` (if non-empty) + `ad_languages` (mode → `languages[]`) + `ad_guardrails` (`task`/`always`/`no_hypothesis`) + `ad_formats` (per planned ad `format` + `output_schema`) + `persona_seeds` (transparent whole object) + `ad_hooks`…`ad_support_shapes` via `HYPOTHESIS_FILES` (type+style+instruction/definition) + `concept` (once per ad).
**Code:** `render_structured_copy.py:590 assemble_copy_llm_request` → `copy_system.py:216 format_layer` / `474 hypothesis_layer` / `310 language_layers` / `136 _persona` (now transparent) / `444 hypothesis_catalog`. **Edit:** change `ad_formats[HERO].output_fields` → `output_schema` changes; change `ad_hooks.question_led.instruction` → next copy request sends it.
**Transport:** API `opencode.ai/zen/v1/chat/completions` via `provider_relay` (local agent) with `provider.generate_callable:1543` (adds `provider:{allow_fallback:true}` on fallback `ccc764f`), or Browser `browser_copy.py` warmup (product doc + copy_starting_prompt once) then chunk JSON without repeating product doc.

### Structured flow — 4:5 image generation (local Chrome)
**Input:** `starting_prompt` (**image** global product rules `starting_prompt.txt`, **not** `copy_starting_prompt`) + `product_lock_block`/`proof_bar_text`/`output_spec_lines`/`persona_lines` (15 lines `core_pattern`...`headline_anchor`)/`negative`/`quality`/`visual`/`typography`/`safezone_45` from `prompt_assembler_templates.json` + `background_variant` (`BG-322`) + `persona_seeds` (full) + `copy LLM` `copy[LANG]` per `output_fields` + `visual_archetype` (from `copy_prompt_templates`) + `copy_starting_prompt` is **not** in image (only in copy).
**Code:** cloud `render_structured_copy.py:931 _prompts_from_copy_batch` `1045 render_prompt("4:5")` **prepends** `starting_prompt` (fixed `ccc764f`+`3da942d`), writes `staging/structured-browser/<job_id>/<prompt_id>/45/<stem>.txt` via `_materialize:484`; local `structured_browser.py:680` reads `prompt["local_path"]` and for `4:5` uses it verbatim (with starter already), for `9:16` builds `starter + conversion_body + bounded_context`. **Edit:** change `prompt_assembler_templates.json:32 persona_lines` add `"- Seasonal: {seasonal_trigger}"` → `generate_ads.py:798` `_persona_fill` already handles any persona key transparently.

### Structured flow — 9:16 creation (conversion)
**Input:** same `starting_prompt` + `conversion_916_prompt` (config `conversion_916_prompt`, not copy) + `4:5` source `output_sha256` + `bounded_context` (`prompt_id`/`source_output_id`/`conversion_prompt_resource_id`).
**Code:** `structured_browser.py:734` `if aspect_ratio=="9:16": source=_existing_output("4:5")` → `conversion_body = conversion_prompt` + `bounded_context` + `starter` outer. No new LLM; same `prompt_assembler_templates: safezone_916/outpaint_lock`.

### Structured flow — revision
**Input:** `comment` (user typed) + `aspect_ratio` + `original prompt` (`prompt` resource) + `assembler_templates` + `conversion_916_prompt`.
**Code:** `local_agent.py:1335 _write_revision_upload_manifest` + `storage.py:1220 queue_output_revision` → `revision_prompt` resource via `revision_prompt.py:255 build_output_revision_prompt` → `structured_browser.py` (same as image but `source_output_version` + `revision`).

### Reference flow — 4:5 generation
**Input:** `reference_starting_prompt` + `product_document` (`reference_product_master_doc`) + `persona_seeds` (transparent `json.dumps(persona)`) + `reference_images` + `comment` + `concept`.
**Code:** `reference_workflow.py:255 _put_prompt` `parts=[starting_prompt, "TARGET PERSONA:\n"+json.dumps(persona), "TARGET CONCEPT:"+..., "REFERENCE INSTRUCTION:"+comment, "PRODUCT DOCUMENT:"+..., "Create one exact 4:5..."]` → `body = "\n\n".join(parts)` → `staging/reference-workflow/.../prompt.txt`. **No copy LLM.** Changing `persona_seeds` keys immediately appears in `TARGET PERSONA:`.

### Reference flow — 9:16 creation
**Input:** same as 4:5 + `conversion_916_prompt` (re-derived, not reusing 4:5 starter because 4:5 source already had starter).
**Code:** `reference_workflow.py:546` `effective_prompt = conversion_prompt + "\n\n[LOCAL BOUNDED...]"`.

### Reference flow — revision
Same as structured revision but via `reference_workflow`'s `queue_output_revision` (same `local_agent.py:1335` path).

---

## 7. How to add more language (end-to-end)

1. **Add group** `ad_languages.MARATHI` → Fields `label`=`Marathi`, List `rules`=`["Write Devanagari Marathi..."]`, Group `persona_map` (`pain`→`pain_mr`, `desire`→`desire_mr`...), Group `persona_fallbacks` (5 keys like `pain_mr`).
2. **Add mode** `_modes.MARATHI` → Group `MARATHI` with List `languages`=`["MARATHI"]` and `label`=`Marathi`.
3. **Add persona fields** to each `persona_seeds` entry: `pain_mr`, `desire_mr` etc. (or keep English fallback — image `persona_prompt_values:642` will fallback to `FALLBACK_PERSONA_EN` if missing, but you want real Marathi).
4. **Save** Config → Studio `language_mode` chip refreshes → pick `MARATHI` → `resolve_language_ids` → copy and image both see `MARATHI`.
5. **No code change** — `language_layers` and `persona` are transparent; new `MARATHI` id automatically sent.

---

## 8. JSON editor buttons in detail

See §3 for `+ Add field / + Add list / + Add group` and `_meta` headers — every plate file uses them. Example: to add a new hypothesis style `my_hook` to `ad_hooks.json`: open `ad_hooks` → `+ Add group` name `my_hook` → inside add Fields `label`/`instruction`/`definition`/`skeleton`. To add a new language, add a **Group** `MARATHI` inside `ad_languages`, not a Field.

---

## 9. Dependencies — if you change one file, what else must change

| You change | Must also change or it falls back | Why |
|---|---|---|
| `ad_formats.NEW` | `prompt_assembler_templates.json: style_descriptions[NEW]` + `copy_prompt_templates.json: visual_archetypes[NEW]` or images use `default_NEW` | `format_visual_archetypes` sync |
| `persona_seeds` new key `seasonal_trigger` | `prompt_assembler_templates.json: persona_lines` add `"- Seasonal: {seasonal_trigger}"` to see it in image prompt | `persona_lines` is the image template |
| `ad_languages.MARATHI` | `persona_seeds` `*_mr` fields + `_modes.MARATHI` | otherwise persona fallback or mode missing |
| `background_variant` new `id` | `prompt_assembler_templates.json: background_sentence` pools | fallback to `BG-322` |
| `proof_bar_text` | nothing else — image `proof_bar_block` templated | brand lock |
| `starting_prompt` | nothing else — image `4:5`+`9:16` both prepend (fixed) | not sent to copy LLM |

---

## 10. Where to read the full skeletons

`docs/COMPLETE_PROMPT_SKELETON.md:105` — line-by-line for every flow, file→prompt-key map, code line, and edit recipes. `/guide` now embeds that whole file (no redirect), and `/docs/COMPLETE_PROMPT_SKELETON.md` is the same file on GitHub.

