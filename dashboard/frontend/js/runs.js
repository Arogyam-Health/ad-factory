import { state } from "./state.js";
import { appendLog, skeletonRunCard } from "./ui.js";
import { fetchJSON, invalidateRuns } from "./api.js";
import { buildImageGallery, showPromptFullscreen } from "./images.js";
import { buildPromptEditor } from "./prompts.js";
import { refreshSelect } from "./custom-select.js";

const runsEl = document.getElementById("runs");
const runPrevEl = document.getElementById("runPrev");
const runNextEl = document.getElementById("runNext");
const runIndexEl = document.getElementById("runIndex");

let batchDropdownInitialized = false;
let runRenderVersion = 0;

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
      const opts = promptDoc?.prompt_id ? {
        fetchUrl: `/api/runs/${encodeURIComponent(runId)}/prompts/${encodeURIComponent(promptDoc.prompt_id)}/content`,
        saveUrl: `/api/runs/${encodeURIComponent(runId)}/prompts/${encodeURIComponent(promptDoc.prompt_id)}/content`,
        saveBody: (text) => ({ content: text }),
      } : {
        fetchUrl: `/api/prompt-file-content?prompt_path=${encodeURIComponent(path)}`,
        saveUrl: "/api/prompt-file-content",
        saveBody: (text) => ({ prompt_path: path, content: text }),
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
  header.innerHTML = `<strong>${run.run_id}</strong><span class="run-meta">batch ${run.batch} &middot; prompts ${run.prompt_files.length} &middot; images ${run.image_files.length}</span><button class="ghost-btn run-delete-btn" type="button" title="Delete this entire run">Delete</button>`;
  div.appendChild(header);

  header.querySelector(".run-delete-btn")?.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (!confirm(`Delete entire run ${run.run_id} and all its images?`)) return;
    try {
      await fetchJSON(`/api/runs/${run.run_id}`, { method: "DELETE" });
      appendLog(`Deleted run ${run.run_id}`);
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

  if (run.prompt_files && run.prompt_files.length) {
    div.appendChild(buildPromptFileSummary(run.run_id, run.prompt_files, promptsData));
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
  const latestBatch = total ? (state.runsData[0].batch || "-") : "-";
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
      const label = run.batch || run.run_id;
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
  try {
    const data = await fetchJSON("/api/runs");
    state.runsData = data.runs || [];
    applyLocalArtifactsToRuns();
    state.currentRunIndex = 0;
    updatePreviousRunOptions();

    const batchMenu = document.getElementById("batchDropdownMenu");
    batchMenu.innerHTML = "";

    const batches = new Set();
    state.runsData.forEach((r) => { if (r.batch) batches.add(r.batch); });

    const grid = document.createElement("div");
    grid.className = "batch-grid";
    const batchList = Array.from(batches).sort(compareBatchesLatestFirst);
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
    labelSpan.textContent = batch;
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
const LOCAL_ARTIFACT_CACHE_KEY = "adFactoryLocalArtifacts";
const LOCAL_ARTIFACT_MANIFEST_URL = "http://127.0.0.1:8765/artifacts";
let agentJobPollTimer = null;
let localArtifactImages = [];
let localArtifactSignature = "";
let localManifestRefreshInFlight = false;

function artifactSignature(images) {
  return images.map((image) => {
    const runIds = Array.isArray(image.run_ids) ? image.run_ids.join(",") : "";
    return `${image.url || ""}:${image.bytes || 0}:${image.batch || ""}:${runIds}`;
  }).join("|");
}

function appendGenerationResult(data, fallback) {
  if (data?.status === "queued_local_agent") {
    appendLog(`Queued local agent job ${data.job_id} on ${data.agent_name || data.agent_id}. Images will save on the local agent machine.`);
    localStorage.setItem(LOCAL_STORAGE_JOB_KEY, JSON.stringify({
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

function renderLocalAgentArtifacts(job) {
  const wrap = document.getElementById("localAgentArtifacts");
  if (!wrap) return;
  const result = job?.result || {};
  let images = Array.isArray(result.images) ? result.images : [];
  if (!images.length) {
    return;
  }
  try {
    const previous = JSON.parse(localStorage.getItem(LOCAL_ARTIFACT_CACHE_KEY) || "null");
    const previousByUrl = new Map((previous?.images || []).map((image) => [image.url, image]));
    images = images.map((image) => ({ ...(previousByUrl.get(image.url) || {}), ...image }));
  } catch {
    // Ignore malformed previous metadata and replace it below.
  }
  localArtifactImages = images;
  localArtifactSignature = artifactSignature(images);
  try {
    localStorage.setItem(LOCAL_ARTIFACT_CACHE_KEY, JSON.stringify({
      local_output_dir: result.local_output_dir || "",
      artifact_base_url: result.artifact_base_url || "http://127.0.0.1:8765",
      images: images.slice(0, 500),
      cached_at: Date.now(),
    }));
  } catch {
    // Local metadata is an optimization; image files remain on the agent machine.
  }
  wrap.classList.remove("hidden");
  const outputDir = result.local_output_dir || "local agent output";
  wrap.innerHTML = `
    <div class="local-agent-artifacts-head">
      <div>
        <strong>Local agent images</strong>
        <span>${images.length} image(s) served from your machine</span>
      </div>
      <code>${escapeHtml(outputDir)}</code>
    </div>
    <div class="local-agent-artifacts-grid">
      ${images.map((img) => `
        <a class="local-agent-artifact" href="${escapeAttr(img.url)}" target="_blank" rel="noopener">
          <img src="${escapeAttr(img.url)}" alt="${escapeAttr(img.name || "Generated image")}" loading="lazy" />
          <span>${escapeHtml(img.name || "generated image")}</span>
        </a>
      `).join("")}
    </div>
  `;
}

export function applyLocalArtifactsToRuns() {
  if (!localArtifactImages.length || !state.runsData.length) return;
  localArtifactImages.forEach((image) => {
    const explicitRunIds = Array.isArray(image.run_ids) ? image.run_ids : [];
    let targets = state.runsData.filter((run) => explicitRunIds.includes(run.run_id));
    if (!targets.length && image.batch) {
      const latestBatchRun = state.runsData.find((run) => run.batch === image.batch);
      if (latestBatchRun) targets = [latestBatchRun];
    }
    targets.forEach((run) => {
      if (!Array.isArray(run.image_files)) run.image_files = [];
      if (!run.image_files.includes(image.url)) run.image_files.push(image.url);
      run.image_generated = true;
    });
  });
}

function restoreCachedLocalArtifacts() {
  try {
    const cached = JSON.parse(localStorage.getItem(LOCAL_ARTIFACT_CACHE_KEY) || "null");
    if (cached?.images?.length) {
      localArtifactImages = cached.images;
      renderLocalAgentArtifacts({ result: cached });
      applyLocalArtifactsToRuns();
    }
  } catch {
    localStorage.removeItem(LOCAL_ARTIFACT_CACHE_KEY);
  }
}

async function refreshLocalArtifactManifest() {
  if (localManifestRefreshInFlight) return;
  localManifestRefreshInFlight = true;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 2500);
  try {
    const response = await fetch(`${LOCAL_ARTIFACT_MANIFEST_URL}?t=${Date.now()}`, {
      cache: "no-store",
      mode: "cors",
      signal: controller.signal,
    });
    if (!response.ok) return;
    const manifest = await response.json();
    if (!Array.isArray(manifest.images) || !manifest.images.length) return;
    const nextSignature = artifactSignature(manifest.images);
    if (nextSignature === localArtifactSignature) return;
    localArtifactImages = manifest.images;
    localArtifactSignature = nextSignature;
    renderLocalAgentArtifacts({
      result: {
        local_output_dir: manifest.local_output_dir,
        artifact_base_url: manifest.artifact_base_url,
        images: manifest.images,
      },
    });
    applyLocalArtifactsToRuns();
    if (state.runsData.length) renderRunCarousel().catch(() => {});
  } catch {
    // The local agent may be offline; keep the last cached metadata visible.
  } finally {
    clearTimeout(timeout);
    localManifestRefreshInFlight = false;
  }
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/'/g, "&#39;");
}

function hideAgentJobBar() {
  const bar = document.getElementById("agentJobBar");
  if (bar) bar.classList.add("hidden");
  localStorage.removeItem(LOCAL_STORAGE_JOB_KEY);
  if (agentJobPollTimer) { clearInterval(agentJobPollTimer); agentJobPollTimer = null; }
}

function startAgentJobPolling() {
  if (agentJobPollTimer) return;
  showAgentJobBar("Agent job in progress...");
  agentJobPollTimer = setInterval(async () => {
    try {
      const data = await fetchJSON("/api/batch/job-status", { cache: "no-store" });
      if (!data || !data.job) {
        appendLog("No recent agent job found.");
        hideAgentJobBar();
        loadRuns();
        return;
      }
      const job = data.job || {};
      if (!data.active) {
        if (job.status === "completed") {
          const warningText = Array.isArray(job.result?.warnings) && job.result.warnings.length ? ` ${job.result.warnings[0]}` : "";
          showAgentJobBar(`Agent job completed. ${job.result?.images?.length || 0} local image(s) ready.${warningText}`, false);
          renderLocalAgentArtifacts(job);
          localStorage.removeItem(LOCAL_STORAGE_JOB_KEY);
          if (agentJobPollTimer) { clearInterval(agentJobPollTimer); agentJobPollTimer = null; }
          return;
        }
        const terminalLabel = job.status === "canceled" ? "canceled" : "failed";
        showAgentJobBar(`Agent job ${terminalLabel}: ${job.error || "unknown error"}`, false, job);
        localStorage.removeItem(LOCAL_STORAGE_JOB_KEY);
        if (agentJobPollTimer) { clearInterval(agentJobPollTimer); agentJobPollTimer = null; }
        return;
      }
      const progress = job.progress || "";
      const status = job.status || "pending";
      const label = status === "cancel_requested" ? "Canceling" : (status === "running" ? "Running" : "Queued");
      const msg = `Agent job ${label}: ${progress || "waiting for pickup..."}`;
      showAgentJobBar(msg, status !== "cancel_requested", job);
      renderLocalAgentArtifacts(job);
    } catch (err) {
      if (agentJobPollTimer) {
        clearInterval(agentJobPollTimer);
        agentJobPollTimer = null;
      }
    }
  }, 1000);
}

function checkActiveAgentJob() {
  const raw = localStorage.getItem(LOCAL_STORAGE_JOB_KEY);
  if (!raw) return;
  try {
    const saved = JSON.parse(raw);
    if (!saved || !saved.job_id || (Date.now() - saved.timestamp > 7200000)) {
      localStorage.removeItem(LOCAL_STORAGE_JOB_KEY);
      return;
    }
    startAgentJobPolling();
  } catch {
    localStorage.removeItem(LOCAL_STORAGE_JOB_KEY);
  }
}

restoreCachedLocalArtifacts();
refreshLocalArtifactManifest();
setInterval(refreshLocalArtifactManifest, 2000);
checkActiveAgentJob();
fetchJSON("/api/batch/job-status", { cache: "no-store" }).then((data) => {
  if (data?.job && !data.active && data.job.status === "completed") {
    renderLocalAgentArtifacts(data.job);
    const warningText = Array.isArray(data.job.result?.warnings) && data.job.result.warnings.length ? ` ${data.job.result.warnings[0]}` : "";
    showAgentJobBar(`Last local agent job completed. ${data.job.result?.images?.length || 0} local image(s) ready.${warningText}`, false);
  }
}).catch(() => {});

document.getElementById("batchGen45")?.addEventListener("click", async () => {
  const selectedBatches = getSelectedBatchValues();
  if (!selectedBatches.length) { appendLog("Select at least one batch."); return; }

  const engine = await showEngineSelector("4:5");
  if (!engine) return;

  const runsForBatches = state.runsData.filter((r) => selectedBatches.includes(r.batch));
  if (!runsForBatches.length) { appendLog("No runs found for selected batch(es)."); return; }
  const runIds = runsForBatches.map((r) => r.run_id);
  const engineLabel = engine === "chatgpt" ? "ChatGPT" : "Gemini";
  appendLog(`Batch generating 4:5 in ${engineLabel} for ${runIds.length} run(s)...`);
  showStopGenButton();
  try {
    const data = await fetchJSON("/api/batch/generate-images-45", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_ids: runIds, headless: state.headlessModeEnabled, engine, visible: false }),
    });
    const batchKey = data.batch_key || "";
    if (batchKey && state.headlessModeEnabled) {
      import("./chrome.js").then((m) => m.startProgressPolling(batchKey));
    }
    appendGenerationResult(data, (d) => `Done. Batch: ${d.batch_key}, Prompts: ${d.total_prompts}`);
    loadRuns();
  } catch (err) {
    appendLog(String(err));
  } finally {
    hideStopGenButton();
  }
});

document.getElementById("batchGenBoth")?.addEventListener("click", async () => {
  const selectedBatches = getSelectedBatchValues();
  if (!selectedBatches.length) { appendLog("Select at least one batch."); return; }

  const engine = await showEngineSelector("4:5 & 9:16");
  if (!engine) return;

  const runsForBatches = state.runsData.filter((r) => selectedBatches.includes(r.batch));
  if (!runsForBatches.length) { appendLog("No runs found for selected batch(es)."); return; }
  const runIds = runsForBatches.map((r) => r.run_id);
  const engineLabel = engine === "chatgpt" ? "ChatGPT" : "Gemini";
  appendLog(`Batch generating 4:5 + 9:16 in ${engineLabel} for ${runIds.length} run(s)...`);
  showStopGenButton();
  try {
    const data = await fetchJSON("/api/batch/generate-images-both", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_ids: runIds, headless: state.headlessModeEnabled, engine, visible: false }),
    });
    appendGenerationResult(data, (d) => `Done. 4:5 prompts: ${d.total_45_prompts}, 9:16 images: ${d.total_916_completed}, Batches: ${d.batch_key}`);
    loadRuns();
  } catch (err) {
    appendLog(String(err));
  } finally {
    hideStopGenButton();
  }
});

document.getElementById("batchGen916")?.addEventListener("click", async () => {
  const selectedBatches = getSelectedBatchValues();
  if (!selectedBatches.length) { appendLog("Select at least one batch."); return; }
  const engine = await showEngineSelector("9:16");
  if (!engine) return;

  const runsForBatches = state.runsData.filter((r) => selectedBatches.includes(r.batch));
  if (!runsForBatches.length) { appendLog("No runs found for selected batch(es)."); return; }
  const runIds = runsForBatches.map((r) => r.run_id);
  const engineLabel = engine === "chatgpt" ? "ChatGPT" : "Gemini";
  appendLog(`Batch generating 9:16 in ${engineLabel} for ${runIds.length} run(s)...`);
  showStopGenButton();
  try {
    const data = await fetchJSON("/api/batch/generate-images-916", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_ids: runIds, headless: state.headlessModeEnabled, engine, visible: false }),
    });
    const batchKey = data.batch_key || "";
    if (batchKey && state.headlessModeEnabled) {
      import("./chrome.js").then((m) => m.startProgressPolling(batchKey));
    }
    appendGenerationResult(data, (d) => `Done. Batch: ${d.batch_key}, Prompts: ${d.total_prompts}`);
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
    const res = await fetch("/api/runs/download-batches", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ batch_ids: selectedBatches }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      appendLog(`Download failed: ${err.detail || res.statusText}`);
      return;
    }
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    const disposition = res.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="?(.+?)"?$/);
    a.download = match ? match[1] : `${selectedBatches.join("_")}.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(a.href);
    appendLog("Batch download complete.");
  } catch (err) {
    appendLog(`Download error: ${String(err)}`);
  }
});

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
    const data = await fetchJSON("/api/stop-generation", { method: "POST" });
    appendLog(`Generation stopped. ${JSON.stringify(data)}`);
  } catch (err) {
    appendLog(`Stop generation error: ${String(err)}`);
  }
  if (btn) { btn.style.display = "none"; btn.textContent = "⏹ Stop Gen"; }
});
