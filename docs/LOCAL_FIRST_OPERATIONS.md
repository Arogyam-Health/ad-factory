# Local-First Deployment and Operations

This guide covers the production topology introduced by the local data plane.
Render is a stateless control plane. User content and generation work remain on
the paired local device.

## Security boundary

Render and MongoDB may store authentication, organization, device, run, job,
status, count, hash, version, timestamp, and deletion metadata. They must not
store or proxy:

- Upload or generated-image bytes
- Prompt, document, config, comment, trace, request, or response bodies
- Provider credentials or local session capabilities
- Localhost URLs, absolute local paths, browser profiles, or raw browser logs

The local agent stores those bodies under its data root, calls selected
providers, assembles prompts, drives ChatGPT or Gemini in the local browser, and
serves authenticated content to the dashboard on loopback.

The default data root is `~/ad-factory-agent`. Override it with
`AGENT_DATA_DIR` or `scripts/local_agent.py --data-dir`. Treat the entire root
as sensitive user data.

## Production topology

Use only the services already declared by the repository:

1. A free Render web service running
   `dashboard.backend.control_app:app`.
2. A free MongoDB Atlas database for bounded control metadata.
3. One local agent per device that owns content.
4. A local Chrome or Brave profile logged in to ChatGPT and/or Gemini.

Do not add a Render disk, Cloudinary, GridFS, Redis, or another object store.
The Render filesystem may be ephemeral and read-only except for ordinary
framework temporary files.

## Deploy the control plane

Deploy `render.yaml`, then set these secret or deployment-specific values in the
Render dashboard:

```text
DEPLOYMENT_MODE=production
BROWSER_AUTOMATION_MODE=local-agent
MONGODB_URI
MONGODB_DB_NAME=ad_factory
APP_SECRET_KEY
ENCRYPTION_KEY
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
GOOGLE_REDIRECT_URI
FRONTEND_ORIGIN
CORS_ORIGINS
SESSION_EXPIRE_MINUTES=1440
```

Use the public HTTPS dashboard origin for the redirect and frontend values.
`CORS_ORIGINS` must be an explicit comma-separated allowlist with no wildcard.
Do not set `STORAGE_PROVIDER`, content-directory variables, provider-generation
credentials, localhost URLs, or local capability tokens on Render.

After deployment, verify:

```bash
curl -fsS https://YOUR-SERVICE.onrender.com/healthz
curl -fsS https://YOUR-SERVICE.onrender.com/api/version
```

The version response must report `"content_plane": "localhost"`. Authenticated
admin readiness must show the local protocol, metadata-only job policy, TTL
indexes, device availability, resource references, and absent content storage.

## Start a local device

Install dependencies and the Playwright browser:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dashboard.txt
playwright install chromium
```

Start the agent and optionally let it launch a local browser:

```bash
python scripts/local_agent.py \
  --api-base https://YOUR-SERVICE.onrender.com \
  --data-dir "$HOME/ad-factory-agent" \
  --launch-browser \
  --browser chrome
```

Alternatively, start Chrome yourself with remote debugging on loopback:

```bash
google-chrome --remote-debugging-port=9222
```

Log in to ChatGPT and Gemini in that browser profile. Never expose port 9222 or
the local data-plane port 8765 to the LAN or internet.

If the agent does not already have a token, registration requires an
authenticated dashboard session. Avoid putting a session cookie on a command
line or in shell history. Once registered, the agent persists its device
identity locally and reconnects through WebSocket with HTTP polling fallback.

Useful options include `--name`, `--poll-interval` (default 25 seconds),
`--cdp-port` (9222), `--artifact-port` (8765), `--token`, and
`--session-cookie`. Prefer the `AD_FACTORY_SESSION` environment variable over a
cookie command-line argument for first registration, read it without echoing,
and unset it immediately. Only one supervisor may use a data root; a lock error
means another agent is already running there.

Inspect and migrate the older local artifact tree dry-run-first:

```bash
python scripts/local_agent.py storage inspect \
  --legacy-root "$HOME/ad-factory-agent-output"
python scripts/local_agent.py storage migrate \
  --legacy-root "$HOME/ad-factory-agent-output"
# Review the report, then repeat migrate with --apply.
```

`storage gc` reports candidates only and never deletes referenced or unverified
data.

## Pair the dashboard

Open the HTTPS dashboard on the same machine as the local agent. The browser:

1. Discovers `http://127.0.0.1:8765/v1/info`.
2. Requests a one-time local challenge.
3. Sends only challenge and device metadata to Render.
4. Receives approval through the authenticated agent connection.
5. Exchanges the approved challenge for a short-lived scoped local session.

The browser keeps that session in `sessionStorage`. Images are loaded with
authenticated fetches and Blob URLs, never capability-bearing image URLs. A
dashboard running on another machine cannot use this device's loopback service.

If pairing fails, confirm that:

- The local agent is running on the browser's machine.
- The dashboard lists the same active `device_id`.
- Port 8765 is bound only to `127.0.0.1`.
- Browser local-network access is allowed for the production dashboard.
- System time is correct so challenges and sessions are not expired.

## Provider configuration

Configure generation providers from the dashboard after pairing. Provider
secrets go directly to localhost and are encrypted under:

```text
<data-root>/config/providers/
<data-root>/config/provider-secrets.key
```

The provider directory is mode `0700`; secret files and the key are mode
`0600`. MongoDB receives only bounded provider metadata and local resource
references. Do not copy the key without the encrypted provider files, and do
not put either in Render environment variables.

Google generation credentials use the `x-goog-api-key` header. Dashboard Google
OAuth credentials remain on Render because they authenticate users rather than
generate content.

## Local data layout

Important paths under `<data-root>` are:

```text
state/agent.sqlite3       Authoritative manifests, versions, jobs, outbox
objects/sha256/           Content-addressed immutable objects
artifacts/                Legacy-compatible local artifacts
staging/                  Atomic upload and workflow temporary files
config/                   Device identity, registration, and local keys
config/providers/         Encrypted generation-provider configurations
migration/backups/        Encrypted pre-cleanup Mongo document snapshots
migration/checkpoint.json Resumable migration checkpoint
legacy/unassigned/        Legacy content without verified ownership
```

The backup ZIP snapshots SQLite, objects, and config. Migration backup-vault
files are separate encrypted evidence retained before a verified Mongo body is
removed. Keep both until migration sign-off.

## Backup

Back up before migration, upgrades, and destructive cleanup. Stop the local
agent for the most conservative offline procedure, then run:

```bash
export AGENT_DATA_DIR="$HOME/ad-factory-agent"
python3 - <<'PY'
import os
from pathlib import Path
from local_agent_runtime.lifecycle import backup_local_data
from local_agent_runtime.storage import AgentPaths

root = Path(os.environ["AGENT_DATA_DIR"]).expanduser()
destination = Path.home() / "ad-factory-local-backup.zip"
print(backup_local_data(AgentPaths(root), destination))
PY
```

The archive contains a consistent SQLite snapshot, content-addressed objects,
and local encrypted configuration. Its manifest records a SHA-256 hash for
every file. Store the archive in a user-controlled encrypted backup location.
It contains sensitive content even though provider values remain encrypted.

The paired dashboard can also download `/v1/backup` directly from localhost;
Render never receives the archive.

## Restore

Stop the local agent. Restore into an empty or disposable data root first:

```bash
export AGENT_DATA_DIR="$HOME/ad-factory-agent-restored"
export BACKUP_FILE="$HOME/ad-factory-local-backup.zip"
python3 - <<'PY'
import os
from pathlib import Path
from local_agent_runtime.lifecycle import restore_local_data
from local_agent_runtime.storage import AgentPaths

root = Path(os.environ["AGENT_DATA_DIR"]).expanduser()
backup = Path(os.environ["BACKUP_FILE"]).expanduser()
print(restore_local_data(AgentPaths(root), backup))
PY
```

Restore rejects unsafe archive paths and any file whose hash differs from the
manifest. Start the agent with the restored `--data-dir`, verify runs, prompts,
outputs, provider metadata, and device ownership, then switch production use to
that root. Keep the previous root until verification succeeds.

An authenticated operator may instead send the ZIP body to `POST /v1/restore`
with `Content-Type: application/zip` and a unique `Idempotency-Key`. The
frontend `restoreBackup` method implements this request. Stop active jobs first
and never proxy the ZIP through Render.

## Migrate legacy content

The migration is dry-run-first, idempotent, owner-scoped, checkpointed, and
hash-verified. Start the local agent and run inspection without `--apply`:

```bash
python scripts/migrate_content_to_local.py \
  --owner-key 'user:OWNER_ID' \
  --data-dir "$HOME/ad-factory-agent"
```

Add explicit legacy sources when applicable:

```bash
python scripts/migrate_content_to_local.py \
  --owner-key 'user:OWNER_ID' \
  --local-source "artifacts=$HOME/legacy-artifacts" \
  --render-owner-root "user:OWNER_ID=/path/to/owner-scoped-export" \
  --render-unassigned-root "/path/to/global-legacy-content"
```

Review only the redacted counts and statuses. Ownerless Render files are
reported as unassigned and are never imported automatically.

For apply, obtain a short-lived paired local session with `documents:write`
scope. Read it without echoing and keep it out of command history:

```bash
read -rsp "Local migration session: " LOCAL_AGENT_MIGRATION_TOKEN
echo
export LOCAL_AGENT_MIGRATION_TOKEN
python scripts/migrate_content_to_local.py \
  --apply \
  --owner-key 'user:OWNER_ID' \
  --data-dir "$HOME/ad-factory-agent"
unset LOCAL_AGENT_MIGRATION_TOKEN
```

Apply order is backup, local import, hash verification, metadata-reference
write, then removal of the verified legacy body. A mismatch or malformed body
is preserved. Re-running uses stable operation IDs and the checkpoint.

Use `--skip-mongo` when migrating only explicit filesystem sources. The retired
owner-schema and seed scripts must not be used for content migration.
The default agent endpoint is `http://127.0.0.1:8765`; override it with
`--agent-url` or `LOCAL_AGENT_URL`.

Mongo inspection covers legacy prompts, user configs, config versions, agent
jobs, LLM traces, JSON blobs, and provider configs. Encrypted originals are
written under `<data-root>/migration/backups/` before verified removal.

## Organization configuration replication

Organization-shared config content has one authority device. Replication to an
approved device is an explicit encrypted export/import:

- The package is encrypted with a replication secret of at least 32 bytes.
- Its authenticated metadata pins the authority and approved replica devices.
- Import fails on a different device.
- MongoDB stores authority and verified-replica metadata only.

Transfer the encrypted package and replication secret through separate trusted
channels. If no authority or approved replica is online, the dashboard should
show the resource as unavailable rather than falling back to cloud content.

Operator procedure:

1. Pair with the authority device and generate at least 32 random bytes for a
   one-use replication secret.
2. Call `POST /v1/configs/{logical_key}/replicas/export` on that device with the
   approved replica `device_id` and replication secret.
3. Transfer the encrypted package and secret through separate trusted channels.
4. Pair with the approved replica and call
   `POST /v1/configs/{logical_key}/replicas/import`.
5. Register only resource/version, authority-device, and verified-replica
   metadata with `PUT /api/local-config-references/{logical_key}`.
6. Confirm availability with `GET /api/local-config-references`, then destroy
   the one-use secret.

The frontend `exportSharedConfig` and `importSharedConfig` methods perform the
authenticated localhost calls. Never send the plaintext config or replication
secret to Render.

## Restart, outage, and deletion behavior

Local resource changes commit before their metadata projections. If Render is
asleep or unavailable, work already running locally can finish and durable
outbox events remain pending. Reconnect resumes after the last acknowledged
sequence. WebSocket notification loss falls back to job polling.

Jobs and browser outputs are idempotent. After a local-agent restart, completed
outputs are not regenerated and interrupted work resumes from committed local
state.

Run deletion first creates a Render tombstone and metadata-only purge job. An
offline authority device retains that tombstone until it reconnects, purges
run-owned local resources, performs reference-aware garbage collection, and
publishes a durable deletion receipt.

## Operational checks

Before and after a deployment:

1. Confirm the dashboard upload request targets `127.0.0.1`, not Render.
2. Confirm MongoDB documents contain only bounded metadata and references.
3. Confirm Render starts with content directories absent or read-only.
4. Generate one Structured 4:5 and matching 9:16 output with each enabled
   engine.
5. Generate one Reference 4:5 and matching 9:16 output and verify
   reference-first upload ordering.
6. Reload the dashboard and verify local prompts and images reconnect.
7. Exercise revision, replacement, archive, restore, individual deletion,
   whole-run deletion, single download, and ZIP download.
8. Stop Render during a local job, reconnect it, and verify outbox delivery.
9. Restart the local agent during a job and verify resume without duplicates.
10. Create and verify a backup before removing any legacy source.

## Troubleshooting

- **A legacy content route returns 410:** Expected. Use the paired localhost
  `/v1` operation; do not restore the Render write path.
- **Local device is unavailable:** Start the agent on the browser's machine,
  verify its heartbeat is newer than 180 seconds, and pair the exact device.
- **A job stays queued:** Jobs are pinned to one device. Check that device,
  WebSocket connectivity, and HTTP polling fallback.
- **A lock is held:** Stop the duplicate supervisor using the same data root.
  Never remove the lock while the original process is alive.
- **Provider migration is required:** Run the content migration dry run, then
  apply only after local hash verification.
- **Chrome cannot attach:** Confirm loopback port 9222, the selected browser
  profile, and active ChatGPT/Gemini logins.
- **Render is waking:** Free-tier cold starts are expected. Local work and
  outbox events remain durable and synchronize after wake.
- **Readiness reports missing references:** Bring the authority or approved
  replica online; never copy content into MongoDB as a fallback.
- **Provider secrets cannot decrypt:** Restore `provider-secrets.key` with the
  encrypted files or re-enter credentials; there is no cloud recovery.
- **Legacy Mongo values cannot decrypt:** Do not rotate `ENCRYPTION_KEY` until
  those values are migrated and verified locally.

## External security actions

Repository cleanup cannot invalidate a secret that was previously exposed.
The owner must:

1. Rotate the previously exposed MongoDB password in Atlas.
2. Update `MONGODB_URI` on Render.
3. Revoke the previously exposed dashboard session.
4. Review Atlas and dashboard audit activity around the exposure window.

For routine rotation:

- Rotating `APP_SECRET_KEY` invalidates every dashboard session; schedule a
  re-login window.
- Migrate legacy encrypted Mongo provider values before rotating
  `ENCRYPTION_KEY`.
- Rotate the Google OAuth client secret in Google Cloud and Render together.
- Re-register an agent whose bearer token is revoked; never reuse an exposed
  dashboard cookie.
- Loss or rotation of `provider-secrets.key` requires provider credentials to
  be entered again on each authority device.
- Do not rotate device identity or local runtime secrets during active pairing,
  jobs, deletion reconciliation, or config replication.

Never paste replacement credentials into issues, chat, tests, logs, migration
reports, or commits.
