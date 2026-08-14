# Dashboard editable fields

This is the user-facing map of what you can change in Studio and Config. Content lives in Mongo (the selected personal or org source). Images, prompts, and run outputs stay on the paired local device.

Studio’s **Source** buttons and Config’s source tabs are the same choice. Switching org vs My Config loads that Mongo document; it is not a local-only overlay.

## Shared vs flow-only

| Key / card | Structured Flow | Reference Image Flow | Notes |
|---|---|---|---|
| `persona_seeds` | yes | yes | Shared. Persona cards in both flows. |
| `conversion_916_prompt` | yes | yes | Shared 9:16 conversion prompt. |
| `starting_prompt` | yes | no | Structured Input Prompts only. |
| `copy_architecture` | yes | no | Headline architectures + hypothesis styles. |
| `copy_prompt_templates` | yes | no | Includes `visual_archetypes` (pattern dropdowns). |
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

### 2) Input Prompts

| Card | Config key | What to edit |
|---|---|---|
| Starting Prompt | `starting_prompt` | Plain text prepended to structured generation prompts. |
| 9:16 Conversion Prompt | `conversion_916_prompt` | Plain text that converts a 4:5 creative to 9:16. |

### 3) Input Images

Product packshots on this machine (`~/ad-factory-agent`). Not stored on Render.

### 4) Config Files

Click a card to edit the Mongo field for the **current Source**.

| Card | Config key | Format |
|---|---|---|
| Persona Seeds | `persona_seeds` | JSON |
| Copy Architecture | `copy_architecture` | JSON — `headline_architectures` drives hypothesis styles |
| Copy Prompt Templates | `copy_prompt_templates` | JSON — `visual_archetypes` drives pattern dropdowns |
| Prompt Assembler Templates | `prompt_assembler_templates` | JSON |
| Background Variants | `background_variant` | JSON |
| Product Master Doc | `product_master_doc` | Plain text |
| Reference Starting Prompt | `reference_starting_prompt` | Plain text (Reference flow only) |
| Reference Product Doc | `reference_product_master_doc` | Plain text (Reference flow only) |

### 5) Hypothesis Testing

Options come from `copy_architecture` → `headline_architectures` (and related hypothesis controls in that file). The selected style is sent on the live structured-copy path.

### 6) Execution

| Control | Sent to copy? |
|---|---|
| LLM provider / model / credentials | yes (account provider config) |
| Ad multiplier | yes |
| Ads per LLM call (batch size) | yes |
| Keep same background across personas | yes |
| Reuse backgrounds from previous run | yes |
| Reuse visual patterns from previous run | yes |
| Selected visual archetypes | yes |

If the chosen copy model fails, the dashboard logs the error and retries **once** with the next free OpenCode catalog model (`opencode/mimo-v2.5-free`, then `opencode/north-mini-code-free`, …). Both errors stay on the run if fallback also fails.

---

## Studio — Reference Image Flow

Uses `persona_seeds`, `reference_starting_prompt`, `reference_product_master_doc`, and the shared `conversion_916_prompt`. Reference images, product packshots, and per-card comments stay on the local device.

---

## Config page

Same ten keys as Studio §4. JSON keys are validated on save.

**Copy to Org** copies the current source’s ten files onto an org you can manage (creates a versioned snapshot). Creating a team also copies the creator’s current config onto the new org.

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

## Example: add a hypothesis / headline style

In **Copy Architecture** (`copy_architecture`), under `headline_architectures.<group>.<style_id>`:

```json
"headline_architectures": {
  "concept_structure": {
    "four_us": {
      "template": "Structure: one short, catchy, finished ad line built from ONE payload piece...",
      "examples": [
        "Lose Weight Without a Separate Diet",
        "A 15-Day Weight-Loss Reset"
      ],
      "support_strategy": {
        "direction": "Make the short promise specific and believable with proof, mechanism, and practical ease.",
        "must_include": ["specific detail", "unique mechanism or proof"],
        "avoid": "Do not turn support into another slogan."
      }
    }
  }
}
```

The hypothesis dropdown lists these style ids/labels. Changing `template` / `examples` changes what the copy LLM is told for that style.

---

## Local agent token

The dashboard counts **one active agent per Google account + device**. The token is stored at:

`~/ad-factory-agent/config/agent.json`

Keep that file when you restart the agent. Do **not** pass a fresh `--session-cookie` unless you intend to rebind this machine to a different dashboard session. A new cookie without the saved token registers another Mongo agent row.

Pairing itself is remembered in the browser (`localStorage`). Re-pairing every 1.5s is not required while the saved session is valid.
