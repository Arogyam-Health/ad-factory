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
