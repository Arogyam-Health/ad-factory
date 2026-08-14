import { fetchJSON } from "./api.js";
import { isAuthenticated } from "./auth.js";

let pollInterval = null;
const HEARTBEAT_STALE_SEC = 90;

function agentStatus(agent) {
  const now = Date.now() / 1000;
  const elapsed = now - (agent.last_heartbeat_at || 0);
  if (!agent.is_active) return "offline";
  if (elapsed > HEARTBEAT_STALE_SEC * 2) return "offline";
  if (elapsed > HEARTBEAT_STALE_SEC) return "stale";
  return "online";
}

function formatTime(ts) {
  if (!ts) return "-";
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString();
}

export async function refreshAgentStatus() {
  const el = document.getElementById("agentStatus");
  if (!el) return;

  if (!isAuthenticated()) {
    el.innerHTML = `<span class="agent-status-dot offline"></span><span class="agent-status-label">Login to view agents</span>`;
    return;
  }

  try {
    const agents = await fetchJSON("/api/agents");
    const active = Array.isArray(agents) ? agents.filter((a) => a.is_active) : [];
    if (!active.length) {
      el.innerHTML = `<span class="agent-status-dot offline"></span><span class="agent-status-label">No agents registered</span>`;
      return;
    }

    const online = active.filter((a) => agentStatus(a) === "online").length;
    const total = active.length;
    const latest = active[0];
    const status = agentStatus(latest);
    const statusClass = status === "online" ? "online" : (status === "stale" ? "stale" : "offline");
    const statusLabel = status === "online" ? "Online" : (status === "stale" ? "Stale" : "Offline");

    el.innerHTML = `
      <span class="agent-status-dot ${statusClass}"></span>
      <span class="agent-status-label">${online}/${total} agents ${statusLabel}</span>
      <span class="agent-status-detail">${latest.name || "agent"} · ${formatTime(latest.last_heartbeat_at)}</span>
    `;
    el.title = active.map((a) =>
      `${a.name || a.agent_id}: ${agentStatus(a)} (${formatTime(a.last_heartbeat_at)})`
    ).join("\n");
  } catch {
    el.innerHTML = `<span class="agent-status-dot offline"></span><span class="agent-status-label">Agent status unavailable</span>`;
  }
}

export function initAgentStatus() {
  refreshAgentStatus();
  if (pollInterval) clearInterval(pollInterval);
  pollInterval = setInterval(refreshAgentStatus, 15000);
}

export function stopAgentStatus() {
  if (pollInterval) {
    clearInterval(pollInterval);
    pollInterval = null;
  }
}
