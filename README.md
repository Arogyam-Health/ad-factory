# Ad Creative System

**Interview prep:** see [`INTERVIEW_README.md`](INTERVIEW_README.md) for full architecture, auth, orgs, agents, admin, and Q&A.

Structured-prompt generator for **Obesity Killer Kit** ad creatives. Produces 9-section image-generation prompts across 5 formats (HERO, BA, TEST, FEAT, UGC) and 3 languages (EN, HI, HINGLISH). The output is text — actual images are rendered downstream in Gemini Web / ChatGPT.

The full system map and pipeline live in [`docs/HANDOVER.md`](docs/HANDOVER.md). Rules live in [`AD_CREATIVE_SYSTEM_PLAYBOOK.md`](AD_CREATIVE_SYSTEM_PLAYBOOK.md).

## Prerequisites

- Python 3.10+
- Node.js LTS (only required if `opencode` CLI is not already installed — the bootstrap script installs it via npm)

## Platform setup guides

| Platform | Guide |
| --- | --- |
| **Download zip (Windows / Ubuntu / macOS)** | [`docs/LOCAL_AGENT_README.md`](docs/LOCAL_AGENT_README.md) |
| **Ubuntu local agent** | [`docs/LOCAL_AGENT_UBUNTU.md`](docs/LOCAL_AGENT_UBUNTU.md) |
| **Windows local agent** | [`docs/LOCAL_AGENT_WINDOWS.md`](docs/LOCAL_AGENT_WINDOWS.md) |
| **macOS local agent** | [`docs/LOCAL_AGENT_MAC.md`](docs/LOCAL_AGENT_MAC.md) |
| **Windows + WSL2** full dashboard stack | [`docs/WSL_SETUP.md`](docs/WSL_SETUP.md) |
| **macOS** full dashboard stack | [`docs/MAC_SETUP.md`](docs/MAC_SETUP.md) |

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

`local_agent_runtime/browser.py` finds Chrome/Brave on the current machine:

1. `CHROME_PATH`, `BROWSER_PATH`, or `AD_FACTORY_CHROME` if set
2. Names on `PATH` (`google-chrome`, `chrome`, `chrome.exe`, `chromium`, …)
3. Common Linux, macOS, and Windows install locations, using `Path.home()`,
   `LOCALAPPDATA`, `PROGRAMFILES`, and `PROGRAMFILES(X86)` — not a hardcoded
   user folder

Install Google Chrome before running the local agent. On Ubuntu see
[`docs/LOCAL_AGENT_UBUNTU.md`](docs/LOCAL_AGENT_UBUNTU.md). On Windows see
[`docs/LOCAL_AGENT_WINDOWS.md`](docs/LOCAL_AGENT_WINDOWS.md). On macOS install
Chrome from <https://google.com/chrome/>.

The local agent attaches to that installed Chrome over CDP. Do not run
`playwright install chromium` for the local agent.

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

## Gitignored (not in the repo)

- `.venv/` — Python virtualenv
- `.local-stack/` — local dashboard pids and logs
- `.sixth/`, `.commandcode/` — local tool caches

---

## Cloud Deployment (Render)

The dashboard can be deployed to Render as a multi-user stateless control
plane. Uploads, provider calls, prompt assembly, browser automation, and all
content storage stay on the paired local machine.

For production deployment, pairing, migration, backup/restore, organization
replication, outage recovery, and security procedures, follow
[`docs/LOCAL_FIRST_OPERATIONS.md`](docs/LOCAL_FIRST_OPERATIONS.md).

### Architecture

```
[Render]
  ├── FastAPI backend + static frontend
  ├── MongoDB Atlas (bounded control metadata only)
  ├── Google OAuth login
  └── REST API for local agent

[Your Machine]
  └── Local data plane + Playwright agent
      ├── Stores configs, prompts, uploads, outputs, logs, and revisions
      ├── Connects to Chrome at http://127.0.0.1:9222
      ├── Polls Render for jobs
      └── Runs provider and browser workflows
```

### Setup

1. **MongoDB Atlas** — Create a free cluster at https://mongodb.com, get your connection string
2. **Google OAuth** — Create credentials at https://console.cloud.google.com/apis/credentials, configure redirect URI
3. **Render** — Deploy from GitHub and set the authentication/control-plane env vars (see `.env.example`). Do not configure content storage.
4. **Local agent** — On another machine download
   [`ad-factory-local-agent.zip`](ad-factory-local-agent.zip) and follow
   [`docs/LOCAL_AGENT_README.md`](docs/LOCAL_AGENT_README.md), then:
   ```bash
   python scripts/start_local_agent.py
   ```

### Environment variables

See `.env.example` for all required env vars.

### Browser automation stays local

Render does **not** launch Chrome. The local agent:
- Connects to your Chrome at `http://127.0.0.1:9222` (start it with `--remote-debugging-port=9222`)
- Polls the Render API for assigned jobs
- Reports bounded progress and content references back to Render
- Serves authenticated content to the paired browser at `http://127.0.0.1:8765`

### Local agent setup

Download [`ad-factory-local-agent.zip`](ad-factory-local-agent.zip) and follow
[`docs/LOCAL_AGENT_README.md`](docs/LOCAL_AGENT_README.md)
(Windows, Ubuntu, or macOS). Short version after unzip and `pip install`:

```bash
python scripts/start_local_agent.py
```

The launcher asks for the dashboard `session` cookie (hidden input), then starts
Chrome with CDP and registers with Render. Log in to ChatGPT/Gemini in the
Chrome window it opens.

Keep `~/ad-factory-agent/config/agent.json`. Restarting the agent reuses that token. Pass a fresh session cookie only when you intend to rebind this Google account; the control plane reuses one active agent per user+device instead of inserting duplicates.

### Production mode

Set `DEPLOYMENT_MODE=production` on Render. This enables:

- **Auth middleware** — All `/api/*` routes (except `/api/auth/*`) require a valid session cookie. Returns 401 if missing.
- **Startup validation** — App refuses to start if critical env vars are missing/default (MONGODB_URI, APP_SECRET_KEY, ENCRYPTION_KEY, GOOGLE OAuth, CORS)
- **Stateless runtime boundary** — Render has no content disk. Uploaded/generated content uses localhost; the eight dashboard config files use MongoDB.
- **Chrome routes disabled** — `/api/launch-visible-browser`, `/api/kill-chrome`, `/api/stop-generation` return 400 with "Use local agent"

### Content storage boundary

User uploads, generated prompts/images, revisions, exports, traces, and browser
logs stay on the paired local device. The eight bounded dashboard configuration
files and user-scoped provider settings are stored in MongoDB so they load
without a local agent. Provider API keys are encrypted with `ENCRYPTION_KEY` and
ordinary API responses expose only whether a key is configured. Render has no
content storage provider or persistent disk and does not use Cloudinary,
GridFS, or Redis.
