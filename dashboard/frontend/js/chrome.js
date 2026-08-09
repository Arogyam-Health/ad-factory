import { appendLog } from "./ui.js";

export async function killChrome() {
  appendLog("Browser processes are owned by the paired local agent. Cancel the active agent job instead.");
}

let currentPollingInterval = null;

export function startProgressPolling() {
  stopProgressPolling();
}

export function stopProgressPolling() {
  if (currentPollingInterval) {
    clearInterval(currentPollingInterval);
    currentPollingInterval = null;
  }
}
