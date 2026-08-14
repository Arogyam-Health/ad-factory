import { clearCache, fetchJSON, invalidateRuns } from "./api.js";
import { state } from "./state.js";
import { appendLog } from "./ui.js";
import { applyLocalArtifactsToRuns, renderRunCarousel } from "./runs.js";
import { showPromptFullscreen } from "./images.js";
import { localDataPlane } from "./local-data-plane.js";
import { checkAuth, getAuthUser } from "./auth.js";

const $ = (id) => document.getElementById(id);
const selectedPersonas = new Set();
const selectedReferences = new Set();
const selectedProducts = new Set();
const referenceComments = new Map();
let referenceItems = [];
let workspace = null;
let activeRunId = "";
let statusTimer = null;
let personaTimer = null;
let lastStatusSignature = "";
let referenceDeviceId = "";
let activeReferenceJobId = "";
let referenceObjectUrls = [];
let referenceProductObjectUrls = [];
let referenceOutputObjectUrls = [];
const REFERENCE_PRODUCT_IDS_KEY = "reference-workspace-product-assets";
const PAIRING_BACKOFFS_MS = [2000, 5000, 15000];
let pairingBackoffIndex = 0;
let pairingErrorSticky = "";
let referenceRunInFlight = false;

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

function studioOrgId() {
  const src = document.querySelector(".studio-source-btn.active")?.dataset.source;
  if (src && src !== "personal") return src;
  const userId = getAuthUser()?.user_id || "";
  if (userId) {
    const stored = localStorage.getItem(`adFactoryStudioOrg:${userId}`) || "";
    if (stored && stored !== "personal") return stored;
  }
  return "";
}

function effectiveConfigUrl() {
  const orgId = studioOrgId();
  return orgId
    ? `/api/config/effective?org_id=${encodeURIComponent(orgId)}`
    : "/api/config/effective";
}

function configText(value) {
  if (typeof value === "string") return value;
  return value && typeof value === "object" ? JSON.stringify(value) : "";
}

// The reference starting prompt and product document are Mongo-backed like the
// structured config files, so edits are pinned locally and mirrored to the
// account config that seeds a fresh device.
async function saveReferenceConfigFile(configKey, content) {
  try {
    const effective = await fetchJSON(effectiveConfigUrl());
    const orgId = effective?.owner_type === "org" ? effective?.owner_id : "";
    const path = orgId
      ? `/api/orgs/${encodeURIComponent(orgId)}/config`
      : "/api/user/config";
    await fetchJSON(path, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        config: { [configKey]: content },
        expected_version: effective?.version,
      }),
    });
    clearCache("/api/config/effective");
    clearCache("/api/config/sources");
  } catch (error) {
    appendLog(`Saved locally, but syncing ${configKey} failed: ${String(error)}`);
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
    state.runsData = (data.runs || []).filter((run) => mode === "reference"
      ? ["reference", "reference_image"].includes(run.flow_type)
      : !["reference", "reference_image"].includes(run.flow_type));
    applyLocalArtifactsToRuns();
    state.currentRunIndex = 0;
    renderRunCarousel();
    populateBatchMenu();
    if (mode === "reference" && !activeRunId) {
      const active = state.runsData.find((run) => ["queued", "running"].includes(run.status));
      if (active) {
        activeRunId = active.run_id;
        activeReferenceJobId = active.reference_job_id || "";
        startPolling();
      }
    }
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

function applyFlowConfigCards(mode) {
  document.querySelectorAll(".input-prompt-card[data-flow]").forEach((el) => {
    const vis = el.dataset.flow;
    el.classList.toggle("hidden", vis !== "shared" && vis !== mode);
  });
}

function setFlow(mode) {
  const reference = mode === "reference";
  $("structuredFlowTab").classList.toggle("active", !reference);
  $("referenceFlowTab").classList.toggle("active", reference);
  $("structuredFlowPanel").classList.toggle("hidden", reference);
  $("referenceFlowPanel").classList.toggle("hidden", !reference);
  applyFlowConfigCards(mode);
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
    const orgId = studioOrgId();
    const personaUrl = orgId
      ? `/api/config/persona-summary?org_id=${encodeURIComponent(orgId)}`
      : "/api/config/persona-summary";
    const [defaultsResult, summaryResult] = await Promise.allSettled([
      fetchJSON("/api/defaults", { cache: "no-store" }),
      fetchJSON(personaUrl, { cache: "no-store" }),
    ]);
    const defaults = defaultsResult.status === "fulfilled" ? defaultsResult.value : {};
    const summary = summaryResult.status === "fulfilled" ? summaryResult.value : {};
    let personas = defaults.personas || [];
    if (Array.isArray(summary?.personas) && summary.personas.length) {
      personas = summary.personas.map((entry) => ({
        number: Number(entry.number),
        name: String(entry.name || `Persona ${entry.number}`),
        core_pattern: entry.core_pattern || "",
      })).filter((persona) => persona.number);
    }
    if (!personas.length && Array.isArray(state.defaultData?.personas)) {
      personas = state.defaultData.personas;
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
        item.object_url = await localDataPlane.assetObjectUrl(item.resource_id, referenceDeviceId, item.version);
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
    card.classList.toggle("selected", selectedProducts.has(item.resource_id));
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "product-select-checkbox";
    checkbox.checked = selectedProducts.has(item.resource_id);
    checkbox.setAttribute("aria-label", "Select product image for this run");
    checkbox.addEventListener("click", (event) => event.stopPropagation());
    checkbox.addEventListener("change", () => {
      checkbox.checked
        ? selectedProducts.add(item.resource_id)
        : selectedProducts.delete(item.resource_id);
      card.classList.toggle("selected", checkbox.checked);
      const summary = $("referenceProductImageSummary");
      if (summary) summary.textContent = `${items.length} stored · ${selectedProducts.size} selected`;
    });
    const img = document.createElement("img");
    img.src = item.object_url || "";
    img.alt = item.filename || "product image";
    img.loading = "lazy";
    const label = document.createElement("div");
    const strong = document.createElement("strong");
    strong.title = item.filename || item.resource_id;
    strong.textContent = item.filename || item.resource_id;
    label.appendChild(strong);
    card.append(checkbox, img, label);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "asset-remove";
    remove.textContent = "×";
    remove.title = "Remove product image";
    remove.addEventListener("click", async () => {
      if (!confirm(`Remove product image ${item.filename || item.resource_id}?`)) return;
      await localDataPlane.deleteAsset(item.resource_id, { deviceId: referenceDeviceId });
      selectedProducts.delete(item.resource_id);
      await writeReferenceProductIds(items.filter((entry) => entry.resource_id !== item.resource_id).map((entry) => entry.resource_id));
      await loadReferenceWorkspace();
    });
    card.appendChild(remove);
    card.addEventListener("click", (event) => {
      if (event.target.closest("button,input")) return;
      checkbox.checked = !checkbox.checked;
      checkbox.dispatchEvent(new Event("change"));
    });
    track.appendChild(card);
  });
  if (!items.length) track.innerHTML = '<div class="empty-asset-state">Upload and select at least one product image.</div>';
  const summary = $("referenceProductImageSummary");
  if (summary) summary.textContent = items.length
    ? `${items.length} stored · ${selectedProducts.size} selected`
    : "No product images stored yet.";
  setupManualSwiper("referenceProductGallery", "productPrev", "productNext");
}

async function loadReferenceWorkspace() {
  try {
    await ensureReferenceLocal();
    referenceProductObjectUrls.forEach((url) => URL.revokeObjectURL(url));
    referenceProductObjectUrls = [];
    const effective = await fetchJSON(effectiveConfigUrl());
    const sourceConfig = effective?.config || {};
    const [allProducts, productIds, productDocument, startingPrompt, personaSeed, conversionPrompt] = await Promise.all([
      localDataPlane.listAssets({ kind: "product_image", deviceId: referenceDeviceId }),
      readReferenceProductIds(),
      readLocalText("documents", "reference-product-document"),
      readLocalText("configs", "reference-starting-prompt"),
      readLocalText("configs", "reference-persona-seed"),
      readLocalText("configs", "reference-conversion-916-prompt"),
    ]);
    const hydratedText = {
      productDocument: configText(sourceConfig.reference_product_master_doc) || productDocument,
      startingPrompt: configText(sourceConfig.reference_starting_prompt) || startingPrompt,
      personaSeed: configText(sourceConfig.persona_seeds) || personaSeed,
      conversionPrompt: configText(sourceConfig.conversion_916_prompt) || conversionPrompt,
    };
    const hydrationWrites = [
      [productDocument, "documents", "reference-product-document", hydratedText.productDocument],
      [startingPrompt, "configs", "reference-starting-prompt", hydratedText.startingPrompt],
      [personaSeed, "configs", "reference-persona-seed", hydratedText.personaSeed],
      [conversionPrompt, "configs", "reference-conversion-916-prompt", hydratedText.conversionPrompt],
    ]
      .filter(([existing, , , mongo]) => mongo && existing !== mongo)
      .map(([, collection, key, mongo]) => localDataPlane.putText(
        collection,
        key,
        mongo,
        { deviceId: referenceDeviceId, operationId: `hydrate-${key}` },
      ));
    await Promise.all(hydrationWrites);

    const availableProductIds = new Set(allProducts.map((item) => item.resource_id));
    const reconciledProductIds = productIds.filter((id) => availableProductIds.has(id));
    if (reconciledProductIds.length !== productIds.length) {
      await writeReferenceProductIds(reconciledProductIds);
    }
    const productIdSet = new Set(reconciledProductIds);
    const productImages = allProducts.filter((item) => productIdSet.has(item.resource_id));
    const validProducts = new Set(productImages.map((item) => item.resource_id));
    [...selectedProducts].forEach((id) => {
      if (!validProducts.has(id)) selectedProducts.delete(id);
    });
    if (!selectedProducts.size) {
      productImages.forEach((item) => selectedProducts.add(item.resource_id));
    }
    await Promise.all(productImages.map(async (item) => {
      try {
        item.object_url = await localDataPlane.assetObjectUrl(item.resource_id, referenceDeviceId, item.version);
        referenceProductObjectUrls.push(item.object_url);
      } catch {
        item.object_url = "";
      }
    }));
    workspace = {
      product_images: productImages,
      product_document: {
        name: "reference-product-document",
        size_bytes: new Blob([hydratedText.productDocument]).size,
        content: hydratedText.productDocument,
      },
      starting_prompt: { content: hydratedText.startingPrompt },
      persona_seed: { content: hydratedText.personaSeed },
      conversion_prompt: { content: hydratedText.conversionPrompt },
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
    const content = await file.text();
    await localDataPlane.putText("documents", "reference-product-document", content, {
      deviceId: referenceDeviceId,
    });
    await saveReferenceConfigFile("reference_product_master_doc", content);
    await loadReferenceWorkspace();
    appendLog(`Reference product document updated: ${file.name}`);
  } catch (error) {
    appendLog(`Product document upload failed: ${String(error)}`);
  }
}

function openWorkspaceText(kind) {
  if (!workspace) return;
  if (kind === "doc") {
    showPromptFullscreen("Reference product document", workspace.product_document?.content || "", {
      onSave: async (content) => {
        await localDataPlane.putText("documents", "reference-product-document", content, {
          deviceId: referenceDeviceId,
        });
        await saveReferenceConfigFile("reference_product_master_doc", content);
        workspace.product_document = {
          name: "reference-product-document",
          size_bytes: new Blob([content]).size,
          content,
        };
      },
    });
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
  } else if (kind === "starter") {
    showPromptFullscreen("Reference Flow starting prompt", workspace.starting_prompt?.content || "", {
      onSave: async (content) => {
        await localDataPlane.putText("configs", "reference-starting-prompt", content, {
          deviceId: referenceDeviceId,
        });
        await saveReferenceConfigFile("reference_starting_prompt", content);
        workspace.starting_prompt = { content };
      },
    });
  } else {
    showPromptFullscreen("Reference 9:16 conversion prompt", workspace.conversion_prompt?.content || "", {
      onSave: async (content) => {
        await localDataPlane.putText("configs", "reference-conversion-916-prompt", content, {
          deviceId: referenceDeviceId,
        });
        workspace.conversion_prompt = { content };
      },
    });
  }
}

function renderLiveGallery(outputs) {
  const container = $("referenceLiveGallery");
  if (!container) return;
  if (!outputs?.length) {
    container.classList.add("hidden");
    container.innerHTML = "";
    return;
  }
  container.classList.remove("hidden");
  container.innerHTML = "";
  const header = document.createElement("div");
  header.className = "gallery-header";
  header.innerHTML = `<strong>Generated so far (${outputs.length})</strong>`;
  container.appendChild(header);
  const grid = document.createElement("div");
  grid.className = "image-grid";
  outputs.forEach((output) => {
    const url = output.object_url || "";
    const card = document.createElement("div");
    card.className = "image-card";
    card.dataset.outputId = output.output_id;
    const is916 = output.aspect_ratio === "9:16";
    card.dataset.aspect = is916 ? "9_16" : "4_5";
    const imgWrap = document.createElement("div");
    imgWrap.className = "image-wrap";
    const img = document.createElement("img");
    img.className = "gallery-thumb";
    img.loading = "lazy";
    img.src = url;
    img.alt = output.output_id;
    imgWrap.appendChild(img);
    card.appendChild(imgWrap);
    const badge = document.createElement("span");
    badge.className = `aspect-badge ${is916 ? "ar-916" : "ar-45"}`;
    badge.textContent = is916 ? "9:16" : "4:5";
    card.appendChild(badge);
    const fname = document.createElement("div");
    fname.className = "image-filename";
    fname.textContent = `${output.output_id} · v${output.current_version}`;
    fname.title = output.output_id;
    card.appendChild(fname);
    card.addEventListener("click", (event) => {
      if (event.target.closest("button") || event.target.closest("input") || event.target.closest("details") || event.target.closest("label")) return;
      if (url) window.open(url, "_blank");
    });
    grid.appendChild(card);
  });
  container.appendChild(grid);
}

async function loadLiveOutputs() {
  if (!activeRunId || !referenceDeviceId) return [];
  referenceOutputObjectUrls.forEach((url) => URL.revokeObjectURL(url));
  referenceOutputObjectUrls = [];
  const outputs = await localDataPlane.listOutputs(activeRunId, referenceDeviceId);
  await Promise.all(outputs.map(async (output) => {
    try {
      output.object_url = await localDataPlane.outputObjectUrl(
        output.output_id,
        referenceDeviceId,
        output.current_version,
      );
      referenceOutputObjectUrls.push(output.object_url);
    } catch {
      output.object_url = "";
    }
  }));
  return outputs;
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
}

function stopPolling() {
  if (statusTimer) clearInterval(statusTimer);
  statusTimer = null;
}

function isUnreachableAgentError(error) {
  return /failed to fetch|networkerror|pairing challenge expired|offline|load failed/i.test(
    String(error?.message || error || ""),
  );
}

async function pollStatus() {
  if (!activeRunId) return;
  try {
    const ownerId = getAuthUser()?.user_id || "";
    const owner = ownerId ? `user:${ownerId}` : "";
    if (!referenceDeviceId || !localDataPlane.session(referenceDeviceId, owner)) {
      await ensureReferenceLocal();
    }
    const run = await fetchJSON(`/api/runs/${activeRunId}?t=${Date.now()}`);
    const generation = run.image_generation || {};
    const data = {
      ...generation,
      status: generation.status || run.status,
      phase: generation.status === "running" ? "local generation" : generation.status,
      completed_jobs: generation.completed_count || 0,
      total_jobs: generation.total_count || 0,
      failures: generation.status === "failed" ? 1 : 0,
      message: generation.status === "completed"
        ? "Reference generation completed"
        : `Local Reference generation ${generation.completed_count || 0}/${generation.total_count || 0}`,
    };
    showStatus(data);
    renderLiveGallery(await loadLiveOutputs());
    pairingBackoffIndex = 0;
    pairingErrorSticky = "";
    if (["completed", "failed", "canceled"].includes(data.status)) {
      stopPolling();
      referenceRunInFlight = false;
      $("referenceRunBtn").disabled = false;
      $("referenceCancelBtn").disabled = true;
      invalidateRuns();
      await loadWorkspaceRuns("reference");
    }
  } catch (error) {
    if (isUnreachableAgentError(error)) {
      const line = `Reference status error: ${String(error)}`;
      if (pairingErrorSticky !== line) {
        pairingErrorSticky = line;
        appendLog(line);
      }
      stopPolling();
      const wait = PAIRING_BACKOFFS_MS[pairingBackoffIndex];
      pairingBackoffIndex = Math.min(pairingBackoffIndex + 1, PAIRING_BACKOFFS_MS.length - 1);
      statusTimer = window.setTimeout(() => {
        statusTimer = null;
        startPolling();
      }, wait);
      return;
    }
    appendLog(`Reference status error: ${String(error)}`);
  }
}

function startPolling() {
  stopPolling();
  pollStatus();
  statusTimer = setInterval(pollStatus, 1500);
}

async function startRun() {
  const runBtn = $("referenceRunBtn");
  if (referenceRunInFlight) return;
  referenceRunInFlight = true;
  if (runBtn) runBtn.disabled = true;
  let started = false;
  try {
    await refreshReferencePersonas();
    await loadReferenceWorkspace();
    if (!selectedPersonas.size) {
      appendLog("Select at least one persona.");
      return;
    }
    if (!selectedReferences.size) {
      appendLog("Select at least one reference image.");
      return;
    }
    if (!selectedProducts.size) {
      appendLog("Select at least one product image for Reference Image Flow.");
      return;
    }
    if (!workspace?.product_document?.content?.trim()) {
      appendLog("Store a local Reference product document.");
      return;
    }
    if (!workspace?.starting_prompt?.content?.trim()) {
      appendLog("Store a local Reference starting prompt.");
      return;
    }
    if (!workspace?.persona_seed?.content?.trim()) {
      appendLog("Store a local Reference persona config.");
      return;
    }
    if ($("referenceGenerate916").checked && !workspace?.conversion_prompt?.content?.trim()) {
      appendLog("Store a local Reference 9:16 conversion prompt.");
      return;
    }
    $("referenceCancelBtn").disabled = true;
    $("referenceProgressBar").style.width = "2%";
    $("referenceProgressText").textContent = "Preparing reference run…";
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
      },
    });
    referenceDeviceId = envelope.device_id;
    const productDocument = await localDataPlane.putText(
      "documents",
      `${envelope.run_id}-reference-product-document`,
      workspace.product_document.content,
      {
        deviceId: referenceDeviceId,
        operationId: `${envelope.run_id}-product-document`,
        runId: envelope.run_id,
        role: "reference_product_document",
      },
    );
    const startingPrompt = await localDataPlane.putText(
      "configs",
      `${envelope.run_id}-reference-starting-prompt`,
      workspace.starting_prompt.content,
      {
        deviceId: referenceDeviceId,
        operationId: `${envelope.run_id}-starting-prompt`,
        runId: envelope.run_id,
        role: "reference_starting_prompt",
      },
    );
    const personaConfig = await localDataPlane.putText(
      "configs",
      `${envelope.run_id}-reference-personas`,
      workspace.persona_seed.content,
      {
        deviceId: referenceDeviceId,
        operationId: `${envelope.run_id}-persona-config`,
        runId: envelope.run_id,
        role: "reference_persona_config",
      },
    );
    let conversionPrompt = null;
    if ($("referenceGenerate916").checked) {
      conversionPrompt = await localDataPlane.putText(
        "configs",
        `${envelope.run_id}-reference-conversion-916`,
        workspace.conversion_prompt.content,
        {
          deviceId: referenceDeviceId,
          operationId: `${envelope.run_id}-conversion-916`,
          runId: envelope.run_id,
          role: "conversion_prompt",
        },
      );
    }
    const selectedReferenceItems = referenceItems.filter((item) =>
      selectedReferences.has(item.resource_id));
    const selectedProductItems = (workspace.product_images || []).filter((item) =>
      selectedProducts.has(item.resource_id));
    const referenceDeclarations = [];
    for (const item of selectedReferenceItems) {
      const declaration = {
        resource_id: item.resource_id,
        version: item.version,
      };
      const comment = referenceComments.get(item.resource_id)?.trim();
      if (comment) {
        const savedComment = await localDataPlane.putText(
          "configs",
          `${envelope.run_id}-reference-comment-${item.resource_id}`,
          comment,
          {
            deviceId: referenceDeviceId,
            operationId: `${envelope.run_id}-comment-${item.resource_id}`,
          },
        );
        declaration.comment_resource_id = savedComment.resource_id;
        declaration.comment_version = savedComment.version;
      }
      referenceDeclarations.push(declaration);
    }
    await localDataPlane.putText(
      "configs",
      `${envelope.run_id}-reference-settings`,
      JSON.stringify({
        references: referenceDeclarations,
        products: selectedProductItems.map((item) => ({
          resource_id: item.resource_id,
          version: item.version,
        })),
        persona_ids: [...selectedPersonas].map(String),
        product_document: {
          resource_id: productDocument.resource_id,
          version: productDocument.version,
        },
        starting_prompt: {
          resource_id: startingPrompt.resource_id,
          version: startingPrompt.version,
        },
        persona_config: {
          resource_id: personaConfig.resource_id,
          version: personaConfig.version,
        },
        ...(conversionPrompt ? {
          conversion_prompt: {
            resource_id: conversionPrompt.resource_id,
            version: conversionPrompt.version,
          },
        } : {}),
      }),
      {
        deviceId: referenceDeviceId,
        operationId: `${envelope.run_id}-settings`,
        runId: envelope.run_id,
        role: "reference_settings",
      },
    );
    const mode = $("referenceGenerate916").checked ? "both" : "45";
    const queued = await fetchJSON(
      `/api/runs/${encodeURIComponent(envelope.run_id)}/reference-generation`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          operation_id: `${envelope.run_id}-reference-generation`,
          engine: $("referenceEngine").value,
          mode,
        }),
      },
    );
    activeRunId = envelope.run_id;
    activeReferenceJobId = queued.job_id;
    $("referenceCancelBtn").disabled = false;
    $("referenceProgressText").textContent = `Run ${envelope.display_batch} queued locally`;
    appendLog(`Reference batch ${envelope.display_batch} queued on this device`);
    started = true;
    startPolling();
    invalidateRuns();
    await loadWorkspaceRuns("reference");
  } catch (error) {
    $("referenceCancelBtn").disabled = true;
    $("referenceProgressText").textContent = "Run failed to start";
    appendLog(`Reference flow failed to start: ${String(error)}`);
  } finally {
    if (!started) {
      referenceRunInFlight = false;
      if (runBtn) runBtn.disabled = false;
    }
  }
}

async function cancelRun() {
  if (!activeRunId) return;
  $("referenceCancelBtn").disabled = true;
  try {
    if (!activeReferenceJobId) throw new Error("No active local Reference job");
    await fetchJSON(`/api/agents/jobs/${encodeURIComponent(activeReferenceJobId)}/cancel`, {
      method: "POST",
    });
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
$("editReferenceConversionPrompt")?.addEventListener("click", () => openWorkspaceText("conversion"));
$("referenceRunBtn")?.addEventListener("click", startRun);
$("referenceCancelBtn")?.addEventListener("click", cancelRun);
$("refreshRuns")?.addEventListener("click", (event) => {
  if (activeMode() !== "reference") return;
  event.stopImmediatePropagation();
  Promise.all([refreshReferencePersonas({ silent: false }), loadReferenceLibrary(), loadReferenceWorkspace(), loadWorkspaceRuns("reference")]);
}, true);
window.addEventListener("focus", () => { if (activeMode() === "reference") refreshReferencePersonas(); });
document.addEventListener("visibilitychange", () => { if (!document.hidden && activeMode() === "reference") refreshReferencePersonas(); });

applyFlowConfigCards(activeMode());
checkAuth().then(() => setFlow(activeMode()));
