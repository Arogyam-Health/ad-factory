# Operator plate guide

Ad Factory is a copy desk plus a local image press. The website on Render holds the plate, the copy job, and team access. The paired local agent on your machine generates images in Chrome and stores uploads and outputs locally.

This page is the product intro and the how-to. Dedicated docs live on GitHub:

- [All docs](https://github.com/Vinay-003/ad-factory/tree/render-setup/docs)
- [Repo, `render-setup` branch](https://github.com/Vinay-003/ad-factory/tree/render-setup)
- [Local agent setup](/docs/LOCAL_AGENT_README.md)
- [Developer cloud notes](/docs/DEVELOPER_CLOUD_MIGRATION.md)
- [Structured copy request](/docs/STRUCTURED_COPY_SYSTEM.md)
- [Editable fields map](/docs/DASHBOARD_EDITABLE_FIELDS.md)

## Product

### What Ad Factory is

You write and lock brand rules as plate files. Studio turns those files into a run: personas × formats × language × optional hypothesis. Render calls the copy LLM. The local agent turns that copy into images.

Two flows:

- **Structured** — copy LLM plus assembled image prompts. Uses plate files, hypothesis styles, visual archetypes, and business rules.
- **Reference** — you upload reference images and packshots. Uses the reference files and local uploads. It does not run the structured copy assembler.

Guests see the generic bundled plate, read only. Sign in to edit **My Config**. Team plates are editable when you have config access on that org.

### Pages

- **Studio** — pick source, flow, personas, formats, language, patterns, hypothesis, provider key, then send. File cards open the editor. Dry proofs list recent runs.
- **Config** — full file desk. Left list is Plate files, Hypothesis styles, and Business rules. Save all files, copy plates, snapshot versions.
- **Guide** — this page.
- **Teams** — orgs, members, shared vs individual plates.
- **Traces** — copy-LLM requests for a run.
- **Profile** — account and provider keys if you prefer to save keys there instead of Studio.

Studio and Config share one source. **My Config** and any team chip load the same Mongo document. Switching source is not a local overlay.

### How to use the editor

Open a file from a Studio card or from the Config file list. The editor always shows **Form** and **JSON**.

**Form** is the usual path. Each row is a field name and its value. Use JSON only when you need to paste the whole file.

**JSON** is the raw file. If the file is not valid JSON, Form stays disabled until you fix it there.

Text files such as Product Master Doc, Copy Starting Prompt, Leave Pattern To Image Model, and 9:16 Conversion Prompt stay as a textarea. Drag the bottom-right corner of the Studio modal to make that editor wider and taller. If you paste JSON into a text file, Form becomes available.

**Field, list, and group**

- **Field** — one name, one value. Rename the field, edit the value, or Delete the row.
- **List** — one name, many values. Short string lists show as chips. Longer items show as numbered rows. Use **+ Add value** or **+ Add item**.
- **Group** — a named folder of more fields. Open it with Show, then add fields, lists, or groups inside.

At the bottom of a group: **+ Add field**, **+ Add list**, **+ Add group**.

`_meta` and other keys that start with `_` are file headers, not styles. Live copy skips underscore keys when it builds style menus. `_meta.label` is the Studio layer name. `_meta.instruction` is the default copy-LLM rule for that layer. `_meta.type` is documentation.

Persona Seeds is a list of persona objects. Everyone else is an object of named groups.

Save in Config with **Save all files**. Save in Studio from the file modal. A save writes only to the selected owner. Generic bundled files are not rewritten from a personal or org save. After you save `ad_formats`, Studio chips refresh from `/api/defaults`.

### Copy a plate

**Copy to my config** is always on the Config toolbar. Any signed-in org member can copy a team plate onto their own personal plate. The copy snapshots whatever is already on your personal plate first. On My Config, the modal asks which team plate to pull.

**Copy to org** needs config-admin on the destination org. It writes the plate you are viewing onto that team.

Neither button changes the generic bundled files.

### Version snapshots

Saving files overwrites the live plate.

- Personal plates: click **Save version** when you want a snapshot.
- Shared org plates: each save snapshots automatically.

From History you can open a snapshot, roll it back, delete one snapshot, or delete older snapshots. Rollback on an org plate snapshots the current state first. The live plate is what Studio uses on the next run.

### Run a structured plate

1. Pick the source chip (My Config or a team).
2. Stay on Structured.
3. Click language chips. `ALL` expands to every language in `ad_languages`.
4. Click persona cards to select them. **Global formats** apply to selected personas only.
5. Format chips on a card change only that persona. They do not toggle the card. Unselected cards do not inherit global formats.
6. For each selected format, pick a visual pattern: Auto rotate, a named catalog pattern, or Leave it to the image model.
7. Optional: Hypothesis + Style, and Concept.
8. Save the provider key and model if this account does not already have one.
9. Set multiplier, ads per LLM call, shared background, and reuse-from-run if you need them.
10. Pair the local agent if this tab cannot see local files, then send the plate.

Studio shows up to eight selected formats as chips. Job settings still accept any catalog id that matches `[A-Z][A-Z0-9_]{0,15}`.

### Run a reference plate

Switch Studio to Reference. This flow uses local uploads and the reference files.

Shared with Structured: `persona_seeds`, `concept`, `ad_languages` (language chips), and `conversion_916_prompt`.

Reference only: `reference_starting_prompt`, `reference_product_master_doc`, plus reference images, packshots, and per-image comments on this machine.

Structured-only files are hidden here. Do not expect `ad_formats`, hypothesis styles, or visual archetypes to change a Reference run.

### Guardrails and output fields

Three different jobs:

- **Guardrails** (`ad_guardrails.always`, plus `no_hypothesis` when Hypothesis is None) are sent on every live copy call. They keep claims safe. They do not change the JSON schema. `task` and `repair_task` are the copy-LLM job lines.
- **Skeleton** is writing guidance. Changing skeleton text does **not** fail a run.
- **`output_fields`** is the acceptance schema for that format. After the LLM returns, the run requires those fields (except `trust_line`, which stays optional). Extra keys are ignored. One repair pass runs if a required field is empty; a second miss fails the run (`headline_missing`, `note_missing`, …).

If a format has no `output_fields`, the plate requires `headline` and `cta`.

### Language rules

`ad_languages` is a plate file. Studio chips come from `_modes`. The selected mode expands to language ids. Each id sends `label` and `rules` on the copy request.

Bundled rules: English stays fully English, Hindi stays Devanagari, Hinglish stays Roman-letter spoken Hindi. Edit those strings like any other plate file. Changing them does not fail a run.

### What will and will not fail a run

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

## Files

### Which file does what

Every Config / Studio card is one Mongo field on the selected plate. Structured uses most of them. Reference uses only the files marked below.

**Plate files (Structured unless noted)**

- **Ad Formats** (`ad_formats`) — format chips, `label`, `description`, `skeleton`, and required `output_fields`. Add a format here if you want a new chip.
- **Ad Languages** (`ad_languages`) — language chips, writing rules, persona field maps, `_modes`, `_persona_source_map`. Used by Structured copy and by Reference language chips.
- **Copy Guardrails** (`ad_guardrails`) — `always`, `no_hypothesis`, `task`, `repair_task`.
- **Concept** (`concept`) — Concept dropdown. Creative formats, not Concept Angle. Shared with Reference.
- **Copy Starting Prompt** (`copy_starting_prompt`) — copy-only starter. Sent to the copy LLM as `starting_prompt` when non-empty. Do not put image-only instructions here.
- **Copy Prompt Templates** (`copy_prompt_templates`) — `visual_archetypes` only. Pattern dropdowns and image layout. Live copy does not read this file.
- **Leave Pattern To Image Model** (`visual_archetype_llm_prompt`) — paragraph used when a format pattern is Leave it to the image model.
- **Prompt Assembler Templates** (`prompt_assembler_templates`) — image-prompt assembly. Includes this brand's `proof_bar_text` and `headline_bans`.
- **Background Variant** (`background_variant`) — background catalog for Structured image prompts.
- **9:16 Conversion Prompt** (`conversion_916_prompt`) — converts a 4:5 creative to 9:16. Shared with Reference.

**Hypothesis styles (Structured only, and only when you pick a type + style)**

- **Hook Structures** (`ad_hooks`)
- **Concept Angles** (`ad_angles`)
- **Copy Frameworks** (`ad_frameworks`) — there is no PAB framework. Do not add one.
- **Proof Strategies** (`ad_proof`)
- **Objection Strategies** (`ad_objections`)
- **Value Propositions** (`ad_value_props`)
- **Awareness Stages** (`ad_awareness`)
- **Emotional Drivers** (`ad_emotions`)
- **Specificity Levels** (`ad_specificity`)
- **Feature Focus** (`ad_feature_focus`)
- **Support Shapes** (`ad_support_shapes`)

Each style is a small object: `label`, `instruction`, `definition`, `skeleton`. Missing keys are fine. Studio lists `label`. A stored `{}` inherits the generic bundled file. To send no styles, save a non-empty object such as `{ "_meta": { "label": "Hook Structure" } }`. Blank `""` fields are omitted.

**Business rules (this brand's lock)**

- **Product Master Doc** (`product_master_doc`) — product truth for Structured copy. Empty fails a Structured run.
- **Persona Seeds** (`persona_seeds`) — persona cards for both flows.
- **Starting Prompt** (`starting_prompt`) — image starter prepended to ChatGPT / local image prompts. Not sent to the copy LLM.
- **Reference Starting Prompt** (`reference_starting_prompt`) — Reference flow only.
- **Reference Product Doc** (`reference_product_master_doc`) — Reference flow only.

Persona seed field names are mapped in `ad_languages._persona_source_map`. Hindi / Hinglish fillers are not invented.

The live proof bar text is `prompt_assembler_templates.proof_bar_text`. Headline replacement regexes are `headline_bans`. Both keep this brand's current wording.

### Visual archetypes

Copy Prompt Templates is image-pattern only. The name is leftover from when it held copy-LLM blocks.

Open **Copy Prompt Templates** → Form → `visual_archetypes` → a format (`HERO`, `BA`, `TEST`, `FEAT`, `UGC`, or a format you added) → a pattern.

Each pattern has `id` (stored on the run), `label` (Studio dropdown), `layout_lines`, and `direction_lines`.

If Form only shows `format` and `_description`, the plate was hollow. Refresh Config so the bundled catalog fills in, then Save once. You can also Copy to my config from a full team plate.

**Auto rotate** picks a random catalog pattern, preferring ones not yet used in the same batch.

**Leave it to the image model** (`llm_decide`) sends `visual_archetype_llm_prompt` instead of a named pattern. It is not sent to the copy LLM.

A named catalog id locks that pattern for image assembly.

### Add or remove a format

Add a new object key in **Ad Formats** (`ad_formats`), for example `STORY` or `HERO_V4`. Use an id like `[A-Z][A-Z0-9_]{0,15}` — no spaces. Include `label`, `description`, `skeleton`, and `output_fields`. Adding a key only in Copy Prompt Templates does not create a Studio chip.

On personal or org save:

- New format ids get a stub visual archetype (`{id}_default`) in that owner's `copy_prompt_templates`.
- Removed format ids drop their archetype arrays on the same owner.
- The save notice looks like: `Added default visual archetypes for STORY. Edit Copy Prompt Templates and make them meaningful.`

Those stubs are placeholders. Edit layout and direction lines. Background slots are not auto-created; if no variant lists the new format, the run uses the full background pool.

## Local agent

### What the agent does

Render does not launch Chrome and does not store image bytes. The paired agent on this machine:

- Registers with the dashboard using your `session` cookie
- Polls for image jobs
- Drives installed Chrome over CDP (`http://127.0.0.1:9222`)
- Writes uploads, prompts, and outputs under `~/ad-factory-agent` (Windows: `%USERPROFILE%\ad-factory-agent`)
- Serves those files back to the paired browser tab on loopback (`http://127.0.0.1:8765`)

Copy LLM calls (OpenCode / Gemini text) already run from the control plane. Image generation is the local step.

### Setup

Operators should install the zip, not clone the repo.

1. Download [ad-factory-local-agent.zip](https://github.com/Vinay-003/ad-factory/raw/render-setup/ad-factory-local-agent.zip).
2. Unzip it. Leave `scripts/`, `local_agent_runtime/`, `dashboard/backend/`, and `docs/` inside the folder.
3. Install Google Chrome. Do not run `playwright install chromium`.
4. Install **Python 3.12 exactly**. 3.13+ fails because the agent still uses
   Python's `cgi` module, which was removed after 3.12. Create a local `.venv`
   with that 3.12 binary. Do not `pip install` globally.

```text
Windows:  py -3.12 -m venv .venv
          .venv\Scripts\python.exe -m pip install -r requirements-local-agent.txt

Ubuntu / macOS:  python3.12 -m venv .venv
                 .venv/bin/python -m pip install -r requirements-local-agent.txt
```

5. Open the dashboard **on this same machine**, sign in, copy the `session` cookie (DevTools → Application → Cookies).
6. Start the agent and paste the cookie:

```text
Windows:  .venv\Scripts\python.exe scripts\start_local_agent.py
Ubuntu / macOS:  .venv/bin/python scripts/start_local_agent.py
```

7. Sign in to ChatGPT and/or Gemini in the Chrome window the agent opens. Keep it open.
8. In Studio, click **Pair local agent** if the tab cannot read local files.

Later starts can press Enter with a blank cookie if `~/ad-factory-agent/config/agent.json` already exists. Pass a fresh cookie only when you intend to rebind this Google account.

OS-by-OS guides:

- [Overview](/docs/LOCAL_AGENT_README.md)
- [Ubuntu](/docs/LOCAL_AGENT_UBUNTU.md)
- [Windows](/docs/LOCAL_AGENT_WINDOWS.md)
- [macOS](/docs/LOCAL_AGENT_MAC.md)
- [Production pairing and recovery](/docs/LOCAL_FIRST_OPERATIONS.md)

### Using the agent from Studio

- Image engine (ChatGPT or Gemini) is a Studio dry-proof setting, not a plate file.
- Reuse backgrounds or visual patterns from an earlier run when you want the same look.
- Keep same background across personas shares one background group in a batch.
- Packshots and reference uploads stay on this machine.
- If pairing fails, you are probably in a browser on a different machine than the agent.

## Developers

This section is for people who clone [Vinay-003/ad-factory](https://github.com/Vinay-003/ad-factory/tree/render-setup) on the `render-setup` branch. Operators can skip it.

The long form of these notes is [Developer cloud notes](/docs/DEVELOPER_CLOUD_MIGRATION.md). Source: [docs/DEVELOPER_CLOUD_MIGRATION.md](https://github.com/Vinay-003/ad-factory/blob/render-setup/docs/DEVELOPER_CLOUD_MIGRATION.md).

### Clone and run

```bash
git clone https://github.com/Vinay-003/ad-factory.git
cd ad-factory
git checkout render-setup
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dashboard.txt
uvicorn dashboard.backend.control_app:app --host 0.0.0.0 --port 4090
```

Dashboard UI source is `dashboard/web/src`. After a UI change: `cd dashboard/web && npm install && npm run build`. The served SPA is `dashboard/web/dist`.

Local agent from a clone: `python scripts/start_local_agent.py`. Env vars are in [`.env.example`](https://github.com/Vinay-003/ad-factory/blob/render-setup/.env.example). Production topology is [LOCAL_FIRST_OPERATIONS.md](/docs/LOCAL_FIRST_OPERATIONS.md).

### Add an API image path next to the browser

Today `POST /api/runs/{run_id}/image-generation` queues `execute_run` / `generate_images`. The local agent runs `StructuredBrowserExecutor`, which shells into `chatgpt_web_sutomation.py` or `gemini_web_automation.py`.

To add an official image API (OpenAI Images, Gemini image API, or similar) **alongside** that browser path:

1. Add an engine id in Studio next to ChatGPT / Gemini, for example `openai_api`.
2. Pass that `engine` on the existing image-generation POST.
3. In `local_agent_runtime/local_agent.py` `execute_job`, branch on the new engine instead of always using `StructuredBrowserExecutor`.
4. Write a new executor that builds the same assembled prompt + packshot payload, calls the HTTP API, and commits bytes through the existing `_commit_output` / projection path so Studio still lists the run the same way.
5. Reuse provider-config materialization. Keys are already encrypted in Mongo. Do not log them.
6. Keep browser engines working. The API path is an extra engine, not a replacement, until you choose to retire Chrome.

Do not put image bytes on Render for this step. The agent still writes `~/ad-factory-agent` and serves loopback.

### Move image API calls onto Render

Copy LLM calls already leave the laptop. Image jobs do not. To run **image API calls on Render** and drop the local browser for that path:

- Render still cannot launch Chrome. Browser engines stay local or go away.
- Render has no content disk. You need object storage (see S3 below) for packshots and outputs before Render can generate.
- Add a Render-side worker that claims the same job type, or a new job type, and calls the image API there. HTTP request handlers on Render will time out on a full batch.
- Dashboard asset URLs must stop pointing at `http://127.0.0.1:8765` for those runs.
- Env policy today forbids putting generation credentials and `STORAGE_PROVIDER` content backends on Render. You would change that policy, `validate_production_settings`, and the pairing requirement so an API-only user can run without an agent.
- Pairing stays required for anyone still using ChatGPT / Gemini in a local window.

Full checklist: [Developer cloud notes](/docs/DEVELOPER_CLOUD_MIGRATION.md#move-image-api-calls-onto-render).

### Move local files to S3

Uploads, generated images, prompts, revisions, and traces live under `~/ad-factory-agent` today. `STORAGE_PROVIDER` defaults to `local` and is not wired to a bucket.

To move that content to Amazon S3:

- Create a private bucket, IAM user or role, and prefix per `user_id` / `run_id`.
- Replace local byte writes in `local_agent_runtime/storage.py` and the loopback data plane with upload + signed GET.
- Studio / Reference upload endpoints must send bytes to S3 instead of the agent disk.
- Keep Mongo for plate files, run metadata, and encrypted keys. Do not store image bytes in Mongo.
- Plan a one-time copy of existing `~/ad-factory-agent` trees if you need old runs.

Full checklist: [Developer cloud notes](/docs/DEVELOPER_CLOUD_MIGRATION.md#move-local-files-to-amazon-s3).
