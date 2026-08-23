const LOCAL_API_ORIGIN = "http://127.0.0.1:8765";

function localFetch(url, options = {}) {
  return fetch(url, {
    cache: "no-store",
    targetAddressSpace: "loopback",
    ...options,
  });
}
const SESSION_PREFIX = "ad_factory_local_session:";
const ACTIVE_OWNER_PREFIX = "ad_factory_local_owner:";
const PAIRING_WAIT_MS = 600_000;
const DEFAULT_SCOPES = Object.freeze([
  "manifest:read",
  "content:read",
  "assets:write",
  "documents:write",
  "prompts:write",
  "runs:execute",
  "outputs:write",
  "revisions:write",
  "delete",
]);

function ownerKeyOf(ownerType, ownerId) {
  return `${ownerType || "user"}:${ownerId || ""}`;
}

function storageGet(key) {
  try {
    const local = window.localStorage?.getItem(key);
    if (local) return local;
  } catch {
    /* ignore */
  }
  try {
    return window.sessionStorage?.getItem(key) || "";
  } catch {
    return "";
  }
}

function storageSet(key, value) {
  try {
    window.localStorage.setItem(key, value);
    window.sessionStorage.removeItem(key);
  } catch {
    try {
      window.sessionStorage.setItem(key, value);
    } catch {
      /* ignore quota / private-mode failures */
    }
  }
}

const CAS_CACHE_NAME = "ad-factory-local-cas";
const CAS_CACHE_CAP_BYTES = 200 * 1024 * 1024;
const CAS_LRU_KEY = "adFactoryCasLru";
const liveBlobUrls = new Map();

function casCacheKey(kind, id, version) {
  return `${kind}:${id}:v${version || 0}`;
}

function readCasLru() {
  try {
    const raw = JSON.parse(localStorage.getItem(CAS_LRU_KEY) || "[]");
    return Array.isArray(raw) ? raw : [];
  } catch {
    return [];
  }
}

function writeCasLru(entries) {
  try {
    localStorage.setItem(CAS_LRU_KEY, JSON.stringify(entries));
  } catch {
    // ignore quota
  }
}

function touchCasLru(key, bytes) {
  const entries = readCasLru().filter((item) => item.key !== key);
  entries.push({ key, bytes: Number(bytes) || 0, at: Date.now() });
  writeCasLru(entries);
}

async function trimCasCache(cache) {
  let entries = readCasLru();
  let total = entries.reduce((sum, item) => sum + (Number(item.bytes) || 0), 0);
  while (total > CAS_CACHE_CAP_BYTES && entries.length) {
    const evicted = entries.shift();
    total -= Number(evicted.bytes) || 0;
    try {
      await cache.delete(new Request(`https://local-cas/${evicted.key}`));
    } catch {
      // ignore
    }
  }
  writeCasLru(entries);
}

async function cachedText(kind, id, version, loader) {
  const key = casCacheKey(kind, id, version);
  try {
    const cache = await caches.open(CAS_CACHE_NAME);
    const request = new Request(`https://local-cas/${key}`);
    const hit = await cache.match(request);
    if (hit) return hit.text();
    const text = await loader();
    await cache.put(request, new Response(text, { headers: { "Content-Type": "text/plain; charset=utf-8" } }));
    touchCasLru(key, new Blob([text]).size);
    await trimCasCache(cache);
    return text;
  } catch {
    return loader();
  }
}

async function fetchImmutableBlob(url, authorizedFetch, deviceId) {
  const response = await authorizedFetch(url, { method: "GET" }, deviceId);
  if (!response.ok) await readJson(response);
  return response.blob();
}

async function cachedObjectUrl(kind, id, version, loader) {
  const key = casCacheKey(kind, id, version);
  let blob;
  try {
    const cache = await caches.open(CAS_CACHE_NAME);
    const request = new Request(`https://local-cas/${key}`);
    const hit = await cache.match(request);
    if (hit) {
      blob = await hit.blob();
    } else {
      blob = await loader();
      await cache.put(request, new Response(blob));
      touchCasLru(key, blob.size);
      await trimCasCache(cache);
    }
  } catch {
    blob = await loader();
  }
  const url = URL.createObjectURL(blob);
  liveBlobUrls.set(url, key);
  return url;
}

function revokeLiveBlobUrls() {
  for (const url of liveBlobUrls.keys()) {
    try { URL.revokeObjectURL(url); } catch { /* ignore */ }
  }
  liveBlobUrls.clear();
}

if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
  window.addEventListener("pagehide", revokeLiveBlobUrls);
}

function storageRemove(key) {
  try {
    window.localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
  try {
    window.sessionStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

function delay(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function operationId(prefix = "op") {
  const random = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}_${random}`;
}

async function readJson(response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload?.error?.message || payload?.detail || "Request failed");
    error.status = response.status;
    error.code = payload?.error?.code || "";
    throw error;
  }
  return payload;
}

async function submitControlApproval(payload) {
  const response = await fetch("/api/agents/pairing/challenges", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export class LocalDataPlaneClient {
  constructor(baseUrl = LOCAL_API_ORIGIN) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this._pairedOwner = "";
  }

  async discover() {
    return readJson(await localFetch(`${this.baseUrl}/v1/info`, {
      method: "GET",
    }));
  }

  _isOnlineAgent(agent) {
    const heartbeatAge = Date.now() / 1000 - Number(agent.last_heartbeat_at || 0);
    return Boolean(agent.is_active) && heartbeatAge <= 180;
  }

  async registeredAgent(deviceId, preferredAgentId = "") {
    const response = await fetch("/api/agents", {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
    });
    const agents = await readJson(response);
    const list = Array.isArray(agents) ? agents : [];
    const byDevice = list.filter((item) => item.device_id === deviceId);
    const pairingCapable = byDevice.filter((item) => item.supports_pairing);
    const pool = pairingCapable.length ? pairingCapable : byDevice;
    const online = pool.filter((item) => this._isOnlineAgent(item));
    const ranked = (online.length ? online : pool).sort(
      (a, b) => Number(b.last_heartbeat_at || 0) - Number(a.last_heartbeat_at || 0),
    );
    const agent = (
      (preferredAgentId
        ? ranked.find((item) => item.agent_id === preferredAgentId)
        : null)
      || ranked[0]
      || null
    );
    if (!agent) {
      throw new Error(
        "This dashboard has no pairing record for this machine. Restart the local agent with --api-base pointing at this site.",
      );
    }
    return agent;
  }

  async pair({ agentId, ownerType = "user", ownerId, scopes = DEFAULT_SCOPES }) {
    if (!agentId || !ownerId) throw new Error("Agent and owner are required");
    const info = await this.discover();
    const challenge = await readJson(await localFetch(
      `${this.baseUrl}/v1/pairing/challenges`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      },
    ));
    if (!info.device_id || info.device_id !== challenge.device_id) {
      throw new Error("Local device identity changed during pairing");
    }

    await submitControlApproval({
      agent_id: agentId,
      device_id: challenge.device_id,
      owner_type: ownerType,
      owner_id: ownerId,
      challenge_id: challenge.challenge_id,
      challenge: challenge.challenge,
      scopes: [...scopes],
    });

    const deadline = Math.min(
      Number(challenge.expires_at || 0) * 1000,
      Date.now() + PAIRING_WAIT_MS,
    );
    while (Date.now() < deadline) {
      try {
        const session = await readJson(await localFetch(
          `${this.baseUrl}/v1/pairing/sessions`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              challenge_id: challenge.challenge_id,
              challenge: challenge.challenge,
            }),
          },
        ));
        session.owner_type = ownerType;
        session.owner_id = ownerId;
        this.storeSession(session);
        return session;
      } catch (error) {
        if (error.status !== 401 || error.code !== "pairing_not_approved") throw error;
        await delay(400);
      }
    }
    throw new Error("Local pairing challenge expired");
  }

  // Several Google accounts can share one device, so sessions are stored per
  // owner and reads default to the owner this tab last paired with.
  storeSession(session) {
    const owner = ownerKeyOf(session.owner_type, session.owner_id);
    storageSet(
      `${SESSION_PREFIX}${session.device_id}:${owner}`,
      JSON.stringify(session),
    );
    storageSet(`${ACTIVE_OWNER_PREFIX}${session.device_id}`, owner);
    this._pairedOwner = owner;
  }

  activeOwnerKey(deviceId) {
    return storageGet(`${ACTIVE_OWNER_PREFIX}${deviceId}`) || "";
  }

  session(deviceId, ownerKey = "") {
    const owner = ownerKey || this._pairedOwner || "";
    if (!owner) return null;
    const storageKey = `${SESSION_PREFIX}${deviceId}:${owner}`;
    const raw = storageGet(storageKey);
    if (!raw) return null;
    try {
      const session = JSON.parse(raw);
      if (Number(session.expires_at || 0) <= Date.now() / 1000) {
        storageRemove(storageKey);
        return null;
      }
      return session;
    } catch {
      storageRemove(storageKey);
      return null;
    }
  }

  clearSessions() {
    this._pairedOwner = "";
    const prefixes = [SESSION_PREFIX, ACTIVE_OWNER_PREFIX];
    for (const store of [window.localStorage, window.sessionStorage]) {
      for (let index = store.length - 1; index >= 0; index -= 1) {
        const key = store.key(index);
        if (prefixes.some((prefix) => key?.startsWith(prefix))) {
          store.removeItem(key);
        }
      }
    }
  }

  async ensurePaired({
    ownerType = "user",
    ownerId,
    deviceId = "",
    agentId = "",
    scopes = DEFAULT_SCOPES,
  }) {
    const info = await this.discover();
    this._liveDeviceId = info.device_id || "";
    const owner = ownerKeyOf(ownerType, ownerId);
    const current = this.session(info.device_id, owner);
    if (current?.access_token && scopes.every((scope) => current.scopes?.includes(scope))) {
      storageSet(`${ACTIVE_OWNER_PREFIX}${info.device_id}`, owner);
      this._pairedOwner = owner;
      let agent = { agent_id: current.agent_id };
      try {
        const preferred = !deviceId || deviceId === info.device_id
          ? (agentId || current.agent_id || "")
          : "";
        agent = await this.registeredAgent(info.device_id, preferred);
      } catch {
        /* Live device already answered /v1/info; keep the stored session. */
      }
      return { info, agent, session: current };
    }
    const preferredAgentId = !deviceId || deviceId === info.device_id ? agentId : "";
    const agent = await this.registeredAgent(info.device_id, preferredAgentId);
    const session = await this.pair({
      agentId: agent.agent_id,
      ownerType,
      ownerId,
      scopes,
    });
    return { info, agent, session };
  }

  async authorizedFetch(path, options = {}, deviceId) {
    const liveDeviceId = this._liveDeviceId || deviceId;
    let session = this.session(liveDeviceId, this._pairedOwner)
      || this.session(deviceId, this._pairedOwner);
    if (!session?.access_token) throw new Error("Local pairing session is required");
    const send = (accessToken) => {
      const headers = new Headers(options.headers || {});
      headers.set("Authorization", `Bearer ${accessToken}`);
      return localFetch(`${this.baseUrl}${path}`, { ...options, headers });
    };
    let response = await send(session.access_token);
    if (response.status !== 401) return response;
    const payload = await response.clone().json().catch(() => ({}));
    if (payload?.error?.code !== "invalid_session") return response;

    storageRemove(
      `${SESSION_PREFIX}${deviceId}:${ownerKeyOf(session.owner_type, session.owner_id)}`,
    );
    const paired = await this.ensurePaired({
      ownerType: session.owner_type || "user",
      ownerId: session.owner_id,
      deviceId,
      agentId: session.agent_id || "",
      scopes: session.scopes || DEFAULT_SCOPES,
    });
    session = paired.session;
    return send(session.access_token);
  }

  async allocateRun({
    agentId,
    deviceId,
    ownerType = "user",
    ownerId,
    flowType,
    settings = {},
  }) {
    return readJson(await fetch("/api/runs/allocate", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        agent_id: agentId,
        device_id: deviceId,
        owner_type: ownerType,
        owner_id: ownerId,
        flow_type: flowType,
        settings,
      }),
    }));
  }

  createWorkspaceId() {
    return operationId("wrk");
  }

  async createRun({ runId, workspaceId, runNumber, flowType, deviceId, displayBatch }) {
    return readJson(await this.authorizedFetch("/v1/runs", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": `create-${runId}`,
      },
      body: JSON.stringify({
        run_id: runId,
        workspace_id: workspaceId,
        run_number: runNumber,
        display_batch: displayBatch || "",
        flow_type: flowType,
        operation_id: `create-${runId}`,
      }),
    }, deviceId));
  }

  async listRuns(deviceId) {
    const payload = await readJson(await this.authorizedFetch(
      "/v1/runs",
      { method: "GET", cache: "no-store" },
      deviceId,
    ));
    return payload.items || [];
  }

  async deleteRun(runId, deviceId) {
    return readJson(await this.authorizedFetch(
      `/v1/runs/${encodeURIComponent(runId)}`,
      {
        method: "DELETE",
        headers: { "Idempotency-Key": operationId("delete_run") },
      },
      deviceId,
    ));
  }

  async allocateLocalRun({
    ownerType = "user",
    ownerId,
    flowType,
    settings = {},
  }) {
    const info = await this.discover();
    const agent = await this.registeredAgent(info.device_id);
    await this.ensurePaired({
      ownerType,
      ownerId,
      deviceId: info.device_id,
      agentId: agent.agent_id,
    });
    const envelope = await this.allocateRun({
      agentId: agent.agent_id,
      deviceId: info.device_id,
      ownerType,
      ownerId,
      flowType,
      settings,
    });
    const workspaceId = this.createWorkspaceId();
    await this.createRun({
      runId: envelope.run_id,
      workspaceId,
      runNumber: envelope.run_number,
      displayBatch: envelope.display_batch,
      flowType,
      deviceId: envelope.device_id,
    });
    return { ...envelope, workspace_id: workspaceId };
  }

  async uploadAssets(files, {
    kind = "product_image",
    deviceId,
    operationId: requestOperationId = operationId("upload"),
  } = {}) {
    const form = new FormData();
    [...files].forEach((file, index) => {
      const filename = file.name || `${kind}-${index + 1}.png`;
      form.append("files", file, filename);
    });
    const response = await this.authorizedFetch(
      `/v1/assets?kind=${encodeURIComponent(kind)}`,
      {
        method: "POST",
        headers: { "Idempotency-Key": requestOperationId },
        body: form,
      },
      deviceId,
    );
    const payload = await readJson(response);
    return payload.items || [payload];
  }

  async listAssets({ kind = "", deviceId } = {}) {
    const payload = await readJson(await this.authorizedFetch(
      "/v1/assets",
      { method: "GET", cache: "no-store" },
      deviceId,
    ));
    const items = payload.items || [];
    return kind ? items.filter((item) => item.kind === kind) : items;
  }

  async deleteAsset(resourceId, {
    deviceId,
    operationId: requestOperationId = operationId("delete"),
  } = {}) {
    return readJson(await this.authorizedFetch(
      `/v1/assets/${encodeURIComponent(resourceId)}`,
      {
        method: "DELETE",
        headers: { "Idempotency-Key": requestOperationId },
      },
      deviceId,
    ));
  }

  async assetObjectUrl(resourceId, deviceId, version) {
    return cachedObjectUrl("asset", resourceId, version, () =>
      fetchImmutableBlob(
        `/v1/assets/${encodeURIComponent(resourceId)}/content`,
        this.authorizedFetch.bind(this),
        deviceId,
      ),
    );
  }

  async putText(collection, logicalKey, content, {
    deviceId,
    expectedVersion,
    runId,
    role,
    operationId: requestOperationId = operationId("write"),
  } = {}) {
    if (!["documents", "configs"].includes(collection)) {
      throw new Error("Unsupported local text collection");
    }
    const payload = {
      content,
      operation_id: requestOperationId,
    };
    if (Number.isInteger(expectedVersion)) payload.expected_version = expectedVersion;
    if (runId) payload.run_id = runId;
    if (role) payload.role = role;
    return readJson(await this.authorizedFetch(
      `/v1/${collection}/${encodeURIComponent(logicalKey)}`,
      {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": requestOperationId,
        },
        body: JSON.stringify(payload),
      },
      deviceId,
    ));
  }

  async getText(collection, logicalKey, deviceId) {
    const response = await this.authorizedFetch(
      `/v1/${collection}/${encodeURIComponent(logicalKey)}`,
      { method: "GET", cache: "no-store" },
      deviceId,
    );
    if (!response.ok) await readJson(response);
    return response.text();
  }

  async listProviderConfigs(deviceId) {
    const payload = await readJson(await this.authorizedFetch(
      "/v1/provider-configs",
      { method: "GET", cache: "no-store" },
      deviceId,
    ));
    return payload.items || [];
  }

  async getProviderConfig(provider, deviceId) {
    return readJson(await this.authorizedFetch(
      `/v1/provider-configs/${encodeURIComponent(provider)}`,
      { method: "GET", cache: "no-store" },
      deviceId,
    ));
  }

  async putProviderConfig(provider, config, {
    deviceId,
  } = {}) {
    return readJson(await this.authorizedFetch(
      `/v1/provider-configs/${encodeURIComponent(provider)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config }),
      },
      deviceId,
    ));
  }

  async deleteProviderConfig(provider, deviceId) {
    return readJson(await this.authorizedFetch(
      `/v1/provider-configs/${encodeURIComponent(provider)}`,
      { method: "DELETE" },
      deviceId,
    ));
  }

  async listOutputs(runId, deviceId) {
    const payload = await readJson(await this.authorizedFetch(
      `/v1/runs/${encodeURIComponent(runId)}/outputs`,
      { method: "GET", cache: "no-store" },
      deviceId,
    ));
    return payload.items || [];
  }

  async generateRun(runId, { engine, mode, count = 0 } = {}, deviceId) {
    const requestOperationId = operationId("generation");
    return readJson(await this.authorizedFetch(
      `/v1/runs/${encodeURIComponent(runId)}/generations`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": requestOperationId },
        body: JSON.stringify({ engine, mode, count, operation_id: requestOperationId }),
      },
      deviceId,
    ));
  }

  async outputRawBlob(outputId, deviceId) {
    const response = await this.authorizedFetch(
      `/v1/outputs/${encodeURIComponent(outputId)}/raw`,
      { method: "GET" },
      deviceId,
    );
    if (!response.ok) await readJson(response);
    return response.blob();
  }

  async outputObjectUrl(outputId, deviceId, version) {
    return cachedObjectUrl("output", outputId, version, () =>
      fetchImmutableBlob(
        `/v1/outputs/${encodeURIComponent(outputId)}/content`,
        this.authorizedFetch.bind(this),
        deviceId,
      ),
    );
  }

  async listPrompts(runId, deviceId) {
    const payload = await readJson(await this.authorizedFetch(
      `/v1/runs/${encodeURIComponent(runId)}/prompts`,
      { method: "GET", cache: "no-store" },
      deviceId,
    ));
    return payload.items || [];
  }

  async promptContent(promptId, deviceId, version) {
    return cachedText("prompt", promptId, version, async () => {
      const response = await this.authorizedFetch(
        `/v1/prompts/${encodeURIComponent(promptId)}/content`,
        { method: "GET" },
        deviceId,
      );
      if (!response.ok) await readJson(response);
      return response.text();
    });
  }

  async putPrompt(promptId, runId, content, expectedVersion, deviceId) {
    const requestOperationId = operationId("prompt");
    return readJson(await this.authorizedFetch(
      `/v1/prompts/${encodeURIComponent(promptId)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json", "Idempotency-Key": requestOperationId },
        body: JSON.stringify({
          run_id: runId,
          content,
          expected_version: expectedVersion,
          operation_id: requestOperationId,
        }),
      },
      deviceId,
    ));
  }

  async deletePrompt(promptId, deviceId) {
    return readJson(await this.authorizedFetch(
      `/v1/prompts/${encodeURIComponent(promptId)}`,
      {
        method: "DELETE",
        headers: { "Idempotency-Key": operationId("delete_prompt") },
      },
      deviceId,
    ));
  }

  async exportPrompts(runId, deviceId) {
    const response = await this.authorizedFetch(
      `/v1/runs/${encodeURIComponent(runId)}/prompt-export`,
      { method: "GET" },
      deviceId,
    );
    if (!response.ok) await readJson(response);
    return response.blob();
  }

  async importPrompts(runId, file, deviceId) {
    const requestOperationId = operationId("prompt_import");
    return readJson(await this.authorizedFetch(
      `/v1/runs/${encodeURIComponent(runId)}/prompt-imports`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          "X-Filename": file.name || "prompts.xlsx",
          "Idempotency-Key": requestOperationId,
        },
        body: file,
      },
      deviceId,
    ));
  }

  async outputAction(outputId, action, deviceId, payload = {}) {
    const requestOperationId = operationId(action);
    return readJson(await this.authorizedFetch(
      `/v1/outputs/${encodeURIComponent(outputId)}/${action}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": requestOperationId },
        body: JSON.stringify({ ...payload, operation_id: requestOperationId }),
      },
      deviceId,
    ));
  }

  async revisionStatus(revisionId, deviceId) {
    return readJson(await this.authorizedFetch(
      `/v1/revisions/${encodeURIComponent(revisionId)}`,
      { method: "GET", cache: "no-store" },
      deviceId,
    ));
  }

  async replaceOutput(outputId, file, deviceId) {
    const requestOperationId = operationId("replacement");
    return readJson(await this.authorizedFetch(
      `/v1/outputs/${encodeURIComponent(outputId)}/replacements`,
      {
        method: "POST",
        headers: {
          "Content-Type": file.type || "application/octet-stream",
          "X-Filename": file.name || "replacement.png",
          "Idempotency-Key": requestOperationId,
        },
        body: file,
      },
      deviceId,
    ));
  }

  async deleteOutput(outputId, deviceId) {
    return readJson(await this.authorizedFetch(
      `/v1/outputs/${encodeURIComponent(outputId)}`,
      { method: "DELETE", headers: { "Idempotency-Key": operationId("delete_output") } },
      deviceId,
    ));
  }

  async listTraces(deviceId) {
    const payload = await readJson(await this.authorizedFetch(
      "/v1/traces",
      { method: "GET", cache: "no-store" },
      deviceId,
    ));
    return payload.items || [];
  }

  async traceContent(traceId, deviceId) {
    const response = await this.authorizedFetch(
      `/v1/traces/${encodeURIComponent(traceId)}/content`,
      { method: "GET", cache: "no-store" },
      deviceId,
    );
    if (!response.ok) await readJson(response);
    return response.json();
  }

  async deleteTrace(traceId, deviceId) {
    return readJson(await this.authorizedFetch(
      `/v1/traces/${encodeURIComponent(traceId)}`,
      {
        method: "DELETE",
        headers: { "Idempotency-Key": operationId("delete_trace") },
      },
      deviceId,
    ));
  }

  async deleteAllTraces(deviceId) {
    return readJson(await this.authorizedFetch(
      "/v1/traces",
      {
        method: "DELETE",
        headers: { "Idempotency-Key": operationId("delete_all_traces") },
      },
      deviceId,
    ));
  }

  async downloadRun(runId, deviceId, { includeRaw = false } = {}) {
    const query = includeRaw ? "?include_raw=1" : "";
    const response = await this.authorizedFetch(
      `/v1/runs/${encodeURIComponent(runId)}/download${query}`,
      { method: "GET" },
      deviceId,
    );
    if (!response.ok) await readJson(response);
    return response.blob();
  }

  async streamEvents({
    after = 0,
    deviceId,
    onEvent,
    signal,
    reconnectDelay = 1000,
  } = {}) {
    let cursor = Math.max(0, Number(after) || 0);
    while (!signal?.aborted) {
      try {
        const response = await this.authorizedFetch(
          `/v1/events?after=${encodeURIComponent(cursor)}`,
          {
            method: "GET",
            cache: "no-store",
            headers: { Accept: "text/event-stream" },
            signal,
          },
          deviceId,
        );
        if (!response.ok) await readJson(response);
        if (!response.body) throw new Error("Local event stream is unavailable");
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (!signal?.aborted) {
          const { value, done } = await reader.read();
          buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
          const frames = buffer.split(/\r?\n\r?\n/);
          buffer = frames.pop() || "";
          for (const frame of frames) {
            const data = frame
              .split(/\r?\n/)
              .filter((line) => line.startsWith("data:"))
              .map((line) => line.slice(5).trim())
              .join("\n");
            if (!data) continue;
            const event = JSON.parse(data);
            cursor = Math.max(cursor, Number(event.sequence) || cursor);
            await onEvent?.(event);
            if (signal?.aborted) return cursor;
          }
          if (done) break;
        }
      } catch (error) {
        if (signal?.aborted || error?.name === "AbortError") return cursor;
      }
      if (!signal?.aborted) await delay(Math.max(0, reconnectDelay));
    }
    return cursor;
  }

  async exportBackup(deviceId) {
    const response = await this.authorizedFetch("/v1/backup", { method: "GET" }, deviceId);
    if (!response.ok) await readJson(response);
    return response.blob();
  }

  async restoreBackup(file, deviceId) {
    const requestOperationId = operationId("restore");
    return readJson(await this.authorizedFetch(
      "/v1/restore",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/zip",
          "X-Filename": file.name || "backup.zip",
          "Idempotency-Key": requestOperationId,
        },
        body: file,
      },
      deviceId,
    ));
  }

  async exportSharedConfig(logicalKey, approvedDeviceId, replicationSecret, deviceId) {
    const response = await this.authorizedFetch(
      `/v1/configs/${encodeURIComponent(logicalKey)}/replicas/export`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          approved_device_id: approvedDeviceId,
          replication_secret: replicationSecret,
        }),
      },
      deviceId,
    );
    if (!response.ok) await readJson(response);
    return response.blob();
  }

  async importSharedConfig(logicalKey, file, replicationSecret, deviceId) {
    return readJson(await this.authorizedFetch(
      `/v1/configs/${encodeURIComponent(logicalKey)}/replicas/import`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/vnd.ad-factory.encrypted-config+json",
          "X-Replication-Secret": replicationSecret,
          "Idempotency-Key": operationId("config_replica"),
        },
        body: file,
      },
      deviceId,
    ));
  }
}

export const localDataPlane = new LocalDataPlaneClient();
export function clearLocalPairingSessions() {
  localDataPlane.clearSessions();
}

if (typeof window !== "undefined") {
  window.AdFactoryLocalDataPlane = Object.freeze({
    allocateLocalRun: (...args) => localDataPlane.allocateLocalRun(...args),
    uploadAssets: (...args) => localDataPlane.uploadAssets(...args),
    listAssets: (...args) => localDataPlane.listAssets(...args),
    deleteAsset: (...args) => localDataPlane.deleteAsset(...args),
    assetObjectUrl: (...args) => localDataPlane.assetObjectUrl(...args),
    putText: (...args) => localDataPlane.putText(...args),
    getText: (...args) => localDataPlane.getText(...args),
    listProviderConfigs: (...args) => localDataPlane.listProviderConfigs(...args),
    getProviderConfig: (...args) => localDataPlane.getProviderConfig(...args),
    putProviderConfig: (...args) => localDataPlane.putProviderConfig(...args),
    deleteProviderConfig: (...args) => localDataPlane.deleteProviderConfig(...args),
    listOutputs: (...args) => localDataPlane.listOutputs(...args),
    generateRun: (...args) => localDataPlane.generateRun(...args),
    outputObjectUrl: (...args) => localDataPlane.outputObjectUrl(...args),
    listPrompts: (...args) => localDataPlane.listPrompts(...args),
    promptContent: (...args) => localDataPlane.promptContent(...args),
    putPrompt: (...args) => localDataPlane.putPrompt(...args),
    exportPrompts: (...args) => localDataPlane.exportPrompts(...args),
    importPrompts: (...args) => localDataPlane.importPrompts(...args),
    outputAction: (...args) => localDataPlane.outputAction(...args),
    revisionStatus: (...args) => localDataPlane.revisionStatus(...args),
    replaceOutput: (...args) => localDataPlane.replaceOutput(...args),
    deleteOutput: (...args) => localDataPlane.deleteOutput(...args),
    deleteRun: (...args) => localDataPlane.deleteRun(...args),
    exportBackup: (...args) => localDataPlane.exportBackup(...args),
    restoreBackup: (...args) => localDataPlane.restoreBackup(...args),
    exportSharedConfig: (...args) => localDataPlane.exportSharedConfig(...args),
    importSharedConfig: (...args) => localDataPlane.importSharedConfig(...args),
  });
}
