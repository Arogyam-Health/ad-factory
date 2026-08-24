# Developer cloud notes

This is for people cloning
[Vinay-003/ad-factory](https://github.com/Vinay-003/ad-factory/tree/render-setup)
on the `render-setup` branch. It is not an operator setup guide.

Docs index: [https://github.com/Vinay-003/ad-factory/tree/render-setup/docs](https://github.com/Vinay-003/ad-factory/tree/render-setup/docs)

Current production shape:

```text
Render  = FastAPI + SPA + Mongo metadata + copy-LLM relay
Laptop  = local agent + Chrome CDP + ~/ad-factory-agent bytes
```

Copy text already uses HTTP APIs (OpenCode / Gemini) from the control plane.
Images are assembled locally, then ChatGPT or Gemini is driven in a local
Chrome window. Bytes never go to Render.

`STORAGE_PROVIDER` exists in `dashboard/backend/db/settings.py` and defaults to
`local`. It is not an S3 client. Production policy still says: do not put a
content store on Render.

## Clone and run

```bash
git clone https://github.com/Vinay-003/ad-factory.git
cd ad-factory
git checkout render-setup
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\python.exe ...
pip install -r requirements-dashboard.txt
uvicorn dashboard.backend.control_app:app --host 0.0.0.0 --port 4090
```

UI:

```bash
cd dashboard/web
npm install
npm run build
```

The control plane serves `dashboard/web/dist`. Source edits in
`dashboard/web/src` are invisible until you rebuild.

Local agent from the same clone:

```bash
python scripts/start_local_agent.py
```

Operators should use
[ad-factory-local-agent.zip](https://github.com/Vinay-003/ad-factory/raw/render-setup/ad-factory-local-agent.zip)
instead of a clone. See
[LOCAL_AGENT_README.md](https://github.com/Vinay-003/ad-factory/blob/render-setup/docs/LOCAL_AGENT_README.md).

## How a Structured image job works today

1. Studio `POST /api/runs/{run_id}/image-generation` with `engine` (`chatgpt` or
   `gemini`) and `mode` (`45`, `916`, `both`).
2. Render stores a job. The paired agent claims it.
3. `local_agent_runtime/local_agent.py` `execute_job` handles
   `execute_run` + `generate_images`.
4. It loads `/api/agents/runs/{run_id}/image-context` (assembled prompts,
   `conversion_916_prompt`, metadata).
5. `StructuredBrowserExecutor` writes a prompt file and shells
   `chatgpt_web_sutomation.py` or `gemini_web_automation.py`.
6. Those scripts attach to Chrome at `http://127.0.0.1:9222`.
7. Output bytes are committed locally. The dashboard reads them from the
   loopback data plane on port `8765`.

Reference jobs use `generate_reference` and `ReferenceWorkflowExecutor`.
The same browser scripts run underneath.

Copy jobs are a different path: `dashboard/backend/services/render_structured_copy.py`
calls the text provider from Render. Do not confuse the two.

## Add an API image path next to the browser

Goal: keep Chrome ChatGPT / Gemini working, and add an official image HTTP API
as another Studio engine.

### What to change

1. **Studio engine chip**
   `dashboard/web/src/pages/Studio.tsx` (`imageEngine`, ChatGPT / Gemini).
   Add a third value such as `openai_api` (or `gemini_api`). Persist it the
   same way (`adFactoryImageEngine`). Send it as `engine` on
   `POST /api/runs/{run_id}/image-generation`.

2. **Queue validation**
   `dashboard/backend/agent/routes.py` `queue_structured_image_generation`
   already reads `engine`. Allow the new id next to `chatgpt` / `gemini`.
   Keep requiring a paired `agent_id` / `device_id` while outputs still land
   on the laptop.

3. **Agent branch**
   In `local_agent_runtime/local_agent.py` `execute_job`, the
   `generate_images` arm always builds `StructuredBrowserExecutor`.
   Branch on `engine` (from job parameters or image-context):

   - `chatgpt` / `gemini` — existing browser executor
   - `openai_api` (or similar) — new executor

4. **New executor**
   Mirror `StructuredBrowserExecutor` enough to reuse:

   - assembled prompt text from image-context
   - packshot / product asset references
   - `_commit_output` / `_projection` so Studio, traces, and reuse-from-run
     keep working

   Call the vendor image HTTP API from the agent process. Write the same
   local output layout `local_agent_runtime/storage.py` already expects.

5. **Secrets**
   Provider keys already live encrypted in Mongo
   (`/api/user/provider-config/...`). Reuse the existing materialize-to-agent
   path. Do not print keys. Do not store a second plaintext copy on disk.

6. **Timeouts and batching**
   Browser jobs can run many minutes. An HTTP API is faster but still too
   slow to do a full multiplier batch inside one Render request. Keep the
   work on the agent loop for this step.

7. **Tests**
   Add a unit test that a fake API executor commits the same projection
   shape as the browser path. Do not hit a live vendor in CI.

### What not to do in this step

- Do not delete the Chrome scripts.
- Do not upload image bytes to Render or Mongo.
- Do not put vendor image keys in Render env if the agent is still the caller.
- Do not invent a second output folder schema.

## Move image API calls onto Render

Goal: Render calls the image HTTP API. The laptop is optional for that path.

This is a product change, not a flag flip. Browser automation still cannot
run on Render (`BROWSER_AUTOMATION_MODE=local-agent`, no Chrome on the
service).

### Blockers in the current code

- Render is stateless. There is no content disk.
- Packshots and reference images live on the agent disk.
- Dashboard image URLs are loopback (`http://127.0.0.1:8765`).
- Production startup forbids content-storage env vars and generation
  credentials on Render (`docs/LOCAL_FIRST_OPERATIONS.md`,
  `validate_production_settings`).
- A full batch will exceed a Render HTTP timeout. You need a worker or a
  job runner, not work inside the Studio POST.

### What you would add

1. **Object storage first**
   See the S3 section. Render cannot generate if it cannot read packshots
   and cannot write outputs.

2. **A worker process**
   A Render background worker (or equivalent) that claims
   `generate_images` jobs the same way the agent does, then calls the
   vendor API. Keep `POST /api/runs/{run_id}/image-generation` as the
   enqueue API.

3. **Engine split**
   - API engines → Render worker
   - `chatgpt` / `gemini` browser engines → still the local agent, or
     retire them

4. **Auth and keys**
   Decide whether Render decrypts the user's stored provider key for the
   worker, or uses a platform key. Either way, tighten audit logs and never
   return the key on ordinary GETs (the current fingerprint-only reads
   should stay).

5. **Pairing policy**
   API-only users should be able to send a plate without
   `Pair local agent`. Browser users still must pair. That means run
   records can exist with no `device_id` for API engines, and the
   `409 Run has no authoritative local device` check must become
   engine-aware.

6. **Asset URLs**
   Studio `LazyAsset` / local data plane clients must accept signed HTTPS
   object URLs for API runs. Do not keep pointing those runs at port 8765.

7. **Policy files to edit**
   - `dashboard/backend/db/settings.py` / `validate_production_settings`
   - `docs/LOCAL_FIRST_OPERATIONS.md` (today: do not add Cloudinary,
     GridFS, Redis, or another object store)
   - `.env.example`
   - pairing and job claim routes in `dashboard/backend/agent/`

8. **Cost and limits**
   Vendor image APIs bill per image. Multiplier × personas × formats adds
   up. Add a server-side cap next to the Studio `1..20` multiplier.

Until S3 (or similar) exists, do not move generation onto Render. You would
have nowhere durable to put the files.

## Move local files to Amazon S3

Goal: uploads, generated images, and optional traces live in a private S3
bucket instead of `~/ad-factory-agent`.

Mongo stays the source of truth for plate files, run metadata, job rows,
and encrypted keys. Do not store image bytes in Mongo (16 MiB document
limit; the dashboard already caps config files at 12 MiB).

### Bucket layout

Suggested key prefix (adjust, but keep it per-tenant):

```text
s3://<bucket>/v1/<user_id>/runs/<run_id>/outputs/<filename>
s3://<bucket>/v1/<user_id>/uploads/packshots/<resource_id>
s3://<bucket>/v1/<user_id>/uploads/reference/<resource_id>
```

Use private ACLs. No public-read. Serve with short-lived signed GET URLs.

### What to build

1. **IAM**
   A dedicated user or role with `s3:PutObject`, `s3:GetObject`,
   `s3:DeleteObject`, `s3:ListBucket` on that prefix only. Put
   `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`,
   `S3_BUCKET` in env. Prefer an instance role if you leave Render later.

2. **`STORAGE_PROVIDER=s3`**
   Wire the existing setting. Today it is read and otherwise unused for
   content. Add a thin store interface:

   - `put(key, bytes, content_type)`
   - `sign_get(key, ttl)`
   - `delete(key)`

   Keep `local` as the default so clones keep working without AWS.

3. **Write path**
   `local_agent_runtime/storage.py` and the upload routes in the local
   data plane (`local_agent_runtime/data_plane.py`) currently write files
   under the agent data root. Those writes become `put`. Metadata rows
   (resource id, filename, hash, owner) can stay in the local SQLite
   store **or** move to Mongo. Pick one and migrate both Structured and
   Reference.

4. **Read path**
   Dashboard image tags must use the signed URL, not
   `http://127.0.0.1:8765/...`. Pairing for preview is no longer required
   once bytes are in S3.

5. **Uploads from the browser**
   Either:

   - browser → Render → S3 (Render becomes a proxy; watch body size), or
   - browser → signed POST / presigned PUT straight to S3, Render only
     stores the key

   Presigned PUT is the better default.

6. **Encryption**
   Enable bucket default encryption (SSE-S3 or SSE-KMS). Do not put
   provider API keys in object metadata.

7. **Lifecycle**
   Expire abandoned multipart uploads. Decide how long generated ads
   live. Wire delete-run (`purge_run`) to delete the prefix.

8. **CORS**
   If the browser uploads to S3 directly, allow the dashboard origin only.

9. **Migration**
   One-off copy from `~/ad-factory-agent` into the prefix, then write a
   mapping from old local resource ids to S3 keys so old runs still open.
   Do this per device; each laptop has its own tree.

10. **What stays local even after S3**
    Chrome profiles, CDP, and the agent token file can stay on disk if
    you still support browser engines. Plate JSON stays in Mongo.

### What you do not need

- Cloudinary, GridFS, or Redis for this design
- Public bucket URLs
- Storing prompts that contain secrets in a world-readable prefix

### Suggested order

1. API engine on the **local agent** (browser stays).
2. S3 for new uploads and new outputs (`STORAGE_PROVIDER=s3`).
3. Render worker for API engines, reading and writing S3.
4. Optional: retire Chrome engines.
5. Optional: copy historical local trees into S3.

## Related files

| Path | Role |
| --- | --- |
| `dashboard/web/src/pages/Studio.tsx` | Engine chip, queue image job |
| `dashboard/backend/agent/routes.py` | `POST /api/runs/{run_id}/image-generation` |
| `local_agent_runtime/local_agent.py` | Job claim + browser / future API branch |
| `local_agent_runtime/structured_browser.py` | Current Chrome image path |
| `local_agent_runtime/storage.py` | Local byte + metadata store |
| `local_agent_runtime/data_plane.py` | Loopback file server |
| `dashboard/backend/db/settings.py` | `STORAGE_PROVIDER`, production checks |
| `dashboard/backend/services/render_structured_copy.py` | Copy LLM (already an API) |

## Related docs

- [docs/README.md](https://github.com/Vinay-003/ad-factory/blob/render-setup/docs/README.md)
- [docs/OPERATOR_PLATE_GUIDE.md](https://github.com/Vinay-003/ad-factory/blob/render-setup/docs/OPERATOR_PLATE_GUIDE.md)
- [docs/LOCAL_FIRST_OPERATIONS.md](https://github.com/Vinay-003/ad-factory/blob/render-setup/docs/LOCAL_FIRST_OPERATIONS.md)
- [docs/STRUCTURED_COPY_SYSTEM.md](https://github.com/Vinay-003/ad-factory/blob/render-setup/docs/STRUCTURED_COPY_SYSTEM.md)
- [README.md](https://github.com/Vinay-003/ad-factory/blob/render-setup/README.md)
