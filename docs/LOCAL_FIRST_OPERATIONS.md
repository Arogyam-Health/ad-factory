# Local-First Deployment and Operations

This guide covers the production topology introduced by the local data plane.
Render has no runtime content disk. Generation content and work remain on the
paired local device; the eight dashboard config files are stored in MongoDB.

## Security boundary

Render and MongoDB store authentication, organization, device, run, job,
status, count, hash, version, timestamp, and deletion metadata. MongoDB also
stores the eight bounded personal/organization dashboard config files and their
version snapshots plus user-scoped provider URL/model settings and encrypted
API keys. They must not otherwise store or proxy:

- Upload or generated-image bytes
- Generated prompt, uploaded document, comment, trace, request, or response bodies
- Plaintext provider credentials or local session capabilities
- Localhost URLs, absolute local paths, browser profiles, or raw browser logs

The local agent stores generation bodies under its data root, calls selected
providers, assembles prompts, drives ChatGPT or Gemini in the local browser, and
serves authenticated content to the dashboard on loopback.

Config sources, editors, personas, organization sharing, copying, and rollback
must load after dashboard login even when no local agent is running. Config
updates accept only the eight known keys, with a 12 MiB per-file and 12 MiB
total limit that leaves headroom below MongoDB's 16 MiB document limit.
Provider API keys are encrypted in MongoDB with Render's `ENCRYPTION_KEY`.
Ordinary reads return only a configured indicator; an authenticated `no-store`
request materializes the key to the paired agent immediately before local
execution.

The default data root is `~/ad-factory-agent`. Override it with
`AGENT_DATA_DIR` or `scripts/start_local_agent.py --data-dir`. Treat the entire root
as sensitive user data.

## Production topology

Use only the services already declared by the repository:

1. A free Render web service running
   `dashboard.backend.control_app:app`.
2. A free MongoDB Atlas database for bounded control metadata and dashboard configs.
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

Download [`ad-factory-local-agent.zip`](../ad-factory-local-agent.zip) instead of
cloning the whole repo. Guides:

- [`docs/LOCAL_AGENT_README.md`](LOCAL_AGENT_README.md)
- [`docs/LOCAL_AGENT_WINDOWS.md`](LOCAL_AGENT_WINDOWS.md)
- [`docs/LOCAL_AGENT_UBUNTU.md`](LOCAL_AGENT_UBUNTU.md)
- [`docs/LOCAL_AGENT_MAC.md`](LOCAL_AGENT_MAC.md)

Install **Python 3.12 exactly** (3.13+ cannot run the agent: `cgi` was
removed). Install dependencies from the unzipped folder.
`requirements-local-agent.txt` is already in the zip. Use the venv Python
(no activate, no global pip):

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-local-agent.txt
.venv/bin/python scripts/start_local_agent.py
```

Do not run `playwright install chromium`. The agent uses installed Google Chrome.

That is equivalent to setting `AD_FACTORY_SESSION` and running:

```bash
python scripts/start_local_agent.py \
  --api-base https://YOUR-SERVICE.onrender.com \
  --data-dir "$HOME/ad-factory-agent" \
  --launch-browser \
  --browser chrome
```

`--data-dir` defaults to `~/ad-factory-agent` on the current user account. Do
not hardcode another machine's home path.

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

Inspect local storage, reclaim unreferenced CAS objects, or wipe run content
while keeping device config and the product-image library:

```bash
python local_agent_runtime/local_agent.py storage gc
python local_agent_runtime/local_agent.py storage gc --apply
python local_agent_runtime/local_agent.py reset-local-data --confirm
```

`storage gc` reports unreferenced objects first. Re-run with `--apply` to
delete them and sweep abandoned staging trees. `reset-local-data` also removes
the retired `~/ad-factory-agent-output` tree.

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

One-time Mongo or filesystem body moves use
`dashboard.backend.agent.content_migration.MongoContentMigrator` plus the local
agent backup/restore flow above. There is no operator CLI for this. Encrypted
originals stay under `<data-root>/migration/backups/` until verification
succeeds. Dashboard configs are not removed by content migration.

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
- Decrypt and re-encrypt provider values as part of any planned
  `ENCRYPTION_KEY` rotation; changing it without migration makes saved keys
  unreadable.
- Rotate the Google OAuth client secret in Google Cloud and Render together.
- Re-register an agent whose bearer token is revoked; never reuse an exposed
  dashboard cookie.
- Do not rotate device identity or local runtime secrets during active pairing,
  jobs, deletion reconciliation, or config replication.

Never paste replacement credentials into issues, chat, tests, logs, migration
reports, or commits.
