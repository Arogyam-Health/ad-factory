/* extension.js — Chrome Extension status indicator and management */

import { fetchJSON } from "./api.js";
import { appendLog } from "./ui.js";

let statusPollingInterval = null;
let cachedExtensionStatus = { connected: false };

/* ─── DOM refs (created dynamically) ─── */

let statusEl = null;
let targetsEl = null;

/* ─── Initialize ─── */

export function initExtensionUI() {
  // Find or create the extension status container in the runs toolbar
  const toolbar = document.querySelector(".runs-toolbar");
  if (!toolbar) return;

  // Create status indicator
  statusEl = document.createElement("span");
  statusEl.id = "extensionStatus";
  statusEl.className = "extension-status";
  statusEl.innerHTML = `<span class="extension-dot offline"></span> Extension: <span class="extension-label">Checking...</span>`;
  statusEl.title = "Chrome Extension CDP Bridge status";
  toolbar.insertBefore(statusEl, toolbar.firstChild);

  // Create targets dropdown (hidden by default)
  targetsEl = document.createElement("div");
  targetsEl.id = "extensionTargets";
  targetsEl.className = "extension-targets hidden";
  toolbar.parentNode.insertBefore(targetsEl, toolbar.nextSibling);

  // Start polling
  pollExtensionStatus();
  statusPollingInterval = setInterval(pollExtensionStatus, 10000);
}

/* ─── Polling ─── */

async function pollExtensionStatus() {
  try {
    const data = await fetchJSON("/api/extension/status");
    cachedExtensionStatus = data;
    renderStatus(data);
  } catch {
    cachedExtensionStatus = { connected: false };
    renderStatus({ connected: false });
  }
}

function renderStatus(data) {
  if (!statusEl) return;
  const dot = statusEl.querySelector(".extension-dot");
  const label = statusEl.querySelector(".extension-label");

  if (data.connected) {
    dot.className = "extension-dot online";
    label.textContent = `Connected`;
    statusEl.title = `Extension connected (${data.active_connections} total)`;
  } else {
    dot.className = "extension-dot offline";
    label.textContent = "Not connected";
    statusEl.title = "Install the Chrome Extension and connect from its popup";
  }
}

/* ─── Public API ─── */

export function getExtensionStatus() {
  return cachedExtensionStatus;
}

export async function navigateTab(url, targetId = "") {
  const params = new URLSearchParams({ url });
  if (targetId) params.set("target_id", targetId);
  return fetchJSON(`/api/extension/navigate?${params}`, { method: "POST" });
}

export async function executeCommand(method, params = {}, targetId = "") {
  const qs = new URLSearchParams({ method });
  if (targetId) qs.set("target_id", targetId);
  return fetchJSON(`/api/extension/command?${qs}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
}

export async function getTargets() {
  return fetchJSON("/api/extension/targets");
}

export async function takeScreenshot(targetId = "") {
  const params = targetId ? `?target_id=${targetId}` : "";
  return fetchJSON(`/api/extension/screenshot${params}`, { method: "POST" });
}

/* ─── Cleanup ─── */

export function stopExtensionPolling() {
  if (statusPollingInterval) {
    clearInterval(statusPollingInterval);
    statusPollingInterval = null;
  }
}
