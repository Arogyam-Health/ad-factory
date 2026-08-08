import { fetchJSON, invalidateRuns } from "./api.js";
import { state } from "./state.js";
import { appendLog } from "./ui.js";
import { applyLocalArtifactsToRuns, renderRunCarousel } from "./runs.js";
import { showPromptFullscreen } from "./images.js";
import { localDataPlane } from "./local-data-plane.js";
import { checkAuth, getAuthUser } from "./auth.js";

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
let referenceDeviceId = "";
let referenceObjectUrls = [];
let referenceProductObjectUrls = [];
const REFERENCE_PRODUCT_IDS_KEY = "reference-workspace-product-assets";

async function ensureReferenceLocal() {
  const ownerId = getAuthUser()?.user_id || "";
  if (!ownerId) throw new Error("Sign in before accessing local assets");
  const paired = await localDataPlane.ensurePaired({
    ownerType: "user",
    ownerId,
  });
  referenceDeviceId = paired.info.device_id;
  return paired;
}

async function readLocalText(collection, logicalKey, fallback = "") {
  try {
    return await localDataPlane.getText(collection, logicalKey, referenceDeviceId);
  } catch (error) {
    if (error.status === 404) return fallback;
    throw error;
  }
}

async function readReferenceProductIds() {
  const raw = await readLocalText("configs", REFERENCE_PRODUCT_IDS_KEY, "[]");
  try {
    const ids = JSON.parse(raw);
    return Array.isArray(ids) ? ids.filter((item) => typeof item === "string") : [];
  } catch {
    return [];
  }
}

async function writeReferenceProductIds(ids) {
  await localDataPlane.putText("configs", REFERENCE_PRODUCT_IDS_KEY, JSON.stringify(ids), {
    deviceId: referenceDeviceId,
  });
}

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
    const [defaults, summary] = await Promise.all([
      fetchJSON("/api/defaults"),
      fetchJSON("/api/config/persona-summary"),
    ]);
    let personas = defaults.personas || [];
    if (Array.isArray(summary?.personas) && summary.personas.length) {
      personas = summary.personas.map((entry) => ({
        number: Number(entry.number),
        name: String(entry.name || `Persona ${entry.number}`),
        core_pattern: entry.core_pattern || "",
      })).filter((persona) => persona.number);
    }
    renderPersonas(personas);
    if (!silent) appendLog("Reference personas refreshed from the effective config.");
  } catch (error) {
    if (!silent) appendLog(`Persona refresh failed: ${String(error)}`);
  }
}

function startPersonaSync() {
  stopPersonaSync();
  personaTimer = setInterval(() => {
    if (activeMode() === "reference" && !document.hidden) refreshReferencePersonas();
  }, 60000);
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
    card.classList.toggle("selected", selectedReferences.has(item.resource_id));
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "reference-select-checkbox";
    checkbox.checked = selectedReferences.has(item.resource_id);
    const img = document.createElement("img");
    img.src = item.object_url || "";
    img.alt = item.filename || "reference image";
    img.loading = "lazy";
    const body = document.createElement("div");
    body.className = "reference-slide-body";
    const title = document.createElement("div");
    title.className = "reference-slide-title";
    title.innerHTML = `<strong>${index + 1}. ${item.filename || item.resource_id}</strong><span>${Math.max(1, Math.round((item.bytes || 0) / 1024))} KB</span>`;
    const comment = document.createElement("textarea");
    comment.placeholder = "Optional instruction for only this reference image…";
    comment.value = referenceComments.get(item.resource_id) || "";
    comment.addEventListener("input", () => {
      if (comment.value.trim()) referenceComments.set(item.resource_id, comment.value);
      else referenceComments.delete(item.resource_id);
    });
    const actions = document.createElement("div");
    actions.className = "reference-slide-actions";
    const view = document.createElement("button");
    view.type = "button";
    view.className = "ghost-btn";
    view.textContent = "Open";
    view.addEventListener("click", () => {
      if (item.object_url) window.open(item.object_url, "_blank");
    });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "reference-remove-btn";
    remove.textContent = "Remove";
    remove.addEventListener("click", async () => {
      if (!confirm(`Remove ${item.filename || item.resource_id}?`)) return;
      remove.disabled = true;
      try {
        await localDataPlane.deleteAsset(item.resource_id, { deviceId: referenceDeviceId });
        selectedReferences.delete(item.resource_id);
        referenceComments.delete(item.resource_id);
        await loadReferenceLibrary();
      } catch (error) {
        appendLog(`Reference delete failed: ${String(error)}`);
        remove.disabled = false;
      }
    });
    checkbox.addEventListener("change", () => {
      checkbox.checked ? selectedReferences.add(item.resource_id) : selectedReferences.delete(item.resource_id);
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
    await ensureReferenceLocal();
    referenceObjectUrls.forEach((url) => URL.revokeObjectURL(url));
    referenceObjectUrls = [];
    referenceItems = await localDataPlane.listAssets({
      kind: "reference_image",
      deviceId: referenceDeviceId,
    });
    await Promise.all(referenceItems.map(async (item) => {
      try {
        item.object_url = await localDataPlane.assetObjectUrl(item.resource_id, referenceDeviceId);
        referenceObjectUrls.push(item.object_url);
      } catch {
        item.object_url = "";
      }
    }));
    const valid = new Set(referenceItems.map((item) => item.resource_id));
    [...selectedReferences].forEach((id) => { if (!valid.has(id)) selectedReferences.delete(id); });
    renderReferenceLibrary();
  } catch (error) {
    $("referenceImageSummary").textContent = `Could not load references: ${String(error)}`;
  }
}

async function uploadReferences(files) {
  const images = [...(files || [])].filter((file) => file.type.startsWith("image/"));
  if (!images.length) return;
  $("referenceImageSummary").textContent = `Uploading ${images.length} image(s)…`;
  try {
    await ensureReferenceLocal();
    const items = await localDataPlane.uploadAssets(images, {
      kind: "reference_image",
      deviceId: referenceDeviceId,
    });
    items.forEach((item) => selectedReferences.add(item.resource_id));
    await loadReferenceLibrary();
    appendLog(`Stored ${items.length} reference image(s) on this device.`);
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
    const img = document.createElement("img");
    img.src = item.object_url || "";
    img.alt = item.filename || "product image";
    img.loading = "lazy";
    const label = document.createElement("div");
    const strong = document.createElement("strong");
    strong.title = item.filename || item.resource_id;
    strong.textContent = item.filename || item.resource_id;
    label.appendChild(strong);
    card.append(img, label);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "asset-remove";
    remove.textContent = "×";
    remove.title = "Remove product image";
    remove.addEventListener("click", async () => {
      if (!confirm(`Remove product image ${item.filename || item.resource_id}?`)) return;
      await localDataPlane.deleteAsset(item.resource_id, { deviceId: referenceDeviceId });
      await writeReferenceProductIds(items.filter((entry) => entry.resource_id !== item.resource_id).map((entry) => entry.resource_id));
      await loadReferenceWorkspace();
    });
    card.appendChild(remove);
    card.addEventListener("dblclick", () => {
      if (item.object_url) window.open(item.object_url, "_blank");
    });
    track.appendChild(card);
  });
  if (!items.length) track.innerHTML = '<div class="empty-asset-state">Upload at least one product image. Every reference job will receive all stored product images.</div>';
  setupManualSwiper("referenceProductGallery", "productPrev", "productNext");
}

async function loadReferenceWorkspace() {
  try {
    await ensureReferenceLocal();
    referenceProductObjectUrls.forEach((url) => URL.revokeObjectURL(url));
    referenceProductObjectUrls = [];
    const [allProducts, productIds, productDocument, startingPrompt, personaSeed] = await Promise.all([
      localDataPlane.listAssets({ kind: "product_image", deviceId: referenceDeviceId }),
      readReferenceProductIds(),
      readLocalText("documents", "reference-product-document"),
      readLocalText("configs", "reference-starting-prompt"),
      readLocalText("configs", "reference-persona-seed"),
    ]);
    const productIdSet = new Set(productIds);
    const productImages = allProducts.filter((item) => productIdSet.has(item.resource_id));
    await Promise.all(productImages.map(async (item) => {
      try {
        item.object_url = await localDataPlane.assetObjectUrl(item.resource_id, referenceDeviceId);
        referenceProductObjectUrls.push(item.object_url);
      } catch {
        item.object_url = "";
      }
    }));
    workspace = {
      product_images: productImages,
      product_document: {
        name: "reference-product-document",
        size_bytes: new Blob([productDocument]).size,
        content: productDocument,
      },
      starting_prompt: { content: startingPrompt },
      persona_seed: { content: personaSeed },
    };
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
  try {
    await ensureReferenceLocal();
    const replace = Boolean($("referenceClearProductImages")?.checked);
    const existingIds = await readReferenceProductIds();
    if (replace) {
      await Promise.all(existingIds.map((resourceId) => localDataPlane.deleteAsset(resourceId, {
        deviceId: referenceDeviceId,
      })));
    }
    const saved = await localDataPlane.uploadAssets(images, {
      kind: "product_image",
      deviceId: referenceDeviceId,
    });
    await writeReferenceProductIds([
      ...(replace ? [] : existingIds),
      ...saved.map((item) => item.resource_id),
    ]);
    if ($("referenceClearProductImages")) $("referenceClearProductImages").checked = false;
    await loadReferenceWorkspace();
    appendLog(`Stored ${images.length} reference-flow product image(s).`);
  } catch (error) {
    appendLog(`Product image upload failed: ${String(error)}`);
  }
}

async function uploadProductDoc(file) {
  if (!file) return;
  try {
    await ensureReferenceLocal();
    await localDataPlane.putText("documents", "reference-product-document", await file.text(), {
      deviceId: referenceDeviceId,
    });
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
      onSave: async (content) => {
        await localDataPlane.putText("configs", "reference-persona-seed", content, {
          deviceId: referenceDeviceId,
        });
        workspace.persona_seed = { content };
      },
    });
  } else {
    showPromptFullscreen("Reference Flow starting prompt", workspace.starting_prompt?.content || "", {
      onSave: async (content) => {
        await localDataPlane.putText("configs", "reference-starting-prompt", content, {
          deviceId: referenceDeviceId,
        });
        workspace.starting_prompt = { content };
      },
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
  for (const resourceId of selectedReferences) {
    const value = referenceComments.get(resourceId)?.trim();
    if (value) comments[resourceId] = value;
  }
  $("referenceRunBtn").disabled = true;
  $("referenceCancelBtn").disabled = true;
  $("referenceProgressBar").style.width = "2%";
  $("referenceProgressText").textContent = "Preparing reference run…";
  try {
    await ensureReferenceLocal();
    const user = getAuthUser();
    const envelope = await localDataPlane.allocateLocalRun({
      ownerType: "user",
      ownerId: user.user_id,
      flowType: "reference",
      settings: {
        engine: $("referenceEngine").value,
        generate_916: $("referenceGenerate916").checked,
        headless: state.headlessModeEnabled,
        selected_personas: [...selectedPersonas],
      },
    });
    referenceDeviceId = envelope.device_id;
    await localDataPlane.putText(
      "configs",
      `${envelope.run_id}-reference-settings`,
      JSON.stringify({
        selected_personas: [...selectedPersonas],
        reference_resource_ids: [...selectedReferences],
        product_resource_ids: (workspace.product_images || []).map((item) => item.resource_id),
        reference_comments: comments,
        engine: $("referenceEngine").value,
        generate_916: $("referenceGenerate916").checked,
        headless: state.headlessModeEnabled,
      }),
      { deviceId: referenceDeviceId, operationId: `${envelope.run_id}-settings` },
    );
    activeRunId = envelope.run_id;
    $("referenceProgressBar").style.width = "100%";
    $("referenceProgressText").textContent = `Run ${envelope.display_batch} staged locally`;
    appendLog(`Reference run ${envelope.run_id} staged on this device. Local generation is not enabled in this phase.`);
    invalidateRuns();
    await loadWorkspaceRuns("reference");
    $("referenceRunBtn").disabled = false;
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

checkAuth().then(() => setFlow(activeMode()));
