import { appendLog } from "./ui.js";
import { fetchJSON } from "./api.js";
import { state } from "./state.js";
import { getExtensionStatus } from "./extension.js";

export async function killChrome() {
  try {
    const data = await fetchJSON(`/api/kill-chrome`, { method: "POST" });
    appendLog(`Chrome killed. Chrome: ${data.chrome}, Gemini: ${data.gemini_processes}`);
  } catch (err) {
    appendLog(`Kill error: ${String(err)}`);
  }
}

let currentPollingInterval = null;
let progressEntries = [];

export function startProgressPolling(batchKey) {
  if (currentPollingInterval) clearInterval(currentPollingInterval);
  progressEntries = [];
  let lastCount = 0;
  currentPollingInterval = setInterval(async () => {
    try {
      const res = await fetch(`/api/progress/${encodeURIComponent(batchKey)}`);
      if (!res.ok) {
        if (res.status === 404) { clearInterval(currentPollingInterval); return; }
        return;
      }
      const data = await res.json();
      const entries = data.entries || [];
      if (entries.length > lastCount) {
        for (let i = lastCount; i < entries.length; i++) {
          const e = entries[i];
          const step = e.step || "";
          const msg = e.message || "";
          const time = e.time ? new Date(e.time * 1000).toLocaleTimeString() : "";
          progressEntries.push(`[${time}] [${step}] ${msg}`);
          appendLog(`[${time}] [${step}] ${msg}`);
        }
        lastCount = entries.length;
      }
    } catch (_) {}
  }, 3000);
}

export function stopProgressPolling() {
  if (currentPollingInterval) {
    clearInterval(currentPollingInterval);
    currentPollingInterval = null;
  }
}
