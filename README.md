# Ad Creative System

Structured-prompt generator for **Obesity Killer Kit** ad creatives. Produces 9-section image-generation prompts across 5 formats (HERO, BA, TEST, FEAT, UGC) and 3 languages (EN, HI, HINGLISH). The output is text — actual images are rendered downstream in Gemini Web / ChatGPT.

The full system map and pipeline live in [`docs/HANDOVER.md`](docs/HANDOVER.md). Rules live in [`AD_CREATIVE_SYSTEM_PLAYBOOK.md`](AD_CREATIVE_SYSTEM_PLAYBOOK.md).

## Prerequisites

- Python 3.10+
- Node.js LTS (only required if `opencode` CLI is not already installed — the bootstrap script installs it via npm)

## Platform setup guides

| Platform | Guide |
| --- | --- |
| **Windows + WSL2** (Intel/AMD or Snapdragon) | [`docs/WSL_SETUP.md`](docs/WSL_SETUP.md) |
| **macOS** (Apple Silicon or Intel) | [`docs/MAC_SETUP.md`](docs/MAC_SETUP.md) |

For a quick start on any Linux machine (or inside WSL/WSL2):

```bash
bash scripts/bootstrap_stack.sh
```

The script:
1. Creates `.venv/` if missing
2. Installs pinned deps from `requirements-dashboard.txt`
3. Installs the `opencode` CLI if not on `PATH`
4. Starts the dashboard

Then login a provider:

```bash
opencode providers login
opencode models
```

The dashboard runs at `http://127.0.0.1:8787`.

## Manual setup (skip the script)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dashboard.txt
```

## Server password

`dashboard/backend/app.py` reads the server password from one of:
- `config.json` → `opencode_api_key` field
- env var: `OPENCODE_SERVER_PASSWORD`

Set whichever path you use before starting the dashboard.

## Pinned dependencies

See [`requirements-dashboard.txt`](requirements-dashboard.txt). Current pins:

| Package | Version |
| --- | --- |
| fastapi | 0.115.12 |
| uvicorn[standard] | 0.34.2 |
| python-multipart | 0.0.20 |
| openpyxl | 3.1.5 |
| psutil | 7.2.2 |
| opencode-ai | 0.1.0a36 |
| Pillow | 12.2.0 |
| playwright | 1.59.0 |
| selenium | 4.32.0 |

CDP (Chrome DevTools Protocol) is used via Playwright's `connect_over_cdp` and `new_cdp_session` (`scripts/gemini_web_automation.py:536`, `scripts/chatgpt_web_sutomation.py:558`) — no separate CDP library is needed. The dashboard launches a real Chrome instance with a remote-debugging port at `http://127.0.0.1:9222` (`dashboard/backend/app.py:5700`) and the automation scripts attach to it.

### Browser binary (Playwright + Chrome)

`scripts/gemini_web_automation.py:505` and `scripts/chatgpt_web_sutomation.py:521` look for Chrome/Chromium in this order:

1. `--chrome-path` CLI arg, if passed
2. `/usr/bin/google-chrome`
3. `/usr/bin/google-chrome-stable`
4. `/snap/bin/chromium`
5. `/usr/bin/chromium-browser`
6. `/usr/bin/chromium`
7. `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`
8. `/Applications/Chromium.app/Contents/MacOS/Chromium`

Install one of these before running browser automation. On Debian/Ubuntu: `sudo apt install google-chrome-stable` (or `chromium-browser`). On macOS: install Chrome from <https://google.com/chrome/> (the path #7 above will be found automatically).

After `pip install`, also run:

```bash
playwright install chromium
```

to fetch the Playwright-bundled Chromium build used as a fallback when no system Chrome is found.

## Run

```bash
# Start the full stack (dashboard + opencode server)
bash scripts/start_dashboard_stack.sh

# Stop it
bash scripts/stop_dashboard_stack.sh
```

## Assemble prompts

```bash
# From an LLM-generated copy JSON
python scripts/generate_ads.py --copy-file path/to/copy_batch.json

# From an xlsx export
python scripts/assemble_from_xlsx.py --xlsx path/to/on-image-copy.xlsx

# Dry run (validate without writing)
python scripts/generate_ads.py --copy-file copy.json --dry-run
```

See `docs/HANDOVER.md` for the full pipeline reference, validation gates, and what-not-to-do list.

## Gitignored (not in the repo — regenerated locally)

- `.venv/` — Python virtualenv
- `output/`, `generated_images/` — generated prompts and images
- `dashboard_storage/` — dashboard run manifests
- `runtime/` — generation logs, queues, prompt caches
- `.sixth/`, `.commandcode/` — local tool caches

---

## Cloud Deployment (Render)

The dashboard can be deployed to Render as a multi-user cloud service, while browser automation stays on your local machine.

### Architecture

```
[Render]
  ├── FastAPI backend + static frontend
  ├── MongoDB Atlas (all persistent data)
  ├── Google OAuth login
  └── REST API for local agent

[Your Machine]
  └── Local Playwright agent
      ├── Connects to Chrome at http://127.0.0.1:9222
      ├── Polls Render for jobs
      └── Runs Gemini/ChatGPT automation scripts
```

### Setup

1. **MongoDB Atlas** — Create a free cluster at https://mongodb.com, get your connection string
2. **Google OAuth** — Create credentials at https://console.cloud.google.com/apis/credentials, configure redirect URI
3. **Render** — Deploy from GitHub, set env vars (see `.env.example`)
4. **Local agent** — Run on your machine:
   ```bash
   python scripts/local_agent.py --api-base https://your-app.onrender.com
   ```

### Environment variables

See `.env.example` for all required env vars.

### Browser automation stays local

Render does **not** launch Chrome. The local agent:
- Connects to your Chrome at `http://127.0.0.1:9222` (start it with `--remote-debugging-port=9222`)
- Polls the Render API for assigned jobs
- Reports progress and results back to Render

### Local agent setup

```bash
# 1. Start Chrome with remote debugging
google-chrome --remote-debugging-port=9222

# 2. Login to ChatGPT/Gemini in that Chrome window

# 3. Run the local agent
python scripts/local_agent.py --api-base https://your-app.onrender.com --name my-laptop

# 4. The agent registers with Render and waits for jobs
```

### Production mode

Set `DEPLOYMENT_MODE=production` on Render. This enables:

- **Auth middleware** — All `/api/*` routes (except `/api/auth/*`) require a valid session cookie. Returns 401 if missing.
- **Startup validation** — App refuses to start if critical env vars are missing/default (MONGODB_URI, APP_SECRET_KEY, ENCRYPTION_KEY, GOOGLE OAuth, CORS)
- **No public data mounts** — `/storage`, `/output`, `/generated_images` are NOT mounted in production. Use `/api/files/download/*` endpoints instead (authenticated, path-traversal protected)
- **Chrome routes disabled** — `/api/launch-visible-browser`, `/api/kill-chrome`, `/api/stop-generation` return 400 with "Use local agent"

### Current status (what's still local-only)

The following operations still use the local filesystem in dev mode, even when MongoDB is available:

| Area | Filesystem path | Status |
|------|----------------|--------|
| Run manifests, configs | `dashboard_storage/runs/` | Local-only |
| Generated prompt files | `output/v{NN}/` | Local-only |
| Generated images | `generated_images/` | Local-only |
| Image metadata JSONs | Sidecar `.json` next to images | Local-only |
| Input images | `input/images/` | Local-only |
| LLM traces | `runtime/llm_traces/` | **Dual**: writes to MongoDB, still reads from disk |
| Product master doc | `input/docs/product master doc.txt` | Local-only |
| Persona seeds | `persona_seeds.json` | Local-only |
| Copy architecture | `dashboard/backend/copy_architecture.json` | Local-only |
| Copy prompt templates | `dashboard/backend/copy_prompt_templates.json` | Local-only |
| Google/Gemini provider config | `.env.dashboard` | Local-only |

Full migration of these to MongoDB-backed services is ongoing.

### Migration from local files

```bash
# Import existing local data into MongoDB
python scripts/migrate_to_mongo.py --user-id <your-user-id>

# See what would be imported first
python scripts/migrate_to_mongo.py --user-id <your-user-id> --dry-run
```
