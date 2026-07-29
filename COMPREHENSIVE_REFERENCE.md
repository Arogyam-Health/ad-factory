# Ad Factory — Full Codebase Reference

> Walk a junior through every system: what it does, where the code lives, and how the pieces connect.
> The 7 pillars: Architecture, Data Flow, Auth, Orgs, Agents, Admin Dashboard, Config Versioning.

---

## 1. Architecture — How Backend, Frontend, DB, and Agents Fit Together

### The Physical Layout

```
                    ┌──────────────────────────┐
                    │     User's Browser        │
                    │  index.html / admin.html  │
                    │  JS modules (vanilla)     │
                    └──────────┬───────────────┘
                               │ HTTP fetch() ← JSON →
                               ▼
┌──────────────────────────────────────────────────────────┐
│  FastAPI Server (uvicorn, port 4090)                      │
│                                                           │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │  Auth Layer   │  │  Middleware   │  │  Static Mounts  │  │
│  │  (auth/*)     │  │  CORS, Auth   │  │  / → frontend  │  │
│  │               │  │  check on     │  │  /generated     │  │
│  │  Google OAuth │  │  every /api/* │  │  /output        │  │
│  │  session      │  │  request      │  │  /input         │  │
│  │  cookies      │  │  (prod only)  │  │  /storage       │  │
│  └──────┬───────┘  └───────┬──────┘  └────────────────┘  │
│         │                  │                              │
│         ▼                  ▼                              │
│  ┌──────────────────────────────────────────────────────┐ │
│  │  Route Modules (mounted via app.include_router)       │ │
│  │                                                       │ │
│  │  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ │ │
│  │  │ Auth    │ │ Routes/  │ │ Agent    │ │ Admin    │ │ │
│  │  │ routes  │ │ defaults │ │ routes   │ │ routes   │ │ │
│  │  │         │ │ runs     │ │          │ │          │ │ │
│  │  │ /auth/* │ │ progress │ │ /agents/*│ │ /admin/* │ │ │
│  │  └─────────┘ │ generate │ └──────────┘ └──────────┘ │ │
│  │              │ batch    │                            │ │
│  │              │ execute  │                            │ │
│  │              └──────────┘                            │ │
│  │                                                       │ │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐             │ │
│  │  │ Org      │ │ Invite   │ │ Config   │             │ │
│  │  │ routes   │ │ routes   │ │ routes   │             │ │
│  │  │ /orgs/*  │ │ /invites*│ │ /configs*│             │ │
│  │  └──────────┘ └──────────┘ └──────────┘             │ │
│  └──────────────────────────────────────────────────────┘ │
│                           │                               │
│                           ▼                               │
│  ┌──────────────────────────────────────────────────────┐ │
│  │  Services Layer (business logic, no HTTP knowledge)   │ │
│  │                                                       │ │
│  │  user_config.py   org_helper.py   invite_service.py   │ │
│  │  run_storage.py   provider_config.py                  │ │
│  │  config_version_service.py   email_service.py         │ │
│  │  config_permissions.py   storage/*                    │ │
│  └──────────────────────┬───────────────────────────────┘ │
│                         │                                  │
│                         ▼                                  │
│  ┌──────────────────────────────────────────────────────┐ │
│  │  Database Layer (dashboard/backend/db/)               │ │
│  │                                                       │ │
│  │  settings.py  → reads env vars                        │ │
│  │  client.py    → sync (pymongo) + async (motor)        │ │
│  │  collections.py → 19 collection name constants        │ │
│  │  indexes.py   → all index specs, created on startup   │ │
│  └──────────────────────┬───────────────────────────────┘ │
│                         │                                  │
│                         ▼                                  │
│              ┌────────────────────┐                       │
│              │     MongoDB        │                       │
│              │  (Atlas / local)   │                       │
│              └────────────────────┘                       │
└──────────────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────┐
│  Agent Process (scripts/local_agent.py)                   │
│  Runs separately, polls backend for jobs                  │
│  Opens Playwright browser → navigates Gemini/ChatGPT      │
│  Pastes prompts → takes screenshots → reports back        │
└──────────────────────────────────────────────────────────┘
```

### The `app.py` Skeleton (7,994 lines)

The file has a clear internal order. Here's the map:

| Lines | Section | What it contains |
|---|---|---|
| 1–55 | Imports + paths | Path constants for ROOT, STORAGE, RUNS, INPUT, etc. |
| 56–425 | Helpers | `_resolve_user_config`, `_record_run_owner`, `_store_output_mapping`, persona parsing, format pattern loading |
| 426–4260 | Pipeline functions | Prompt assembly, copy generation, image generation, batch processing, 4:5→9:16 conversion, agent job dispatch |
| 4264 | `app = FastAPI(...)` | Application instance created |
| 4266–4275 | CORS middleware | Reads origins from settings, allows credentials, standard methods/headers |
| 4284–4295 | Auth middleware | In production: checks session cookie on every `/api/*` request (except `/api/auth/*`, `/api/generic-config`, `/api/invites/*`) |
| 4322–4345 | Startup event | Loads `.env.dashboard`, creates dirs, validates prod settings, creates DB indexes, builds LLM catalog cache |
| 7696–7731 | Router mounts | Includes all modular routers in order: defaults, progress, runs, generate, batch, export_import, execute, chrome, auth, provider, blob, user_config, agent, org, invite, config, admin |
| 7736–8024 | Inline routes | Remaining endpoints: generic-config, file downloads (image/prompt by ID), seed files, invite page, static mounts |
| 8024 | `app.mount("/")` | Final fallback — serves frontend static files for all unmatched routes |

### Key Design Decisions

1. **No database ORM** — raw pymongo, no SQLAlchemy/MongoEngine. Every query is a dict.
2. **Sync-first** — routes are sync FastAPI functions, using sync pymongo. Async motor is available but unused for now.
3. **Two-tier config** — filesystem defaults (`input/`, `persona_seeds.json`) + MongoDB per-user/org overrides. The filesystem is always the fallback.
4. **Agents are separate processes** — they don't run in the web server. They poll from outside.
5. **Vanilla JS frontend** — no build step, no React/Vue, no npm. Static files served by FastAPI.

---

## 2. Data Flow — How a Run Goes From Creation to Finished Images

This is the primary user workflow. Every run produces a batch of ad prompts and images.

```
                           TIME
                          ────►
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  1.      │    │  2.      │    │  3.      │    │  4.      │    │  5.      │
│  Create  │──► │ Configure│──► │ Generate │──► │ Generate │──► │  View    │
│  Run     │    │  Config  │    │  Prompts │    │  Images  │    │  Results │
│          │    │          │    │ (LLM)    │    │ (LLM)    │    │          │
└──────────┘    └──────────┘    └──────────┘    └──────────┘    └──────────┘
     │               │               │               │               │
     ▼               ▼               ▼               ▼               ▼
  POST /api      GET /api/      POST /api/      POST /api/      GET /api/
  /runs          /defaults      /runs/{id}/     /runs/{id}/     /runs/{id}/
                                 execute          generate-       images
                                                   images-45
```

### Step 1: Create Run

**Frontend** → `POST /api/runs` → `routes/runs.py`:

```python
@router.post("/api/runs")
def create_run(user=Depends(require_user_dependency)):
    run_id = f"run_{int(time.time())}_{random.randint(1000,9999)}"
    _record_run_owner(run_id, user["user_id"])
    return {"run_id": run_id, "status": "created"}
```

**What happens**: A run document is created in `COLL_RUNS` with owner info. A `.owner` file is written to `dashboard_storage/runs/{run_id}/.owner`. The run appears in the frontend run list.

### Step 2: Configure Config

**Frontend** → `GET /api/defaults` → `api_defaults()` in app.py:

Reads everything from the filesystem (or user's MongoDB overrides):
- `product master doc.txt` — product claims, features, ingredients
- `startingprompt.txt` — the base prompt template
- `persona_seeds.json` — 26 persona definitions
- `background_variant.json` — background scene rules
- `image_sources.txt` — reference image sources
- `copy_architecture.json` — structure rules for ad copy

The user can edit any of these in the browser. Changes are saved to MongoDB via `PUT /api/defaults/{key}`.

### Step 3: Execute Pipeline (Generate Prompts)

**Frontend** → `POST /api/runs/{run_id}/execute` → `api_run_execute()` in app.py:

This is the most complex step. Here's what happens internally:

```
api_run_execute()
│
├─ 1. Resolve effective config
│     _resolve_user_config("product_info") → product master doc
│     _resolve_user_config("persona_seeds") → persona definitions
│     _resolve_starting_prompt_path() → the base prompt
│
├─ 2. Determine format/persona plan
│     Reads format_patterns.json → picks which formats to generate
│     Randomly selects personas from seeds (configurable count)
│     Determines language modes (EN/HI/HINGLISH/ALL)
│
├─ 3. Build ad copy (per candidate)
│     For each format × persona × language:
│       build_ad_copy_for_prompt() with LLM
│       → Gets headline, subheadline, support line, bullets, CTA
│       → Validates against hypothesis (check concept angle matches)
│       → Saves to filesystem in run's context/ dir
│
├─ 4. Assemble full 9-section prompts
│     For each copy candidate:
│       assemble_final_prompt() from playbook rules
│       → Subject, Background, Composition, Style
│         Lighting, Color, Typography, Mood, Technical
│       → Includes safe-zone rules (4:5 or 9:16)
│       → Writes to output/{batch}/{FORMAT}_{slug}_{LANG}_{angle}.txt
│
└─ 5. Store in MongoDB
      _store_output_mapping() → upserts into COLL_PROMPTS
```

**Key function**: `assemble_final_prompt()` (~line 1500 in app.py) takes:
- The product master doc content
- The starting prompt template
- The copy (headline, subheadline, etc.)
- The persona seed data
- The format-specific rules
- Background variation from `background_variant.json`
- Safe-zone constraints (4:5 or 9:16)

...and produces a full prompt ready for image generation.

### Step 4: Generate 4:5 Images

**Frontend** → `POST /api/runs/{run_id}/generate-images-45` → `api_run_generate_images_45()` in app.py:

```
For each prompt in the batch:
  1. Read the prompt text from output/{batch}/{prompt_file}.txt
  2. Call LLM to generate image:
     - If opencode provider: call opencode API with the prompt
     - If Google Gemini: call Gemini API
     - If browser agent mode: create an agent job instead
  3. Save image to generated_images/{batch}/{prompt_name}.png
  4. Upsert into COLL_IMAGES with metadata (format, persona, language)
```

If `BROWSER_AUTOMATION_MODE` is set, the backend skips API calls and instead creates jobs for an agent to execute in a real browser.

### Step 5: Convert to 9:16 (Optional)

**Frontend** → `POST /api/runs/{run_id}/generate-916` → `api_run_generate_916()`:

Takes the generated 4:5 images, reads the 4:5→9:16 conversion template (`prompt_916_from_45.txt`), sends to LLM which re-imagines the scene for vertical format while keeping the same subject and theme.

### Step 6: View Results

Two frontend tabs:
- **Prompts tab** → `GET /api/runs/{run_id}/prompts` — lists all prompt files with format/language badges
- **Images tab** → `GET /api/runs/{run_id}/images` — shows generated image thumbnails with download

---

## 3. What Lives Where — Local Filesystem vs MongoDB

This is the single most important architectural decision to understand. Every system stores data in one of two places (or both).

### The Rule of Thumb

| Storage | Stores |
|---|---|
| **Local filesystem** | Files, documents, images — anything too large for MongoDB or needing direct filesystem access |
| **MongoDB** | Metadata, relationships, auth, orgs, configs, versions — structured and queryable |
| **Both** | Run data — filesystem is **authoritative**, MongoDB mirrors key fields for cross-cutting queries |

### Auth — 100% MongoDB

| Collection | What | Why not filesystem |
|---|---|---|
| `users` | User accounts | Need query by email/google_id for OAuth flow |
| `auth_identities` | Google ID → user_id links | Fast identity resolution |
| `sessions` | Token hashes, user_id, expires_at | TTL index auto-expires; fast token lookup |

**Filesystem: nothing.**

### Runs — Filesystem Authoritative, MongoDB Mirrors

**Filesystem holds the actual data:**

```
dashboard_storage/runs/{run_id}/
├── .owner         # user_id (DB-less ownership fallback)
├── manifest.json  # batch, format list, status, timestamps
├── inputs/        # product doc, reference images
├── logs/          # pipeline error logs
├── context/       # hypothesis, reference flow state
├── partial/       # partial generation results
└── imports/       # uploaded XLSX copy imports

output/{batch}/          → Generated prompt .txt files
generated_images/{batch}/ → Generated .png image files
runtime/                  → Temp working dirs, LLM traces, queue
```

**MongoDB stores metadata for fast queries:**

| Collection | What it stores | Purpose |
|---|---|---|
| `runs` | `{run_id, user_id, status, batch, timestamps}` | Dashboard listing, ownership |
| `prompts` | `{prompt_id, run_id, format, persona, language, content}` | Listing, download-by-ID |
| `images` | `{image_id, run_id, file_path, format, local_path}` | Listing, download-by-ID |
| `llm_traces` | `{run_id, model, prompt, response, tokens}` | Debugging |
| `file_map` | `{file_path, run_id, user_id}` | Resolve file path → owner |

**Why both?** Filesystem is the truth. MongoDB lets you query "all runs for user X" without scanning directories. The `.owner` file is a MongoDB-independent fallback.

### Configs — Filesystem Defaults, MongoDB Overrides

**Filesystem (immutable baselines, shipped with code):**

```
input/docs/product master doc.txt
input/startingprompt.txt
persona_seeds.json
dashboard/backend/copy_architecture.json
dashboard/backend/copy_prompt_templates.json
background_variant.json
input/prompt_916_from_45.txt
```

**MongoDB stores user/org customizations:**

| Collection | What |
|---|---|
| `user_configs` | `{config_id, owner_type, owner_id, files: {key: {content, content_type}}}` — overrides for any config key |
| `config_versions` | Full file snapshots before each change — enables rollback |

**Resolution order**: filesystem defaults → user config → org shared config.

### Images — Filesystem for Files, MongoDB for Metadata

| Storage | What |
|---|---|
| **Filesystem** | `input/images/` (uploads), `generated_images/{batch}/` (outputs), `dashboard_storage/reference_images/` (library) |
| **MongoDB** (`images`) | `{image_id, run_id, filename, local_path, storage_url, format}` — just a record pointing to the file |

The image file itself never goes into MongoDB.

### Orgs — 100% MongoDB

| Collection | What |
|---|---|
| `orgs` | `{org_id, name, domain, config_mode, owner_id}` |
| `org_members` | `{org_id, user_id, role, status}` |
| `org_invites` | `{org_id, email, token_hash, role, expires_at}` |
| `audit_logs` | `{event_type, actor, target, metadata, ip}` |

**Filesystem: nothing.**

### Agents — 100% MongoDB

| Collection | What |
|---|---|
| `agents` | `{agent_id, token_hash, user_id, is_active, last_heartbeat}` |
| `agent_jobs` | `{job_id, agent_id, job_type, payload, status, result}` |

Scripts themselves live in `scripts/` (source code), but all runtime data is in MongoDB.

### Provider Configs — 100% MongoDB

| Collection | What |
|---|---|
| `provider_configs` | `{user_id, provider, config: {encrypted_key, ...}}` |

API keys are **Fernet-encrypted** at rest. No `.env` file for provider keys — managed per-user through the UI.

### Decision Matrix

| Question | Answer |
|---|---|
| "Where does the user session live?" | **MongoDB** (sessions) |
| "Where is the generated ad prompt file?" | **Filesystem** (`output/{batch}/`) |
| "Where is run metadata for the dashboard?" | **Both** — manifest.json + MongoDB (runs) |
| "Where is the default product master doc?" | **Filesystem** (`input/docs/`) |
| "Where is a user's customized product doc?" | **MongoDB** (user_configs) |
| "Where are generated images stored?" | **Filesystem** (`generated_images/`) |
| "Where is image metadata?" | **MongoDB** (images) |
| "Where do org memberships live?" | **MongoDB** (org_members) |
| "Where is config version history?" | **MongoDB** (config_versions with full snapshots) |
| "Where are API keys stored?" | **MongoDB** (provider_configs, encrypted) |

### The Migration Path to "Fully Online"

Currently: **filesystem-primary** for runs, **MongoDB-primary** for everything else. To shift fully online:

1. **Images**: Move from `generated_images/` to Cloudinary/S3. The `storage/` service already has the abstract base and Cloudinary backend — just needs wiring.
2. **Run artifacts**: Move prompt files and manifests into MongoDB. The `prompts` collection already stores content — make it primary instead of filesystem.
3. **Input files**: Move uploaded images to S3/Cloudinary.
4. **Env config**: Move `SUPER_ADMIN_EMAILS`, provider URLs to MongoDB settings (already partially done with `provider_configs`).

The architecture is designed for this — filesystem and MongoDB layers exist in parallel. Switching is about changing which is consulted first, not rewriting data access.

---

## 4. Auth System — Google OAuth + Session Cookies

### Files

| File | Role |
|---|---|
| `dashboard/backend/auth/models.py` | Pydantic models: `UserDocument`, `AuthIdentityDocument`, `SessionDocument` |
| `dashboard/backend/auth/service.py` | Business logic: code exchange, session create/validate/delete |
| `dashboard/backend/auth/routes.py` | HTTP endpoints: `/api/auth/*` |
| `dashboard/backend/security/crypto.py` | `hash_token()`, `generate_token()` — used for session tokens |
| `dashboard/backend/db/settings.py` | Reads `google_client_id`, `google_client_secret`, `google_redirect_uri`, `session_expire_minutes` |
| `dashboard/frontend/js/auth.js` | Frontend auth state management, login/logout UI |

### User Model

```python
# From auth/models.py
user = {
    "user_id": "usr_a1b2c3d4",       # generated by generate_user_id()
    "email": "user@example.com",
    "display_name": "User Name",
    "google_id": "123456789",         # from Google's id field
    "avatar_url": "https://...",
    "is_active": True,
    "is_super_admin": False,
    "is_platform_admin": False,
    "created_at": 1700000000.0,
    "updated_at": 1700000000.0,
}
```

### The Auth Flow (End-to-End)

#### Login

```
Browser                           FastAPI                        Google
  │                                 │                              │
  │  Click "Login with Google"      │                              │
  │ ───── GET /api/auth/google/login ──►                            │
  │                                  │                              │
  │  ◄── 302 Redirect ──────────────                              │
  │                                     │                          │
  │  ──── Redirect to ───────────────────────────────────►         │
  │       accounts.google.com                                     │
  │       ?client_id=...&redirect_uri=...                          │
  │       &response_type=code                                      │
  │                                     │                          │
  │  User signs in on Google            │                          │
  │                                     │                          │
  │  ◄── Google redirects to ──────────                           │
  │       /api/auth/google/callback                                 │
  │       ?code=AUTH_CODE                                          │
  │                                     │                          │
  │  ──── GET /api/auth/google/callback ──►                        │
  │       ?code=AUTH_CODE                │                          │
  │                                     │                          │
  │                           1. exchange_google_code(code)
  │                              POST oauth2.googleapis.com/token
  │                              GET www.googleapis.com/oauth2/v2/userinfo
  │                                     │                          │
  │                           2. find_user_by_google_id(id)
  │                              OR find_user_by_email(email)
  │                                     │                          │
  │                           3. If new: create_user_from_google()
  │                              → Insert into COLL_USERS
  │                              → Insert into COLL_AUTH_IDENTITIES
  │                                     │                          │
  │                           4. bootstrap_super_admin()
  │                              → If email in SUPER_ADMIN_EMAILS:
  │                                set is_super_admin=True
  │                                     │                          │
  │                           5. create_session(user_id)
  │                              → Generate random token
  │                              → Hash token (SHA-256)
  │                              → Store hash in COLL_SESSIONS
  │                              → Return raw token
  │                                     │                          │
  │  ◄── 302 Redirect to frontend_origin ──────────────────
  │       Set-Cookie: session=RAW_TOKEN
  │       (httponly, samesite=lax, max-age=1440*60)
  │                                     │                          │
  │  Browser stores cookie              │                          │
```

#### Session Validation (Every Protected Request)

```python
# From app.py (auth middleware, ~line 4284)
@app.middleware("http")
async def auth_middleware(request, call_next):
    if app_settings.is_production:
        path = request.url.path
        if path.startswith("/api/") and not path.startswith(PUBLIC_API_PREFIXES):
            session_token = request.cookies.get("session")
            user = get_current_user_from_cookie(session_token)
            if user is None:
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            request.state.user = user    # Attach user to request for downstream use
    response = await call_next(request)
    return response
```

`get_current_user_from_cookie()` in `auth/service.py`:
1. Hash the raw token with SHA-256
2. Look up `{token: hash}`, `{expires_at: {$gt: now}}` in COLL_SESSIONS
3. If found and not expired → `find_user_by_id(session["user_id"])`
4. If expired → delete session, return None

#### Route-level Auth

```python
# In route files — FastAPI dependency injection
@router.get("/api/some-protected-route")
def protected_route(user=Depends(require_user_dependency)):
    return {"user_id": user["user_id"]}
```

`require_user_dependency` (auth/service.py:154):
1. Reads `session` cookie automatically (FastAPI `Cookie(None)`)
2. Calls `require_user(session)` which calls `get_current_user_from_cookie`
3. If no valid session → raises `HTTPException(401)`
4. If user is disabled (`is_active: False`) → raises `HTTPException(403)`

#### Logout

`POST /api/auth/logout`:
1. Hash the raw token
2. Delete from COLL_SESSIONS
3. Frontend reloads page → auth status shows "Not logged in"

### Frontend Auth (auth.js)

```javascript
// On every page load
await initAuth();
// → checkAuth() calls GET /api/auth/status
// → Renders login button OR user avatar + logout button

// Auth state is cached in memory
let authState = { authenticated: false, user_id: "", email: "", ... };
export function isAuthenticated() { return authState.authenticated; }
export function getAuthUser() { return authState; }
```

The `GET /api/auth/status` endpoint reads the cookie directly (no dependency injection) and returns `{authenticated: true/false, user_id, email, display_name, is_super_admin}`.

### Super Admin Auth (admin_auth.py)

```python
SUPER_ADMIN_EMAILS = "admin1@example.com,admin2@example.com"  # env var

def bootstrap_super_admin(user):
    """Called on every login. If user's email is in SUPER_ADMIN_EMAILS,
    auto-grants is_super_admin=True."""
    if user.email in get_super_admin_emails():
        update user in DB → set is_super_admin=True

def require_super_admin(session_token):
    user = require_user(session_token)
    if not user.get("is_super_admin"):
        raise HTTPException(403, "Super admin access required")
```

The `SUPER_ADMIN_EMAILS` list is cached in memory after first read. Changing it requires a server restart (or cache clear).

---

## 4. Org System — Teams, Invites, Config Sharing

### Files

| File | Role |
|---|---|
| `services/org_helper.py` | Core org CRUD, membership, role permissions, audit events |
| `services/invite_service.py` | Invite creation, token hashing, accept flow |
| `services/org_routes.py` | HTTP endpoints: `/api/orgs/*` |
| `services/invite_routes.py` | HTTP endpoints: `/api/orgs/invites/*` |
| `services/config_permissions.py` | Permission checks for config access |
| `services/email_service.py` | SMTP/Resend email sending for invites |
| `services/config_routes.py` | Config sharing endpoints |

### Three Org Modes

| Mode | What it means |
|---|---|
| `shared_org_config` | Everyone in the org uses ONE config. Config admins edit it. Creators use it read-only. |
| `individual_member_config` | Each member has their own config. Can optionally share with org. |

### Three Roles

| Role | Permissions |
|---|---|
| `owner` | Can manage org, invite/remove members, change roles, edit config, generate ads, view org audit |
| `config_admin` | Can invite members, edit config, generate ads (cannot manage org, remove members, or view audit) |
| `creator` | Can only generate ads using the shared config (read-only on config) |

The permission matrix is defined in `org_helper.py` as `_ORG_ROLE_PERMISSIONS`:

```python
_ORG_ROLE_PERMISSIONS = {
    "owner": {
        "can_manage_org": True,
        "can_invite_members": True,
        "can_remove_members": True,
        "can_change_roles": True,
        "can_edit_org_config": True,
        "can_generate_ads": True,
        "can_view_org_runs": True,
        "can_view_org_images": True,
        "can_view_org_audit": True,
    },
    "config_admin": {
        "can_manage_org": False,
        "can_invite_members": True,
        "can_remove_members": False,
        "can_change_roles": False,
        "can_edit_org_config": True,
        "can_generate_ads": True,
        "can_view_org_runs": True,
        "can_view_org_images": True,
        "can_view_org_audit": False,
    },
    "creator": {
        "can_manage_org": False,
        "can_invite_members": False,
        "can_remove_members": False,
        "can_change_roles": False,
        "can_edit_org_config": False,
        "can_generate_ads": True,
        "can_view_org_runs": False,
        "can_view_org_images": False,
        "can_view_org_audit": False,
    },
}
```

### Invite Flow (End-to-End)

```
Step 1: Owner/config_admin creates invite
───────────────────────────────────────────
POST /api/orgs/{org_id}/invites
Body: { "email": "newmember@example.com", "role": "creator" }

Backend:
  → invite_service.create_invite()
  → Generates random token (secrets.token_urlsafe)
  → Hashes token (SHA-256)
  → Stores {token_hash, org_id, email, role, status: "pending", expires_at: now+7d}
  → Returns raw token (unhashed) to caller

Step 2: Build invite URL
───────────────────────────
{frontend_origin}/invite/{raw_token}

Step 3: Send email
────────────────────
email_service.send_invite_email() via SMTP or Resend API
Email body includes the invite URL

Step 4: Recipient clicks link
───────────────────────────────
Browser opens {frontend_origin}/invite/{raw_token}
Frontend loads invite.html → shows accept/reject UI

Step 5: Accept invite
───────────────────────
POST /api/orgs/invites/accept
Body: { "token": "raw_token" }

Backend:
  → Hash the raw token
  → Find matching invite in COLL_ORG_INVITES
  → Validate: not expired, status=pending, email matches (if restricted)
  → Create membership in COLL_ORG_MEMBERS
    {membership_id, org_id, user_id, role, status: "active"}
  → Write audit event: "org_member_added"
  → Update invite status to "accepted"
```

### Config Sharing

A config document has:
```python
{
    "config_id": "cfg_...",
    "owner_type": "user" | "org",    # Who owns this config
    "owner_id": "usr_..." | "org_...",
    "config_scope": "personal" | "org_shared" | "org_individual",
    "files": {
        "product_master_doc": { "content": "...", "content_type": "text/plain" },
        "starting_prompt": { "content": "...", ... },
        ...
    }
}
```

When a user belongs to a `shared_org_config` org:
1. Their personal config is checked first
2. If empty, the org's shared config is used as fallback
3. The effective config is merged in `get_effective_config()` (user_config.py)

`config_permissions.py` determines who can view/edit based on `owner_type` and org membership.

### Audit Events

Every significant action in the org system is logged to `COLL_AUDIT_LOGS`:

```python
{
    "event_id": "evt_abc123",
    "event_type": "org_member_added" | "org_created" | "config_updated" | ...,
    "actor_user_id": "usr_...",
    "actor_email": "admin@example.com",
    "target_type": "org" | "user" | "config",
    "target_id": "org_...",
    "org_id": "org_...",
    "metadata": { ... },
    "ip": "192.168.1.1",
    "user_agent": "Mozilla/...",
    "created_at": 1700000000.0,
}
```

Public email domains (gmail.com, yahoo.com, etc.) are blocked from auto-creating orgs to prevent abuse. Only work/business emails can create organizations.

---

## 5. Agent System — Browser Automation With Playwright

### Why Agents Exist

The backend can generate text prompts, but actually turning those prompts into images often requires interacting with AI web UIs (Gemini Web, ChatGPT). These UIs don't have reliable public APIs for image generation. So instead of trying to call APIs that may not exist, the system uses **browser automation agents**:

1. Backend creates a "job" describing what to do
2. An external agent process polls for jobs
3. Agent opens a real browser, navigates to the right website
4. Agent pastes the prompt, triggers generation, waits for result
5. Agent captures the image, saves it, reports completion

### Architecture

```
┌─────────────┐    Polls /api/agents/jobs/poll    ┌─────────────┐
│  FastAPI    │ ◄─────────────────────────────── │  Agent       │
│  Backend    │     every 5 seconds               │  Process     │
│             │    with Bearer token               │             │
│  Creates    │                                   │  Playwright  │
│  job in     │  Claims job:                      │  Chromium    │
│  COLL_AGENT │  POST /api/agents/jobs/{id}/claim  │             │
│  _JOBS      │ ───────────────────────────────► │             │
│             │                                   │  Opens URL,  │
│             │  Reports result:                  │  pastes      │
│             │  POST /api/agents/jobs/{id}/      │  prompt,     │
│             │       complete                    │  screenshots │
│             │ ◄─────────────────────────────── │             │
└─────────────┘                                   └─────────────┘
```

### Agent Lifecycle

```
REGISTER → HEARTBEAT (every 30s) → POLL → CLAIM → EXECUTE → COMPLETE/FAIL
```

1. **Register**: `POST /api/agents/register` with agent name
   → Backend creates agent doc, generates a token, returns `{agent_id, token}`
   → Token is hashed before storage, only shown once at registration

2. **Heartbeat**: `POST /api/agents/heartbeat` with `Authorization: Bearer <token>`
   → Updates `last_heartbeat_at` timestamp in DB
   → If no heartbeat for 5+ minutes, agent considered dead

3. **Poll**: `GET /api/agents/jobs/poll?agent_type=browser`
   → Returns oldest pending jobs (up to 5) for this agent
   → Jobs are ordered by `created_at` ascending

4. **Claim**: `POST /api/agents/jobs/{job_id}/claim`
   → Atomically changes status from `pending` → `running`
   → Uses `find_one_and_update` with filter `{status: "pending"}` to prevent double-claim

5. **Execute**: Agent runs the actual work:
   - Playwright opens Chromium (headless or headed)
   - Navigates to Gemini Web or ChatGPT
   - Uploads reference images if in payload
   - Pastes the prompt using keyboard events (not DOM manipulation — more reliable)
   - Clicks Send, waits for response
   - Downloads the generated image
   - Saves to `generated_images/{batch}/` directory

6. **Complete**: `POST /api/agents/jobs/{job_id}/complete` with result data
   → Sets `status: "completed"`, stores result
   → Backend updates COLL_IMAGES with the new image path

   **Fail**: `POST /api/agents/jobs/{job_id}/fail` with error message
   → Sets `status: "failed"`, stores error

### Job Model

```python
job = {
    "job_id": "job_abc123",
    "agent_id": "agent_xyz789",       # Which agent this belongs to
    "user_id": "usr_...",              # Who created the job
    "job_type": "browser_automation",  # Determines execution logic
    "payload": {
        "action": "generate_image",    # What to do
        "prompt": "...",               # The full ad prompt text
        "format": "HERO",              # Ad format
        "reference_images": [...],     # Optional image files to upload
        "output_path": "generated_images/batch_v3/HERO_always_hungry_EN.png",
    },
    "status": "pending",               # pending → running → completed/failed
    "progress": "",                     # Free-text progress updates
    "result": None,                    # Completed: {image_path, url, ...}
    "error": None,                     # Failed: "error message"
    "created_at": 1700000000.0,
    "started_at": 1700000001.0,
    "completed_at": 1700000030.0,
}
```

### The Local Agent Script (scripts/local_agent.py)

This is a standalone Python script (~350 lines) that:

1. Accepts command-line args: `--register`, `--mode` (headless/cdp/local-agent), `--api-base`
2. Registers with the backend, stores the token in a local file
3. Starts a main loop:
   - Every 5 seconds: `GET /api/agents/jobs/poll`
   - If job found: claim → determine mode → execute
   - In CDP mode: connects to existing Chrome on port 9222 (launched separately)
   - In headless mode: Playwright launches Chromium
   - In local-agent mode: uses the user's actual Chrome profile
4. Reports progress, completion, or failure back to backend

### The Gemini Web Automation Script (scripts/gemini_web_automation.py, 3,727 lines)

This is the heavy-lifting script for generating images through Gemini's web interface. It:

1. Opens a fresh Gemini chat tab (reuses initial blank tab for first prompt)
2. Selects the right model version (e.g., "Pro" or "Create image")
3. Uploads reference images, waits for attachments to fully process
4. Pastes the prompt using keyboard events (Ctrl+V or Shift+Insert)
5. Verifies the pasted text matches the expected prompt (integrity check)
6. Clicks Send, waits for response
7. Detects the generated image in the assistant's response
8. Downloads the image, waits for file to be complete
9. Moves to next prompt tab (never closes previous tabs — allows parallel processing)

Concurrency: Uses `ThreadPoolExecutor` to process multiple prompts in parallel. Format order: `["BA", "FEAT", "HERO", "TEST", "UGC"]`.

---

## 6. Admin Dashboard — Phase 6 Super Admin Features

### What Phase 6 Delivered

Phase 6 added a full super admin dashboard with 5 major features:
1. **Platform overview** — aggregate stats
2. **User management** — search, grant/revoke SA, disable
3. **Organization management** — list, disable orgs
4. **Config management** — view all configs, replace, version history
5. **Audit log** — searchable event log with redacted sensitive data
6. **Readiness checks** — 12 health checks
7. **Exports** — safe JSON downloads for users, orgs, configs, audit

### Files

| File | Lines | Purpose |
|---|---|---|
| `admin/admin_auth.py` | 71 | `bootstrap_super_admin()`, `require_super_admin()`, `require_super_admin_dependency()` |
| `admin/admin_routes.py` | 1,106 | All `/api/admin/*` endpoints |
| `admin/admin_serializers.py` | 150+ | `safe_user()`, `safe_org()`, `safe_config()`, `safe_audit()` |
| `frontend/admin.html` | 200+ | Admin page HTML layout |
| `frontend/js/admin.js` | 2,000+ | Admin dashboard JS logic |

### Super Admin Detection

```python
# admin_auth.py
SUPER_ADMIN_EMAILS = os.getenv("SUPER_ADMIN_EMAILS", "")  # comma-separated
# Cached in memory after first read

def bootstrap_super_admin(user):
    """Called on every login. If email matches, grant super admin."""
    if user.email in SUPER_ADMIN_EMAILS:
        user.is_super_admin = True
        user.is_platform_admin = True
        if not user.is_active:
            user.is_active = True
        update in DB

def require_super_admin(session_token):
    user = require_user(session_token)
    if not user.is_super_admin:
        raise HTTPException(403)
```

### All Admin Endpoints

#### Overview

| Method | Endpoint | What it does |
|---|---|---|
| `GET` | `/api/admin/overview` | Returns `{total_users, total_orgs, active_runs, total_configs, recent_audit_events}` |

#### User Management

| Method | Endpoint | What it does |
|---|---|---|
| `GET` | `/api/admin/users` | Paginated user list. Query params: `search`, `page`, `per_page`, `sort_by`, `sort_order`. Returns `{users[], total, page, per_page}` |
| `GET` | `/api/admin/users/{user_id}` | Single user detail with membership info |
| `POST` | `/api/admin/users/{user_id}/grant-sa` | Grant super admin. Requires typed confirmation "GRANT" |
| `POST` | `/api/admin/users/{user_id}/revoke-sa` | Revoke super admin. Requires typed confirmation "REVOKE" |
| `POST` | `/api/admin/users/{user_id}/disable` | Disable user account |

#### Organization Management

| Method | Endpoint | What it does |
|---|---|---|
| `GET` | `/api/admin/orgs` | Paginated org list. Query params: `search`, `page`, `per_page` |
| `GET` | `/api/admin/orgs/{org_id}` | Single org detail with members |
| `POST` | `/api/admin/orgs/{org_id}/disable` | Disable org. Requires typed confirmation "DISABLE" |

#### Config Management

| Method | Endpoint | What it does |
|---|---|---|
| `GET` | `/api/admin/configs` | List all configs across users and orgs |
| `GET` | `/api/admin/configs/{config_id}` | Single config detail |
| `POST` | `/api/admin/configs/{config_id}/replace` | Replace config content. Requires typed confirmation "REPLACE" |
| `GET` | `/api/admin/configs/{config_id}/versions` | List version history |
| `GET` | `/api/admin/configs/{config_id}/versions/{version_id}` | Get specific version |
| `POST` | `/api/admin/configs/{config_id}/revert` | Revert to version |

#### Audit Log

| Method | Endpoint | What it does |
|---|---|---|
| `GET` | `/api/admin/audit-logs` | Paginated audit log. Query params: `search`, `event_type`, `page`, `per_page`. Results are redacted (sensitive keys masked) |

#### Readiness

| Method | Endpoint | What it does |
|---|---|---|
| `GET` | `/api/admin/readiness` | Returns 12 checks: `{mongodb, required_env, google_oauth, frontend_origin, super_admins, indexes, storage, config_integrity, invite_security, provider_config_security, disabled_users, admin_routes}`. Each returns `{status: "pass"/"fail", detail: "..."}` |

#### Exports

| Method | Endpoint | What it does |
|---|---|---|
| `GET` | `/api/admin/exports/users` | Safe JSON export of all users (no hashes, no secrets) |
| `GET` | `/api/admin/exports/orgs` | Safe JSON export of all orgs |
| `GET` | `/api/admin/exports/configs` | Safe JSON export of all configs (files stripped, metadata only) |
| `GET` | `/api/admin/exports/audit-logs` | Safe JSON export of audit logs (redacted) |

#### Impersonation

| Method | Endpoint | What it does |
|---|---|---|
| `POST` | `/api/admin/impersonate` | Create a session as another user (for debugging). Returns session cookie |

### Safe Serializers (admin_serializers.py)

Every export endpoint uses a `safe_*()` function that:

1. Strips `_id` (MongoDB internal)
2. Strips `token_hash` from agents
3. Strips `files` from configs (replaces with `files_summary: {key: content_type}`)
4. Redacts sensitive keys via `redact_sensitive()` → case-insensitive frozenset matching (`api_key`, `token`, `secret`, `password`, `encryption`, `credential`)
5. Recursive depth limit of 20 to prevent infinite loops

### Frontend (admin.js) — Key Patterns

**Typed confirmation** (`confirmTyped()`): Before destructive actions (grant SA, revoke SA, replace config, disable org), the user must type a specific word into a prompt:

```javascript
// admin.js
async function confirmTyped(requiredWord, message) {
    const input = prompt(`Type "${requiredWord}" to confirm: ${message}`);
    return input?.trim() === requiredWord;
}
```

**Readiness rendering**: Each of 12 checks gets a colored card:
- `pass` → green ✓
- `fail` → red ✗
- Other → yellow ⚠

**Export buttons**: Section headers in users/orgs/configs/audit have a "Export JSON" button that triggers a download.

---

## 7. Config Versioning — How Changes Are Tracked

### Why Version Configs

Config files (product master doc, starting prompt, persona seeds, etc.) are the most critical content in the system. If someone accidentally deletes or corrupts a config, it breaks every ad generation. Versioning allows:

1. **Rollback** — restore any previous version
2. **Audit** — see who changed what and when
3. **Diff** — compare two versions to see exactly what changed
4. **Safety** — before any destructive replacement, a snapshot is automatically created

### Files

| File | Purpose |
|---|---|
| `services/config_version_service.py` | All versioning logic (283 lines) |
| `services/user_config.py` | Config CRUD, calls config_version_service before writes |

### How Versioning Works

Every time a config is saved (via `create_or_update_config()` in user_config.py), the flow is:

```
create_or_update_config(owner_type, owner_id, files, ...)
  │
  ├─ 1. Get current config from DB (COLL_USER_CONFIGS)
  │
  ├─ 2. Call config_version_service.create_config_version_before_update()
  │     ├─ Extract current files for hashing
  │     ├─ Compute SHA-256 hash of current files
  │     ├─ Compute SHA-256 hash of new files
  │     ├─ Compare → if same, skip (no-op)
  │     ├─ If different:
  │     │   ├─ Calculate changed_keys[] (which keys changed)
  │     │   ├─ Create version document:
  │     │   │   {
  │     │   │       "version_id": "ver_uuid",
  │     │   │       "config_id": "cfg_...",
  │     │   │       "owner_type": "user",
  │     │   │       "owner_id": "usr_...",
  │     │   │       "changed_by_user_id": "usr_...",
  │     │   │       "changed_by_email": "user@example.com",
  │     │   │       "change_reason": "user_save",
  │     │   │       "changed_keys": ["product_master_doc"],
  │     │   │       "before_hash": "abc123...",
  │     │   │       "after_hash": "def456...",
  │     │   │       "snapshot": { files: { ... full current files ... } },
  │     │   │       "created_at": 1700000000.0,
  │     │   │   }
  │     │   └─ Insert into COLL_CONFIG_VERSIONS
  │     └─ Return version document (or None if no change)
  │
  └─ 3. Update config in COLL_USER_CONFIGS with new files
```

### Version Document Schema

```python
{
    "version_id": "ver_a1b2c3d4e5f6",     # Unique version ID
    "config_id": "cfg_xyz789",             # Links to the config
    "owner_type": "user",                   # "user" or "org"
    "owner_id": "usr_abc123",              # Who owns this config
    "org_id": "org_def456",                # Optional org scope
    "changed_by_user_id": "usr_abc123",    # Who made the change
    "changed_by_email": "admin@example.com",
    "change_reason": "user_save",          # "rollback_before", "admin_replace", "copy"
    "changed_keys": [                      # Which specific keys changed
        "product_master_doc",
        "persona_seeds"
    ],
    "before_hash": "sha256_of_before",     # Hash of previous state
    "after_hash": "sha256_of_after",       # Hash of new state
    "snapshot": {                          # Full files at time of version creation
        "files": {
            "product_master_doc": {"content": "...", "content_type": "text/plain"},
            "starting_prompt": {"content": "...", "content_type": "text/plain"},
            # ... all CONFIG_KEYS
        }
    },
    "created_at": 1700000000.0,
}
```

The `snapshot` field stores the **full files at the time of the version**, not a diff. This means you can restore any version without needing to replay a chain of diffs.

### Key Functions

| Function | What it does |
|---|---|
| `create_config_version_before_update()` | Creates a snapshot BEFORE a change. Returns None if nothing changed. Called by `create_or_update_config()` in user_config.py |
| `get_config_versions(config_id, limit, offset)` | Lists version metadata (no snapshots) with pagination. Returns `{versions[], total}` |
| `get_config_version(config_id, version_id)` | Returns a single version WITH its snapshot |
| `rollback_config_to_version(config_id, version_id, actor, reason)` | 1. Gets version snapshot. 2. Creates a "rollback_before" version of current state. 3. Restores files from snapshot. |
| `copy_config(source, target, mode, actor)` | Copies config between users/orgs. `mode="merge_missing"` only fills empty keys. |

### Hash Computation

```python
def canonical_hash(value: dict) -> str:
    """Deterministic hash: sort keys, no whitespace, SHA-256."""
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
```

### Endpoints

| Method | Endpoint | What it does |
|---|---|---|
| `GET` | `/api/admin/configs/{config_id}/versions` | List all versions (paginated) |
| `GET` | `/api/admin/configs/{config_id}/versions/{version_id}` | Get specific version with snapshot |
| `POST` | `/api/admin/configs/{config_id}/revert` | Revert to version (requires body: `{version_id, reason}`) |

### Important: Versioning Happens Automatically

You don't need to call versioning explicitly. Every `create_or_update_config()` call automatically:
1. Compares old vs new
2. Creates a version if changed
3. Only then updates the config

This means even admin replacements, org config copies, and rollbacks are tracked as versions.

---

## 8. Quick Reference — Database Collections

| Collection | Key Fields | Used By |
|---|---|---|
| `users` | user_id, email, google_id, is_super_admin, is_active | Auth, Admin |
| `auth_identities` | provider, provider_user_id, user_id | Auth |
| `sessions` | token_hash, user_id, expires_at | Auth (TTL index auto-deletes) |
| `runs` | run_id, user_id, batch, status, config | Runs pipeline |
| `prompts` | prompt_id, run_id, format, persona_slug, language, content | Pipeline |
| `images` | image_id, run_id, file_path, batch, format | Pipeline |
| `llm_traces` | run_id, model, prompt, response, tokens, latency | Debugging |
| `provider_configs` | provider, user_id, config (encrypted) | LLM config |
| `user_configs` | config_id, owner_type, owner_id, files | Config system |
| `config_versions` | version_id, config_id, snapshot, changed_keys | Config versioning |
| `orgs` | org_id, name, domain, mode, owner_id | Org system |
| `org_members` | membership_id, org_id, user_id, role, status | Org system |
| `org_invites` | token_hash, org_id, email, role, expires_at | Org invites |
| `audit_logs` | event_id, event_type, actor_user_id, target_type, target_id | Audit |
| `agents` | agent_id, token_hash, user_id, is_active | Agent system |
| `agent_jobs` | job_id, agent_id, job_type, status, payload, result | Agent system |
| `browser_sessions` | session_id, agent_id, user_id | Browser automation |
| `json_blobs` | blob_id, data, content_type | Generic storage |
| `file_map` | file_path, run_id, user_id, batch | File resolution |

---

## 9. Quick Start

```bash
# Prerequisites: Python 3.11+, MongoDB running locally or Atlas URI

# Clone
git clone <repo> info && cd info

# Environment
cp .env.example .env.dashboard
# Edit .env.dashboard with your config

# Virtualenv & deps
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dashboard.txt

# Start (auto-creates indexes on first run)
uvicorn dashboard.backend.app:app --reload --port 4090

# Open browser
open http://localhost:4090

# Optional: start local agent
python scripts/local_agent.py --register --mode headless
```

---

## 10. File Cheat Sheet

| File | Lines | What it does |
|---|---|---|
| `dashboard/backend/app.py` | 7,994 | Main application — everything |
| `admin/admin_routes.py` | 1,106 | All admin endpoints |
| `admin/admin_serializers.py` | 150+ | Safe data redaction |
| `admin/admin_auth.py` | 71 | Super admin auth logic |
| `auth/service.py` | 158 | Google OAuth + session management |
| `auth/routes.py` | 119 | Auth HTTP endpoints |
| `auth/models.py` | 49 | Pydantic models |
| `agent/service.py` | 141 | Agent + job CRUD |
| `agent/routes.py` | 106 | Agent HTTP endpoints |
| `db/settings.py` | 100+ | Environment config |
| `db/client.py` | 62 | MongoDB connection |
| `db/collections.py` | 37 | Collection name constants |
| `db/indexes.py` | 185 | Index definitions |
| `services/user_config.py` | 1,200+ | Config management (big) |
| `services/org_helper.py` | 303 | Org + membership + audit |
| `services/config_version_service.py` | 283 | Config versioning |
| `services/invite_service.py` | 200+ | Invite flow |
| `services/email_service.py` | 200+ | Email sending |
| `services/run_storage.py` | 200+ | Run/prompt/image CRUD |
| `services/provider_config.py` | 150+ | Encrypted provider configs |
| `services/config_permissions.py` | 150+ | Config access control |
| `security/crypto.py` | 70+ | Encryption, hashing, tokens |
| `scripts/local_agent.py` | 350+ | Playwright browser agent |
| `scripts/gemini_web_automation.py` | 3,727 | Gemini web automation |
| `scripts/generate_ads.py` | 1,000+ | Offline prompt assembler |
| `frontend/js/app.js` | 7,000+ | Main frontend logic |
| `frontend/js/admin.js` | 2,000+ | Admin dashboard logic |
| `frontend/js/auth.js` | 68 | Frontend auth state |
| `frontend/js/api.js` | 44 | fetchJSON with cache |
| `frontend/styles.css` | 4,000+ | All styles (dark/light theme) |
| `AD_CREATIVE_SYSTEM_PLAYBOOK.md` | 1,200+ | Master playbook |
