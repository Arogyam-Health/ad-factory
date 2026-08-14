import { state } from "./state.js";
import { appendLog, skeletonLocalSection, skeletonRunCard } from "./ui.js";
import { fetchJSON, invalidateRuns } from "./api.js";
import { buildImageGallery, showPromptFullscreen } from "./images.js";
import { buildPromptEditor } from "./prompts.js";
import { refreshSelect } from "./custom-select.js";
import { getAuthUser } from "./auth.js";
import { localDataPlane } from "./local-data-plane.js";

const runsEl = document.getElementById("runs");
const runPrevEl = document.getElementById("runPrev");
const runNextEl = document.getElementById("runNext");
const runIndexEl = document.getElementById("runIndex");

let batchDropdownInitialized = false;
let runRenderVersion = 0;

function currentFlowMode() {
  try {
    return localStorage.getItem("adFactoryFlowMode") === "reference" ? "reference" : "structured";
  } catch {
    return "structured";
  }
}

function runMatchesFlow(run, mode = currentFlowMode()) {
  const flow = String(run?.flow_type || "");
  const isReference = flow === "reference" || flow === "reference_image";
  return mode === "reference" ? isReference : !isReference;
}

function parsePromptPath(path) {
  const name = path.split("/").pop() || path;
  // Canonical: <FMT>_P<NN>_<LANG>_<angle>[_A<NN>].txt  (angle required)
  // Legacy:    <FMT>_P<NN>_<LANG>[_A<NN>][_<angle>].txt  (angle optional)
  // Both also accept optional OUTPUT_ / FINAL_ prefixes.
  const canonical = name.match(
    /^(?:(?:OUTPUT|FINAL)_)?([A-Z0-9]+)_P(\d+)_([A-Z0-9]+)_([a-z][a-z_]*?)(?:_A(\d+))?\.txt$/i
  );
  const legacy = !canonical && name.match(
    /^(?:(?:OUTPUT|FINAL)_)?([A-Z0-9]+)_P(\d+)_([A-Z0-9]+)(?:_A(\d+))?(?:_([a-z_]+))?\.txt$/i
  );
  const match = canonical || legacy;
  const angle = canonical ? canonical[4] : (legacy && legacy[5] ? legacy[5] : null);
  const creative = canonical ? canonical[5] : (legacy ? legacy[4] : null);
  const aspect = path.includes("/916/") || path.includes("/96/") ? "9:16" : path.includes("/45/") ? "4:5" : "Other";
  return {
    name,
    aspect,
    format: match ? match[1].toUpperCase() : "PROMPT",
    persona: match ? `P${String(Number(match[2])).padStart(2, "0")}` : "P--",
    lang: match ? match[3].toUpperCase() : "--",
    creative: creative ? `A${String(Number(creative)).padStart(2, "0")}` : "A01",
    conceptAngle: angle,
  };
}

function buildPromptFileSummary(runId, promptFiles, promptsData) {
  const promptDocsByPath = new Map();
  if (Array.isArray(promptsData)) {
    promptsData.forEach((doc) => {
      if (doc?.file_path) promptDocsByPath.set(doc.file_path, doc);
    });
  }
  const wrap = document.createElement("div");
  wrap.className = "run-prompt-files";

  const header = document.createElement("div");
  header.className = "run-prompt-files-header";
  const byAspect = promptFiles.reduce((acc, path) => {
    const parsed = parsePromptPath(path);
    acc[parsed.aspect] = (acc[parsed.aspect] || 0) + 1;
    return acc;
  }, {});
  const parts = Object.entries(byAspect).map(([aspect, count]) => `${aspect}: ${count}`).join(" · ");
  header.innerHTML = `<strong>Prompt files</strong><span>${promptFiles.length} total${parts ? ` · ${parts}` : ""}</span>`;
  wrap.appendChild(header);

  const grid = document.createElement("div");
  grid.className = "prompt-file-grid";
  grid.style.display = "none";

  const frag = document.createDocumentFragment();
  for (let i = 0; i < promptFiles.length; i++) {
    const path = promptFiles[i];
    const promptDoc = promptDocsByPath.get(path);
    const parsed = parsePromptPath(path);
    const card = document.createElement("div");
    card.className = "prompt-file-card";
    card.title = path;
    card.innerHTML = `<span class="prompt-file-aspect">${parsed.aspect}</span><strong>${parsed.format} ${parsed.persona}</strong><span>${parsed.creative} · ${parsed.lang}${parsed.conceptAngle ? ` · <em>${parsed.conceptAngle}</em>` : ''}</span>`;
    card.addEventListener("click", () => {
      if (!promptDoc?.prompt_id) {
        appendLog(`Prompt metadata is not available for ${path}. Refresh runs after regeneration.`);
        return;
      }
      const opts = {
        fetchUrl: `/api/runs/${encodeURIComponent(runId)}/prompts/${encodeURIComponent(promptDoc.prompt_id)}/content`,
        saveUrl: `/api/runs/${encodeURIComponent(runId)}/prompts/${encodeURIComponent(promptDoc.prompt_id)}/content`,
        saveBody: (text) => ({ content: text }),
      };
      showPromptFullscreen(Path(path).name || path, "", opts);
    });
    frag.appendChild(card);
  }
  grid.appendChild(frag);
  wrap.appendChild(grid);

  let isOpen = false;
  header.addEventListener("click", () => {
    isOpen = !isOpen;
    grid.style.display = isOpen ? "" : "none";
    header.classList.toggle("open", isOpen);
  });

  return wrap;
}

function Path(p) { return { name: p.split("/").pop() || p }; }

async function fetchImagesData(runId) {
  try {
    const data = await fetchJSON(`/api/runs/${runId}/images`);
    return Array.isArray(data?.images) ? data.images : [];
  } catch {
    return [];
  }
}

async function fetchPromptsData(runId) {
  try {
    const data = await fetchJSON(`/api/runs/${runId}/prompts`);
    return Array.isArray(data?.prompts) ? data.prompts : [];
  } catch {
    return [];
  }
}

export async function renderRun(run) {
  const div = document.createElement("div");
  div.className = "run run-active";

  const header = document.createElement("div");
  header.className = "run-header";
  header.innerHTML = `<strong>${run.run_id}</strong><span class="run-meta">batch ${displayBatch(run)} &middot; prompts ${run.prompt_count} &middot; images ${run.image_count}</span><button class="ghost-btn run-delete-btn" type="button" title="Delete this entire run">Delete</button>`;
  if (run.status === "purge_failed" || run.status === "deleting") {
    const status = document.createElement("span");
    status.className = `run-status-${run.status}`;
    status.textContent = run.status === "purge_failed" ? "Purge failed" : "Deleting";
    header.insertBefore(status, header.querySelector(".run-delete-btn"));
    if (run.status === "purge_failed") {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "ghost-btn run-retry-purge-btn";
      retry.textContent = "Retry purge";
      header.insertBefore(retry, header.querySelector(".run-delete-btn"));
      retry.addEventListener("click", async (e) => {
        e.stopPropagation();
        retry.disabled = true;
        try {
          await fetchJSON(`/api/runs/${run.run_id}`, {
            method: "DELETE",
            headers: { "Idempotency-Key": `delete-${run.run_id}-${Date.now()}` },
          });
          appendLog(`Retrying local purge for ${run.run_id}`);
          invalidateRuns();
          const { loadRuns } = await import("./runs.js");
          loadRuns();
        } catch (err) {
          appendLog(`Retry purge failed: ${String(err)}`);
          retry.disabled = false;
        }
      });
    }
  }
  if (run.device_id && run.local_device_status === "unavailable") {
    const unavailable = document.createElement("span");
    unavailable.className = "run-device-unavailable";
    unavailable.textContent = "Authoritative device unavailable";
    header.insertBefore(unavailable, header.querySelector(".run-delete-btn"));
  }
  div.appendChild(header);

  header.querySelector(".run-delete-btn")?.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (!confirm(`Delete entire run ${run.run_id} and all its images?`)) return;
    try {
      await fetchJSON(`/api/runs/${run.run_id}`, {
        method: "DELETE",
        headers: { "Idempotency-Key": `delete-${run.run_id}` },
      });
      appendLog(`Run ${run.run_id} queued for local purge`);
      invalidateRuns();
      const { loadRuns } = await import("./runs.js");
      loadRuns();
    } catch (err) {
      appendLog(`Delete failed: ${String(err)}`);
    }
  });

  const llm = document.createElement("div");
  llm.className = "run-updated";
  llm.textContent = `Updated: ${run.updated_at || "-"}`;
  div.appendChild(llm);

  const [imagesData, promptsData] = await Promise.all([
    fetchImagesData(run.run_id),
    fetchPromptsData(run.run_id),
  ]);

  // Until the first local listing resolves, an empty prompt or image section is
  // indistinguishable from a run that genuinely has no content.
  const awaitingLocalContent = Boolean(run.device_id) && !structuredOutputsLoaded;
  if (awaitingLocalContent) {
    div.appendChild(skeletonLocalSection("Loading prompts from this machine", 3));
    div.appendChild(skeletonLocalSection("Loading generated images from this machine", 6));
    return div;
  }

  const promptActions = document.createElement("div");
  promptActions.className = "prompt-actions";
  buildPromptEditor(run, promptActions, promptsData);
  div.appendChild(promptActions);

  const galleryContainer = document.createElement("div");
  div.appendChild(galleryContainer);

  let galleryBuilt = false;
  const observer = new IntersectionObserver((entries) => {
    if (entries[0].isIntersecting && !galleryBuilt) {
      galleryBuilt = true;
      observer.disconnect();
      const gallery = buildImageGallery(run, imagesData);
      if (gallery) galleryContainer.appendChild(gallery);
    }
  }, { rootMargin: "400px" });
  observer.observe(galleryContainer);

  return div;
}

function updateRunNav() {
  const total = state.runsData.length;
  const latestBatch = total ? displayBatch(state.runsData[0]) : "-";
  if (runIndexEl) {
    const position = total ? `${state.currentRunIndex + 1}/${total}` : "0/0";
    runIndexEl.textContent = `${position} | latest batch ${latestBatch}`;
  }
  if (runPrevEl) runPrevEl.disabled = total <= 1;
  if (runNextEl) runNextEl.disabled = total <= 1;
}

export async function renderRunCarousel() {
  if (!runsEl) return;
  const renderVersion = ++runRenderVersion;
  runsEl.innerHTML = "";
  if (!state.runsData.length) {
    const empty = document.createElement("div");
    empty.className = "hint empty-runs";
    empty.textContent = "No runs yet.";
    runsEl.appendChild(empty);
    updateRunNav();
    return;
  }
  if (state.currentRunIndex < 0) state.currentRunIndex = 0;
  if (state.currentRunIndex >= state.runsData.length) state.currentRunIndex = state.runsData.length - 1;
  const runEl = await renderRun(state.runsData[state.currentRunIndex]);
  if (renderVersion !== runRenderVersion) return;
  runsEl.replaceChildren(runEl);
  updateRunNav();
}

export function showRunsSkeletons(count = 2) {
  if (!runsEl) return;
  runsEl.innerHTML = "";
  const frag = document.createDocumentFragment();
  for (let i = 0; i < count; i++) frag.appendChild(skeletonRunCard());
  runsEl.appendChild(frag);
}

function getSelectedBatchValues() {
  return Array.from(document.querySelectorAll(".batch-check:checked")).map((c) => c.value);
}

function updateBatchDropdownButtonLabel() {
  const btn = document.getElementById("batchDropdownBtn");
  if (!btn) return;
  const count = getSelectedBatchValues().length;
  btn.textContent = count ? `${count} batch(es) selected` : "Select batch(es)";
}

function updatePreviousRunOptions() {
  [
    ["backgroundReuseRun", "Select background source run"],
    ["visualPatternReuseRun", "Select visual-pattern source run"],
  ].forEach(([selectId, placeholder]) => {
    const select = document.getElementById(selectId);
    if (!select) return;
    const previous = select.value;
    select.innerHTML = "";
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = placeholder;
    select.appendChild(empty);
    state.runsData.forEach((run) => {
      const label = displayBatch(run) || run.run_id;
      const opt = document.createElement("option");
      opt.value = run.run_id;
      opt.textContent = label;
      select.appendChild(opt);
    });
    if (previous && Array.from(select.options).some((opt) => opt.value === previous)) {
      select.value = previous;
    }
    refreshSelect(select);
  });
}

async function reconcileRunInventory(runs) {
  const user = getAuthUser();
  if (!user?.user_id) return { removed: 0, pending: [], pruned: 0 };
  const owners = new Map([
    ["user:" + user.user_id, { ownerType: "user", ownerId: user.user_id }],
  ]);
  for (const run of runs) {
    const ownerType = run.owner_type || "user";
    const ownerId = run.owner_id || user.user_id;
    if (ownerType === "org" && ownerId) {
      owners.set(`org:${ownerId}`, { ownerType, ownerId });
    }
  }
  const mongoIds = new Set(runs.map((run) => run.run_id));
  const recentCutoff = Date.now() / 1000 - 120;
  const pending = [];
  let pruned = 0;
  for (const owner of owners.values()) {
    try {
      const paired = await localDataPlane.ensurePaired(owner);
      const localRuns = await localDataPlane.listRuns(paired.info.device_id);
      const result = await fetchJSON("/api/runs/reconcile-local", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          agent_id: paired.agent.agent_id,
          device_id: paired.info.device_id,
          owner_type: owner.ownerType,
          owner_id: owner.ownerId,
          local_run_ids: localRuns.map((run) => run.run_id),
        }),
      });
      (result.pending || result.run_ids || []).forEach((runId) => {
        pending.push({
          run_id: runId,
          agent_id: paired.agent.agent_id,
          device_id: paired.info.device_id,
          owner_type: owner.ownerType,
          owner_id: owner.ownerId,
          local_run_ids: localRuns.map((run) => run.run_id),
        });
      });
      for (const local of localRuns) {
        if (mongoIds.has(local.run_id)) continue;
        if (Number(local.created_at || 0) >= recentCutoff) continue;
        await localDataPlane.deleteRun(local.run_id, paired.info.device_id);
        pruned += 1;
      }
    } catch {
      // Do not reconcile an owner scope unless its current local inventory is online.
    }
  }
  return { removed: 0, pending, pruned };
}

async function removeMissingRuns(pending) {
  const unique = new Map();
  pending.forEach((item) => {
    unique.set(`${item.agent_id}:${item.device_id}:${item.owner_type}:${item.owner_id}`, item);
  });
  let removed = 0;
  for (const item of unique.values()) {
    const result = await fetchJSON("/api/runs/reconcile-local", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        agent_id: item.agent_id,
        device_id: item.device_id,
        owner_type: item.owner_type,
        owner_id: item.owner_id,
        local_run_ids: item.local_run_ids,
        confirm: true,
      }),
    });
    removed += Number(result.removed || 0);
  }
  return removed;
}

function renderMissingRunsBanner() {
  const banner = document.getElementById("missingRunsBanner");
  if (!banner) return;
  const pending = state.missingLocalRuns || [];
  const uniqueIds = [...new Set(pending.map((item) => item.run_id).filter(Boolean))];
  if (!uniqueIds.length) {
    banner.classList.add("hidden");
    banner.replaceChildren();
    return;
  }
  banner.classList.remove("hidden");
  const label = document.createElement("span");
  label.textContent = `${uniqueIds.length} run${uniqueIds.length === 1 ? "" : "s"} on this account are missing from this machine.`;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "ghost-btn";
  button.textContent = `Remove ${uniqueIds.length} missing run${uniqueIds.length === 1 ? "" : "s"}`;
  button.addEventListener("click", async () => {
    if (!confirm(`Delete ${uniqueIds.length} dashboard run record${uniqueIds.length === 1 ? "" : "s"} that are not on this machine?`)) {
      return;
    }
    button.disabled = true;
    try {
      const removed = await removeMissingRuns(pending);
      appendLog(`Removed ${removed} missing run${removed === 1 ? "" : "s"} from the dashboard.`);
      invalidateRuns();
      const { loadRuns } = await import("./runs.js");
      loadRuns();
    } catch (err) {
      appendLog(`Could not remove missing runs: ${String(err)}`);
      button.disabled = false;
    }
  });
  banner.replaceChildren(label, button);
}

function batchSortValue(batch) {
  const match = String(batch || "").trim().match(/^v(\d+)$/i);
  return match ? Number(match[1]) : -1;
}

function compareBatchesLatestFirst(a, b) {
  const diff = batchSortValue(b) - batchSortValue(a);
  return diff || String(b || "").localeCompare(String(a || ""));
}

function closeBatchDropdown() {
  const menu = document.getElementById("batchDropdownMenu");
  const btn = document.getElementById("batchDropdownBtn");
  if (menu && !menu.classList.contains("hidden")) menu.classList.add("hidden");
  if (btn) btn.setAttribute("aria-expanded", "false");
}

function openBatchDropdown() {
  const menu = document.getElementById("batchDropdownMenu");
  const btn = document.getElementById("batchDropdownBtn");
  if (menu) menu.classList.remove("hidden");
  if (btn) btn.setAttribute("aria-expanded", "true");
}

export async function loadRuns() {
  if (state.isRunsLoading) return;
  state.isRunsLoading = true;
  showRunsSkeletons();
  try {
    const flow = currentFlowMode();
    let data = await fetchJSON(`/api/runs?flow=${encodeURIComponent(flow)}`);
    const inventory = await reconcileRunInventory(data.runs || []);
    if (inventory.pruned) {
      appendLog(`Removed ${inventory.pruned} local run${inventory.pruned === 1 ? "" : "s"} that no longer exist in the dashboard.`);
    }
    state.runsData = (data.runs || []).map(normalizeRun).filter((run) => runMatchesFlow(run, flow));
    state.missingLocalRuns = inventory.pending || [];
    applyLocalArtifactsToRuns();
    renderMissingRunsBanner();
    state.currentRunIndex = 0;
    updatePreviousRunOptions();

    const batchMenu = document.getElementById("batchDropdownMenu");
    batchMenu.innerHTML = "";

    const batches = new Set();
    const batchLabels = new Map();
    state.runsData.forEach((run) => {
      const key = runKey(run);
      batches.add(key);
      batchLabels.set(key, displayBatch(run));
    });

    const grid = document.createElement("div");
    grid.className = "batch-grid";
    const batchList = Array.from(batches).sort((a, b) => compareBatchesLatestFirst(batchLabels.get(a), batchLabels.get(b)));
    const num = batchList.length;
    const cols = Math.max(1, Math.ceil(Math.sqrt(num)));
    const rows = Math.max(1, Math.ceil(num / cols));
    grid.style.gridTemplateColumns = `repeat(${cols}, minmax(140px, 1fr))`;
    grid.style.gridAutoRows = "auto";
    grid.style.gridTemplateRows = `repeat(${rows}, auto)`;

    batchList.forEach((batch) => {
    const item = document.createElement("div");
    item.className = "batch-grid-item";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = batch;
    cb.className = "batch-check";
    const labelSpan = document.createElement("span");
    labelSpan.className = "batch-label";
    labelSpan.textContent = batchLabels.get(batch) || batch;
    cb.addEventListener("change", updateBatchDropdownButtonLabel);
    item.addEventListener("click", (event) => {
      if (event.target.closest("input[type='checkbox']")) return;
      cb.checked = !cb.checked;
      cb.dispatchEvent(new Event("change", { bubbles: true }));
    });
    item.append(cb, labelSpan);
    grid.appendChild(item);
    });

    batchMenu.appendChild(grid);

    if (!batchDropdownInitialized) {
      batchDropdownInitialized = true;
      const dropdownRoot = document.querySelector(".batch-dropdown");
      const btn = document.getElementById("batchDropdownBtn");
      btn?.addEventListener("click", (e) => {
        e.stopPropagation();
        const menu = document.getElementById("batchDropdownMenu");
        if (!menu) return;
        menu.classList.contains("hidden") ? openBatchDropdown() : closeBatchDropdown();
      });
      document.addEventListener("click", (e) => {
        const menu = document.getElementById("batchDropdownMenu");
        if (!menu || menu.classList.contains("hidden")) return;
        if (dropdownRoot && !dropdownRoot.contains(e.target)) closeBatchDropdown();
      });
      document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeBatchDropdown(); });
    }

    updateBatchDropdownButtonLabel();
    renderRunCarousel();
    refreshStructuredLocalOutputs().catch(() => {});
  } finally {
    state.isRunsLoading = false;
  }
}

if (runPrevEl) {
  runPrevEl.addEventListener("click", () => {
    if (!state.runsData.length) return;
    state.currentRunIndex = (state.currentRunIndex - 1 + state.runsData.length) % state.runsData.length;
    renderRunCarousel().catch(() => {});
  });
}
if (runNextEl) {
  runNextEl.addEventListener("click", () => {
    if (!state.runsData.length) return;
    state.currentRunIndex = (state.currentRunIndex + 1) % state.runsData.length;
    renderRunCarousel().catch(() => {});
  });
}

document.getElementById("refreshRuns")?.addEventListener("click", () => {
  invalidateRuns();
  loadRuns().catch(() => {});
});

const LOCAL_STORAGE_JOB_KEY = "adFactoryActiveJob";
const LEGACY_ARTIFACT_CACHE_KEY = "adFactoryLocalArtifacts";
const STRUCTURED_OUTPUT_POLL_MS = 30000;
let agentJobPollTimer = null;
let structuredLocalImages = [];
let structuredRefreshInFlight = false;
let structuredRefreshQueued = false;
let structuredOutputsLoaded = false;
const localDataEventStreams = new Map();

function purgeLegacyArtifactCache() {
  try {
    for (let index = localStorage.length - 1; index >= 0; index -= 1) {
      const key = localStorage.key(index);
      if (key && key.startsWith(LEGACY_ARTIFACT_CACHE_KEY)) localStorage.removeItem(key);
    }
  } catch {
    // A blocked storage quota must never prevent the CAS feed from loading.
  }
}

function displayBatch(run) {
  return run?.display_batch || (run?.run_number ? `v${run.run_number}` : "") || "-";
}

function runKey(run) {
  return String(run?.run_id || run?.display_batch || run?.run_number || "");
}

function normalizeRun(run) {
  return {
    ...run,
    display_batch: displayBatch(run),
    prompt_count: Number(run?.prompt_count || 0),
    image_count: Number(run?.image_count || 0),
    prompt_files: Array.isArray(run?.prompt_files) ? run.prompt_files : [],
    image_files: Array.isArray(run?.image_files) ? run.image_files : [],
    regeneration_queue_files: Array.isArray(run?.regeneration_queue_files)
      ? run.regeneration_queue_files
      : [],
  };
}

function localOutputRenderSignature(runs) {
  return JSON.stringify(runs.map((run) => [
    run.run_id,
    run.local_device_status || "",
    run.image_files || [],
    run.regeneration_queue_files || [],
  ]));
}

function scopedStorageKey(baseKey) {
  const userId = getAuthUser()?.user_id || "anonymous";
  return `${baseKey}:${userId}`;
}

function currentJobStorageKey() {
  return scopedStorageKey(LOCAL_STORAGE_JOB_KEY);
}

function appendGenerationResult(data, fallback) {
  if (data?.status === "queued_local_agent") {
    appendLog(`Queued local agent job ${data.job_id} on ${data.agent_name || data.agent_id}. Images will save on the local agent machine.`);
    localStorage.setItem(currentJobStorageKey(), JSON.stringify({
      job_id: data.job_id,
      agent_name: data.agent_name || data.agent_id,
      mode: data.mode || "both",
      timestamp: Date.now(),
    }));
    startAgentJobPolling();
    return;
  }
  appendLog(fallback(data));
}

function showAgentJobBar(text, spinning = true, job = null) {
  const bar = document.getElementById("agentJobBar");
  const status = document.getElementById("agentJobStatus");
  if (!bar || !status) return;
  bar.classList.remove("hidden");
  status.innerHTML = `${spinning ? '<span class="agent-job-spinner"></span> ' : ''}${escapeHtml(text)}`;
  let cancelBtn = bar.querySelector(".agent-job-cancel-btn");
  const canCancel = job?.job_id && ["pending", "running", "cancel_requested"].includes(job.status || "");
  if (!canCancel) {
    cancelBtn?.remove();
    return;
  }
  if (!cancelBtn) {
    cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "agent-job-cancel-btn";
    bar.appendChild(cancelBtn);
  }
  cancelBtn.textContent = job.status === "cancel_requested" ? "Canceling..." : "Cancel agent job";
  cancelBtn.disabled = job.status === "cancel_requested";
  cancelBtn.onclick = async () => {
    if (!confirm("Cancel the running local agent job? The local browser automation process will be terminated.")) return;
    cancelBtn.disabled = true;
    cancelBtn.textContent = "Canceling...";
    try {
      await fetchJSON(`/api/agents/jobs/${encodeURIComponent(job.job_id)}/cancel`, { method: "POST" });
      appendLog(`Cancel requested for local agent job ${job.job_id}.`);
      showAgentJobBar("Agent job Canceling: cancel requested", false, { ...job, status: "cancel_requested" });
    } catch (err) {
      appendLog(`Cancel failed: ${String(err)}`);
      cancelBtn.disabled = false;
      cancelBtn.textContent = "Cancel agent job";
    }
  };
}

export function applyLocalArtifactsToRuns() {
  if (!state.runsData.length) return;
  const validLocalUrls = new Set(structuredLocalImages.map((image) => image.url));
  state.runsData.forEach((run) => {
    if (!Array.isArray(run.image_files)) run.image_files = [];
    run.image_files = run.image_files.filter((path) => {
      const value = String(path);
      const isLocal = value.startsWith("blob:") || value.startsWith("http://127.0.0.1:");
      return !isLocal || validLocalUrls.has(value);
    });
  });
  structuredLocalImages.forEach((image) => {
    const explicitRunIds = Array.isArray(image.run_ids) ? image.run_ids : (image.run_id ? [image.run_id] : []);
    const targets = state.runsData.filter((run) => explicitRunIds.includes(run.run_id));
    targets.forEach((run) => {
      const archived = image.status === "archived";
      const filesKey = archived ? "regeneration_queue_files" : "image_files";
      const itemsKey = archived ? "regeneration_queue_items" : "image_items";
      if (!Array.isArray(run[filesKey])) run[filesKey] = [];
      if (!run[filesKey].includes(image.url)) run[filesKey].push(image.url);
      if (!Array.isArray(run[itemsKey])) run[itemsKey] = [];
      const existingItem = run[itemsKey].find((item) => item.path === image.url);
      const localItem = {
        path: image.url,
        artifact_id: image.output_id || "",
        output_id: image.output_id || "",
        output_version: image.output_version || 0,
        prompt_id: image.prompt_id || "",
        prompt_file: image.prompt_file || "",
        display_name: image.display_name || "",
        aspect_ratio: image.aspect_ratio || "",
        is_local: true,
        is_queued: archived,
      };
      if (existingItem) Object.assign(existingItem, localItem);
      else run[itemsKey].push(localItem);
      run.image_generated = true;
    });
  });
}

const promptNameCache = new Map();

async function promptNamesForRun(run, promptIds) {
  let cached = promptNameCache.get(run.run_id);
  const missing = promptIds.filter((id) => id && !(cached && cached.has(id)));
  if (!cached || missing.length) {
    const items = await localDataPlane.listPrompts(run.run_id, run.device_id);
    cached = new Map();
    items.forEach((item) => {
      if (!item?.prompt_id) return;
      cached.set(item.prompt_id, {
        display_name: item.display_name || item.prompt_id,
        prompt_file: item.prompt_file || `${item.display_name || item.prompt_id}.txt`,
      });
    });
    promptNameCache.set(run.run_id, cached);
  }
  return cached;
}

function outputDisplayName(output, promptNames) {
  const stem = output.display_name || (() => {
    const mapped = promptNames.get(output.prompt_id);
    if (!mapped) return "";
    const suffix = String(output.aspect_ratio || "").replace(":", "_");
    return suffix ? `${mapped.display_name}_${suffix}` : mapped.display_name;
  })();
  return stem ? `${stem}.png` : "";
}

export async function refreshStructuredLocalOutputs() {
  if (structuredRefreshInFlight) {
    structuredRefreshQueued = true;
    return;
  }
  structuredRefreshInFlight = true;
  try {
    await runStructuredOutputRefresh();
    while (structuredRefreshQueued) {
      structuredRefreshQueued = false;
      await runStructuredOutputRefresh();
    }
  } finally {
    structuredRefreshInFlight = false;
  }
}

async function runStructuredOutputRefresh() {
  const user = getAuthUser();
  if (!user?.user_id) return;
  const beforeRenderSignature = localOutputRenderSignature(state.runsData);
  const activeDeviceIds = new Set(
    state.runsData.map((run) => run?.device_id).filter(Boolean),
  );
  for (const [deviceId, stream] of localDataEventStreams) {
    if (!activeDeviceIds.has(deviceId)) {
      stream.controller.abort();
      localDataEventStreams.delete(deviceId);
    }
  }
  const previous = new Map(
    structuredLocalImages.map((image) => [
      `${image.output_id}:${image.output_version}`,
      image,
    ]),
  );
  const previousByRun = new Map();
  structuredLocalImages.forEach((image) => {
    const bucket = previousByRun.get(image.run_id) || [];
    bucket.push(image);
    previousByRun.set(image.run_id, bucket);
  });
  const next = [];
  for (const run of state.runsData) {
    if (!run?.run_id || !run?.device_id || !run?.agent_id) continue;
    try {
      await localDataPlane.ensurePaired({
        ownerType: run.owner_type || "user",
        ownerId: run.owner_id || user.user_id,
        deviceId: run.device_id,
        agentId: run.agent_id,
      });
      if (!localDataEventStreams.has(run.device_id)) {
        const controller = new AbortController();
        const stream = { controller, cursor: 0 };
        localDataEventStreams.set(run.device_id, stream);
        localDataPlane.streamEvents({
          after: stream.cursor,
          deviceId: run.device_id,
          signal: controller.signal,
          onEvent: async (event) => {
            stream.cursor = Math.max(stream.cursor, Number(event.sequence) || 0);
            await refreshStructuredLocalOutputs();
          },
        }).catch(() => {
          // The authenticated stream reconnects internally; polling remains available.
        }).finally(() => {
          if (localDataEventStreams.get(run.device_id) === stream) {
            localDataEventStreams.delete(run.device_id);
          }
        });
      }
      const outputs = await localDataPlane.listOutputs(run.run_id, run.device_id);
      const promptNames = await promptNamesForRun(
        run,
        outputs.map((output) => output.prompt_id),
      );
      // Only a completed listing may replace this run's content; a partial or failed
      // one must leave the previous object URLs attached to their live DOM nodes.
      run.local_device_status = "online";
      run.image_files = [];
      run.image_items = [];
      run.regeneration_queue_files = [];
      run.regeneration_queue_items = [];
      for (const output of outputs) {
        const key = `${output.output_id}:${output.current_version}`;
        const cached = previous.get(key);
        const url = cached?.url || await localDataPlane.outputObjectUrl(
          output.output_id,
          run.device_id,
        );
        const mapped = promptNames.get(output.prompt_id);
        next.push({
          output_id: output.output_id,
          output_version: output.current_version,
          run_id: run.run_id,
          run_ids: [run.run_id],
          prompt_id: output.prompt_id,
          prompt_file: mapped?.prompt_file || "",
          display_name: outputDisplayName(output, promptNames),
          item_id: output.item_id,
          aspect_ratio: output.aspect_ratio,
          status: output.status,
          url,
        });
      }
    } catch {
      run.local_device_status = "unavailable";
      next.push(...(previousByRun.get(run.run_id) || []));
    }
  }
  const retained = new Set(next.map((image) => image.url));
  structuredLocalImages.forEach((image) => {
    if (!retained.has(image.url) && String(image.url).startsWith("blob:")) {
      URL.revokeObjectURL(image.url);
    }
  });
  structuredLocalImages = next;
  structuredOutputsLoaded = true;
  applyLocalArtifactsToRuns();
  const afterRenderSignature = localOutputRenderSignature(state.runsData);
  if (
    state.runsData.length
    && beforeRenderSignature !== afterRenderSignature
  ) {
    renderRunCarousel().catch(() => {});
  }
}

export function structuredOutputsReady() {
  return structuredOutputsLoaded;
}

export function forgetLocalPromptNames(runId) {
  if (runId) promptNameCache.delete(runId);
  else promptNameCache.clear();
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
}

function selectedOrCurrentRuns() {
  const selectedBatches = getSelectedBatchValues();
  if (selectedBatches.length) {
    return state.runsData.filter((run) => selectedBatches.includes(runKey(run)));
  }
  const current = state.runsData[state.currentRunIndex];
  return current?.run_id ? [current] : [];
}

function hideAgentJobBar() {
  const bar = document.getElementById("agentJobBar");
  if (bar) bar.classList.add("hidden");
  localStorage.removeItem(currentJobStorageKey());
  if (agentJobPollTimer) { clearInterval(agentJobPollTimer); agentJobPollTimer = null; }
}

function startAgentJobPolling() {
  if (agentJobPollTimer) return;
  showAgentJobBar("Agent job in progress...");
  agentJobPollTimer = setInterval(async () => {
    try {
      const saved = JSON.parse(localStorage.getItem(currentJobStorageKey()) || "null");
      const query = saved?.job_id ? `?job_id=${encodeURIComponent(saved.job_id)}` : "";
      const data = await fetchJSON(`/api/batch/job-status${query}`, { cache: "no-store" });
      if (!data || !data.job) {
        appendLog("No recent agent job found.");
        hideAgentJobBar();
        loadRuns();
        return;
      }
      const job = data.job || {};
      if (!data.active) {
        if (job.status === "completed") {
          showAgentJobBar("Agent job completed. Refreshing local results.", false);
          await refreshStructuredLocalOutputs();
          localStorage.removeItem(currentJobStorageKey());
          if (agentJobPollTimer) { clearInterval(agentJobPollTimer); agentJobPollTimer = null; }
          return;
        }
        if (job.status === "canceled") {
          appendLog(`Agent job canceled: ${job.error_message || job.error_code || "canceled"}`);
          hideAgentJobBar();
          loadRuns();
          return;
        }
        showAgentJobBar(`Agent job failed: ${job.error_message || job.error_code || "unknown error"}`, false, job);
        localStorage.removeItem(currentJobStorageKey());
        if (agentJobPollTimer) { clearInterval(agentJobPollTimer); agentJobPollTimer = null; }
        return;
      }
      const progress = job.progress_code || "";
      const status = job.status || "pending";
      const label = status === "cancel_requested" ? "Canceling" : (status === "running" ? "Running" : "Queued");
      const msg = `Agent job ${label}: ${progress || "waiting for pickup..."}`;
      showAgentJobBar(msg, status !== "cancel_requested", job);
      await refreshStructuredLocalOutputs();
    } catch (err) {
      // Keep polling: a transient Render error must not freeze a stale Running/Canceling banner.
    }
  }, 5000);
}

function checkActiveAgentJob() {
  const raw = localStorage.getItem(currentJobStorageKey());
  if (!raw) return;
  try {
    const saved = JSON.parse(raw);
    if (!saved || !saved.job_id || (Date.now() - saved.timestamp > 7200000)) {
      localStorage.removeItem(currentJobStorageKey());
      return;
    }
    startAgentJobPolling();
  } catch {
    localStorage.removeItem(currentJobStorageKey());
  }
}

purgeLegacyArtifactCache();
refreshStructuredLocalOutputs().catch(() => {});
setInterval(() => refreshStructuredLocalOutputs().catch(() => {}), STRUCTURED_OUTPUT_POLL_MS);
checkActiveAgentJob();
fetchJSON("/api/batch/job-status", { cache: "no-store" }).then((data) => {
  if (data?.active && data.job?.job_id) {
    localStorage.setItem(currentJobStorageKey(), JSON.stringify({ job_id: data.job.job_id, timestamp: Date.now() }));
    startAgentJobPolling();
    return;
  }
  if (data?.job && !data.active && data.job.status === "completed") {
    refreshStructuredLocalOutputs().catch(() => {});
    showAgentJobBar("Last local agent job completed.", false);
  }
}).catch(() => {});

async function queueStructuredImages(runs, engine, mode) {
  const queued = await Promise.all(runs.map((run) => fetchJSON(
    `/api/runs/${encodeURIComponent(run.run_id)}/image-generation`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        operation_id: `images-${run.run_id}-${engine}-${mode}-${Date.now()}`,
        engine,
        mode,
      }),
    },
  )));
  const latest = queued.at(-1);
  if (latest?.job_id) {
    localStorage.setItem(currentJobStorageKey(), JSON.stringify({
      job_id: latest.job_id,
      timestamp: Date.now(),
    }));
    startAgentJobPolling();
  }
  appendLog(`Queued ${queued.length} local image-generation job(s).`);
  return queued;
}

document.getElementById("batchGen45")?.addEventListener("click", async () => {
  const runsForBatches = selectedOrCurrentRuns();
  if (!runsForBatches.length) { appendLog("Select a batch or open a run with prompt files first."); return; }

  const engine = await showEngineSelector("4:5");
  if (!engine) return;

  const batchLabel = runsForBatches.map(displayBatch).filter(Boolean).join(", ");
  const engineLabel = engine === "chatgpt" ? "ChatGPT" : "Gemini";
  appendLog(`Generating 4:5 in ${engineLabel} for ${batchLabel || "current run"} (${runsForBatches.length} run(s))...`);
  showStopGenButton();
  try {
    await queueStructuredImages(runsForBatches, engine, "45");
    loadRuns();
  } catch (err) {
    appendLog(String(err));
  } finally {
    hideStopGenButton();
  }
});

document.getElementById("batchGenBoth")?.addEventListener("click", async () => {
  const runsForBatches = selectedOrCurrentRuns();
  if (!runsForBatches.length) { appendLog("Select a batch or open a run with prompt files first."); return; }

  const engine = await showEngineSelector("4:5 & 9:16");
  if (!engine) return;

  const batchLabel = runsForBatches.map(displayBatch).filter(Boolean).join(", ");
  const engineLabel = engine === "chatgpt" ? "ChatGPT" : "Gemini";
  appendLog(`Generating 4:5 + 9:16 in ${engineLabel} for ${batchLabel || "current run"} (${runsForBatches.length} run(s))...`);
  showStopGenButton();
  try {
    await queueStructuredImages(runsForBatches, engine, "both");
    loadRuns();
  } catch (err) {
    appendLog(String(err));
  } finally {
    hideStopGenButton();
  }
});

document.getElementById("batchGen916")?.addEventListener("click", async () => {
  const runsForBatches = selectedOrCurrentRuns();
  if (!runsForBatches.length) { appendLog("Select a batch or open a run with prompt files first."); return; }
  const engine = await showEngineSelector("9:16");
  if (!engine) return;

  const batchLabel = runsForBatches.map(displayBatch).filter(Boolean).join(", ");
  const engineLabel = engine === "chatgpt" ? "ChatGPT" : "Gemini";
  appendLog(`Generating 9:16 in ${engineLabel} for ${batchLabel || "current run"} (${runsForBatches.length} run(s))...`);
  showStopGenButton();
  try {
    await queueStructuredImages(runsForBatches, engine, "916");
    loadRuns();
  } catch (err) {
    appendLog(String(err));
  } finally {
    hideStopGenButton();
  }
});

document.getElementById("batchDownload")?.addEventListener("click", async () => {
  const selectedBatches = getSelectedBatchValues();
  if (!selectedBatches.length) { appendLog("Select at least one batch from the dropdown."); return; }
  appendLog(`Preparing download for ${selectedBatches.length} batch(es)...`);
  try {
    const selectedRuns = state.runsData.filter((run) => selectedBatches.includes(runKey(run)));
    const user = getAuthUser();
    for (const run of selectedRuns) {
      await localDataPlane.ensurePaired({
        ownerType: run.owner_type || "user",
        ownerId: run.owner_id || user.user_id,
        deviceId: run.device_id,
        agentId: run.agent_id,
      });
      const blob = await localDataPlane.downloadRun(run.run_id, run.device_id);
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = `${displayBatch(run)}-${run.run_id}.zip`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(objectUrl);
    }
    appendLog(`Downloaded ${selectedRuns.length} local run archive(s).`);
  } catch (err) {
    appendLog(`Download error: ${String(err)}`);
  }
});

async function purgeRunsLocally(runs) {
  const user = getAuthUser();
  let purged = 0;
  for (const run of runs) {
    try {
      const paired = await localDataPlane.ensurePaired({
        ownerType: run.owner_type || "user",
        ownerId: run.owner_id || user?.user_id,
        deviceId: run.device_id,
        agentId: run.agent_id,
      });
      await localDataPlane.deleteRun(run.run_id, paired.info.device_id);
      purged += 1;
    } catch {
      // A run with no reachable local device has nothing left to reclaim here.
    }
  }
  return purged;
}

document.getElementById("batchDelete")?.addEventListener("click", async () => {
  const selectedBatches = getSelectedBatchValues();
  if (!selectedBatches.length) { appendLog("Select at least one batch from the dropdown."); return; }
  const selectedRuns = state.runsData.filter((run) => selectedBatches.includes(runKey(run)));
  if (!selectedRuns.length) { appendLog("No matching runs to delete."); return; }
  const labels = selectedRuns.map((run) => displayBatch(run) || run.run_id).join(", ");
  if (!confirm(`Delete ${selectedRuns.length} run(s) (${labels}) and every prompt and image stored for them? This cannot be undone.`)) return;
  appendLog(`Deleting ${selectedRuns.length} run(s)...`);
  try {
    const result = await fetchJSON("/api/runs/bulk-delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_ids: selectedRuns.map((run) => run.run_id) }),
    });
    const purged = await purgeRunsLocally(selectedRuns);
    appendLog(
      `Deleted ${result.deleted} run(s), queued ${result.deleting} local purge(s), `
      + `${result.failed} failed. Cleared ${purged} local run(s).`,
    );
    (result.results || [])
      .filter((item) => item.status === "error")
      .forEach((item) => appendLog(`Delete failed for ${item.run_id}: ${item.detail}`));
    invalidateRuns();
    loadRuns();
  } catch (err) {
    appendLog(`Delete failed: ${String(err)}`);
  }
});

document.getElementById("purgeAllRuns")?.addEventListener("click", async () => {
  const typed = prompt(
    "This deletes every run owned by this account, including all prompts and images on this device.\n\nType PURGE to confirm:",
  );
  if (typed === null || typed.trim().toUpperCase() !== "PURGE") return;
  const runs = [...state.runsData];
  appendLog("Purging every run for this account...");
  try {
    const result = await fetchJSON("/api/runs/purge-all", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: "PURGE" }),
    });
    const purged = await purgeRunsLocally(runs);
    appendLog(
      `Purged ${result.runs} run(s), ${result.prompts} prompt(s), ${result.images} image(s), `
      + `${result.llm_traces} trace(s). Cleared ${purged} local run(s).`,
    );
    invalidateRuns();
    loadRuns();
  } catch (err) {
    appendLog(`Purge failed: ${String(err)}`);
  }
});

async function downloadZipResponse(response, fallbackName) {
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const disposition = response.headers.get("Content-Disposition") || "";
  const match = disposition.match(/filename="?(.+?)"?$/);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = match ? match[1] : fallbackName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

function showEngineSelector(aspectLabel = "4:5") {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "engine-selector-overlay";
    overlay.innerHTML = `
      <div class="engine-selector-modal">
        <h3>Select Image Generation Engine</h3>
        <p>Choose which engine to use for generating ${aspectLabel} images:</p>
        <div class="engine-options">
          <button class="engine-option-btn" data-engine="gemini">
            <span class="engine-name">Gemini</span>
            <span class="engine-desc">Google Gemini image generation</span>
          </button>
          <button class="engine-option-btn" data-engine="chatgpt">
            <span class="engine-name">ChatGPT</span>
            <span class="engine-desc">OpenAI ChatGPT image generation</span>
          </button>
        </div>
        <button class="engine-cancel-btn">Cancel</button>
      </div>
    `;

    document.body.appendChild(overlay);

    const cleanup = () => overlay.remove();

    overlay.querySelector(".engine-cancel-btn").onclick = () => {
      cleanup();
      resolve(null);
    };

    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) {
        cleanup();
        resolve(null);
      }
    });

    overlay.querySelectorAll(".engine-option-btn").forEach((btn) => {
      btn.onclick = () => {
        cleanup();
        resolve(btn.dataset.engine);
      };
    });

    document.addEventListener("keydown", function handler(e) {
      if (e.key === "Escape") {
        document.removeEventListener("keydown", handler);
        cleanup();
        resolve(null);
      }
    });
  });
}

function showStopGenButton() {
  const btn = document.getElementById("stopGeneration");
  if (btn) { btn.style.display = "inline-block"; btn.disabled = false; }
}

function hideStopGenButton() {
  const btn = document.getElementById("stopGeneration");
  if (btn) { btn.style.display = "none"; }
}

document.getElementById("stopGeneration")?.addEventListener("click", async () => {
  const btn = document.getElementById("stopGeneration");
  if (btn) { btn.disabled = true; btn.textContent = "Stopping..."; }
  try {
    const saved = JSON.parse(localStorage.getItem(currentJobStorageKey()) || "{}");
    if (!saved.job_id) throw new Error("No active local generation job");
    await fetchJSON(`/api/agents/jobs/${encodeURIComponent(saved.job_id)}/cancel`, {
      method: "POST",
    });
    appendLog(`Cancel requested for local generation job ${saved.job_id}.`);
  } catch (err) {
    appendLog(`Stop generation error: ${String(err)}`);
  }
  if (btn) { btn.style.display = "none"; btn.textContent = "⏹ Stop Gen"; }
});
