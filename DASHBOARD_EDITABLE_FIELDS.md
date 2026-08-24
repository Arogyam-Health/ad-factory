# Dashboard editable fields

This is the user-facing map of what you can change in Studio and Config. Content lives in Mongo (the selected personal or org source). Images, prompts, and run outputs stay on the paired local device.

Studio’s **Source** buttons and Config’s source tabs are the same choice. Switching org vs My Config loads that Mongo document; it is not a local-only overlay.

The operator guide for the live copy request is [`docs/STRUCTURED_COPY_SYSTEM.md`](docs/STRUCTURED_COPY_SYSTEM.md).

## Shared vs flow-only

| Key / card | Structured Flow | Reference Image Flow | Notes |
|---|---|---|---|
| `persona_seeds` | yes | yes | Shared. Persona cards in both flows. |
| `concept` | yes | yes | Shared. Creative-format catalog (IG Stories, Venn, …). Separate from H2 Concept Angle. |
| `conversion_916_prompt` | yes | yes | Shared 9:16 conversion prompt. |
| `starting_prompt` | yes | no | Image starter for ChatGPT/local image prompts. Not sent to the copy LLM. |
| `copy_starting_prompt` | yes | no | Always sent to the copy LLM when non-empty. |
| `ad_formats` | yes | no | Format descriptions, skeletons, and output fields sent to the copy LLM. |
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
| `ad_guardrails` | yes | no | Always-on safety lines plus the no-hypothesis instruction. |
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

Format ids stay `HERO`, `BA`, `TEST`, `FEAT`, `UGC`.

### 2) Input Prompts

| Card | Config key | What to edit |
|---|---|---|
| Starting Prompt | `starting_prompt` | Image starter. Not sent to the copy LLM. |
| Copy Starting Prompt | `copy_starting_prompt` | Always sent to the copy LLM when non-empty. |
| 9:16 Conversion Prompt | `conversion_916_prompt` | Plain text that converts a 4:5 creative to 9:16. |

### 3) Input Images

Product packshots on this machine (`~/ad-factory-agent`). Not stored on Render.

### 4) Config Files

Click a card to edit the Mongo field for the **current Source**.

| Card | Config key | Format |
|---|---|---|
| Persona Seeds | `persona_seeds` | JSON |
| Concept | `concept` | JSON — creative-format catalog for the Concept dropdown |
| Ad Formats | `ad_formats` | JSON — purpose, skeleton, `output_fields` per format |
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
| Copy Guardrails | `ad_guardrails` | JSON — `always` lines plus `no_hypothesis` |
| Copy Starting Prompt | `copy_starting_prompt` | Plain text always sent to the copy LLM when non-empty |
| Copy Prompt Templates | `copy_prompt_templates` | JSON — `visual_archetypes` drives pattern dropdowns |
| Prompt Assembler Templates | `prompt_assembler_templates` | JSON |
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

Same keys as Studio §4. JSON keys are validated on save.

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

## Local agent token

The dashboard counts **one active agent per Google account + device**. The token is stored at:

`~/ad-factory-agent/config/agent.json`

Keep that file when you restart the agent. Do **not** pass a fresh `--session-cookie` unless you intend to rebind this machine to a different dashboard session. A new cookie without the saved token registers another Mongo agent row.

Pairing itself is remembered in the browser (`localStorage`). Re-pairing every 1.5s is not required while the saved session is valid.
