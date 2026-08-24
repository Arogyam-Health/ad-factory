# Structured copy system

This is the operator guide for the live Structured copy request. Edit the split JSON files, save them in Studio or Config, and the next run sends those layers to the copy LLM. Empty fields are omitted. Generation still runs.

The live assembler is `dashboard/backend/services/render_structured_copy.py`. It reads `dashboard/backend/copy_system/` through `dashboard/backend/services/copy_system.py`. Image prompts are assembled by `dashboard/backend/services/generate_ads.py` using `prompt_assembler_templates.json` in that same copy_system folder. `copy_prompt_templates.json` is used only for `visual_archetypes` after copy exists. Old copy-LLM blocks in a stored file (`system_prompt_*`, CTA maps, template copy, and similar) are stripped on read and save. `copy_starting_prompt` is sent as `starting_prompt` when non-empty.

## Request shape

One copy call looks like this. Optional objects appear only when they have content.

```json
{
  "task": "Generate structured advertising copy as JSON",
  "starting_prompt": "<copy_starting_prompt if non-empty>",
  "product_document": "<product master doc if non-empty>",
  "languages": [
    {
      "id": "EN",
      "label": "English",
      "rules": ["Write fully English ad copy. ..."]
    }
  ],
  "guardrails": ["...only non-empty lines..."],
  "planned_ads": [
    {
      "format": {
        "id": "HERO",
        "description": "...from ad_formats...",
        "skeleton": "Headline / Support / optional trust / CTA",
        "output_fields": ["headline", "support_line", "trust_line", "cta"]
      },
      "persona": { "number": 28, "name": "...", "pain": "...", "desire": "..." },
      "hypothesis": {
        "type": "copy_framework",
        "style": "pas",
        "label": "PAS",
        "instruction": "...how to apply this test...",
        "definition": "...style writeup..."
      },
      "creative_concept": { "id": "Concept/iPhone_Notes", "label": "...", "description": "..." }
    }
  ],
  "output_schema": { "ads": [{ "copy": { "EN": { "headline": "string" } } }] }
}
```

Rules:

- Hypothesis **None**: omit `hypothesis`. Do not send `concept_angle` or `desired_outcome`.
- Format always has `id`. `description`, `skeleton`, `label`, and `output_fields` are attached only when those strings exist in `ad_formats`. Unknown ids still send `{id}` plus any layer fields that exist.
- Hypothesis selected: send `type` + `style`. Attach instruction/definition/skeleton only when the style file has them.
- Creative concept appears **once**, on the planned ad, not also at the top level.
- Image-only keys stay out of the copy request (`background_group_key`, share-background, visual layout lines). A locked format pattern may send `{id, label}` only.
- Persona sends only filled fields. Hindi/Hinglish fillers are not invented. Which persona keys are copied is `ad_languages.persona_map`.
- `languages` is a list of objects from `ad_languages` for the selected mode. Each object has `id` plus `label` / `rules` when those exist. Changing rules does not fail a run.
- `output_schema` comes from that format’s `output_fields`. Extra LLM keys are ignored. Missing listed fields (except optional `trust_line`) trigger one repair, then fail.
- Do not send `product_truths`, `requirements`, `background_group_key`, or `format` as a string. Product truths stay inside `product_document` for the model to use, not echo.

Format ids come from `ad_formats` (`HERO`, `BA`, `TEST`, `FEAT`, `UGC` in the bundled file, plus any id matching `[A-Z][A-Z0-9_]{0,15}`). Studio chips and `/api/defaults` read that catalog. Acceptance uses that format’s `output_fields` only. Skeleton text is guidance and is never compared. `ad_guardrails` stay on every live call.

The in-app operator guide is `/guide`. This file is also at `/docs/STRUCTURED_COPY_SYSTEM.md` on the dashboard.

## Which file feeds which request key

| File | Config key | Request key |
|---|---|---|
| `copy_system/ad_formats.json` | `ad_formats` | `planned_ads[].format` and `output_schema` |
| `copy_system/ad_languages.json` | `ad_languages` | top-level `languages` and Studio language chips |
| `copy_system/ad_hooks.json` | `ad_hooks` | `hypothesis` when type is Hook Structure |
| `copy_system/ad_angles.json` | `ad_angles` | `hypothesis` when type is Concept Angle |
| `copy_system/ad_frameworks.json` | `ad_frameworks` | `hypothesis` when type is Copy Framework |
| `copy_system/ad_proof.json` | `ad_proof` | `hypothesis` when type is Proof Strategy |
| `copy_system/ad_objections.json` | `ad_objections` | `hypothesis` when type is Objection Strategy |
| `copy_system/ad_value_props.json` | `ad_value_props` | `hypothesis` when type is Value Proposition |
| `copy_system/ad_awareness.json` | `ad_awareness` | `hypothesis` when type is Awareness Stage |
| `copy_system/ad_emotions.json` | `ad_emotions` | `hypothesis` when type is Emotional Driver |
| `copy_system/ad_specificity.json` | `ad_specificity` | `hypothesis` when type is Specificity Level |
| `copy_system/ad_feature_focus.json` | `ad_feature_focus` | `hypothesis` when type is Feature Focus |
| `copy_system/ad_support_shapes.json` | `ad_support_shapes` | `hypothesis` when type is Support Shape |
| `copy_system/ad_guardrails.json` | `ad_guardrails` | top-level `task`, `guardrails`, and repair `task` |
| copy starting prompt | `copy_starting_prompt` | top-level `starting_prompt` when non-empty |
| product master doc | `product_master_doc` | `product_document` |
| concept catalog | `concept` | `planned_ads[].creative_concept` |
| persona seeds | `persona_seeds` | `planned_ads[].persona` |
| copy prompt templates | `copy_prompt_templates` | not sent to the copy LLM; `visual_archetypes` only, after copy |

`ad_guardrails.task` and `ad_guardrails.repair_task` are the copy-LLM job lines. `ad_guardrails.always` is always attached (non-empty lines only). `ad_guardrails.no_hypothesis` is attached only when Hypothesis is None.

Image prompts read `prompt_assembler_templates`. `proof_bar_text` and `headline_bans` are this brand's lock and keep the current wording. A new business edits those keys plus the Business rules files; they should not need a code change.

## How to edit or remove a style

Each style is a small object. Missing keys are fine.

```json
"question_led": {
  "label": "Question Led",
  "instruction": "The opening hook is the variable under test.",
  "definition": "Open with one specific question the persona would actually ask.",
  "skeleton": "Headline = one self-identifying question"
}
```

- **Add a format**: put a new id in `ad_formats` with `output_fields`. Save on a personal or org plate. Default visual archetypes are stubbed; edit them in Copy Prompt Templates.
- **Add a style**: put a new id in the matching `ad_*` file, save in Config or Studio, refresh Studio. The Style dropdown lists `label`.
- **Edit a style**: change `definition` / `instruction` / `skeleton`. The next run sends the new text.
- **Remove a style**: delete that id. Studio no longer lists it. Generation still runs if someone sends a leftover id; the request then has `type` + `style` and no definition.
- **Clear a whole layer**: a stored `{}` inherits the generic bundled file. To send no styles, save a non-empty object such as `{ "_meta": { "label": "Hook Structure" } }`.
- **Blank field**: `""` is omitted. Do not send `null` just to fill a schema.

There is no PAB framework. Do not add one.

## How to run the compile tests

These tests capture the assembled request the same way the audit did: they call `generate_structured_prompt_bundle` with a fake `generate` and inspect that JSON.

```bash
python -m unittest tests.test_copy_system_request tests.test_render_structured_pipeline tests.test_concept_catalog
```

What they lock:

- HERO vs BA payloads differ by description, skeleton, and output fields
- Hypothesis None has no `concept_angle` and no `hypothesis`
- `pain_point` includes definition text
- Support Shape / contrast includes definition text
- empty `ad_hooks.question_led` still generates
- `creative_concept` appears once, on the planned ad
- TEST uses attribution and its no-fabricate description
- custom `output_fields` (`headline`, `note`, `cta`) are required; extra keys do not fail
- `ad_languages` rules are sent on `languages[]`
- `copy_starting_prompt` is sent only when non-empty

## Studio checklist

1. Source chip is the org or personal config you intend to edit.
2. Hypothesis and Style menus reload from `/api/defaults?org_id=…` after an org chip change. If a style looks wrong, you are probably still on My Config.
3. Format chips come from `ad_formats`. Their descriptions and required fields come from that file.
4. Hypothesis **None** should produce a request with no `hypothesis` key. Check Traces if a run looks like it forced `desired_outcome`.
5. A selected Concept should appear once under the planned ad, not also at the top of the request.
6. Visual pattern dropdowns still come from `copy_prompt_templates.visual_archetypes`. Auto rotate picks a random catalog pattern. Leave it to the image model uses `visual_archetype_llm_prompt`. They are image-only. Hypothesis style files sit in their own Studio / Config block.
7. After you clear a style file, you can still send. The Style dropdown may be empty; the run should not 400.
8. TEST must not invent quotes. If testimonial material is empty, the format description tells the model to keep the layout and skip fabricated claims.

## Out of scope here

- Binding a default framework to a format
- Ranking product truths
- Auto-creating `background_variant` slots for a new format
- Changing the local image agent beyond reading a locked pattern id
