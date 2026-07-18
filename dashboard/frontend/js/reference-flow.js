import { fetchJSON, invalidateRuns } from "./api.js";
import { state } from "./state.js";
import { appendLog } from "./ui.js";
import { renderRunCarousel } from "./runs.js";

const $ = (id) => document.getElementById(id);
const selectedPersonas = new Set();
const selectedReferences = new Set();
let referenceItems = [];
let activeRunId = "";
let statusTimer = null;
let personaRefreshTimer = null;
let personaSignature = "";
let personaRefreshBusy = false;

function activeMode() {
  return localStorage.getItem("adFactoryFlowMode") === "reference" ? "reference" : "structured";
}

function updateJobCount() {
  const count = selectedPersonas.size * selectedReferences.size;
  $("referenceJobCount").textContent = `${count} job${count === 1 ? "" : "s"}`;
}

function setWorkspaceMode(mode) {
  const reference = mode === "reference";
  $("workspaceEyebrow").textContent = reference ? "Reference generation" : "Structured generation";
  $("workspaceTitle").textContent = reference ? "Reference Runs" : "8) Latest Runs";
  $("workspaceDescription").textContent = reference
    ? "Launch Chrome, start the reference job, inspect persona-grouped outputs, revise images, and download reference batches."
    : "Generate, inspect, revise, and download structured-flow batches.";
  $("referenceWorkspaceActions").classList.toggle("hidden", !reference);
  $("referenceProgressArea").classList.toggle("hidden", !reference);
  ["batchGen45", "batchGenBoth", "batchGen916"].forEach((id) => $(id)?.classList.toggle("hidden", reference));
}

async function loadWorkspaceRuns(mode = activeMode()) {
  try {
    const data = await fetchJSON(`/api/runs?flow=${mode}&t=${Date.now()}`);
    const all = data.runs || [];
    state.runsData = all.filter((run) => mode === "reference" ? run.flow_type === "reference_image" : run.flow_type !== "reference_image");
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
  const grid = document.createElement("div");
  grid.className = "batch-grid";
  [...new Set(state.runsData.map((run) => run.batch).filter(Boolean))].forEach((batch) => {
    const item = document.createElement("div");
    item.className = "batch-grid-item";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = batch;
    input.className = "batch-check";
    const label = document.createElement("span");
    label.className = "batch-label";
    label.textContent = batch;
    item.append(input, label);
    item.addEventListener("click", (event) => {
      if (event.target !== input) input.checked = !input.checked;
      const count = menu.querySelectorAll(".batch-check:checked").length;
      $("batchDropdownBtn").textContent = count ? `${count} batch(es) selected` : "Select batch(es)";
    });
    grid.appendChild(item);
  });
  if (!grid.children.length) {
    const empty = document.createElement("div");
    empty.className = "hint";
    empty.textContent = activeMode() === "reference" ? "No reference batches yet." : "No structured batches yet.";
    menu.appendChild(empty);
  } else {
    menu.appendChild(grid);
  }
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
  if (reference) refreshReferencePersonas(true);
}

function currentPersonaSignature(personas) {
  return JSON.stringify((personas || []).map((persona) => ({
    number: Number(persona.number),
    name: String(persona.name || ""),
    core_pattern: String(persona.core_pattern || persona.description || ""),
  })));
}

function renderPersonas() {
  const root = $("referencePersonaList");
  if (!root || !state.defaultData?.personas) return;
  root.innerHTML = "";
  state.defaultData.personas.forEach((persona) => {
    const card = document.createElement("label");
    card.className = "reference-persona-card";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = selectedPersonas.has(persona.number);

    const copy = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent = `${persona.number}. ${persona.name}`;
    const detail = document.createElement("small");
    detail.textContent = persona.core_pattern || persona.description || "";
    copy.append(title, detail);

    checkbox.addEventListener("change", () => {
      checkbox.checked ? selectedPersonas.add(persona.number) : selectedPersonas.delete(persona.number);
      card.classList.toggle("selected", checkbox.checked);
      updateJobCount();
    });
    card.classList.toggle("selected", checkbox.checked);
    card.append(checkbox, copy);
    root.appendChild(card);
  });
}

async function refreshReferencePersonas(force = false) {
  if (personaRefreshBusy) return false;
  personaRefreshBusy = true;
  try {
    const data = await fetchJSON(`/api/defaults?t=${Date.now()}`);
    const personas = Array.isArray(data?.personas) ? data.personas : [];
    const nextSignature = currentPersonaSignature(personas);
    const changed = force || nextSignature !== personaSignature;
    if (!changed) return false;

    const validNumbers = new Set(personas.map((persona) => Number(persona.number)));
    [...selectedPersonas].forEach((number) => {
      if (!validNumbers.has(Number(number))) selectedPersonas.delete(number);
    });

    state.defaultData = { ...(state.defaultData || {}), ...data, personas };
    personaSignature = nextSignature;
    renderPersonas();
    updateJobCount();
    if (!force) appendLog("Reference personas refreshed from persona_seeds.json.");
    return true;
  } catch (error) {
    if (force) appendLog(`Could not refresh persona seeds: ${String(error)}`);
    return false;
  } finally {
    personaRefreshBusy = false;
  }
}

async function waitForDefaults() {
  for (let i = 0; i < 100; i += 1) {
    if (state.defaultData?.personas?.length) {
      personaSignature = currentPersonaSignature(state.defaultData.personas);
      renderPersonas();
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  $("referencePersonaList").textContent = "Could not load persona seeds.";
}

function startPersonaRefreshLoop() {
  if (personaRefreshTimer) clearInterval(personaRefreshTimer);
  personaRefreshTimer = setInterval(() => {
    if (activeMode() === "reference" && document.visibilityState === "visible") {
      refreshReferencePersonas(false);
    }
  }, 5000);
}

function renderReferenceLibrary() {
  const grid = $("referencePreviewGrid");
  grid.innerHTML = "";
  referenceItems.forEach((item) => {
    const card = document.createElement("article");
    card.className = "reference-preview-item";
    card.classList.toggle("selected", selectedReferences.has(item.path));
    const select = document.createElement("input");
    select.type = "checkbox";
    select.className = "reference-select-checkbox";
    select.checked = selectedReferences.has(item.path);
    const image = document.createElement("img");
    image.src = item.url;
    image.alt = item.name;
    image.loading = "lazy";
    const meta = document.createElement("div");
    meta.className = "reference-preview-meta";
    const name = document.createElement("strong");
    name.title = item.name;
    name.textContent = item.name;
    const size = document.createElement("small");
    size.textContent = `${Math.max(1, Math.round((item.size_bytes || 0) / 1024))} KB`;
    meta.append(name, size);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "reference-remove-btn";
    remove.textContent = "Remove";
    remove.addEventListener("click", async (event) => {
      event.stopPropagation();
      if (!confirm(`Remove ${item.name} from the persistent reference library?`)) return;
      remove.disabled = true;
      try {
        await fetchJSON("/api/reference-images", {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: item.path }),
        });
        selectedReferences.delete(item.path);
        await loadReferenceLibrary();
      } catch (error) {
        appendLog(`Reference delete failed: ${String(error)}`);
        remove.disabled = false;
      }
    });
    card.addEventListener("click", (event) => {
      if (event.target.closest("button")) return;
      select.checked = !select.checked;
      select.dispatchEvent(new Event("change"));
    });
    select.addEventListener("change", (event) => {
      event.stopPropagation();
      select.checked ? selectedReferences.add(item.path) : selectedReferences.delete(item.path);
      card.classList.toggle("selected", select.checked);
      updateJobCount();
    });
    card.append(select, image, meta, remove);
    grid.appendChild(card);
  });
  $("referenceImageSummary").textContent = referenceItems.length
    ? `${referenceItems.length} stored · ${selectedReferences.size} selected`
    : "No reference images stored yet.";
  updateJobCount();
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

function showStatus(payload) {
  $("referenceStatus").textContent = JSON.stringify(payload, null, 2);
  const completed = Number(payload.completed_jobs || 0);
  const total = Number(payload.total_jobs || 0);
  let percent = total ? Math.min(90, Math.round((completed / total) * 90)) : 0;
  if (payload.phase === "9:16 conversion") percent = 94;
  if (payload.status === "completed") percent = 100;
  $("referenceProgressBar").style.width = `${percent}%`;
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
      appendLog(data.message || `Reference flow ${data.status}.`);
      if (data.status === "completed") {
        invalidateRuns();
        await loadWorkspaceRuns("reference");
      }
    }
  } catch (error) {
    appendLog(`Reference status error: ${String(error)}`);
  }
}

async function startRun() {
  await refreshReferencePersonas(true);
  if (!selectedPersonas.size) return appendLog("Select at least one persona for the reference flow.");
  if (!selectedReferences.size) return appendLog("Select at least one stored reference image.");
  const form = new FormData();
  form.append("config", JSON.stringify({
    selected_personas: [...selectedPersonas],
    reference_image_paths: [...selectedReferences],
    engine: $("referenceEngine").value,
    generate_916: $("referenceGenerate916").checked,
    headless: state.headlessModeEnabled,
  }));
  const productFile = $("referenceProductFile").files?.[0];
  if (productFile) form.append("product_info_file", productFile, productFile.name);
  [...($("referenceProductImages").files || [])].forEach((file) => form.append("input_image_files", file, file.name));
  form.append("clear_input_images", String($("referenceClearProductImages").checked));
  $("referenceRunBtn").disabled = true;
  $("referenceCancelBtn").disabled = true;
  $("referenceProgressBar").style.width = "2%";
  $("referenceStatus").textContent = "Preparing reference run…";
  try {
    const data = await fetchJSON("/api/runs/execute-reference", { method: "POST", body: form });
    activeRunId = data.run_id;
    $("referenceCancelBtn").disabled = false;
    showStatus(data);
    appendLog(`Reference flow started: ${data.run_id}, ${data.total_jobs} jobs.`);
    stopPolling();
    statusTimer = setInterval(pollStatus, 2000);
    await pollStatus();
  } catch (error) {
    $("referenceRunBtn").disabled = false;
    $("referenceCancelBtn").disabled = true;
    $("referenceStatus").textContent = String(error);
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
$("referenceRunBtn")?.addEventListener("click", startRun);
$("referenceCancelBtn")?.addEventListener("click", cancelRun);
$("refreshRuns")?.addEventListener("click", (event) => {
  if (activeMode() !== "reference") return;
  event.stopImmediatePropagation();
  invalidateRuns();
  refreshReferencePersonas(true);
  loadWorkspaceRuns("reference");
}, true);
window.addEventListener("focus", () => {
  if (activeMode() === "reference") refreshReferencePersonas(false);
});
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && activeMode() === "reference") {
    refreshReferencePersonas(false);
  }
});

setFlow(activeMode());
waitForDefaults();
startPersonaRefreshLoop();
loadReferenceLibrary();
