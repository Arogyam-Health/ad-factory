import { fetchJSON, invalidateRuns } from "./api.js";
import { state } from "./state.js";
import { appendLog } from "./ui.js";

const runsRoot = document.getElementById("runs");
const activePolls = new Map();

function resolveRunId(card) {
  return card.closest(".run")?.querySelector(".run-header strong")?.textContent?.trim() || "";
}

function setRevisionState(box, message, busy = false) {
  const status = box.querySelector(".image-comment-status");
  const button = box.querySelector(".image-comment-submit");
  const textarea = box.querySelector("textarea");
  const select = box.querySelector("select");
  if (status) status.textContent = message || "";
  if (button) button.disabled = busy;
  if (textarea) textarea.disabled = busy;
  if (select) select.disabled = busy;
}

function stopRevisionPoll(revisionId) {
  const timer = activePolls.get(revisionId);
  if (timer) window.clearInterval(timer);
  activePolls.delete(revisionId);
}

async function pollRevision(runId, revisionId, box) {
  try {
    const data = await fetchJSON(`/api/runs/${runId}/revisions/${revisionId}?t=${Date.now()}`);
    setRevisionState(box, data.message || data.status || "", !["completed", "error"].includes(data.status));
    if (["completed", "error"].includes(data.status)) {
      stopRevisionPoll(revisionId);
      setRevisionState(box, data.message || data.status, false);
      appendLog(data.message || `Revision ${data.status}.`);
      if (data.status === "completed") {
        invalidateRuns();
        const { loadRuns } = await import("./runs.js");
        await loadRuns();
      }
    }
  } catch (error) {
    stopRevisionPoll(revisionId);
    setRevisionState(box, String(error), false);
    appendLog(`Revision status error: ${String(error)}`);
  }
}

async function submitRevision(card, box) {
  const runId = resolveRunId(card);
  const imageFile = card.dataset.path || "";
  const textarea = box.querySelector("textarea");
  const engine = box.querySelector("select")?.value || "chatgpt";
  const comment = textarea?.value?.trim() || "";
  if (!runId || !imageFile) {
    appendLog("Could not resolve the run or image for this comment.");
    return;
  }
  if (!comment) {
    setRevisionState(box, "Describe what should change.", false);
    textarea?.focus();
    return;
  }
  setRevisionState(box, "Queuing revision...", true);
  try {
    const data = await fetchJSON(`/api/runs/${runId}/revise-image`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image_file: imageFile,
        comment,
        engine,
        headless: state.headlessModeEnabled,
      }),
    });
    appendLog(`Image revision queued with ${engine === "chatgpt" ? "ChatGPT" : "Gemini"}: ${imageFile.split("/").pop()}`);
    const revisionId = data.revision_id;
    const timer = window.setInterval(() => pollRevision(runId, revisionId, box), 2000);
    activePolls.set(revisionId, timer);
    await pollRevision(runId, revisionId, box);
  } catch (error) {
    setRevisionState(box, String(error), false);
    appendLog(`Revision error: ${String(error)}`);
  }
}

function personaFromPath(path) {
  const match = String(path || "").match(/\/(?:4_5|9_16)\/([^/]+)\/generated images\//);
  return match ? match[1].replace(/_/g, " ") : "";
}

function enhanceRunHeader(runEl) {
  if (runEl.dataset.flowEnhanced === "true") return;
  const runId = runEl.querySelector(".run-header strong")?.textContent?.trim();
  const run = state.runsData.find((item) => item.run_id === runId);
  if (!run || run.flow_type !== "reference_image") return;
  runEl.dataset.flowEnhanced = "true";
  const header = runEl.querySelector(".run-header");
  const badge = document.createElement("span");
  badge.className = "reference-run-badge";
  badge.textContent = "Reference Image Flow";
  header?.insertBefore(badge, header.querySelector(".run-delete-btn"));
}

function enhanceCard(card) {
  if (card.dataset.commentEnhanced === "true") return;
  if ((card.dataset.path || "").includes("/to_be_regenerated/")) return;
  card.dataset.commentEnhanced = "true";

  const persona = personaFromPath(card.dataset.path);
  if (persona) {
    const badge = document.createElement("span");
    badge.className = "reference-persona-badge";
    badge.textContent = persona;
    card.appendChild(badge);
  }

  const box = document.createElement("details");
  box.className = "image-comment-box";
  box.innerHTML = `
    <summary>Comment & revise</summary>
    <div class="image-comment-body">
      <textarea rows="4" maxlength="8000" placeholder="Tell the model exactly what to change, remove, add, emphasize, or preserve."></textarea>
      <div class="image-comment-controls">
        <select aria-label="Revision engine">
          <option value="chatgpt">ChatGPT</option>
          <option value="gemini">Gemini</option>
        </select>
        <button type="button" class="ghost-btn image-comment-submit">Generate revision</button>
      </div>
      <small class="image-comment-status"></small>
    </div>
  `;
  box.addEventListener("click", (event) => event.stopPropagation());
  box.querySelector(".image-comment-submit")?.addEventListener("click", () => submitRevision(card, box));
  card.appendChild(box);
}

export async function submitAllRevisions(runId) {
  const cards = runsRoot?.querySelectorAll(".image-card[data-path]") || [];
  const revisions = [];
  for (const card of cards) {
    if (resolveRunId(card) !== runId) continue;
    const box = card.querySelector(".image-comment-box");
    const textarea = box?.querySelector("textarea");
    if (!textarea?.value?.trim()) continue;
    revisions.push({ card, box, comment: textarea.value.trim(), engine: box.querySelector("select")?.value || "gemini" });
  }
  if (!revisions.length) {
    appendLog("No commented images found.");
    return;
  }
  appendLog(`Submitting ${revisions.length} revision(s)...`);
  let completed = 0;
  let failed = 0;
  for (const { card, box, comment, engine } of revisions) {
    try {
      setRevisionState(box, "Queuing...", true);
      const imageFile = card.dataset.path || "";
      const data = await fetchJSON(`/api/runs/${runId}/revise-image`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image_file: imageFile, comment, engine, headless: state.headlessModeEnabled }),
      });
      appendLog(`Revision queued for: ${imageFile.split("/").pop()}`);
      const revisionId = data.revision_id;
      const timer = window.setInterval(() => pollRevision(runId, revisionId, box), 2000);
      activePolls.set(revisionId, timer);
      await pollRevision(runId, revisionId, box);
      completed++;
    } catch (error) {
      failed++;
      setRevisionState(box, String(error), false);
      appendLog(`Revision error for ${card.dataset.path?.split("/").pop()}: ${String(error)}`);
    }
  }
  if (completed) {
    invalidateRuns();
    const { loadRuns } = await import("./runs.js");
    await loadRuns();
  }
  appendLog(`Revisions done: ${completed} succeeded, ${failed} failed.`);
}

function scanCards() {
  runsRoot?.querySelectorAll(".run").forEach(enhanceRunHeader);
  runsRoot?.querySelectorAll(".image-card[data-path]").forEach(enhanceCard);
}

if (runsRoot) {
  new MutationObserver(scanCards).observe(runsRoot, { childList: true, subtree: true });
  scanCards();
}
