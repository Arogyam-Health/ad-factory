import { fetchJSON, invalidateRuns } from "./api.js";
import { state } from "./state.js";
import { appendLog } from "./ui.js";
import { applyLocalArtifactsToRuns, renderRunCarousel } from "./runs.js";
import { showPromptFullscreen } from "./images.js";

const $ = (id) => document.getElementById(id);
const selectedPersonas = new Set();
const selectedReferences = new Set();
const referenceComments = new Map();
let referenceItems = [];
let workspace = null;
let activeRunId = "";
let statusTimer = null;
let personaTimer = null;
let lastStatusSignature = "";

function activeMode() {
  return localStorage.getItem("adFactoryFlowMode") === "reference" ? "reference" : "structured";
}

function updateJobCount() {
  const count = selectedPersonas.size * selectedReferences.size;
  if ($("referenceJobCount")) $("referenceJobCount").textContent = `${count} job${count === 1 ? "" : "s"}`;
}

function setWorkspaceMode(mode) {
  const reference = mode === "reference";
  $("workspaceEyebrow").textContent = reference ? "Reference generation" : "Structured generation";
  $("workspaceTitle").textContent = reference ? "Reference Runs" : "8) Latest Runs";
  $("workspaceDescription").textContent = reference
    ? "Launch Chrome, run the selected reference jobs, inspect persona-grouped outputs, revise images, and download reference batches."
    : "Generate, inspect, revise, and download structured-flow batches.";
  $("referenceWorkspaceActions").classList.toggle("hidden", !reference);
  $("referenceProgressArea").classList.toggle("hidden", !reference);
  ["batchGen45", "batchGenBoth", "batchGen916"].forEach((id) => $(id)?.classList.toggle("hidden", reference));
}

async function loadWorkspaceRuns(mode = activeMode()) {
  try {
    const data = await fetchJSON(`/api/runs?flow=${mode}&t=${Date.now()}`);
    state.runsData = (data.runs || []).filter((run) => mode === "reference" ? run.flow_type === "reference_image" : run.flow_type !== "reference_image");
    applyLocalArtifactsToRuns();
    state.currentRunIndex = 0;
    renderRunCarousel();
    populateBatchMenu();
  } catch (error) {
    appendLog(`Could not load ${mode} runs: ${String(error)}`);
  }
}

function populateBatchMenu() {
  const menu = $("batchDropdownMenu");
  if (!menu) return;
  menu.innerHTML = "";
  const batches = [...new Set(state.runsData.map((run) => run.batch).filter(Boolean))];
  if (!batches.length) {
    menu.innerHTML = `<div class="hint">No ${activeMode() === "reference" ? "reference" : "structured"} batches yet.</div>`;
    return;
  }
  const grid = document.createElement("div");
  grid.className = "batch-grid";
  batches.forEach((batch) => {
    const item = document.createElement("label");
    item.className = "batch-grid-item";
    item.innerHTML = `<input type="checkbox" value="${batch}" class="batch-check"><span class="batch-label">${batch}</span>`;
    item.querySelector("input").addEventListener("change", () => {
      const count = menu.querySelectorAll(".batch-check:checked").length;
      $("batchDropdownBtn").textContent = count ? `${count} batch(es) selected` : "Select batch(es)";
    });
    grid.appendChild(item);
  });
  menu.appendChild(grid);
}

function setFlow(mode) {
  const reference = mode === "reference";
  $("structuredFlowTab").classList.toggle("active", !reference);
  $("referenceFlowTab").classList.toggle("active", reference);
  $("structuredFlowPanel").classList.toggle("hidden", reference);
  $("referenceFlowPanel").classList.toggle("hidden", !reference);
  localStorage.setItem("adFactoryFlowMode", mode);
  setWorkspaceMode(mode);
  loadWorkspaceRuns(mode);
  if (reference) {
    refreshReferencePersonas();
    loadReferenceLibrary();
    loadReferenceWorkspace();
    startPersonaSync();
  } else {
    stopPersonaSync();
  }
}

function renderPersonas(personas = state.defaultData?.personas || []) {
  const root = $("referencePersonaList");
  if (!root) return;
  const valid = new Set(personas.map((item) => Number(item.number)));
  [...selectedPersonas].forEach((number) => { if (!valid.has(number)) selectedPersonas.delete(number); });
  root.innerHTML = "";
  personas.forEach((persona) => {
    const card = document.createElement("label");
    card.className = "reference-persona-card";
    const checked = selectedPersonas.has(Number(persona.number));
    card.classList.toggle("selected", checked);
    card.innerHTML = `<input type="checkbox" ${checked ? "checked" : ""}><span><strong>${persona.number}. ${persona.name}</strong><small>${persona.core_pattern || persona.description || "Uses the latest persona seed content."}</small></span>`;
    const checkbox = card.querySelector("input");
    checkbox.addEventListener("change", () => {
      checkbox.checked ? selectedPersonas.add(Number(persona.number)) : selectedPersonas.delete(Number(persona.number));
      card.classList.toggle("selected", checkbox.checked);
      updateJobCount();
    });
    root.appendChild(card);
  });
  updateJobCount();
}

async function refreshReferencePersonas({ silent = true } = {}) {
  try {
    const data = await fetchJSON(`/api/defaults?t=${Date.now()}`);
    state.defaultData = data;
    renderPersonas(data.personas || []);
    if (!silent) appendLog("Reference personas refreshed from persona_seeds.json.");
  } catch (error) {
    if (!silent) appendLog(`Persona refresh failed: ${String(error)}`);
  }
}

function startPersonaSync() {
  stopPersonaSync();
  personaTimer = setInterval(() => {
    if (activeMode() === "reference" && !document.hidden) refreshReferencePersonas();
  }, 5000);
}

function stopPersonaSync() {
  if (personaTimer) clearInterval(personaTimer);
  personaTimer = null;
}

function setupManualSwiper(trackId, prevId, nextId) {
  const track = $(trackId);
  const prev = $(prevId);
  const next = $(nextId);
  if (!track || !prev || !next || track.dataset.ready) return;
  track.dataset.ready = "1";
  const amount = () => Math.max(260, Math.round(track.clientWidth * 0.82));
  prev.addEventListener("click", () => track.scrollBy({ left: -amount(), behavior: "smooth" }));
  next.addEventListener("click", () => track.scrollBy({ left: amount(), behavior: "smooth" }));
}

function renderReferenceLibrary() {
  const track = $("referencePreviewGrid");
  if (!track) return;
  track.innerHTML = "";
  referenceItems.forEach((item, index) => {
    const card = document.createElement("article");
    card.className = "reference-slide";
    card.classList.toggle("selected", selectedReferences.has(item.path));
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "reference-select-checkbox";
    checkbox.checked = selectedReferences.has(item.path);
    const img = document.createElement("img");
    img.src = item.url;
    img.alt = item.name;
    img.loading = "lazy";
    const body = document.createElement("div");
    body.className = "reference-slide-body";
    const title = document.createElement("div");
    title.className = "reference-slide-title";
    title.innerHTML = `<strong>${index + 1}. ${item.name}</strong><span>${Math.max(1, Math.round((item.size_bytes || 0) / 1024))} KB</span>`;
    const comment = document.createElement("textarea");
    comment.placeholder = "Optional instruction for only this reference image…";
    comment.value = referenceComments.get(item.path) || "";
    comment.addEventListener("input", () => {
      if (comment.value.trim()) referenceComments.set(item.path, comment.value);
      else referenceComments.delete(item.path);
    });
    const actions = document.createElement("div");
    actions.className = "reference-slide-actions";
    const view = document.createElement("button");
    view.type = "button";
    view.className = "ghost-btn";
    view.textContent = "Open";
    view.addEventListener("click", () => window.open(item.url, "_blank"));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "reference-remove-btn";
    remove.textContent = "Remove";
    remove.addEventListener("click", async () => {
      if (!confirm(`Remove ${item.name}?`)) return;
      remove.disabled = true;
      try {
        await fetchJSON("/api/reference-images", { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: item.path }) });
        selectedReferences.delete(item.path);
        referenceComments.delete(item.path);
        await loadReferenceLibrary();
      } catch (error) {
        appendLog(`Reference delete failed: ${String(error)}`);
        remove.disabled = false;
      }
    });
    checkbox.addEventListener("change", () => {
      checkbox.checked ? selectedReferences.add(item.path) : selectedReferences.delete(item.path);
      card.classList.toggle("selected", checkbox.checked);
      updateJobCount();
      updateReferenceSummary();
    });
    actions.append(view, remove);
    body.append(title, comment, actions);
    card.append(checkbox, img, body);
    track.appendChild(card);
  });
  updateReferenceSummary();
  setupManualSwiper("referencePreviewGrid", "referencePrev", "referenceNext");
}

function updateReferenceSummary() {
  const el = $("referenceImageSummary");
  if (!el) return;
  el.textContent = referenceItems.length ? `${referenceItems.length} stored · ${selectedReferences.size} selected` : "No references stored yet.";
}

async function loadReferenceLibrary() {
  try {
    const data = await fetchJSON(`/api/reference-images?t=${Date.now()}`);
    referenceItems = data.items || [];
    const valid = new Set(referenceItems.map((item) => item.path));
    [...selectedReferences].forEach((path) => { if (!valid.has(path)) selectedReferences.delete(path); });
    renderReferenceLibrary();
  } catch (error) {
    $("referenceImageSummary").textContent = `Could not load references: ${String(error)}`;
  }
}

async function uploadReferences(files) {
  const images = [...(files || [])].filter((file) => file.type.startsWith("image/"));
  if (!images.length) return;
  const form = new FormData();
  images.forEach((file) => form.append("files", file, file.name));
  $("referenceImageSummary").textContent = `Uploading ${images.length} image(s)…`;
  try {
    const data = await fetchJSON("/api/reference-images", { method: "POST", body: form });
    (data.items || []).forEach((item) => selectedReferences.add(item.path));
    await loadReferenceLibrary();
    appendLog(`Stored ${data.saved || 0} reference image(s).`);
  } catch (error) {
    appendLog(`Reference upload failed: ${String(error)}`);
    await loadReferenceLibrary();
  }
}

function renderProductImages() {
  const track = $("referenceProductGallery");
  if (!track) return;
  track.innerHTML = "";
  const items = workspace?.product_images || [];
  items.forEach((item) => {
    const card = document.createElement("article");
    card.className = "product-asset-slide";
    card.innerHTML = `<img src="${item.url}" alt="${item.name}" loading="lazy"><div><strong title="${item.name}">${item.name}</strong></div>`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "asset-remove";
    remove.textContent = "×";
    remove.title = "Remove product image";
    remove.addEventListener("click", async () => {
      if (!confirm(`Remove product image ${item.name}?`)) return;
      await fetchJSON("/api/reference-workspace/product-images", { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: item.path }) });
      await loadReferenceWorkspace();
    });
    card.appendChild(remove);
    card.addEventListener("dblclick", () => window.open(item.url, "_blank"));
    track.appendChild(card);
  });
  if (!items.length) track.innerHTML = '<div class="empty-asset-state">Upload at least one product image. Every reference job will receive all stored product images.</div>';
  setupManualSwiper("referenceProductGallery", "productPrev", "productNext");
}

async function loadReferenceWorkspace() {
  try {
    workspace = await fetchJSON(`/api/reference-workspace?t=${Date.now()}`);
    renderProductImages();
    const doc = workspace.product_document || {};
    $("referenceProductDocMeta").textContent = `${doc.name || "product_document.txt"} · ${Math.max(0, Math.round((doc.size_bytes || 0) / 1024))} KB`;
  } catch (error) {
    appendLog(`Reference workspace failed to load: ${String(error)}`);
  }
}

async function uploadProductImages(files) {
  const images = [...(files || [])].filter((file) => file.type.startsWith("image/"));
  if (!images.length) return;
  const form = new FormData();
  images.forEach((file) => form.append("files", file, file.name));
  form.append("replace", String(Boolean($("referenceClearProductImages")?.checked)));
  try {
    await fetchJSON("/api/reference-workspace/product-images", { method: "POST", body: form });
    if ($("referenceClearProductImages")) $("referenceClearProductImages").checked = false;
    await loadReferenceWorkspace();
    appendLog(`Stored ${images.length} reference-flow product image(s).`);
  } catch (error) {
    appendLog(`Product image upload failed: ${String(error)}`);
  }
}

async function uploadProductDoc(file) {
  if (!file) return;
  const form = new FormData();
  form.append("file", file, file.name);
  try {
    await fetchJSON("/api/reference-workspace/product-document", { method: "POST", body: form });
    await loadReferenceWorkspace();
    appendLog(`Reference product document updated: ${file.name}`);
  } catch (error) {
    appendLog(`Product document upload failed: ${String(error)}`);
  }
}

function openWorkspaceText(kind) {
  if (!workspace) return;
  if (kind === "doc") {
    showPromptFullscreen("Reference product document", workspace.product_document?.content || "", {});
  } else if (kind === "persona") {
    const persona = workspace.persona_seed;
    showPromptFullscreen("Persona seed (editable)", persona?.content || "", {
      fetchUrl: persona?.path ? `/api/prompt-file-content?prompt_path=${encodeURIComponent(persona.path)}` : undefined,
      saveUrl: "/api/prompt-file-content",
      saveBody: (text) => ({ prompt_path: persona.path, content: text }),
    });
  } else {
    showPromptFullscreen("Reference Flow starting prompt", workspace.starting_prompt?.content || "", {
      saveUrl: "/api/reference-workspace/starting-prompt",
      saveBody: (content) => ({ content }),
    });
  }
}

function renderLiveGallery(imageFiles) {
  const container = $("referenceLiveGallery");
  if (!container) return;
  if (!imageFiles?.length) {
    container.classList.add("hidden");
    container.innerHTML = "";
    return;
  }
  container.classList.remove("hidden");
  container.innerHTML = "";
  const header = document.createElement("div");
  header.className = "gallery-header";
  header.innerHTML = `<strong>Generated so far (${imageFiles.length})</strong>`;
  container.appendChild(header);
  const grid = document.createElement("div");
  grid.className = "image-grid";
  imageFiles.forEach((path) => {
    const cleanPath = path.replace(/^generated_images\//, "");
    const url = `/generated_images/${cleanPath}`;
    const card = document.createElement("div");
    card.className = "image-card";
    card.dataset.path = path;
    const is916 = path.includes("/9_16/");
    card.dataset.aspect = is916 ? "9_16" : "4_5";
    const imgWrap = document.createElement("div");
    imgWrap.className = "image-wrap";
    const img = document.createElement("img");
    img.className = "gallery-thumb";
    img.loading = "lazy";
    img.src = url;
    img.alt = path.split("/").pop() || "";
    imgWrap.appendChild(img);
    card.appendChild(imgWrap);
    const badge = document.createElement("span");
    badge.className = `aspect-badge ${is916 ? "ar-916" : "ar-45"}`;
    badge.textContent = is916 ? "9:16" : "4:5";
    card.appendChild(badge);
    const fname = document.createElement("div");
    fname.className = "image-filename";
    fname.textContent = path.split("/").pop() || path;
    fname.title = path;
    card.appendChild(fname);
    card.addEventListener("click", (event) => {
      if (event.target.closest("button") || event.target.closest("input") || event.target.closest("details") || event.target.closest("label")) return;
      window.open(url, "_blank");
    });
    grid.appendChild(card);
  });
  container.appendChild(grid);
}

function showStatus(payload) {
  const completed = Number(payload.completed_jobs || 0);
  const total = Number(payload.total_jobs || 0);
  let percent = total ? Math.round((completed / total) * 90) : 0;
  if (payload.phase === "9:16 conversion") percent = 94;
  if (payload.status === "completed") percent = 100;
  $("referenceProgressBar").style.width = `${Math.min(100, percent)}%`;
  $("referenceProgressText").textContent = payload.message || `${payload.phase || "Running"} · ${completed}/${total}`;
  const signature = `${payload.status}|${payload.phase}|${completed}|${payload.failures || 0}|${payload.message || ""}`;
  if (signature !== lastStatusSignature) {
    lastStatusSignature = signature;
    appendLog(`[Reference] ${payload.message || payload.phase || payload.status}`);
  }
  renderLiveGallery(payload.partial_image_files);
}

function stopPolling() {
  if (statusTimer) clearInterval(statusTimer);
  statusTimer = null;
}

async function pollStatus() {
  if (!activeRunId) return;
  try {
    const data = await fetchJSON(`/api/runs/${activeRunId}/reference-status?t=${Date.now()}`);
    showStatus(data);
    if (["completed", "error", "cancelled"].includes(data.status)) {
      stopPolling();
      $("referenceRunBtn").disabled = false;
      $("referenceCancelBtn").disabled = true;
      $("referenceLiveGallery").classList.add("hidden");
      $("referenceLiveGallery").innerHTML = "";
      invalidateRuns();
      await loadWorkspaceRuns("reference");
    }
  } catch (error) {
    appendLog(`Reference status error: ${String(error)}`);
  }
}

async function startRun() {
  await refreshReferencePersonas();
  await loadReferenceWorkspace();
  if (!selectedPersonas.size) return appendLog("Select at least one persona.");
  if (!selectedReferences.size) return appendLog("Select at least one reference image.");
  if (!(workspace?.product_images || []).length) return appendLog("Upload at least one product image for Reference Image Flow.");
  const comments = {};
  for (const path of selectedReferences) {
    const value = referenceComments.get(path)?.trim();
    if (value) comments[path] = value;
  }
  const form = new FormData();
  form.append("config", JSON.stringify({
    selected_personas: [...selectedPersonas],
    reference_image_paths: [...selectedReferences],
    reference_comments: comments,
    engine: $("referenceEngine").value,
    generate_916: $("referenceGenerate916").checked,
    headless: state.headlessModeEnabled,
  }));
  $("referenceRunBtn").disabled = true;
  $("referenceCancelBtn").disabled = true;
  $("referenceProgressBar").style.width = "2%";
  $("referenceProgressText").textContent = "Preparing reference run…";
  try {
    const data = await fetchJSON("/api/runs/execute-reference", { method: "POST", body: form });
    activeRunId = data.run_id;
    $("referenceCancelBtn").disabled = false;
    appendLog(`Reference flow started: ${data.run_id}, ${data.total_jobs} jobs.`);
    stopPolling();
    statusTimer = setInterval(pollStatus, 2000);
    await pollStatus();
  } catch (error) {
    $("referenceRunBtn").disabled = false;
    $("referenceCancelBtn").disabled = true;
    $("referenceProgressText").textContent = "Run failed to start";
    appendLog(`Reference flow failed to start: ${String(error)}`);
  }
}

async function cancelRun() {
  if (!activeRunId) return;
  $("referenceCancelBtn").disabled = true;
  try {
    await fetchJSON(`/api/runs/${activeRunId}/cancel`, { method: "POST" });
    appendLog(`Cancellation requested for ${activeRunId}.`);
  } catch (error) {
    appendLog(`Cancel error: ${String(error)}`);
  }
}

$("structuredFlowTab")?.addEventListener("click", () => setFlow("structured"));
$("referenceFlowTab")?.addEventListener("click", () => setFlow("reference"));
$("referenceImageFiles")?.addEventListener("change", (event) => uploadReferences(event.target.files));
$("referenceIndividualFiles")?.addEventListener("change", (event) => uploadReferences(event.target.files));
$("referenceChooseFiles")?.addEventListener("click", () => $("referenceIndividualFiles")?.click());
$("referenceProductImages")?.addEventListener("change", (event) => uploadProductImages(event.target.files));
$("referenceProductFile")?.addEventListener("change", (event) => uploadProductDoc(event.target.files?.[0]));
$("viewReferenceProductDoc")?.addEventListener("click", () => openWorkspaceText("doc"));
$("viewReferencePersonaSeed")?.addEventListener("click", () => openWorkspaceText("persona"));
$("editReferenceStartingPrompt")?.addEventListener("click", () => openWorkspaceText("starter"));
$("referenceRunBtn")?.addEventListener("click", startRun);
$("referenceCancelBtn")?.addEventListener("click", cancelRun);
$("refreshRuns")?.addEventListener("click", (event) => {
  if (activeMode() !== "reference") return;
  event.stopImmediatePropagation();
  Promise.all([refreshReferencePersonas({ silent: false }), loadReferenceLibrary(), loadReferenceWorkspace(), loadWorkspaceRuns("reference")]);
}, true);
window.addEventListener("focus", () => { if (activeMode() === "reference") refreshReferencePersonas(); });
document.addEventListener("visibilitychange", () => { if (!document.hidden && activeMode() === "reference") refreshReferencePersonas(); });

setFlow(activeMode());
