## project

Ad Factory — AI ad creative generation platform. Stack: FastAPI + MongoDB + local agent. React press-room UI at `/` (Vite app in `dashboard/web`). Owner-based configs, orgs with shared/individual configs, super admin dashboard. Phase 6 complete.

## Phase 6 (complete)

- Backend: `redact_sensitive()` helper → case-insensitive frozenset, recursive depth 20, used by `safe_audit_log` metadata
- Readiness endpoint `GET /api/admin/readiness` — 12 checks: mongodb, required_env, google_oauth, frontend_origin, super_admins, indexes, storage, config_integrity, invite_security, provider_config_security, disabled_users, admin_routes
- 4 export endpoints: `GET /api/admin/exports/{users,orgs,configs,audit-logs}` — safe serializers, no secrets, configs stripped of files, audit redacted
- Frontend: `confirmTyped()` helper → typed-confirmation for grant SA (GRANT), revoke SA (REVOKE), replace copy (REPLACE), disable org (DISABLE)
- Frontend: Export JSON buttons on users/orgs/configs/audit section headers
- Frontend: Readiness dashboard section (renderReadiness) — summary cards + checks table
- Frontend: Runbook section (renderRunbook) — inline operational guide
- 30 Phase 6 smoke tests (backend + frontend + static analysis)
- Commits: a7d939e (Phase 5 dashboard), 8b75bcb (fixes), d6cd1b7 (reopen fix), pending (Phase 6)

## Chrome Extension CDP Bridge (retired)

The MV3 extension, `extension_bridge`, and HTTP CDP proxy are removed. Local generation uses the paired localhost agent.

Kept only as quiet stubs:
- `GET /api/extension/status` → `{ connected: false, disabled: true }`
- `WS /api/extension/ws` → close 1008 "Use the paired local agent"

Do not recreate the extension stack. Image generation uses the paired local agent.
