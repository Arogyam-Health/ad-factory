const LOCAL_API_ORIGIN = "http://127.0.0.1:8765";
const SESSION_PREFIX = "ad_factory_local_session:";
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
  }

  async discover() {
    return readJson(await fetch(`${this.baseUrl}/v1/info`, {
      method: "GET",
      cache: "no-store",
    }));
  }

  async registeredAgent(deviceId) {
    const response = await fetch("/api/agents", {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
    });
    const agents = await readJson(response);
    const agent = Array.isArray(agents)
      ? agents.find((item) => item.device_id === deviceId && item.supports_pairing)
      : null;
    if (!agent) throw new Error("This local device is not registered to your account");
    const heartbeatAge = Date.now() / 1000 - Number(agent.last_heartbeat_at || 0);
    if (!agent.is_active || heartbeatAge > 180) {
      throw new Error("Your paired local device is offline. Start the local agent and try again.");
    }
    return agent;
  }

  async pair({ agentId, ownerType = "user", ownerId, scopes = DEFAULT_SCOPES }) {
    if (!agentId || !ownerId) throw new Error("Agent and owner are required");
    const info = await this.discover();
    const challenge = await readJson(await fetch(
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
      Date.now() + 120_000,
    );
    while (Date.now() < deadline) {
      try {
        const session = await readJson(await fetch(
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
        sessionStorage.setItem(
          `${SESSION_PREFIX}${session.device_id}`,
          JSON.stringify(session),
        );
        return session;
      } catch (error) {
        if (error.status !== 401 || error.code !== "pairing_not_approved") throw error;
        await delay(400);
      }
    }
    throw new Error("Local pairing challenge expired");
  }

  session(deviceId) {
    const raw = sessionStorage.getItem(`${SESSION_PREFIX}${deviceId}`);
    if (!raw) return null;
    try {
      const session = JSON.parse(raw);
      if (Number(session.expires_at || 0) <= Date.now() / 1000) {
        sessionStorage.removeItem(`${SESSION_PREFIX}${deviceId}`);
        return null;
      }
      return session;
    } catch {
      sessionStorage.removeItem(`${SESSION_PREFIX}${deviceId}`);
      return null;
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
    if (deviceId && info.device_id !== deviceId) {
      throw new Error("The selected run belongs to a different local device");
    }
    const agent = await this.registeredAgent(info.device_id);
    if (agentId && agent.agent_id !== agentId) {
      throw new Error("The selected run belongs to a different local agent");
    }
    const current = this.session(info.device_id);
    if (
      current?.agent_id === agent.agent_id
      && current.owner_type === ownerType
      && current.owner_id === ownerId
      && scopes.every((scope) => current.scopes?.includes(scope))
    ) {
      return { info, agent, session: current };
    }
    const session = await this.pair({
      agentId: agent.agent_id,
      ownerType,
      ownerId,
      scopes,
    });
    return { info, agent, session };
  }

  async authorizedFetch(path, options = {}, deviceId) {
    const session = this.session(deviceId);
    if (!session?.access_token) throw new Error("Local pairing session is required");
    const headers = new Headers(options.headers || {});
    headers.set("Authorization", `Bearer ${session.access_token}`);
    return fetch(`${this.baseUrl}${path}`, { ...options, headers });
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

  async createRun({ runId, workspaceId, runNumber, flowType, deviceId }) {
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
        flow_type: flowType,
        operation_id: `create-${runId}`,
      }),
    }, deviceId));
  }

  async allocateLocalRun({
    ownerType = "user",
    ownerId,
    flowType,
    settings = {},
  }) {
    const info = await this.discover();
    const agent = await this.registeredAgent(info.device_id);
    const envelope = await this.allocateRun({
      agentId: agent.agent_id,
      deviceId: info.device_id,
      ownerType,
      ownerId,
      flowType,
      settings,
    });
    await this.ensurePaired({
      ownerType,
      ownerId,
      deviceId: envelope.device_id,
      agentId: envelope.agent_id,
    });
    const workspaceId = this.createWorkspaceId();
    await this.createRun({
      runId: envelope.run_id,
      workspaceId,
      runNumber: envelope.run_number,
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

  async assetObjectUrl(resourceId, deviceId) {
    const response = await this.authorizedFetch(
      `/v1/assets/${encodeURIComponent(resourceId)}/content`,
      { method: "GET", cache: "no-store" },
      deviceId,
    );
    if (!response.ok) await readJson(response);
    return URL.createObjectURL(await response.blob());
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

  async outputObjectUrl(outputId, deviceId) {
    const response = await this.authorizedFetch(
      `/v1/outputs/${encodeURIComponent(outputId)}/content`,
      { method: "GET", cache: "no-store" },
      deviceId,
    );
    if (!response.ok) await readJson(response);
    return URL.createObjectURL(await response.blob());
  }
}

export const localDataPlane = new LocalDataPlaneClient();

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
    outputObjectUrl: (...args) => localDataPlane.outputObjectUrl(...args),
  });
}
