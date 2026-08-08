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

  async authorizedFetch(path, options = {}, deviceId) {
    const session = this.session(deviceId);
    if (!session?.access_token) throw new Error("Local pairing session is required");
    const headers = new Headers(options.headers || {});
    headers.set("Authorization", `Bearer ${session.access_token}`);
    return fetch(`${this.baseUrl}${path}`, { ...options, headers });
  }
}

export const localDataPlane = new LocalDataPlaneClient();
