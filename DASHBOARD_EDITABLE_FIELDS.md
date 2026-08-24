# Dashboard editable fields

This is the user-facing map of what you can change in Studio and Config. Content lives in Mongo (the selected personal or org source). Images, prompts, and run outputs stay on the paired local device.

Studio’s **Source** buttons and Config’s source tabs are the same choice. Switching org vs My Config loads that Mongo document; it is not a local-only overlay.

The operator guide for the live copy request is [`docs/STRUCTURED_COPY_SYSTEM.md`](docs/STRUCTURED_COPY_SYSTEM.md). The in-dashboard guide is `/guide` ([`docs/OPERATOR_PLATE_GUIDE.md`](docs/OPERATOR_PLATE_GUIDE.md)).

## Shared vs flow-only

| Key / card | Structured Flow | Reference Image Flow | Notes |
|---|---|---|---|
| `persona_seeds` | yes | yes | Shared. Persona cards in both flows. |
| `concept` | yes | yes | Shared. Creative-format catalog (IG Stories, Venn, …). Separate from H2 Concept Angle. |
| `conversion_916_prompt` | yes | yes | Shared 9:16 conversion prompt. |
| `starting_prompt` | yes | no | Image starter for ChatGPT/local image prompts. Not sent to the copy LLM. |
| `copy_starting_prompt` | yes | no | Always sent to the copy LLM when non-empty. |
| `visual_archetype_llm_prompt` | yes | no | Image-prompt text when a format pattern is Leave it to the image model. |
| `ad_formats` | yes | no | Format descriptions, skeletons, and output fields sent to the copy LLM. |
| `ad_languages` | yes | yes | Language chips, writing rules, and persona field maps sent to the copy LLM. |
| `ad_hooks` | yes | no | Hook Structure hypothesis styles. |
| `ad_angles` | yes | no | Concept Angle hypothesis styles. |
| `ad_frameworks` | yes | no | Copy Framework hypothesis styles. No PAB. |
| `ad_proof` | yes | no | Proof Strategy hypothesis styles. |
| `ad_objections` | yes | no | Objection Strategy hypothesis styles. |
| `ad_value_props` | yes | no | Value Proposition hypothesis styles. |
| `ad_awareness` | yes | no | Awareness Stage hypothesis styles. |
| `ad_emotions` | yes | no | Emotional Driver hypothesis styles. |
| `ad_specificity` | yes | no | Specificity Level hypothesis styles. |
| `ad_feature_focus` | yes | no | Feature Focus hypothesis styles. |
| `ad_support_shapes` | yes | no | Support Shape hypothesis styles. |
| `ad_guardrails` | yes | no | Always-on safety lines, copy-LLM task lines, and the no-hypothesis instruction. |
| `copy_prompt_templates` | yes | no | Live path uses `visual_archetypes` only (pattern dropdowns and image prompts). |
| `prompt_assembler_templates` | yes | no | Image-prompt assembly blocks. |
| `background_variant` | yes | no | Background catalog. |
| `product_master_doc` | yes | no | Structured product truth. |
| `reference_starting_prompt` | no | yes | Separate from `starting_prompt`. |
| `reference_product_master_doc` | no | yes | Separate from `product_master_doc`. |

Cross-flow cards are hidden when you switch Structured / Reference in Studio.

---

## Studio — Structured Flow

### 1) Persona + Formats

Not a JSON editor. Personas come from `persona_seeds`. Click personas, formats, language, and per-format visual patterns.

Visual pattern options are **not** a separate file. They are `visual_archetypes` inside **Copy Prompt Templates**. Edit that JSON (Studio §4 or Config) to add or rename patterns. The dropdown shows the `label`; the `id` is on hover.

**Auto rotate** picks a random catalog pattern per ad (unused ones first in the same batch). **Leave it to the image model** sends `visual_archetype_llm_prompt` instead of a named pattern.

Format chips come from `ad_formats`. Bundled ids are `HERO`, `BA`, `TEST`, `FEAT`, `UGC`. A new format id appears after save; default visual archetypes are stubbed on that owner.

### 2) Input Prompts

| Card | Config key | What to edit |
|---|---|---|
| Starting Prompt | `starting_prompt` | Image starter. Not sent to the copy LLM. |
| Copy Starting Prompt | `copy_starting_prompt` | Always sent to the copy LLM when non-empty. |
| Leave Pattern To Image Model | `visual_archetype_llm_prompt` | Image-prompt text when the pattern is Leave it to the image model. |
| 9:16 Conversion Prompt | `conversion_916_prompt` | Plain text that converts a 4:5 creative to 9:16. |

### 3) Input Images

Product packshots on this machine (`~/ad-factory-agent`). Not stored on Render.

### 4) Config Files

Click a card to edit the Mongo field for the **current Source**. Studio splits these into Plate files, Hypothesis styles, and Business rules. Business rules are this brand's lock.

| Card | Config key | Format |
|---|---|---|
| Persona Seeds | `persona_seeds` | JSON |
| Concept | `concept` | JSON — creative-format catalog for the Concept dropdown |
| Ad Formats | `ad_formats` | JSON — purpose, skeleton, `output_fields` per format |
| Ad Languages | `ad_languages` | JSON — modes, writing rules, persona maps, `_persona_source_map` |
| Hook Structures | `ad_hooks` | JSON — Hook Structure styles |
| Concept Angles | `ad_angles` | JSON — Concept Angle styles |
| Copy Frameworks | `ad_frameworks` | JSON — PAS, BAB, FAB, AIDA, … No PAB |
| Proof Strategies | `ad_proof` | JSON |
| Objection Strategies | `ad_objections` | JSON |
| Value Propositions | `ad_value_props` | JSON |
| Awareness Stages | `ad_awareness` | JSON |
| Emotional Drivers | `ad_emotions` | JSON |
| Specificity Levels | `ad_specificity` | JSON |
| Feature Focus | `ad_feature_focus` | JSON |
| Support Shapes | `ad_support_shapes` | JSON — Pain First, Contrast, Bridge |
| Copy Guardrails | `ad_guardrails` | JSON — `task`, `repair_task`, `always` lines, plus `no_hypothesis` |
| Copy Starting Prompt | `copy_starting_prompt` | Plain text always sent to the copy LLM when non-empty |
| Leave Pattern To Image Model | `visual_archetype_llm_prompt` | Plain text sent to the image model when pattern is Leave it to the image model |
| Copy Prompt Templates | `copy_prompt_templates` | JSON — `visual_archetypes` drives pattern dropdowns |
| Prompt Assembler Templates | `prompt_assembler_templates` | JSON — image assembly, including this brand's `proof_bar_text` and `headline_bans` |
| Background Variants | `background_variant` | JSON |
| Product Master Doc | `product_master_doc` | Plain text |
| Reference Starting Prompt | `reference_starting_prompt` | Plain text (Reference flow only) |
| Reference Product Doc | `reference_product_master_doc` | Plain text (Reference flow only) |

Empty or missing fields are omitted from the copy LLM request. Generation still runs. A stored `{}` inherits the generic file; to clear a layer, keep a non-empty object such as `{ "_meta": {} }`.

### 5) Hypothesis Testing

Options come from the `ad_*` files above. Switching the org chip reloads `/api/defaults?org_id=…` so the Hypothesis and Style menus match that source.

| Studio type | Config file |
|---|---|
| Hook Structure | `ad_hooks` |
| Concept Angle | `ad_angles` |
| Copy Framework | `ad_frameworks` |
| Proof Strategy | `ad_proof` |
| Objection Strategy | `ad_objections` |
| Value Proposition | `ad_value_props` |
| Awareness Stage | `ad_awareness` |
| Emotional Driver | `ad_emotions` |
| Specificity Level | `ad_specificity` |
| Feature Focus | `ad_feature_focus` |
| Support Shape | `ad_support_shapes` |

**None** omits `hypothesis` entirely. The request does not send `concept_angle` or a hidden `desired_outcome`.

### 6) Execution

| Control | Sent to copy? |
|---|---|
| LLM provider / model / credentials | yes (account provider config) |
| Ad multiplier | yes |
| Ads per LLM call (batch size) | yes |
| Keep same background across personas | image assembly only |
| Reuse backgrounds from previous run | image assembly only |
| Reuse visual patterns from previous run | image assembly only |
| Selected visual archetypes | `{id, label}` only, when locked |
| Creative concept | once, on each planned ad |

If the chosen copy model fails, the dashboard logs the error and retries **once** with the next free OpenCode catalog model (`opencode/mimo-v2.5-free`, then `opencode/north-mini-code-free`, …). Both errors stay on the run if fallback also fails.

---

## Studio — Reference Image Flow

Uses `persona_seeds`, `concept`, `reference_starting_prompt`, `reference_product_master_doc`, and the shared `conversion_916_prompt`. Reference images, product packshots, and per-card comments stay on the local device.

---

## Config page

Same keys as Studio §4. Plate files, hypothesis style files, and business-rule files are listed in separate blocks. JSON keys are validated on save. Business rules are this brand's lock (product doc, personas, starting prompts). The proof bar lives in Prompt Assembler Templates.

**Copy to Org** copies the current source’s files onto an org you can manage (creates a versioned snapshot). Creating a team also copies the creator’s current config onto the new org.

Org **shared** mode: members edit one org config. Org **individual** mode: each member keeps a personal config.

---

## Example: add a visual pattern

In **Copy Prompt Templates** (`copy_prompt_templates`), under `visual_archetypes.<FORMAT>`:

```json
"HERO": [
  {
    "id": "hero_center_stage",
    "label": "Centered premium packshot",
    "layout_lines": [
      "- Archetype: centered premium packshot with headline centered above the product block."
    ],
    "direction_lines": [
      "- Archetype direction: centered composition with soft pedestal energy."
    ]
  }
]
```

- `id` must be unique within that format. Studio uses it as the stored value.
- `label` is what the dropdown shows.
- Add an object to the format array; save; refresh Studio. The new pattern appears for that format chip.

---

## Example: add a hypothesis style

In the matching `ad_*` file (for example `ad_frameworks` for Copy Framework), add a style object:

```json
"pas": {
  "label": "PAS",
  "instruction": "Follow Problem, Agitate, Solution. Do not print those labels in consumer copy.",
  "definition": "Identify the relevant problem, explain why it is frustrating, then introduce the product as support.",
  "skeleton": "Problem → Agitate → Solution"
}
```

`label` is required for the Style dropdown. `instruction`, `definition`, and `skeleton` are optional; blank keys are omitted from the LLM JSON.

---

## Example: add a format

In `ad_formats`, add an object key. Save on a personal or org plate. Studio chips pick it up from `/api/defaults`. Default visual archetypes are stubbed on that owner.

```json
"STORY": {
  "label": "Story",
  "description": "A narrative sequence with one clear payoff.",
  "skeleton": "Headline\nNote\nCTA",
  "output_fields": ["headline", "note", "cta"]
}
```

Acceptance requires those `output_fields` (except optional `trust_line`). Skeleton text does not fail a run. Extra LLM keys are ignored.

---

## Local agent token

The dashboard counts **one active agent per Google account + device**. The token is stored at:

`~/ad-factory-agent/config/agent.json`

Keep that file when you restart the agent. Do **not** pass a fresh `--session-cookie` unless you intend to rebind this machine to a different dashboard session. A new cookie without the saved token registers another Mongo agent row.

Pairing itself is remembered in the browser (`localStorage`). Re-pairing every 1.5s is not required while the saved session is valid.
