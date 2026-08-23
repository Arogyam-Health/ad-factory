# Ad Factory — Interview Reference

> **Purpose:** Single source for explaining this project in technical interviews (system design, backend, full-stack, security, DevOps).  
> **Product name:** Ad Factory (repo folder: `info`).  
> **Domain:** AI-assisted **static ad creative** generation for DTC/e‑commerce (primary product: Obesity Killer Kit — Ayurvedic weight-management kit).  
> **Aligned with:** local-first control/data plane (Render `control_app` + localhost agent), Phase 6 admin, org/config versioning.  
> **Deeper ops:** [`docs/LOCAL_FIRST_OPERATIONS.md`](docs/LOCAL_FIRST_OPERATIONS.md) · **Implementation handoff:** [`LOCAL_DATA_PLANE_IMPLEMENTATION.md`](LOCAL_DATA_PLANE_IMPLEMENTATION.md) · **Creative rules:** [`AD_CREATIVE_SYSTEM_PLAYBOOK.md`](AD_CREATIVE_SYSTEM_PLAYBOOK.md)

---

## 30-Second Pitch

**Ad Factory** turns product truth + buyer personas into **on-brand static ad creatives at scale**. It is not “call an image API.” It runs a **rules-heavy pipeline** (playbook + registry + dedup) to produce **LLM ad copy**, then **structured 9-section image prompts**, then **images** via provider APIs or **browser automation** (ChatGPT / Gemini Web).

Production architecture is a deliberate split:

- **Render = stateless metadata control plane** (`dashboard.backend.control_app:app`) — auth, orgs, eight config files, job/run metadata, structured-copy planning.
- **User laptop = local data plane** (`scripts/local_agent.py` + `127.0.0.1:8765`) — uploads, prompt bodies, images, traces, Playwright/CDP, provider HTTPS.

Stack: **FastAPI + MongoDB Atlas + React (Vite) press-room UI**, Google OAuth, org RBAC, Fernet-encrypted provider keys, super-admin ops dashboard.

---

## 2-Minute Story (STAR-Friendly)

| | |
|---|---|
| **Situation** | Marketing needed many static ads (5 formats × 3 languages × dozens of personas) without repeating copy/visuals or inventing non-compliant health claims — and without paying for cloud object storage / Redis on free tiers. |
| **Task** | Encode creative rules, support multi-user teams, run a cloud dashboard, keep creative bytes and browser automation on a real Chrome machine. |
| **Action** | Built FastAPI control plane on Render + Mongo for bounded metadata; local agent with SQLite + content-addressed objects; pairing challenge so the browser talks to loopback only; encrypted TTL prompt delivery; in-memory provider relay for structured copy validation; Phase 6 admin readiness/exports. |
| **Result** | End-to-end Structured + Reference flows; multi-user auth/orgs; content never lands on Render disk; free-tier deploy with cold-start resilience via WS + poll + local outbox. |

---

## What the System Actually Produces

1. **Ad copy** — headline, support, bullets, CTA under `copy_architecture.json` intent rules (not fill-in-the-blank templates).
2. **Image prompts** — 9 mandatory sections (subject, background, composition, style, lighting, color, typography, mood, technical) per playbook.
3. **Images** — PNGs via provider APIs or browser automation (local CDP).
4. **Metadata** — hypothesis IDs, background slots, registry entries for dedup / A/B cells; Mongo holds **hashes/ids/status**, not bodies.

**Formats:** `HERO`, `BA`, `TEST`, `FEAT`, `UGC`  
**Languages:** `EN`, `HI`, `HINGLISH`  
**Aspect ratios:** 4:5 (default), 9:16 (conversion pass)

---

## Tech Stack & Rationale

| Layer | Choice | Why (interview answer) |
|-------|--------|-------------------------|
| Control API | **FastAPI** (`control_app.py`) | Typed routes, middleware, static frontend, WebSockets for agent runtime; production entry in `render.yaml`. |
| Legacy monolith | **`app.py` (~9k lines)** | Still exists for local/dev full mounts (chrome/extension/content). **Production does not start it.** |
| DB | **MongoDB Atlas (free)** | Flexible docs for auth/orgs/configs/jobs; TTL indexes; no ORM — explicit pymongo. |
| Frontend | **Vanilla JS** (no bundler) | Zero build; FastAPI serves static; domain modules (`runs.js`, `local-data-plane.js`, `admin.js`). |
| Auth | **Google OAuth 2.0** + **HttpOnly session cookie** | Server-side session (SHA-256 hashed token); not JWT-in-localStorage. |
| Secrets | **Fernet** (`ENCRYPTION_KEY`) | Provider keys encrypted in `provider_configs`; UI only sees “configured?”. |
| Content | **Local agent disk** (`~/ad-factory-agent`) | SQLite manifests + `objects/sha256/`; **no Cloudinary / GridFS / Redis / Render disk**. |
| Automation | **Playwright + CDP** | Image UIs lack stable APIs; attach to Chrome `:9222`. |
| Deploy | **Render free** | `uvicorn dashboard.backend.control_app:app`; cold starts expected. |
| Coordination | **WebSocket + HTTP poll** | Free Render WS can drop; poll + local outbox keep jobs alive. |

**Pinned deps:** `requirements-dashboard.txt` (FastAPI 0.115, Playwright 1.59, etc.).

---

## Architecture — How Parts Talk to Each Other

This is the #1 system-design question for this project.

```
┌──────────────────────────────┐     HTTPS + session cookie      ┌─────────────────────────────────────┐
│  Browser (dashboard UI)      │◄───────────────────────────────►│  Render: control_app:app            │
│  dashboard/web/ (React SPA)  │                                 │  Auth · orgs · 8 configs · jobs     │
│  src/lib/local-data-plane.js │                                 │  Structured-copy planning/validate  │
└────────────┬─────────────────┘                                 │  Provider relay (in-memory)         │
             │ loopback only                                     │  Content routes → HTTP 410          │
             │ http://127.0.0.1:8765                             └──────────────┬──────────────────────┘
             ▼                                                                  │
┌──────────────────────────────┐     WS /api/agent-runtime/ws                   │
│  Local agent                 │◄──── + GET /api/agents/jobs/poll ──────────────┤
│  scripts/local_agent.py      │     Bearer agent token                         │
│  local_agent_runtime/        │                                  ┌─────────────▼──────────────────────┐
│  • artifact_server :8765     │                                  │  MongoDB Atlas                      │
│  • SQLite + objects/sha256   │                                  │  Metadata + 8 config files          │
│  • CDP → Chrome :9222        │                                  │  Encrypted provider keys            │
│  • Provider HTTPS (direct)   │                                  │  Encrypted TTL prompt deliveries    │
└──────────────────────────────┘                                  └────────────────────────────────────┘
```

### Control plane vs data plane

| Plane | Where | Authoritative for | Entry |
|-------|-------|-------------------|-------|
| **Control** | Render | Users, sessions, orgs, eight configs + versions, run/job **metadata**, encrypted keys, copy-job planning | `control_app.py` + `control_plane_policy.py` |
| **Data** | User machine | Uploads, prompt text, images, traces, logs, revisions, browser automation, provider call bodies | `scripts/local_agent.py` + `local_agent_runtime/` |

**Hard boundary:** `is_render_content_route()` → **410 Gone** for legacy content endpoints (`/generate-images-45`, `/execute`, uploads, file downloads, etc.). Stale clients cannot accidentally write content to Render.

**Metadata gate:** `validate_metadata_document()` rejects content-bearing field names, localhost URLs, local paths, oversized strings before Mongo writes.

### How the three actors communicate

| From → To | Protocol | What crosses |
|-----------|----------|--------------|
| Browser → Render | HTTPS + `session` cookie | Metadata APIs only (allocate run, enqueue jobs, edit eight configs, org admin) |
| Browser → Local agent | `http://127.0.0.1:8765/v1/*` + short-lived pairing token in `sessionStorage` | Uploads, prompt/image bytes, backup ZIP, Blob URL image loads |
| Local agent → Render | Bearer token; **WS** + **HTTP poll fallback** | Heartbeats, claim/progress/complete, prompt-delivery poll/ack, provider relay messages |
| Render → Local agent (structured copy) | In-memory **provider relay** over WS | Bounded provider request → agent HTTPS to allowlisted provider → bounded response back (never persisted) |
| Render → Local agent (final prompts) | Encrypted **prompt_deliveries** (TTL) | Ciphertext until agent imports + acks (then deleted) |

### Why this design (say this out loud)

1. **Free Render has no durable content disk** and sleeps — creative bytes must not live there.
2. **Free Atlas 16 MiB docs** — only eight bounded config files + metadata; not images/prompts.
3. **Browser automation needs a real logged-in Chrome** — impossible on Render.
4. **Privacy / cost** — no Cloudinary/S3/Redis; content stays on the user’s machine.
5. **Configs still edit after login without an agent** — the eight files are the explicit Mongo exception.

---

## Repository Layout (Name These in Interviews)

```
info/
├── dashboard/
│   ├── backend/
│   │   ├── control_app.py          # PRODUCTION entry (Render)
│   │   ├── control_plane_policy.py # 410 content routes + metadata validation
│   │   ├── app.py                  # Legacy/dev monolith (~9k lines)
│   │   ├── auth/                   # Google OAuth + sessions
│   │   ├── admin/                  # Super-admin API + redaction
│   │   ├── agent/                  # Register, pairing, jobs, prompt delivery
│   │   ├── db/                     # settings, client, indexes, collections
│   │   ├── routes/                 # runs, execute, generate, batch, traces, …
│   │   ├── services/               # org, invite, config, provider, relay, copy jobs
│   │   └── security/crypto.py      # Fernet, hash_token
│   └── frontend/                   # Static HTML + js/
├── local_agent_runtime/            # Storage, artifact server, relay, workflows
├── scripts/
│   ├── local_agent.py              # Local supervisor (~2k lines)
│   ├── generate_ads.py             # Offline prompt assembler
│   ├── gemini_web_automation.py / chatgpt_web_sutomation.py
│   └── check_admin_routes.py
├── dashboard/web/                  # React press-room UI (served at /)
├── input/                          # Shipped creative defaults
├── tests/                          # smoke + local-data-plane + control-plane suites
├── render.yaml                     # Starts control_app:app
├── docs/LOCAL_FIRST_OPERATIONS.md
├── LOCAL_DATA_PLANE_IMPLEMENTATION.md
└── INTERVIEW_README.md             # This file
```

---

## Core Pipeline: Structured Run Lifecycle

**User journey (production):** pair local plane → allocate run → structured copy on Render → prompts delivered to laptop → image-generation job → local CDP → view via localhost Blob URLs.

| Step | Who | API / path | What happens |
|------|-----|------------|--------------|
| 0 | Browser ↔ agent | Pairing: `/v1/info` → Render challenges → WS approval → `/v1/pairing/sessions` | Short-lived scoped local session |
| 1 | Browser → Render | `POST /api/runs/allocate` or `/allocate-copy` | Mongo `runs` + `run_counters`; **no content disk on Render** |
| 2 | Browser → localhost | `/v1/runs`, `/v1/assets` | Local workspace + uploads (never proxied through Render) |
| 3 | Browser → Render | `POST /api/runs/{id}/structured-copy` | Enqueue `render_copy_jobs`; worker plans copy |
| 4 | Render ↔ agent | Provider relay WS `provider_call` | Agent calls provider HTTPS; response returns in-memory for validate/repair/assemble |
| 5 | Render → Mongo → agent | Encrypt bundle → `prompt_deliveries` → poll/ack | Final prompts land **only** on local disk; ciphertext deleted after ack |
| 6 | Browser → Render | `POST /api/runs/{id}/image-generation` | Metadata job pinned to `agent_id` + `device_id`, command `generate_images` |
| 7 | Agent | CDP ChatGPT/Gemini | Uses **local** prompt text + ordered upload sets; writes images under data root |
| 8 | Browser → localhost | `/v1/...` authenticated fetch | Images as Blob URLs (no capability-bearing CDN URLs) |

**Reference flow** (different path): `POST /api/runs/{id}/reference-generation` → local prompt assembly per persona×reference; upload order = reference first, then products (`reference_workflow.py` / `reference-flow.js`).

**Legacy monolith path** (local `app.py` only): `POST /api/runs` → `/execute` → `/generate-images-45` writing `output/` + `generated_images/`. On Render those routes are **410**.

---

## Creative Rules Engine (Domain Depth)

Interviewers ask “how do you prevent bad ads?” — answer with **layers**:

1. **Product master doc** — only approved claims; no “fat burner” language.
2. **Playbook** — awareness stages, format specs, safe zones, CHK-01…CHK-29.
3. **`copy_architecture.json`** — intent-level guidance (`avoid_skeletons`, `route_bias`), not skeletons.
4. **Registry** — append-only log + indexes (`used_text`, `slot_exhaustion_tracker`, `concept_combos`).
5. **`background_variant.json`** — deterministic seeded picks (BG slots).
6. **`persona_seeds.json`** — personas with pain/desire/friction/proof/tone.

**9-section prompt** assembled from copy + persona + format + background + safe-zone constraints.

---

## Data Storage: The #1 Architecture Question

### What lives where (current production truth)

| Concern | Store | Notes |
|---------|-------|-------|
| Users, sessions, OAuth identities | MongoDB | Session token **SHA-256 hashed**; TTL on `expires_at` |
| Orgs, members, invites, audit | MongoDB | Invite tokens hashed; ~7-day expiry |
| **Eight dashboard configs** + version snapshots | MongoDB | Explicit exception — editable **without** local agent; ≤12 MiB/file and ≤12 MiB total |
| Provider API keys (control) | MongoDB | Fernet; UI gets boolean “configured”; materialize `no-store` to agent |
| Provider secrets (generation) | Local `config/providers/` | Mode `0700` / files `0600` |
| Agents, pairings, jobs | MongoDB | Jobs are **metadata-only** (no prompt bodies / base64) |
| Structured copy jobs | MongoDB `render_copy_jobs` | Diagnostics only; not full LLM bodies |
| Prompt delivery ciphertext | MongoDB `prompt_deliveries` | TTL; deleted after ack |
| Run / prompt / image **listing metadata** | MongoDB | ids, hashes, version refs, counts, status |
| Prompt text, images, uploads, traces, logs | **Local agent** | SQLite + content-addressed objects |
| Org shared-config replication | Local export/import | Encrypted package; Mongo stores authority/replica **refs** only |

### The eight config keys

`product_master_doc`, `starting_prompt`, `copy_prompt_templates`, `persona_seeds`, `copy_architecture`, `background_variant`, `prompt_assembler_templates`, `conversion_916_prompt`

### Interview sound bite

> “Render is a metadata control plane. Mongo holds auth, orgs, eight bounded config files, and job envelopes. Every creative byte — uploads, prompts, images, traces — stays on the paired laptop and is served over loopback after a pairing challenge. Content routes on Render return 410 so the boundary is enforceable, not just documented.”

**Do not say** (outdated): Cloudinary migration path, Redis queue, GridFS, Render disk for runs, prompts with full `content` in Mongo as primary store.

---

## MongoDB Collections (25)

From `dashboard/backend/db/collections.py`:

| Collection | Role |
|------------|------|
| `users` | Accounts, `is_super_admin`, `is_active` |
| `auth_identities` | Google → user link |
| `sessions` | Hashed session tokens (TTL) |
| `provider_configs` | Encrypted keys + model/URL metadata |
| `json_blobs` | Named bounded blobs |
| `runs` | Run metadata + local resource refs |
| `prompts` | Prompt **metadata/hashes** (bodies local) |
| `images` | Image **metadata/hashes** |
| `llm_traces` | Trace metadata; bodies on localhost |
| `agents` | Registration, heartbeat, `device_id`, protocol |
| `agent_jobs` | Metadata jobs; TTL `purge_at` |
| `agent_pairings` | Pairing challenges (TTL) |
| `render_copy_jobs` | Structured-copy worker state |
| `prompt_deliveries` | Encrypted TTL deliveries |
| `run_counters` | Per-owner run numbering |
| `browser_sessions` | Browser session metadata (TTL) |
| `file_map` | Legacy path map |
| `user_configs` | Eight files, owner schema |
| `local_config_references` | Device authority / replica refs |
| `orgs` / `org_members` / `org_invites` | Teams |
| `audit_logs` | Org/admin events |
| `config_versions` | Full snapshots of eight files |

Indexes created on startup (`db/indexes.py`). Production fails startup if required indexes fail.

---

## Authentication & Authorization

### End-user login (how pieces talk)

```
Browser → GET /api/auth/google/login → 302 Google
Google → GET /api/auth/google/callback?code=...
  → exchange code → find/create user
  → bootstrap_super_admin() if email ∈ SUPER_ADMIN_EMAILS
  → create_session(): raw token → SHA-256 → COLL_SESSIONS
  → Set-Cookie: session=<raw> (HttpOnly, SameSite=Lax)
Browser → subsequent /api/* with cookie
  → control_plane_boundary middleware (prod): 401 if missing
  → Depends(require_user_dependency) on routes; disabled → 403
```

Public prefixes: `/api/auth/*`, `/api/invites/*`. Agent Bearer paths exempt via `is_agent_runtime_path`.

### Super admin

- DB flag `is_super_admin` + env bootstrap list.
- `/api/admin/*` guarded by `require_super_admin_dependency`.
- Destructive UI: typed confirm (`GRANT`, `REVOKE`, `REPLACE`, `DISABLE`).

### Agents (machine auth)

- `POST /api/agents/register` → one-time bearer token (hashed at rest).
- Jobs **device-pinned** — content is machine-specific; no failover to another laptop’s disk.
- Claim uses atomic `find_one_and_update` (`pending` → `running`).

### Pairing (browser ↔ localhost)

1. Dashboard discovers `http://127.0.0.1:8765/v1/info`.
2. Local challenge created; only challenge + device metadata sent to Render.
3. Approval pushed over agent WebSocket; browser exchanges for scoped local session.
4. Another machine **cannot** use this loopback service.

### Chrome extension (retired)

Removed from the repo. Control plane keeps quiet stubs: `/api/extension/status` → `{ connected: false, disabled: true }`, WS close “Use the paired local agent”. Prefer local agent + CDP `:9222` in interviews.

---

## Organizations & Teams

**Modes:**

- `shared_org_config` — one org config; creators read-only on config.
- `individual_member_config` — per-member configs.

**Roles:** `owner`, `config_admin`, `creator` — matrix in `org_helper.py` (`can_edit_org_config`, `can_invite_members`, `can_view_org_audit`, …).

**Invite flow:** create → hash token → email (SMTP/Resend) → `invite.html` → accept → `org_members` + audit. Public email domains blocked for auto-org creation.

**Shared config across devices:** encrypted local export/import between authority and approved replica; Mongo stores refs only (`local_config_references`). Never fall back to putting content in Mongo if replica offline.

---

## Config Versioning

On every `create_or_update_config()`:

1. Canonical SHA-256 of files (sorted JSON).
2. If changed → insert `config_versions` with **full previous snapshot** (not diffs).
3. Then update `user_configs`.

Rollback creates a “before rollback” version, then restores snapshot. Admin replace/copy tracked the same way. Reasons include `user_save`, `rollback_before`, `admin_replace`, `copy`.

---

## Structured Copy vs Reference vs Browser Jobs

| | Structured | Reference | Image job |
|---|---|---|---|
| Planning | Render (`render_structured_copy` / `render_copy_jobs`) | Local per persona×reference | N/A |
| Provider call | Local via **relay**; validate on Render | Local | Browser UI |
| Prompt bodies | Encrypted delivery → local | Built/stored local | Read local |
| Uploads | Optional later | Ordered: reference → products | Local upload sets |
| Queue API | `/structured-copy` | `/reference-generation` | `/image-generation` |

**Idempotency:** `client_operation_id` unique indexes; local outbox stable IDs; restart resumes without duplicating completed outputs.

**Outage story:** if Render sleeps, local work continues; outbox drains on wake; WS loss → poll fallback.

---

## Admin Dashboard (Phase 6 + local-plane checks)

**Core Phase 6:** overview, users/orgs/configs, audit, typed confirms, safe exports, `redact_sensitive()` (case-insensitive frozenset, depth 20).

**Readiness** — `GET /api/admin/readiness` includes classic checks plus local-plane checks such as:

- `protocol_compatibility` — active agents speak v1 + pairing + `device_id`
- `metadata_only_jobs` — jobs lack forbidden content fields
- `ttl_indexes` — terminal job TTL present
- device online / content-storage-absent style checks

**Exports:** `/api/admin/exports/{users,orgs,configs,audit-logs}` — no secrets; configs stripped of file bodies.

**Ops:** `scripts/check_admin_routes.py --base-url --cookie`.

---

## Deployment & Environment

`render.yaml` starts:

```text
uvicorn dashboard.backend.control_app:app --host 0.0.0.0 --port $PORT
```

| Variable | Role |
|----------|------|
| `DEPLOYMENT_MODE=production` | Auth middleware + startup validation |
| `BROWSER_AUTOMATION_MODE=local-agent` | No server Chrome |
| `MONGODB_URI`, `MONGODB_DB_NAME` | Atlas |
| `APP_SECRET_KEY`, `ENCRYPTION_KEY` | Sessions / Fernet |
| `GOOGLE_CLIENT_*`, `GOOGLE_REDIRECT_URI` | OAuth |
| `FRONTEND_ORIGIN`, `CORS_ORIGINS` | Explicit allowlist (no `*`) |
| `SESSION_EXPIRE_MINUTES` | Default 1440 |

**Do not set on Render:** `STORAGE_PROVIDER`, Cloudinary, content dirs, provider generation credentials, localhost URLs, local capability tokens.

Verify: `/healthz`, `/api/version` → `"content_plane": "localhost"`, `/api/readyz` → Mongo ping, `content_storage: false`.

**Local agent:**

```bash
python scripts/local_agent.py \
  --api-base https://YOUR-SERVICE.onrender.com \
  --data-dir "$HOME/ad-factory-agent" \
  --launch-browser --browser chrome
```

Artifact server binds **`127.0.0.1:8765` only**. CDP **`127.0.0.1:9222`**. Never expose either to LAN/WAN.

---

## Security Topics (Likely Interview Probes)

| Topic | Implementation |
|-------|----------------|
| Session theft | HttpOnly cookie; hash at rest; TTL |
| XSS stealing tokens | No session JWT in localStorage |
| Content exfiltration via cloud | 410 content routes; metadata validator; no object store |
| IDOR | Ownership checks; device-pinned jobs |
| Secrets in logs/exports | `redact_sensitive`, `mask_key`, safe serializers |
| Invite leak | Hash in DB; raw token once |
| Provider keys | Encrypted at rest; materialize only to paired agent |
| Loopback abuse | Pairing challenge; Host/PNA; bind 127.0.0.1 |
| Provider SSRF | Relay allowlists outbound URLs |
| Prompt leakage in Mongo | Encrypted TTL delivery + ack delete |
| Production footguns | `validate_production_settings()`; index creation required |

---

## Frontend Modules (Quick Map)

React SPA in `dashboard/web` (served at `/`). Old `.html` URLs redirect into the same shell.

| File | Responsibility |
|------|----------------|
| `src/pages/Studio.tsx` | Structured + reference generation, Studio file cards |
| `src/pages/studio/ReferencePanel.tsx` | Reference desk, local uploads, file editors |
| `src/pages/Config.tsx` | Config desk, versions, copy-to-org |
| `src/pages/Organizations.tsx` | Teams / invites |
| `src/pages/Admin.tsx` | SA UI, readiness, exports, runbook |
| `src/pages/Traces.tsx` | LLM traces |
| `src/pages/Profile.tsx` | Profile / providers |
| `src/pages/Invite.tsx` | Invite accept |
| `src/lib/local-data-plane.js` | Pairing, localhost client, allocate |
| `src/lib/api.ts` | `fetchJSON` to Render |
| `src/lib/auth.tsx` / `theme.tsx` | Auth + theme |
| `src/components/FileViewer.tsx` | Read/edit a single config file |
| `src/components/AgentStatus.tsx` | Agent pairing chip |

---

## Testing Strategy

Broad coverage under `tests/`:

- `test_smoke.py` — auth, crypto, org/admin contracts (no pytest required for classic smoke).
- Control-plane: `test_stateless_render_control_plane.py`, `test_control_plane_indexes.py`, `test_frontend_control_plane_contract.py`.
- Local plane: `test_local_agent_*`, `test_local_data_plane_*`, `test_local_artifact_server.py`, `test_provider_relay.py`.
- Flows: `test_render_structured_pipeline.py`, `test_structured_local_flow.py`, `test_reference_local_flow.py`, `test_browser_local_data_plane_e2e.py`.
- Security: `test_script_mongodb_security.py`, `test_mongo_*`.

**Pattern:** set env before importing app modules; assert 410 on content routes; assert metadata documents reject forbidden fields.

---

## Tradeoffs & Future Work (Strong Closing)

1. **Control/data split** — free-tier + privacy win vs multi-device cloud sync (would need paid object store + new threat model).
2. **`app.py` monolith still exists** — cohesion for local/dev vs cognitive load; production surface is `control_app`.
3. **Sync pymongo** — simple ops vs async throughput; motor available unused.
4. **React press-room SPA** — Vite build served by FastAPI; same-origin cookie + local-agent pairing unchanged.
5. **Browser automation** — fragile selectors vs missing APIs; mitigated with readiness gates, idempotent jobs, local resume.
6. **No Redis** — Mongo jobs + local outbox + poll; add Redis only if fan-out/SLA demands it.
7. **WS + poll** — handles free Render cold starts without a dedicated queue product.

---

## Common Interview Q&A

**Q: How is this different from “just calling DALL·E”?**  
A: Compliance + diversity + scale. We generate **copy + structured prompts** under playbook rules, track variants, support hypothesis testing, then render — often through UIs that need automation. The hard part is the **rules engine + multi-tenant control plane + local content plane**, not a single image API call.

**Q: Walk me through a request end-to-end.**  
A: User logs in via Google → session cookie on Render. Opens dashboard on the same machine as the agent → pairing challenge → local session. Allocates a run (Mongo metadata). Starts structured-copy (Render plans, agent executes provider HTTPS via relay, prompts delivered encrypted). Enqueues image-generation (metadata job). Agent claims, drives Chrome via CDP using local files, stores PNGs locally. UI fetches images from `127.0.0.1:8765` as Blobs. Render never saw the bytes.

**Q: Why not put everything in Mongo/S3?**  
A: Free Atlas size limits, free Render disk constraints, and deliberate choice to keep creative/PII-ish content on-device. Eight configs are the bounded exception so the dashboard remains usable without an agent.

**Q: How do you handle multi-tenancy?**  
A: Google auth, org RBAC, config `owner_type` user vs org, run ownership, device-pinned jobs, encrypted per-user provider configs, audit log.

**Q: What breaks in production?**  
A: Missing OAuth/CORS/encryption env; expecting content routes on Render (410); unpaired browser on another machine; exposing 8765/9222; rotating `ENCRYPTION_KEY` without re-encrypting keys; agent protocol mismatch (readiness fails).

**Q: How would you scale?**  
A: Control plane is already horizontally scalable (stateless). Bottleneck is **per-user local agents** and Atlas document bounds. Scale agents by more devices per org with explicit config replication; add object storage only if product requires multi-device cloud sync; add Redis only for higher job fan-out SLAs.

**Q: Biggest technical achievement?**  
A: Pick what you own — e.g. **local-first control/data plane with enforceable 410 boundary**, **encrypted prompt delivery + provider relay**, **org/config versioning**, **creative rule engine + registry**, or **Phase 6 admin readiness**.

**Q: Why a Vite React SPA instead of Next.js?**  
A: Production mounts a static `dist` from FastAPI (`control_app` / `app.py`). Auth is a same-origin `session` cookie and the local data plane is `http://127.0.0.1:8765`. Next.js would break that.

**Q: Extension vs local agent?**  
A: Extension was a CDP bridge when Render couldn’t launch Chrome. Local agent now owns **content + automation**, so the extension path is retired on the control plane to keep one production story.

---

## Related Docs

| Doc | Use when |
|-----|----------|
| `PROJECT_PITCH.md` | Spoken “tell me about your project” script (steps) |
| `docs/LOCAL_FIRST_OPERATIONS.md` | Deploy, pair, backup, restore, outages |
| `LOCAL_DATA_PLANE_IMPLEMENTATION.md` | Why/how of the refactor, non-negotiables |
| `docs/HANDOVER.md` | Creative files, registry, directory map |
| `AD_CREATIVE_SYSTEM_PLAYBOOK.md` | Creative/marketing rules |
| `docs/AB_TESTING_PLAYBOOK.md` | Hypothesis testing |
| `AGENTS.md` | Agent notes (extension section is historical) |
| `README.md` | Setup + cloud topology summary |

---

## Glossary

| Term | Meaning |
|------|---------|
| Control plane | Render `control_app` — metadata + configs + job envelopes |
| Data plane / local plane | Laptop agent + `:8765` artifact server |
| Pairing | Challenge/approval so browser may call loopback |
| Run | One batch with `run_id`; Mongo metadata + local resources |
| Structured copy | Render-planned LLM copy + assembled prompts |
| Reference flow | Local prompts driven by reference images |
| Prompt delivery | Encrypted TTL Mongo blob → local import/ack |
| Provider relay | In-memory WS bridge for provider request/response |
| Device pin | Job only runs on the agent that owns the content |
| Hypothesis | Controlled A/B message variable |
| BG slot | Entry in `background_variant.json` |
| SA | Super admin (`is_super_admin`) |
| CDP | Chrome DevTools Protocol (`:9222`) |
| 410 | Content route retired from Render — use localhost |

---

*Last aligned with codebase: local-first `control_app` + `local_agent_runtime`, React UI at `/`, 25 Mongo collections, structured-copy relay + prompt delivery, Phase 6 admin + local-plane readiness. Do not cite Cloudinary/Redis/GridFS as the production design.*
