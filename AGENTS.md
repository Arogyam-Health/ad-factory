## project

Ad Factory — AI ad creative generation platform. Stack: FastAPI + MongoDB + Cloudinary + vanilla JS frontend. Owner-based configs, orgs with shared/individual configs, super admin dashboard. Phase 6 complete.

## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- For cross-module "how does X relate to Y" questions, prefer `graphify query "<question>"`, `graphify path "<A>" "<B>"`, or `graphify explain "<concept>"` over grep — these traverse the graph's EXTRACTED + INFERRED edges instead of scanning files
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost) , first start the venv then run this graphify command in the project root

## Phase 6 (complete)

- Backend: `redact_sensitive()` helper → case-insensitive frozenset, recursive depth 20, used by `safe_audit_log` metadata
- Readiness endpoint `GET /api/admin/readiness` — 12 checks: mongodb, required_env, google_oauth, frontend_origin, super_admins, indexes, storage, config_integrity, invite_security, provider_config_security, disabled_users, admin_routes
- 4 export endpoints: `GET /api/admin/exports/{users,orgs,configs,audit-logs}` — safe serializers, no secrets, configs stripped of files, audit redacted
- Frontend: `confirmTyped()` helper → typed-confirmation for grant SA (GRANT), revoke SA (REVOKE), replace copy (REPLACE), disable org (DISABLE)
- Frontend: Export JSON buttons on users/orgs/configs/audit section headers
- Frontend: Readiness dashboard section (renderReadiness) — summary cards + checks table
- Frontend: Runbook section (renderRunbook) — inline operational guide
- Route smoke script `scripts/check_admin_routes.py` — --base-url + --cookie
- 30 Phase 6 smoke tests (backend + frontend + static analysis)
- Commits: a7d939e (Phase 5 dashboard), 8b75bcb (fixes), d6cd1b7 (reopen fix), pending (Phase 6)

## Chrome Extension CDP Bridge

Chrome Extension for remote browser automation on Render production (server can't launch Chrome locally).

### Architecture
- **Extension** (`chrome-extension/`): Manifest V3 service worker, connects via WebSocket to `wss://<server>/api/extension/ws?session=<cookie>`
- **Server** (`dashboard/backend/services/extension_bridge.py`): WebSocket connection manager, CDP command dispatch
- **Routes** (`dashboard/backend/routes/extension.py`): WebSocket endpoint + REST API (`/api/extension/*`)
- **Frontend** (`dashboard/frontend/js/extension.js`): Status indicator in runs toolbar

### Key Commands
- `POST /api/extension/command?method=Page.navigate` — Execute CDP command via extension
- `POST /api/extension/navigate?url=...` — Navigate a tab
- `POST /api/extension/screenshot` — Capture screenshot
- `GET /api/extension/targets` — List browser tabs
- `GET /api/extension/status` — Check connection status

### Auth
- WebSocket: `?session=<session_cookie>` query param, validated via `get_current_user_from_cookie()`
- REST: Same session cookie as web frontend
- Rate limit: 10 commands/second per user

### CDP Domains
Whitelisted: Page, Runtime, DOM, Input, Target, Browser, Network

### Files
- `chrome-extension/manifest.json` — Extension manifest (Manifest V3)
- `chrome-extension/background.js` — Service worker: WebSocket + CDP bridge
- `chrome-extension/popup.html` + `popup.js` — Extension popup UI
- `chrome-extension/icons/` — Extension icons
- `dashboard/backend/services/extension_bridge.py` — Connection manager singleton
- `dashboard/backend/routes/extension.py` — FastAPI router (WebSocket + REST)
- `dashboard/frontend/js/extension.js` — Frontend status module
- `dashboard/frontend/js/chrome.js` — Updated to prefer extension bridge when connected
