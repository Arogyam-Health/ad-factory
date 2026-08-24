# Operator plate guide

How the live Structured copy plate works, what each file does, and what will or will not fail a run. Edit files in Studio or Config, then send a plate. Image generation stays on the paired local agent.

## Plate files vs hypothesis files vs business rules

Plate files shape every run. Hypothesis files only apply when you pick a Hypothesis type and Style in Studio. Business rules are this brand's lock. A new business edits that block first.

**Plate files**

- `ad_formats` — format chips, copy skeletons, and required `output_fields`
- `ad_languages` — language chips, writing rules, persona field maps, and `_persona_source_map`
- `ad_guardrails` — safety lines plus `task` / `repair_task` sent to the copy LLM
- `concept` — Concept dropdown (creative formats, not Concept Angle)
- `copy_starting_prompt` — copy-only starter, sent as `starting_prompt` when non-empty
- `copy_prompt_templates` — `visual_archetypes` only (pattern dropdowns and image layout)
- `visual_archetype_llm_prompt` — used when a pattern is Leave it to the image model
- `prompt_assembler_templates` — image-prompt assembly. Includes this brand's `proof_bar_text` and `headline_bans`
- `background_variant` — background catalog

**Business rules**

- `product_master_doc` — this brand's product truth for copy
- `persona_seeds` — this brand's persona cards
- `starting_prompt` — this brand's image starter for ChatGPT / local image prompts. Not sent to the copy LLM
- `reference_starting_prompt` / `reference_product_master_doc` — Reference flow only

The live proof bar text is `prompt_assembler_templates.proof_bar_text`. Headline replacement regexes are `headline_bans`. Both keep this brand's current wording.

**Hypothesis styles**

`ad_hooks`, `ad_angles`, `ad_frameworks`, `ad_proof`, `ad_objections`, `ad_value_props`, `ad_awareness`, `ad_emotions`, `ad_specificity`, `ad_feature_focus`, `ad_support_shapes`. These feed Hypothesis and Style menus. They are not mixed into plate-file cards.

## Language rules

`ad_languages` is a plate file. Studio chips come from `_modes`. The selected mode expands to language ids. Each id sends `label` and `rules` on the copy request.

Bundled rules: English stays fully English, Hindi stays Devanagari, Hinglish stays Roman-letter spoken Hindi. Edit those strings like any other plate file. Changing them does not fail a run.

## Guardrails vs skeleton vs `output_fields`

Three different jobs:

- **Guardrails** (`ad_guardrails.always`, plus `no_hypothesis` when Hypothesis is None) are sent on every live copy call. They keep claims safe. They do not change the JSON schema.
- **Skeleton** is writing guidance. Changing skeleton text does **not** fail a run.
- **`output_fields`** is the acceptance schema for that format. After the LLM returns, the run requires those fields (except `trust_line`, which stays optional). Extra keys such as an unused `support_line` are ignored. One repair pass runs if a required field is empty; a second miss fails the run (`headline_missing`, `note_missing`, …).

If a format has no `output_fields`, the plate requires `headline` and `cta`.

## How to edit and save

1. Pick the Source chip (My config or an org). Studio and Config share that choice.
2. Open a card on the Studio Copy desk, or edit in Config.
3. Save writes to that owner only. Generic bundled files are not rewritten from a personal or org save.
4. After `ad_formats` save, Studio chips refresh from `/api/defaults`.

## Add or remove a format

Add a new object key in `ad_formats`, for example `STORY`. Use an id like `[A-Z][A-Z0-9_]{0,15}`. Include `label`, `description`, `skeleton`, and `output_fields`.

On personal or org save:

- New format ids get a stub visual archetype (`{id}_default`) in that owner's `copy_prompt_templates`.
- Removed format ids drop their archetype arrays on the same owner.
- The save response shows a notice such as: `Added default visual archetypes for STORY. Edit Copy Prompt Templates and make them meaningful.`

Those stubs are placeholders. Edit Copy Prompt Templates so layout and direction lines are real. Background slots are not auto-created; if no variant lists the new format, the run uses the full background pool.

Studio shows up to eight selected formats. Job settings accept any catalog id that matches the format-id rule.

## Auto rotate vs Leave it to the image model

Per-format pattern dropdown:

- **Auto rotate** picks a random catalog pattern, preferring ones not yet used in the same batch.
- **Leave it to the image model** (`llm_decide`) sends `visual_archetype_llm_prompt` instead of a named pattern. It is not sent to the copy LLM.
- A named catalog id locks that pattern for image assembly.

## `copy_starting_prompt` vs image `starting_prompt`

- `copy_starting_prompt` goes to the copy LLM as `starting_prompt` when non-empty.
- Image `starting_prompt` is packshot / image rules. It is not sent to the copy LLM.

Do not put image-only instructions in `copy_starting_prompt`.

## What will and will not fail a run

**Will fail**

- No persona or no format selected
- Unsupported format id (not `[A-Z][A-Z0-9_]{0,15}`)
- Empty Product Master Doc
- After one repair, a required `output_fields` value is still empty
- Provider / relay errors

**Will not fail**

- You changed only skeleton or description text
- The LLM returned extra keys beyond `output_fields`
- Hypothesis is None (guardrails still apply)
- A new format has no dedicated background variant (full pool is used)
- Default visual archetypes are still stub text — edit them, but the run still assembles

See also [Structured copy system](STRUCTURED_COPY_SYSTEM.md) for the live request shape.
