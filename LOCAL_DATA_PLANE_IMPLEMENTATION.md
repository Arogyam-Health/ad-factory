# Local Data Plane Refactor Implementation Guide

## Purpose

This document is the authoritative implementation handoff for converting Ad Factory into a local-first system where Render is stateless, MongoDB stores references and control metadata only, and all user/generated content is stored on the user's local agent machine.

This is a large refactor. Implement it completely, in phases, with focused tests and multiple commits. Do not stop after adding only upload endpoints or only moving image generation. Every content-bearing workflow listed here must be migrated.

## Non-Negotiable Requirements

1. User file uploads must go directly from the dashboard browser to the local agent on `127.0.0.1`.
2. Image, document, prompt, config, log, trace, import, export, and generated-output content must never be uploaded to Render.
3. Render must never proxy user file bytes to the local agent.
4. Render runtime disk must not be used for durable or workflow-critical user content.
5. MongoDB must contain only ownership, IDs, hashes, versions, dimensions, counts, statuses, timestamps, and other bounded metadata references.
6. MongoDB must not contain base64 files, prompt bodies, document bodies, config bodies, provider secrets, LLM request/response bodies, local paths, localhost URLs, or local capability tokens.
7. Structured copy generation and prompt assembly must execute on the local agent.
8. Structured and Reference browser automation must resolve prompts and exact ordered upload sets from local storage.
9. Generated 4:5 and 9:16 images, revisions, replacements, and history must remain local.
10. Existing functionality must continue to work after migration.
11. Render and MongoDB are on free plans. Do not introduce Redis, GridFS, Cloudinary, paid Render disks, or another cloud object store.
12. The local agent and browser may send content directly to external providers selected by the user. Render must not receive or persist that provider content.

## Repository Context

- Stack: FastAPI, MongoDB, vanilla JavaScript, local Playwright/browser automation.
- Render URL: `https://ad-factory-3rn5.onrender.com/`.
- Current local-agent root: `~/ad-factory-agent`.
- Current local artifact server: `http://127.0.0.1:8765`.
- Current browser CDP port: `127.0.0.1:9222`.
- Current branch at plan creation: `render-setup`.
- Current local-agent foundation commit: `26bcc99 Build durable local agent runtime`.

Before changing architecture, read:

```text
AGENTS.md
graphify-out/GRAPH_REPORT.md
LOCAL_DATA_PLANE_IMPLEMENTATION.md
```

Use `graphify query`, `graphify path`, or `graphify explain` for cross-module questions.

## Security Actions Required First

A MongoDB credential is hardcoded in these scripts:

```text
scripts/migrate_user_configs_owner_schema.py
scripts/push_all_configs_to_vinay.py
scripts/seed_vinay_config.py
```

Replace hardcoded connection strings with environment-based settings immediately. The external MongoDB password must be rotated by the owner. Removing it from the current files does not invalidate it or remove it from Git history.

A dashboard session token was also previously exposed in chat. It must be revoked externally.

Never place either secret in this document, tests, commands, commits, logs, or migration output.

## Authority Boundary

### Local Agent Is Authoritative For

| Resource | Local responsibility |
|---|---|
| Uploaded product images | Full immutable bytes and versions |
| Uploaded reference images | Full immutable bytes and versions |
| Product documents | Full content and versions |
| User/org config files | Full content and versions |
| Provider credentials | Encrypted local storage only |
| LLM requests and responses | Local trace objects only |
| Generated copy | Full local JSON/text |
| Assembled prompts | Full text, metadata and versions |
| Browser upload sets | Ordered resource-version mappings |
| Generated images | Full bytes and immutable versions |
| Revisions/replacements | Full history and active-version pointer |
| Logs/debug data | Local files/resources only |
| Imports/exports | Local resources and streamed local downloads |
| Run manifest | Full authoritative local manifest |

### Render and MongoDB Are Authoritative For

| Resource | Control-plane responsibility |
|---|---|
| Users and sessions | Authentication metadata |
| Organizations | Membership, role and ownership metadata |
| Agents/devices | Registration, online state and protocol support |
| Runs | Owner, run number, status, counts and local references |
| Jobs | Metadata-only command state, leases and bounded progress |
| Resource projections | IDs, hashes, versions, type, size and availability |
| Audits | Bounded metadata events with no content bodies |
| Deletions | Tombstones and local acknowledgement state |

### Render Runtime Disk May Contain

Only immutable deployed application files and ordinary framework/process temporary data that is not used as workflow state. No request handler or background worker may rely on a user-content path surviving another request or process restart.

## Existing Problems That Must Be Removed

### Structured Flow

- Browser uploads currently go to `POST /api/runs/execute` and Render filesystem paths.
- Product images currently share global `input/images` storage.
- Generated copy is written under Render run directories.
- Assembled prompts are written under `output/`.
- Full prompt content is stored in MongoDB `prompts.content`.
- Agent jobs contain full prompts and base64 images.
- LLM traces contain request and response bodies.
- Selected-prompt generation can bypass the local agent.
- Prompt import, edit, replacement, regeneration and some downloads use Render files.

### Reference Flow

- Reference library is globally stored under `dashboard_storage/reference_images`.
- Product workspace is globally stored under `dashboard_storage/reference_workspace`.
- The active V2 Reference worker executes directly on Render.
- Per-run reference/product copies are written to Render disk.
- Prompt text, comments, source lists, browser profiles, logs and outputs are written on Render.
- Reference resources are not correctly owner-scoped.
- Reference runs are not consistently represented in MongoDB production listings.

### Local Agent

- Local SQLite job payloads currently retain embedded prompt and base64 content.
- The local manifest currently models images but not complete runs, documents, prompts or upload sets.
- Current owner HMAC capabilities are permanent and embedded in URLs stored in MongoDB.
- Current artifacts are mutable files with incomplete version/history indexing.
- Whole-run deletion does not purge local data.
- Content-addressed objects have no references or garbage collection.

## Target End-to-End Workflow

1. Dashboard requests a new run envelope from Render.
2. Render allocates `run_id`, owner scope, run number and selected `device_id` only.
3. Dashboard pairs with the local data plane through an authenticated challenge.
4. Browser creates a local workspace for the allocated run.
5. Browser streams images and documents directly to localhost.
6. Local agent validates and commits resources into content-addressed storage.
7. Local agent sends metadata projections to Render through an idempotent outbox.
8. Dashboard requests run execution from Render using only `run_id`, `workspace_id`, command and bounded settings.
9. Render creates a metadata-only agent job pinned to the authoritative device.
10. Local agent resolves local config, local provider credentials, documents and assets.
11. Local agent calls the selected copy provider directly.
12. Local agent stores provider traces, generated copy and assembled prompts locally.
13. Local agent creates explicit ordered browser upload sets for each prompt.
14. Local browser automation uploads exact local resources to ChatGPT or Gemini.
15. Local agent stores partial/final outputs before announcing metadata changes.
16. Dashboard reads run metadata from Render and resource content directly from localhost.
17. Edits, revisions, replacements, regeneration, exports and downloads go directly to localhost.
18. Deletion creates a MongoDB tombstone and an idempotent local purge command.

## Device Selection

Do not select the most recently active agent blindly. Local browser access to `127.0.0.1` refers only to the machine running that browser.

Each agent registration must expose a stable random `device_id` and supported protocol/features. A dashboard session must pair with the localhost device on the same machine. New jobs must be pinned to that `device_id` and `agent_id`.

If the authoritative device is offline, show metadata with a clear unavailable state. Do not silently send a job to another computer that does not have the referenced content.

## Secure Local Pairing

Replace permanent query capabilities with this challenge flow:

1. Localhost returns a random challenge and `device_id`.
2. Authenticated dashboard submits the challenge to Render.
3. Render validates that the device belongs to the user/owner.
4. Render sends challenge approval through the authenticated agent WebSocket.
5. Browser exchanges the approved challenge at localhost.
6. Localhost issues a short-lived scoped session token.
7. Browser keeps the token in memory or `sessionStorage`.
8. Mutation requests use an authorization header.
9. Images use authenticated `fetch()` and Blob object URLs rather than tokenized `<img>` URLs.
10. Event streaming uses authenticated streaming fetch or a dedicated short-lived event token.

Required scopes:

```text
manifest:read
content:read
assets:write
documents:write
prompts:write
runs:execute
outputs:write
revisions:write
delete
```

Require exact production-origin CORS, reject `null` origins, validate loopback `Host`, support Private Network Access preflight, and never treat CORS as authentication.

## Local Database Schema

Use transactional schema migrations and preserve the existing database with a backup before upgrading.

### `objects`

```text
sha256 PRIMARY KEY
relative_path UNIQUE
bytes
media_type
created_at
verified_at
```

### `resources`

```text
resource_id PRIMARY KEY
owner_key
kind
logical_key
current_version
status
created_at
updated_at
deleted_at
```

Supported kinds include:

```text
product_image
reference_image
product_document
config_file
provider_config
copy_batch
prompt
run_manifest
output_image
revision_prompt
log
trace
import
export
```

### `resource_versions`

```text
resource_id
version
object_sha256
content_hash
metadata_json
created_at
PRIMARY KEY(resource_id, version)
```

### `runs`

```text
run_id PRIMARY KEY
owner_key
device_id
workspace_id
run_number
display_batch
flow_type
status
manifest_resource_id
manifest_version
created_at
updated_at
```

### `run_entries`

```text
run_id
entry_id
resource_id
resource_version
role
prompt_id
item_id
aspect_ratio
position
metadata_json
PRIMARY KEY(run_id, entry_id)
```

### `upload_sets`

```text
upload_set_id PRIMARY KEY
run_id
prompt_id
phase
version
created_at
```

### `upload_set_entries`

```text
upload_set_id
position
resource_id
resource_version
role
PRIMARY KEY(upload_set_id, position)
```

Supported roles include:

```text
product
logo
reference
source_creative
replacement
```

### `outputs`

```text
output_id PRIMARY KEY
run_id
prompt_id
item_id
aspect_ratio
current_version
status
created_at
updated_at
```

### `output_versions`

```text
output_id
version
resource_id
resource_version
source_output_version
revision_id
created_at
PRIMARY KEY(output_id, version)
```

### `revisions`

Keep source and result output versions, local comment/prompt resource, engine, status, attempt, error and timestamps. Never overwrite history without an indexed prior version.

### `change_log`

Use a monotonic sequence per local root. Record owner, resource type, resource ID, version, operation and timestamp. Support reconnect after a known sequence.

### `outbox`

Every remote projection event must have a stable `event_id`, operation ID and serialized metadata-only payload. Render must deduplicate event IDs.

### Object Garbage Collection

Maintain references from resource versions and output versions. Delete an object only when no live or retained version references it. Run deletion must respect revision retention and explicit purge behavior.

## Local API Contract

Implement under `/v1` rather than continuing to expand image-only legacy routes.

### Discovery and Pairing

```text
GET    /v1/info
POST   /v1/pairing/challenges
POST   /v1/pairing/sessions
DELETE /v1/pairing/sessions/current
```

`/v1/info` returns protocol versions, device ID and supported capabilities. It must not expose absolute local paths, raw secrets, PIDs, or owner data.

### Assets

```text
POST   /v1/assets
GET    /v1/assets
GET    /v1/assets/{resource_id}
GET    /v1/assets/{resource_id}/content
HEAD   /v1/assets/{resource_id}/content
DELETE /v1/assets/{resource_id}
```

Uploads must stream to a temporary file, enforce aggregate/per-file limits, verify extension and magic bytes, hash while streaming, and atomically commit database/object references.

### Documents and Configs

```text
GET /v1/documents
PUT /v1/documents/{logical_key}
GET /v1/documents/{logical_key}
GET /v1/documents/{logical_key}/versions

GET /v1/configs
PUT /v1/configs/{logical_key}
GET /v1/configs/{logical_key}
GET /v1/configs/{logical_key}/versions
```

Use ETags or explicit expected versions. Return `409 Conflict` for stale writes.

### Runs and Prompts

```text
POST   /v1/runs
GET    /v1/runs
GET    /v1/runs/{run_id}
POST   /v1/runs/{run_id}/execute
GET    /v1/runs/{run_id}/manifest
GET    /v1/runs/{run_id}/prompts
GET    /v1/prompts/{prompt_id}/content
PUT    /v1/prompts/{prompt_id}
POST   /v1/runs/{run_id}/prompt-imports
GET    /v1/runs/{run_id}/prompt-export
DELETE /v1/runs/{run_id}
```

### Generation and Outputs

```text
POST   /v1/runs/{run_id}/generations
GET    /v1/runs/{run_id}/outputs
GET    /v1/outputs/{output_id}
GET    /v1/outputs/{output_id}/content
POST   /v1/outputs/{output_id}/replacements
POST   /v1/outputs/{output_id}/revisions
GET    /v1/outputs/{output_id}/versions
POST   /v1/outputs/{output_id}/versions/{version}/activate
POST   /v1/outputs/{output_id}/archive
POST   /v1/outputs/{output_id}/restore
DELETE /v1/outputs/{output_id}
```

### Downloads and Events

```text
GET /v1/runs/{run_id}/download
GET /v1/changes?after=<sequence>&limit=<limit>
GET /v1/events?after=<sequence>
```

Support range requests and streamed ZIP generation. SSE/stream reconnect must resume from a known sequence.

## Browser Upload Sets

Never ask automation scripts to scan an arbitrary directory and never infer upload membership from filename stems.

Every prompt execution must receive an explicit ordered upload manifest similar to:

```json
{
  "upload_set_id": "ups_...",
  "prompt_id": "prm_...",
  "entries": [
    {"position": 1, "resource_id": "res_reference", "version": 1, "role": "reference"},
    {"position": 2, "resource_id": "res_product_1", "version": 3, "role": "product"},
    {"position": 3, "resource_id": "res_product_2", "version": 1, "role": "product"}
  ]
}
```

The local agent materializes the set into safe local paths and passes a generated JSON manifest to ChatGPT/Gemini automation.

Structured 4:5 prompts normally use selected product assets. Reference 4:5 prompts use exactly one selected reference followed by selected product assets. 9:16 conversion uses the matching generated 4:5 output as `source_creative`.

## Structured Flow Implementation

1. Replace Structured multipart submission to Render with run allocation plus direct localhost workspace upload.
2. Store product documents, optional source files, hypotheses, selected assets and execution config locally.
3. Move provider configuration and credentials to encrypted local storage.
4. Move Google/OpenCode calls into the local worker.
5. Store full provider traces locally and report only provider/model/status/duration/token counts/hashes.
6. Move copy validation, repair and normalized copy JSON local.
7. Run `generate_ads.py` locally or extract its reusable assembly functions into a local module.
8. Store generated prompt bodies and sidecars as local resource versions.
9. Replace Mongo `prompts.content` with local prompt references.
10. Route selected-prompt, 4:5 batch, both-aspect and standalone 9:16 generation through local upload sets.
11. Move prompt editing/import/export to local endpoints.
12. Remove new writes to Render `output`, `runtime`, run inputs and generated-image trees.

## Reference Flow Implementation

1. Move the reference library to owner-scoped local resources.
2. Move Reference Workspace product images, product document and starting prompt local.
3. Remove global Render workspace/list/delete behavior.
4. Preserve explicit selection of references and product images through resource IDs.
5. Resolve effective personas/config locally from versioned local resources.
6. For each persona and reference, build and store the prompt locally.
7. Create one upload set containing that reference first and selected product images after it.
8. Execute ChatGPT/Gemini on the local worker.
9. Store 4:5 outputs and metadata locally.
10. Convert each output to 9:16 using its exact 4:5 output version.
11. Publish run/status metadata through the outbox.
12. Make Reference runs visible through normal metadata-only Mongo run listing.
13. Eliminate Render Reference threads, subprocess files, output folders, logs and status JSON.

## Provider Configuration

Provider secrets must move from MongoDB to encrypted local storage. Use a local encryption key derived from or protected alongside the root-local secret. Restrict secret files to mode `0600`.

The provider-config dashboard must write secret values directly to localhost. MongoDB may retain provider type, model label, authority device and config resource reference, but not API keys or secret values.

Existing encrypted Mongo provider secrets require a one-time authenticated migration to the local agent, followed by verification and removal from MongoDB.

Google OAuth credentials used for dashboard login remain Render environment secrets because they belong to authentication, not generation content.

## Organization Configuration

MongoDB stores owner scope and resource references. Content remains on an authority device.

For organization-shared configuration, support explicit encrypted export/import replication between approved local agents. MongoDB records authority and verified replica device IDs. Do not implement plaintext cloud synchronization.

When no approved local replica is online, show metadata and an unavailable state instead of silently falling back to Render content.

## MongoDB Target Fields

### Runs

```json
{
  "run_id": "run_...",
  "owner_type": "user",
  "owner_id": "usr_...",
  "created_by_user_id": "usr_...",
  "agent_id": "agent_...",
  "device_id": "device_...",
  "run_number": 12,
  "display_batch": "v12",
  "flow_type": "structured",
  "status": "completed",
  "local_workspace_id": "wrk_...",
  "local_manifest_resource_id": "res_...",
  "local_manifest_version": 8,
  "prompt_count": 20,
  "image_count": 40,
  "created_at": 0,
  "updated_at": 0
}
```

### Prompts

```json
{
  "prompt_id": "prm_...",
  "run_id": "run_...",
  "resource_id": "res_...",
  "resource_version": 2,
  "sha256": "...",
  "format": "HERO",
  "persona": "always_hungry",
  "language": "EN",
  "status": "ready"
}
```

### Images

```json
{
  "artifact_id": "art_...",
  "run_id": "run_...",
  "prompt_id": "prm_...",
  "resource_id": "res_...",
  "resource_version": 3,
  "device_id": "device_...",
  "sha256": "...",
  "bytes": 123456,
  "width": 1080,
  "height": 1350,
  "aspect_ratio": "4:5",
  "status": "available"
}
```

### Agent Jobs

```json
{
  "job_id": "job_...",
  "agent_id": "agent_...",
  "device_id": "device_...",
  "user_id": "usr_...",
  "run_id": "run_...",
  "job_type": "execute_run",
  "command": "generate_images",
  "parameters": {
    "engine": "chatgpt",
    "mode": "both"
  },
  "status": "pending",
  "progress_code": "queued",
  "created_at": 0,
  "purge_at": null
}
```

Never put content, paths, comments, URLs, logs, local capabilities or provider secrets into these documents.

## Idempotency and Offline Behavior

1. Every mutation receives a client-generated operation ID.
2. MongoDB enforces operation uniqueness within owner scope.
3. Every outbox event has a stable event ID.
4. Render acknowledges already-processed events safely.
5. Every claimed job has a lease generation/fencing token.
6. Progress and terminal updates must include the current fence.
7. A stale worker cannot complete a reassigned job.
8. Local changes commit before remote announcements.
9. Render outages do not stop active local work.
10. Reconnect synchronizes changes after the last acknowledged sequence.
11. Local authority wins for local resource content and versions.
12. Render authority wins only for ownership and control-state transitions.

## Deletion Contract

1. Render marks a run `deleting` and stores a tombstone.
2. Render queues a metadata-only local purge command.
3. Local agent stops active operations for that run.
4. Local agent deletes run entries, prompts, assets owned only by the run, outputs, revisions, staging and logs according to retention policy.
5. Local agent decrements object references and removes unreferenced objects.
6. Local agent writes a durable deletion receipt/outbox event.
7. Render acknowledges the event and removes/minimizes prompt/image/job projections.
8. Render marks the run `deleted` or removes it after a grace period.
9. Offline devices retain tombstones until local deletion is acknowledged.

## Migration Strategy

### New Writes

Once local protocol V2 is ready, stop all new content writes to Render and MongoDB immediately. Do not dual-write new content bodies.

### Existing Local Data

Extend the current idempotent migration foundation to import:

- Existing local artifacts.
- Existing legacy output roots.
- Existing local revision history.
- Existing content-store objects.
- Recoverable local prompt/job staging.

### Existing Mongo Content

Provide a one-time migration command that:

1. Inspects prompt/config/job/trace content without mutation.
2. Imports content into the local resource store.
3. Computes and verifies hashes.
4. Writes metadata references.
5. Produces a redacted report.
6. Removes content bodies only with `--apply` after verification.
7. Is idempotent on rerun.

### Existing Render Files

Existing owner-scoped Render files may be imported only through an explicit migration path. Global ownerless reference/workspace files must be reported as unassigned and must not be automatically assigned to a user.

### Compatibility Window

Legacy content reads may remain read-only for one migration window because persisted production data exists. Do not maintain permanent backward-compatible write paths.

## Render Cleanup

After migration, remove runtime content writes and static mounts for:

```text
/generated_images
/output
/storage
/input
```

Retain only the frontend static mount and immutable application assets.

Update readiness checks to verify:

- Mongo connectivity.
- Local-data-plane protocol compatibility.
- Metadata-only agent-job policy.
- TTL/index presence.
- Online device counts.
- Missing/offline resource references.
- Absence of configured cloud/local Render content storage.

Update `render.yaml` so it no longer claims that generated content uses Render-local storage.

## Free-Tier Constraints

- Keep MongoDB documents small and bounded.
- Use TTL for terminal agent jobs.
- Keep progress and errors bounded to short codes/messages.
- Avoid overlapping indexes.
- Do not use GridFS.
- Do not use Render persistent disks.
- Do not add Redis.
- Do not upload generated images to Cloudinary.
- Use one process-compatible control design; WebSocket notification loss must be covered by polling.
- Handle Render sleep/wakeup through durable local outbox and reconnect.

## Feature-Parity Checklist

- [x] Structured run creation.
- [x] Structured copy generation.
- [x] Personal config.
- [x] Shared organization config references and local replication.
- [x] Individual organization config.
- [x] Product document editing.
- [x] Product image upload/list/delete.
- [x] Prompt listing and full-content viewing.
- [x] Prompt editing.
- [x] Prompt XLSX import/export.
- [x] Selected-prompt 4:5 generation.
- [x] Batch 4:5 generation.
- [x] Combined 4:5 and 9:16 generation.
- [x] Standalone 9:16 conversion.
- [x] Reference library upload/list/delete.
- [x] Reference product workspace.
- [x] Reference-specific comments.
- [x] Persona selection.
- [x] Reference 4:5 generation.
- [x] Reference 9:16 conversion.
- [x] ChatGPT engine.
- [x] Gemini engine.
- [x] Live progress.
- [x] Partial image gallery.
- [x] Dashboard reload and reconnect.
- [x] Image revision.
- [x] Image replacement.
- [x] Archive/regenerate/restore.
- [x] Individual image deletion.
- [x] Whole-run deletion.
- [x] Single image download.
- [x] Batch ZIP download.
- [x] Local backup and restore.
- [x] Metadata-only admin exports.
- [x] Offline-device UX.

## Required Test Suites

### Local Schema and Storage

- Schema migration and rollback safety.
- Content hashing and deduplication.
- Resource version immutability.
- Object reference counting and garbage collection.
- Run-manifest consistency.
- Revision and replacement lineage.
- Transactional deletion.

### Local API Security

- Exact-origin CORS.
- `null` origin rejection.
- Loopback host validation.
- Private Network Access preflights.
- Pairing expiry and revocation.
- Scope enforcement.
- Cross-user, cross-org and cross-device denial.
- Upload traversal, MIME mismatch and size limits.
- Idempotent upload retries.
- ETag/version conflicts.

### Control Plane

- Agent jobs contain no bodies, base64, paths, comments, URLs or secrets.
- Job/device ownership enforcement.
- Claim fencing.
- Event idempotency.
- Terminal job TTL.
- Bounded progress/errors.
- Missing/offline resource state.
- Deletion tombstone reconciliation.

### Structured Flow

- Browser uploads never touch Render endpoints.
- Local provider execution.
- Local copy and prompt assembly.
- Exact product upload sets.
- Selected/batch/both/9:16 modes.
- Prompt edit/import/export.

### Reference Flow

- Owner-scoped libraries/workspaces.
- Exact reference-first upload order.
- Selected product-only uploads.
- Local prompt assembly and comments.
- 4:5 and matching 9:16 lineage.
- Live progress and completed-run listing.

### Lifecycle

- Local revisions and version activation.
- Replacement, archive, regeneration and restore.
- Individual and run deletion.
- Downloads and ZIP streaming.
- Agent restart during jobs and revisions.
- Render outage during execution and completion.
- Render restart with no lost workflow data.

### Boundary Enforcement

Add static and dynamic assertions that fail if MongoDB or Render receives:

```text
base64
prompt body
document body
config body
provider key
LLM request/response body
localhost URL
local capability
absolute local path
browser log body
revision comment
```

Run the application with Render content directories read-only during integration tests. Every Structured and Reference feature must still pass.

## Verification Commands

Use the repository's environment and adapt exact test modules as they are introduced:

```bash
source .venv/bin/activate

python -m py_compile \
  scripts/local_agent.py \
  local_agent_runtime/*.py \
  dashboard/backend/app.py \
  dashboard/backend/agent/*.py

python -m unittest \
  tests.test_local_data_plane_schema \
  tests.test_local_data_plane_assets \
  tests.test_local_data_plane_security \
  tests.test_agent_metadata_jobs \
  tests.test_structured_local_flow \
  tests.test_reference_local_flow \
  tests.test_local_output_lifecycle

python tests/test_smoke.py

node --check dashboard/frontend/js/local-data-plane.js
node --check dashboard/frontend/js/main.js
node --check dashboard/frontend/js/reference-flow.js
node --check dashboard/frontend/js/runs.js
node --check dashboard/frontend/js/images.js
node --check dashboard/frontend/js/prompts.js

git diff --check

graphify update .
```

Add browser E2E coverage for HTTPS dashboard to HTTP loopback, local-network permission, uploads, reload, SSE/stream reconnect, and downloads.

Real ChatGPT and Gemini smoke tests must be run manually after deterministic fake-engine tests pass.

## Commit Sequence

Use multiple focused commits. Update the status ledger in this file after each phase.

1. `docs: define local data plane refactor`
2. `security: remove exposed credentials from scripts`
3. `feat(local): add versioned local resource storage`
4. `feat(local): add scoped localhost data plane API`
5. `feat(agent): add secure browser device pairing`
6. `feat(web): upload structured and reference assets locally`
7. `refactor(agent): use metadata-only control jobs`
8. `feat(agent): execute structured copy generation locally`
9. `feat(agent): execute structured browser generation locally`
10. `feat(agent): execute reference workflow locally`
11. `feat(local): complete local content lifecycle`
12. `refactor(server): remove runtime content persistence`
13. `feat(migration): migrate content to local references`
14. `test: enforce stateless render data boundary`
15. `docs: document local-first deployment and backup`

Before each commit, inspect:

```bash
git status
git diff
git diff --check
git log --oneline -10
```

Stage only files for that phase. Preserve unrelated user or concurrent-agent changes. Do not amend commits unless explicitly instructed. Do not push unless explicitly instructed.

## Status Ledger

Update this table during implementation. Include commit SHA and verification result.

| Phase | Status | Commit | Verification | Notes |
|---|---|---|---|---|
| Plan and boundary | Complete | `09c48a4` | Plan and boundary committed | Authoritative implementation boundary recorded |
| Security cleanup | Complete (repository) | `5191b7f` | 3 focused security tests pass | External credential and exposed-session rotation remain outstanding |
| Local schema | Complete (repository) | `7156263` | 11 focused schema tests and 22 existing local-agent storage/migration/runtime tests pass (33 total); local-agent `py_compile` and `git diff --check` pass | Committed by parent |
| Local API | Complete (repository) | `80c3665` | 16 focused API tests and 33 existing local-agent storage/migration/runtime tests pass (49 total); local-agent `py_compile` and `git diff --check` pass | Committed by parent |
| Device pairing | Complete (repository) | `9ade08a` | 8 focused pairing tests and 51 existing local-agent/API tests pass (59 total); `py_compile`, frontend `node --check`, and `git diff --check` pass | Committed by parent |
| Direct browser uploads | Complete (repository) | `7250fd7` | 9 focused allocation/frontend/network-boundary tests and 35 relevant pairing/local-data-plane/transport tests pass (44 total); `py_compile`, edited frontend `node --check`, and `git diff --check` pass | Committed by parent |
| Metadata-only jobs | Complete (repository) | `7ec1845` | 14 focused metadata-job tests and 58 existing agent/local-data-plane tests pass (72 total); `py_compile` passes | Committed by parent; full smoke has 3 pre-existing environment/startup failures with Mongo unavailable |
| Local structured copy | Complete (repository) | `d736a89` | 6 focused structured-copy tests and 71 existing agent/local-data-plane tests pass (77 total); `py_compile`, edited frontend `node --check`, lints, `git diff --check`, and Graphify update pass | Committed by parent |
| Local structured images | Complete (repository) | `8af3035` | 8 focused deterministic browser tests and 55 structured/local-data-plane/control/frontend regressions pass (63 total); `py_compile`, edited frontend `node --check`, lints, `git diff --check`, and Graphify update pass | Committed by parent; real ChatGPT and Gemini browser smoke tests remain manual final verification |
| Local reference flow | Complete (repository) | `29b0e6d` | 8 focused Reference tests and 63 local-data-plane/control/frontend regressions pass (71 total); `py_compile`, edited frontend `node --check`, lints, `git diff --check`, and Graphify update pass | Committed by parent; real ChatGPT and Gemini browser smoke tests remain manual final verification |
| Local lifecycle parity | Complete (repository) | `5eac75f` | Focused lifecycle, config/org/offline, local-data-plane, metadata-boundary, and frontend regressions pass; full verification recorded by implementing agent | Committed by parent |
| Stateless Render cleanup | Complete (repository) | `ae469c4` | 13 focused stateless/read-only boundary tests, 127 current regression tests, standalone smoke, backend `py_compile`, lints, `git diff --check`, and Graphify update pass | Committed by parent |
| Migration | Complete (repository) | `145d7fc` | 10 focused migration tests, 137 regression tests, 406 smoke assertions, `py_compile`, lints, `git diff --check`, and Graphify update pass | Dry-run-first, hash-verified migration committed |
| Full verification | Complete (repository) |  | 75 boundary/parity tests pass, including real Chromium HTTPS-dashboard-to-loopback upload and reload coverage; prior 137-test full regression and 406-assertion smoke suites pass | Commit pending parent; live ChatGPT/Gemini sessions remain final external verification |
| Operations documentation | Pending |  |  |  |

## Definition of Done

The refactor is complete only when all of the following are true:

1. Browser developer tools show user file uploads going to `127.0.0.1`, never the Render origin.
2. MongoDB inspection confirms that no content bodies, base64 files, paths, URLs, capabilities or secrets are present.
3. Render runtime-content directories can be read-only without breaking any feature.
4. Structured copy generation, prompt assembly and browser automation run locally.
5. Reference library, workspace, prompt assembly and browser automation run locally.
6. Exact browser upload membership is represented by tested upload-set resources.
7. Prompt and image lifecycle operations are fully local.
8. Dashboard reload works from Mongo metadata plus localhost content.
9. Offline local agents display unavailable metadata without broken or cross-device URLs.
10. Render restarts do not lose required data.
11. Local agent restarts recover jobs, revisions and synchronization safely.
12. Deletion reconciles correctly after offline periods.
13. Migration is dry-run-first, idempotent and hash-verified.
14. All focused, smoke, security and E2E tests pass.
15. Real ChatGPT and Gemini smoke tests pass.
16. Graphify is updated.
17. The status ledger is fully completed.

Do not declare completion based only on unit tests or only on Structured Flow. Reference Flow and all lifecycle operations are mandatory.

## Primary Files Expected To Change

### Local Runtime

```text
local_agent_runtime/storage.py
local_agent_runtime/artifact_server.py
local_agent_runtime/transport.py
local_agent_runtime/migration.py
local_agent_runtime/__init__.py
scripts/local_agent.py
scripts/chatgpt_web_sutomation.py
scripts/gemini_web_automation.py
scripts/reference_image_job.py
```

Split the growing storage and HTTP implementations into focused modules when doing so makes transactional boundaries and security easier to verify. Do not create abstraction layers that have no concrete reuse.

### Render Control Plane

```text
dashboard/backend/app.py
dashboard/backend/agent/auth.py
dashboard/backend/agent/connections.py
dashboard/backend/agent/routes.py
dashboard/backend/agent/service.py
dashboard/backend/db/collections.py
dashboard/backend/db/indexes.py
dashboard/backend/routes/batch.py
dashboard/backend/routes/defaults.py
dashboard/backend/routes/execute.py
dashboard/backend/routes/export_import.py
dashboard/backend/routes/runs.py
dashboard/backend/services/run_storage.py
dashboard/backend/services/user_config.py
dashboard/backend/services/config_version_service.py
dashboard/backend/services/json_blobs.py
dashboard/backend/reference_flow.py
dashboard/backend/reference_library.py
dashboard/backend/reference_workspace.py
dashboard/backend/reference_workspace_v2.py
render.yaml
```

### Dashboard Frontend

Add a focused client module:

```text
dashboard/frontend/js/local-data-plane.js
```

Integrate it with:

```text
dashboard/frontend/js/api.js
dashboard/frontend/js/agents.js
dashboard/frontend/js/main.js
dashboard/frontend/js/state.js
dashboard/frontend/js/runs.js
dashboard/frontend/js/prompts.js
dashboard/frontend/js/images.js
dashboard/frontend/js/image-comments.js
dashboard/frontend/js/reference-flow.js
dashboard/frontend/js/reference-product-selection.js
dashboard/frontend/index.html
dashboard/frontend/styles.css
```

### Tests

Retain and evolve existing local-agent tests, then add focused suites:

```text
tests/test_local_data_plane_schema.py
tests/test_local_data_plane_assets.py
tests/test_local_data_plane_security.py
tests/test_agent_metadata_jobs.py
tests/test_structured_local_flow.py
tests/test_reference_local_flow.py
tests/test_local_output_lifecycle.py
tests/test_localhost_frontend_integration.py
tests/test_smoke.py
```
