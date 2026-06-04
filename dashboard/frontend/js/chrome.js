import { appendLog } from "./ui.js";
import { fetchJSON } from "./api.js";
import { state } from "./state.js";

const launchChromeBtn = document.getElementById("launchChrome");
const killChromeBtn = document.getElementById("killChrome");
const headlessToggle = document.getElementById("headlessMode");

function showChromeKillButton() {
  if (killChromeBtn) killChromeBtn.style.display = "";
  state.chromeProcessActive = true;
}

function hideChromeKillButton() {
  if (killChromeBtn) killChromeBtn.style.display = "none";
  state.chromeProcessActive = false;
}

async function killChrome() {
  try {
    const data = await fetchJSON(`/api/kill-chrome`, { method: "POST" });
    hideChromeKillButton();
    appendLog(`Chrome killed. Chrome: ${data.chrome}, Gemini: ${data.gemini_processes}`);
  } catch (err) {
    appendLog(`Kill error: ${String(err)}`);
  }
}

if (headlessToggle) {
  headlessToggle.addEventListener("change", () => {
    state.headlessModeEnabled = headlessToggle.checked;
    appendLog(`Headless mode ${state.headlessModeEnabled ? "ON" : "OFF"}`);
  });
}

if (launchChromeBtn) {
  launchChromeBtn.addEventListener("click", async () => {
    launchChromeBtn.disabled = true;
    try {
      const data = await fetchJSON(`/api/launch-visible-browser`, { method: "POST" });
      showChromeKillButton();
      appendLog(`${data.message} | CDP: ${data.cdp_url}`);
    } catch (err) {
      appendLog(`Launch error: ${String(err)}`);
    } finally {
      launchChromeBtn.disabled = false;
    }
  });
}

if (killChromeBtn) {
  killChromeBtn.addEventListener("click", async () => {
    killChromeBtn.disabled = true;
    await killChrome();
    killChromeBtn.disabled = false;
  });
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
