import { state } from "./state.js";
import { appendLog, skeletonRunCard } from "./ui.js";
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
    state.runsData = (data.runs || []).map(normalizeRun);
    applyLocalArtifactsToRuns();
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
const LOCAL_ARTIFACT_CACHE_KEY = "adFactoryLocalArtifacts";
const LOCAL_ARTIFACT_ORIGIN = "http://127.0.0.1:8765";
let localArtifactOrigin = LOCAL_ARTIFACT_ORIGIN;
let localArtifactCapability = "";
let agentJobPollTimer = null;
let localArtifactImages = [];
let structuredLocalImages = [];
let localArtifactSignature = "";
let localManifestRefreshInFlight = false;
let localArtifactEventSource = null;
const localDataEventStreams = new Map();

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

function scopedStorageKey(baseKey) {
  const userId = getAuthUser()?.user_id || "anonymous";
  return `${baseKey}:${userId}`;
}

function currentArtifactCacheKey() {
  return scopedStorageKey(LOCAL_ARTIFACT_CACHE_KEY);
}

function currentJobStorageKey() {
  return scopedStorageKey(LOCAL_STORAGE_JOB_KEY);
}

function artifactSignature(images) {
  return images.map((image) => {
    const runIds = Array.isArray(image.run_ids) ? image.run_ids.join(",") : "";
    return `${image.url || ""}:${image.bytes || 0}:${image.sha256 || ""}:${image.updated_at || image.modified_at || 0}:${image.batch || ""}:${runIds}`;
  }).join("|");
}

function setLocalArtifactAccess(value) {
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(parsed.hostname)) return;
    const nextOrigin = parsed.origin;
    const owner = parsed.searchParams.get("owner") || "";
    const token = parsed.searchParams.get("token") || "";
    const nextCapability = owner && token ? new URLSearchParams({ owner, token }).toString() : localArtifactCapability;
    if (nextOrigin === localArtifactOrigin && nextCapability === localArtifactCapability) return;
    localArtifactOrigin = nextOrigin;
    localArtifactCapability = nextCapability;
    localArtifactEventSource?.close();
    localArtifactEventSource = null;
    startLocalArtifactEvents();
  } catch {
    // Ignore malformed artifact origins received from stale job metadata.
  }
}

function localArtifactUrl(path) {
  const suffix = localArtifactCapability ? `?${localArtifactCapability}` : "";
  return `${localArtifactOrigin}${path}${suffix}`;
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

function syncLocalAgentArtifacts(job, { authoritative = false } = {}) {
  const result = job?.result || {};
  let images = Array.isArray(result.images) ? result.images : [];
  const capabilityUrl = images.find((image) => image?.url)?.url || result.artifact_base_url;
  if (capabilityUrl) setLocalArtifactAccess(capabilityUrl);
  if (!images.length && !authoritative) return false;
  if (!authoritative) {
    try {
      const previous = JSON.parse(localStorage.getItem(currentArtifactCacheKey()) || "null");
      const previousByUrl = new Map((previous?.images || []).map((image) => [image.url, image]));
      images = images.map((image) => ({ ...(previousByUrl.get(image.url) || {}), ...image }));
    } catch {
      // Ignore malformed previous metadata and replace it below.
    }
  }
  const nextSignature = artifactSignature(images);
  const changed = nextSignature !== localArtifactSignature;
  localArtifactImages = images;
  localArtifactSignature = nextSignature;
  try {
    if (images.length) {
      localStorage.setItem(currentArtifactCacheKey(), JSON.stringify({
        local_output_dir: result.local_output_dir || "",
        artifact_base_url: result.artifact_base_url || "http://127.0.0.1:8765",
        images: images.slice(0, 500),
        cached_at: Date.now(),
      }));
    } else {
      localStorage.removeItem(currentArtifactCacheKey());
    }
  } catch {
    // Local metadata is an optimization; image files remain on the agent machine.
  }
  return changed;
}

export function applyLocalArtifactsToRuns() {
  if (!state.runsData.length) return;
  const allImages = [...localArtifactImages, ...structuredLocalImages];
  const validLocalUrls = new Set(allImages.map((image) => image.url));
  state.runsData.forEach((run) => {
    if (!Array.isArray(run.image_files)) run.image_files = [];
    run.image_files = run.image_files.filter((path) => !String(path).startsWith("http://127.0.0.1:") || validLocalUrls.has(path));
  });
  allImages.forEach((image) => {
    const explicitRunIds = Array.isArray(image.run_ids) ? image.run_ids : (image.run_id ? [image.run_id] : []);
    let targets = state.runsData.filter((run) => explicitRunIds.includes(run.run_id));
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
        artifact_id: image.artifact_id || "",
        output_id: image.output_id || "",
        output_version: image.output_version || 0,
        prompt_id: image.prompt_id || "",
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

export async function refreshStructuredLocalOutputs() {
  const user = getAuthUser();
  if (!user?.user_id) return;
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
  const next = [];
  for (const run of state.runsData) {
    if (!run?.run_id || !run?.device_id || !run?.agent_id) continue;
    run.local_device_status = "unavailable";
    run.image_files = [];
    run.image_items = [];
    run.regeneration_queue_files = [];
    run.regeneration_queue_items = [];
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
      run.local_device_status = "online";
      for (const output of outputs) {
        const key = `${output.output_id}:${output.current_version}`;
        const cached = previous.get(key);
        const url = cached?.url || await localDataPlane.outputObjectUrl(
          output.output_id,
          run.device_id,
        );
        next.push({
          output_id: output.output_id,
          output_version: output.current_version,
          artifact_id: output.output_id,
          run_id: run.run_id,
          run_ids: [run.run_id],
          prompt_id: output.prompt_id,
          item_id: output.item_id,
          aspect_ratio: output.aspect_ratio,
          status: output.status,
          url,
        });
      }
    } catch {
      // Render metadata remains visible, but content URLs never fall back across devices.
    }
  }
  const retained = new Set(next.map((image) => image.url));
  structuredLocalImages.forEach((image) => {
    if (!retained.has(image.url) && String(image.url).startsWith("blob:")) {
      URL.revokeObjectURL(image.url);
    }
  });
  structuredLocalImages = next;
  applyLocalArtifactsToRuns();
  if (state.runsData.length) renderRunCarousel().catch(() => {});
}

function startLocalArtifactEvents() {
  if (localArtifactEventSource || !localArtifactCapability || typeof EventSource === "undefined") return;
  const source = new EventSource(localArtifactUrl("/events"));
  localArtifactEventSource = source;
  source.addEventListener("artifacts", () => refreshLocalArtifactManifest());
  source.onerror = () => {
    if (localArtifactEventSource !== source) return;
    source.close();
    localArtifactEventSource = null;
    setTimeout(startLocalArtifactEvents, 3000);
  };
}

function restoreCachedLocalArtifacts() {
  try {
    const cached = JSON.parse(localStorage.getItem(currentArtifactCacheKey()) || "null");
    if (cached?.images?.length) {
      localArtifactImages = cached.images;
      syncLocalAgentArtifacts({ result: cached });
      applyLocalArtifactsToRuns();
    }
  } catch {
    localStorage.removeItem(currentArtifactCacheKey());
  }
}

export async function refreshLocalArtifactManifest() {
  if (localManifestRefreshInFlight || !localArtifactCapability) return;
  localManifestRefreshInFlight = true;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 2500);
  try {
    const url = new URL(localArtifactUrl("/manifest"));
    url.searchParams.set("t", String(Date.now()));
    const response = await fetch(url, {
      cache: "no-store",
      mode: "cors",
      signal: controller.signal,
    });
    if (!response.ok) return;
    const manifest = await response.json();
    if (!Array.isArray(manifest.images)) return;
    const changed = syncLocalAgentArtifacts({
      result: {
        local_output_dir: manifest.local_output_dir,
        artifact_base_url: manifest.artifact_base_url,
        images: manifest.images,
      },
    }, { authoritative: true });
    if (!changed) return;
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
          await refreshLocalArtifactManifest();
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
      await refreshLocalArtifactManifest();
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

restoreCachedLocalArtifacts();
refreshLocalArtifactManifest();
refreshStructuredLocalOutputs().catch(() => {});
startLocalArtifactEvents();
setInterval(refreshLocalArtifactManifest, 10000);
setInterval(() => refreshStructuredLocalOutputs().catch(() => {}), 10000);
checkActiveAgentJob();
fetchJSON("/api/batch/job-status", { cache: "no-store" }).then((data) => {
  if (data?.active && data.job?.job_id) {
    localStorage.setItem(currentJobStorageKey(), JSON.stringify({ job_id: data.job.job_id, timestamp: Date.now() }));
    startAgentJobPolling();
    return;
  }
  if (data?.job && !data.active && data.job.status === "completed") {
    refreshLocalArtifactManifest();
    showAgentJobBar("Last local agent job completed.", false);
  }
}).catch(() => {});

document.getElementById("batchGen45")?.addEventListener("click", async () => {
  const runsForBatches = selectedOrCurrentRuns();
  if (!runsForBatches.length) { appendLog("Select a batch or open a run with prompt files first."); return; }

  const engine = await showEngineSelector("4:5");
  if (!engine) return;

  const runIds = runsForBatches.map((r) => r.run_id);
  const batchLabel = runsForBatches.map(displayBatch).filter(Boolean).join(", ");
  const engineLabel = engine === "chatgpt" ? "ChatGPT" : "Gemini";
  appendLog(`Generating 4:5 in ${engineLabel} for ${batchLabel || "current run"} (${runIds.length} run(s))...`);
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
  const runsForBatches = selectedOrCurrentRuns();
  if (!runsForBatches.length) { appendLog("Select a batch or open a run with prompt files first."); return; }

  const engine = await showEngineSelector("4:5 & 9:16");
  if (!engine) return;

  const runIds = runsForBatches.map((r) => r.run_id);
  const batchLabel = runsForBatches.map(displayBatch).filter(Boolean).join(", ");
  const engineLabel = engine === "chatgpt" ? "ChatGPT" : "Gemini";
  appendLog(`Generating 4:5 + 9:16 in ${engineLabel} for ${batchLabel || "current run"} (${runIds.length} run(s))...`);
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
  const runsForBatches = selectedOrCurrentRuns();
  if (!runsForBatches.length) { appendLog("Select a batch or open a run with prompt files first."); return; }
  const engine = await showEngineSelector("9:16");
  if (!engine) return;

  const runIds = runsForBatches.map((r) => r.run_id);
  const batchLabel = runsForBatches.map(displayBatch).filter(Boolean).join(", ");
  const engineLabel = engine === "chatgpt" ? "ChatGPT" : "Gemini";
  appendLog(`Generating 9:16 in ${engineLabel} for ${batchLabel || "current run"} (${runIds.length} run(s))...`);
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
    const selectedRuns = state.runsData.filter((run) => selectedBatches.includes(runKey(run)));
    const localRunIds = new Set(localArtifactImages.flatMap((image) => image.run_ids || (image.run_id ? [image.run_id] : [])));
    const selectedLocalRuns = selectedRuns.filter((run) => localRunIds.has(run.run_id));
    const selectedLocalBatches = selectedLocalRuns.map((run) => run.run_id);
    if (selectedLocalBatches.length) {
      const params = new URLSearchParams();
      selectedLocalRuns.forEach((run) => params.append("run_id", run.run_id));
      for (const [key, value] of new URLSearchParams(localArtifactCapability)) params.set(key, value);
      const localResponse = await fetch(`${localArtifactOrigin}/download-batches?${params}`, { cache: "no-store", mode: "cors" });
      if (!localResponse.ok) throw new Error(`Local batch download failed (${localResponse.status})`);
      await downloadZipResponse(localResponse, `ad_factory_${selectedLocalBatches.join("_")}.zip`);
      appendLog(`Downloaded ${selectedLocalBatches.length} local batch(es).`);
      if (selectedLocalBatches.length === selectedBatches.length) return;
    }

    const localBatchSet = new Set(selectedLocalBatches);
    const serverBatches = selectedBatches.filter((batch) => !localBatchSet.has(batch));
    if (!serverBatches.length) return;
    const res = await fetch("/api/runs/download-batches", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ batch_ids: serverBatches }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      appendLog(`Download failed: ${err.detail || res.statusText}`);
      return;
    }
    await downloadZipResponse(res, `${serverBatches.join("_")}.zip`);
    appendLog("Batch download complete.");
  } catch (err) {
    appendLog(`Download error: ${String(err)}`);
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
    const data = await fetchJSON("/api/stop-generation", { method: "POST" });
    appendLog(`Generation stopped. ${JSON.stringify(data)}`);
  } catch (err) {
    appendLog(`Stop generation error: ${String(err)}`);
  }
  if (btn) { btn.style.display = "none"; btn.textContent = "⏹ Stop Gen"; }
});
