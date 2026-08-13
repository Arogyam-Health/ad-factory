import { state, getPersonaSelection, getFormatsByPersona, getHypothesisConfig, loadDefaults } from "./state.js";
import { setStatus, setSelectOptions, appendLog } from "./ui.js";
import { renderPersonas, showPersonaSkeletons, renderGlobalFormats, renderLanguageModes, renderFormatPatterns } from "./personas.js";
import { renderHypothesisUI } from "./hypothesis.js";
import { loadRuns as loadAndRenderRuns, showRunsSkeletons } from "./runs.js";
import { showPromptFullscreen } from "./images.js";
import { stopProgressPolling } from "./chrome.js";
import { initTheme } from "./theme.js";
import { fetchJSON, invalidateRuns, clearCache } from "./api.js";
import { enhanceAllSelects, refreshSelect } from "./custom-select.js";
import { localDataPlane } from "./local-data-plane.js";
import { initAuth, getAuthUser } from "./auth.js";

const modelSelectEl = document.getElementById("opencodeModel");
const defaultsInfoEl = document.getElementById("defaultsInfo");
let structuredDeviceId = "";
let inputImageObjectUrls = [];
let activeStructuredRunId = "";

function structuredOwner() {
  const user = getAuthUser();
  return {
    ownerType: studioCurrentOrgId ? "org" : "user",
    ownerId: studioCurrentOrgId || user?.user_id || "",
  };
}

async function ensureStructuredLocal() {
  const owner = structuredOwner();
  if (!owner.ownerId) throw new Error("Sign in before accessing local assets");
  const paired = await localDataPlane.ensurePaired(owner);
  structuredDeviceId = paired.info.device_id;
  return { ...owner, ...paired };
}

async function saveProductMasterDoc(content) {
  const effectiveUrl = studioCurrentOrgId
    ? `/api/config/effective?org_id=${encodeURIComponent(studioCurrentOrgId)}`
    : "/api/config/effective";
  const effective = await fetchJSON(effectiveUrl);
  const isSharedOrg = effective?.owner_type === "org" && studioCurrentOrgId;
  await fetchJSON(
    isSharedOrg
      ? `/api/orgs/${encodeURIComponent(studioCurrentOrgId)}/config`
      : "/api/user/config",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(
        {
          config: { product_master_doc: content },
          expected_version: effective?.version,
        },
      ),
    },
  );
  clearCache("/api/config/effective");
  clearCache("/api/config/sources");
}

async function renderInputImages(images = []) {
  const gallery = document.getElementById("inputImageGallery");
  if (!gallery) return;
  inputImageObjectUrls.forEach((url) => URL.revokeObjectURL(url));
  inputImageObjectUrls = [];
  gallery.innerHTML = "";
  if (!images.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = "No stored input images yet.";
    gallery.appendChild(empty);
    return;
  }
  for (const item of images) {
    const path = item.filename || item.resource_id;
    const card = document.createElement("div");
    card.className = "image-card input-image-card";
    card.dataset.aspect = "INPUT_IMAGE";

    let url = "";
    try {
      url = await localDataPlane.assetObjectUrl(item.resource_id, structuredDeviceId);
      inputImageObjectUrls.push(url);
    } catch {
      card.classList.add("local-content-unavailable");
    }
    const imgWrap = document.createElement("div");
    imgWrap.className = "image-wrap";

    const img = document.createElement("img");
    img.className = "gallery-thumb";
    img.src = url;
    img.alt = path.split("/").pop() || "input image";
    img.loading = "lazy";
    imgWrap.appendChild(img);

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "image-delete-btn";
    deleteBtn.textContent = "✕";
    deleteBtn.title = "Delete this input image";
    imgWrap.appendChild(deleteBtn);

    const downloadBtn = document.createElement("button");
    downloadBtn.type = "button";
    downloadBtn.className = "image-download-btn";
    downloadBtn.textContent = "⬇";
    downloadBtn.title = "Download input image";
    imgWrap.appendChild(downloadBtn);

    card.appendChild(imgWrap);

    const fname = document.createElement("div");
    fname.className = "image-filename";
    fname.textContent = path.split("/").pop() || path;
    card.appendChild(fname);

    card.addEventListener("click", (event) => {
      if (event.target.closest(".image-delete-btn") || event.target.closest(".image-download-btn")) return;
      if (url) window.open(url, "_blank");
    });

    downloadBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      if (!url) return;
      const a = document.createElement("a");
      a.href = url;
      a.download = path.split("/").pop() || "input-image";
      document.body.appendChild(a);
      a.click();
      a.remove();
    });

    deleteBtn.addEventListener("click", async (event) => {
      event.stopPropagation();
      if (!confirm(`Delete input image "${path.split("/").pop()}"?`)) return;
      deleteBtn.disabled = true;
      try {
        await localDataPlane.deleteAsset(item.resource_id, { deviceId: structuredDeviceId });
        card.remove();
      } catch (err) {
        setStatus(`Failed to delete input image: ${String(err)}`);
        deleteBtn.disabled = false;
      }
    });

    gallery.appendChild(card);
  }
}

async function loadStructuredAssets({ silent = true } = {}) {
  try {
    await ensureStructuredLocal();
    const images = await localDataPlane.listAssets({
      kind: "product_image",
      deviceId: structuredDeviceId,
    });
    await renderInputImages(images);
    return images;
  } catch (error) {
    if (!silent) setStatus(`Local device unavailable: ${String(error)}`);
    const gallery = document.getElementById("inputImageGallery");
    if (gallery) gallery.innerHTML = '<p class="hint">Local assets unavailable. Start this device\'s local agent and retry.</p>';
    return [];
  }
}

function renderModelOptions(provider, preferredModel = "") {
  const models = state.modelsByProvider[provider] || [];
  const selected = preferredModel && models.includes(preferredModel) ? preferredModel : (models[0] || "");
  setSelectOptions(modelSelectEl, models.length ? models : [""], selected);
}

async function initDefaults() {
  showPersonaSkeletons();
  try {
    const data = await loadDefaults();
    renderPersonas();
    renderGlobalFormats();
    renderLanguageModes();
    renderFormatPatterns();
    renderHypothesisUI();
    const localImages = await loadStructuredAssets();

    defaultsInfoEl.textContent = `Using defaults: product=${data.default_files.product_info}, mechanism=${data.default_files.playbook}, local product images=${localImages.length} file(s)`;

    initStudioSourceSelector({ reloadInitialPersona: false }).catch(() => {});

    let opencode = data.opencode || {};
    const catalogPromise = Object.keys(opencode.models_by_provider || {}).length
      ? Promise.resolve(opencode)
      : fetchJSON("/api/user/provider-config/opencode/catalog").catch(() => opencode);
    const providerConfigsPromise = loadProviderConfigsIntoFields();

    // Load provider config (may have saved URL/key)
    const pcfg = data.provider || {};
    const provider = pcfg.current || "opencode";
    document.getElementById("llmProvider").value = provider;
    toggleProviderConfig(provider);
    refreshSelect(document.getElementById("llmProvider"));
    populateGoogleModels(pcfg.google_models || [], pcfg.google_model || "");
    document.getElementById("googleApiKey").value = "";

    const [resolvedCatalog] = await Promise.all([catalogPromise, providerConfigsPromise]);
    opencode = resolvedCatalog;

    state.modelsByProvider = opencode.models_by_provider || {};
    const opencodeUrlField = document.getElementById("opencodeApiUrl");
    if (opencodeUrlField && (!opencodeUrlField.value || opencode.api_url !== data.opencode?.api_url)) {
      opencodeUrlField.value = opencode.api_url || opencodeUrlField.value || "";
    }

    const defaultModel = opencode.default_model || "";
    const defaultProvider = (opencode.providers || Object.keys(state.modelsByProvider))[0] || "";

    renderModelOptions(defaultProvider, defaultModel);
  } catch (err) {
    setStatus(`Failed to load defaults: ${String(err)}`);
  }

}

function populateGoogleModels(models, selectedModel) {
  const sel = document.getElementById("googleModel");
  if (!sel) return;
  sel.innerHTML = "";
  const list = Array.isArray(models) && models.length ? models : ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.5-pro"];
  list.forEach((m) => {
    const opt = document.createElement("option");
    opt.value = m;
    opt.textContent = m;
    sel.appendChild(opt);
  });
  if (selectedModel && list.includes(selectedModel)) sel.value = selectedModel;
  refreshSelect(sel);
}

async function fetchGoogleModels(apiKey) {
  if (!apiKey) return;
  setStatus("Google credentials are encrypted in your account and used by local execution.");
}

function toggleProviderConfig(provider) {
  const opencodeEl = document.getElementById("opencodeConfig");
  const googleEl = document.getElementById("googleConfig");
  if (opencodeEl) opencodeEl.classList.toggle("hidden", provider !== "opencode");
  if (googleEl) googleEl.classList.toggle("hidden", provider !== "google");
}

document.getElementById("llmProvider")?.addEventListener("change", (e) => {
  const val = e.target.value || document.getElementById("llmProvider").value;
  toggleProviderConfig(val);
  refreshSelect(document.getElementById("llmProvider"));
  if (val === "google") {
    const key = document.getElementById("googleApiKey").value.trim();
    if (key) fetchGoogleModels(key);
  }
});

async function saveProviderConfig(provider, config) {
  const saved = await fetchJSON(`/api/user/provider-config/${encodeURIComponent(provider)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ config }),
  });
  clearCache("/api/user/provider-config");
  return saved;
}

async function deleteAllCredentials() {
  const providers = ["opencode", "google_gemini"];
  for (const p of providers) {
    await fetchJSON(`/api/user/provider-config/${encodeURIComponent(p)}`, {
      method: "DELETE",
    });
  }
  clearCache("/api/user/provider-config");
}

// ── Studio Config Source Selector ───────────────────────────────────────────
let studioCurrentOrgId = null;  // null = personal

async function initStudioSourceSelector({ reloadInitialPersona = true } = {}) {
  const container = document.getElementById("studioSourceButtons");
  const labelEl = document.querySelector("#studioSourceSelector span");
  if (!container) return;
  let sources = [];
  try {
    const srcData = await fetchJSON("/api/config/sources");
    sources = srcData.sources || [];
  } catch {
    sources = [{ type: "personal", label: "My Config" }];
  }
  container.innerHTML = "";
  // Personal button
  const personalBtn = document.createElement("button");
  personalBtn.type = "button";
  personalBtn.className = "ghost-btn studio-source-btn active";
  personalBtn.dataset.source = "personal";
  personalBtn.textContent = "My Config";
  personalBtn.style.cssText = "font-size:0.78rem;padding:0.3rem 0.7rem;border-radius:var(--radius-sm);font-weight:500";
  container.appendChild(personalBtn);
  // Org buttons
  const orgSources = sources.filter(s => s.type === "org");
  orgSources.forEach(s => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ghost-btn studio-source-btn";
    btn.dataset.source = s.org_id;
    btn.textContent = s.org_name || s.label || s.org_id;
    btn.style.cssText = "font-size:0.78rem;padding:0.3rem 0.7rem;border-radius:var(--radius-sm);font-weight:500";
    container.appendChild(btn);
  });
  // Style active
  function styleButtons() {
    container.querySelectorAll(".studio-source-btn").forEach(b => {
      const isActive = b.dataset.source === (studioCurrentOrgId || "personal");
      b.style.background = isActive ? "var(--primary-muted)" : "transparent";
      b.style.color = isActive ? "var(--primary)" : "var(--muted)";
      b.style.borderColor = isActive ? "var(--primary)" : "var(--line)";
      b.classList.toggle("active", isActive);
    });
    if (labelEl) {
      const activeBtn = container.querySelector(".studio-source-btn.active");
      labelEl.textContent = activeBtn ? `Source: ${activeBtn.textContent}` : "Source:";
    }
  }
  styleButtons();
  // Reload persona seeds for the initial active source
  async function reloadPersonaSeeds() {
    try {
      const summary = await fetchJSON(studioCurrentOrgId
        ? `/api/config/persona-summary?org_id=${encodeURIComponent(studioCurrentOrgId)}`
        : "/api/config/persona-summary");
      const personas = summary?.personas;
      if (Array.isArray(personas) && personas.length) {
        const userPersonas = personas.map((persona) => ({
          number: Number(persona.number),
          name: String(persona.name || `Persona ${persona.number}`),
        })).filter((persona) => persona.number);
        if (userPersonas.length) {
          state.defaultData.personas = userPersonas;
          initPersonaState(userPersonas);
          renderPersonas();
        }
      }
    } catch {}
  }
  if (reloadInitialPersona) await reloadPersonaSeeds();
  // Click handler
  container.addEventListener("click", async (e) => {
    const btn = e.target.closest(".studio-source-btn");
    if (!btn) return;
    const src = btn.dataset.source;
    studioCurrentOrgId = src === "personal" ? null : src;
    styleButtons();
    structuredDeviceId = "";
    try {
      await Promise.all([reloadPersonaSeeds(), loadStructuredAssets({ silent: false })]);
      const suffix = studioCurrentOrgId ? `(org: ${btn.textContent})` : "(personal)";
      setStatus(`Config and local asset scope switched to ${btn.textContent} ${suffix}`);
    } catch (error) {
      setStatus(`Source changed, but local scope is unavailable: ${String(error)}`);
    }
  });
}

async function refreshOpenCodeModels() {
  const currentModel = document.getElementById("opencodeModel")?.value || "";
  const catalog = await fetchJSON("/api/user/provider-config/opencode/catalog", {
    noCache: true,
  });
  state.modelsByProvider = catalog.models_by_provider || {};
  const provider = (catalog.providers || Object.keys(state.modelsByProvider))[0] || "";
  renderModelOptions(provider, currentModel || catalog.default_model || "");
  return Object.values(state.modelsByProvider)
    .reduce((total, models) => total + (Array.isArray(models) ? models.length : 0), 0);
}

async function loadProviderConfigsIntoFields() {
  const saved = {};
  try {
    const configs = await fetchJSON("/api/user/provider-config");
    for (const p of ["opencode", "google_gemini"]) {
      const entry = (configs || []).find((item) => item.provider === p);
      const cfg = entry?.config || {};
      saved[p] = cfg;
      if (p === "opencode") {
        if (cfg.api_url) document.getElementById("opencodeApiUrl").value = cfg.api_url;
        if (cfg.has_secret) document.getElementById("opencodeApiKey").placeholder = "•••••••• (saved securely)";
        if (cfg.default_model) {
          const sel = document.getElementById("opencodeModel");
          if (sel) { sel.value = cfg.default_model; refreshSelect(sel); }
        }
      } else if (p === "google_gemini") {
        if (cfg.has_secret) document.getElementById("googleApiKey").placeholder = "•••••••• (saved securely)";
        if (cfg.default_model) {
          const sel = document.getElementById("googleModel");
          if (sel) { sel.value = cfg.default_model; refreshSelect(sel); }
        }
      }
    }
  } catch (err) {
    setStatus(`Provider config unavailable: ${String(err)}`);
  }
  return saved;
}

document.getElementById("saveGoogleKey")?.addEventListener("click", async () => {
  const key = document.getElementById("googleApiKey").value.trim();
  if (!key) { setStatus("Enter a Google API key first."); return; }
  try {
    const model = document.getElementById("googleModel").value;
    const saved = await saveProviderConfig("google_gemini", { api_key: key, default_model: model });
    document.getElementById("googleApiKey").value = "";
    document.getElementById("googleApiKey").placeholder = "•••••••• (saved)";
    setStatus(`Google API key encrypted and saved (fingerprint ${saved.config.key_fingerprint})`);
    fetchGoogleModels(key);
  } catch (err) { setStatus(`Failed: ${String(err)}`); }
});

document.getElementById("saveOpenCodeUrl")?.addEventListener("click", async () => {
  const url = document.getElementById("opencodeApiUrl").value.trim();
  if (!url) { setStatus("Enter an API URL first."); return; }
  try {
    await saveProviderConfig("opencode", { api_url: url });
    const modelCount = await refreshOpenCodeModels();
    setStatus(modelCount
      ? `OpenCode URL saved; loaded ${modelCount} model${modelCount === 1 ? "" : "s"}`
      : "OpenCode URL saved, but the provider returned no models");
  } catch (err) { setStatus(`Failed: ${String(err)}`); }
});

document.getElementById("saveOpenCodeKey")?.addEventListener("click", async () => {
  const key = document.getElementById("opencodeApiKey").value.trim();
  if (!key) { setStatus("Enter an API key first."); return; }
  try {
    const model = document.getElementById("opencodeModel")?.value || "";
    const saved = await saveProviderConfig("opencode", { api_key: key, default_model: model });
    document.getElementById("opencodeApiKey").value = "";
    document.getElementById("opencodeApiKey").placeholder = "•••••••• (saved)";
    const modelCount = await refreshOpenCodeModels();
    const fingerprint = saved.config.key_fingerprint;
    setStatus(modelCount
      ? `OpenCode API key saved (fingerprint ${fingerprint}); loaded ${modelCount} model${modelCount === 1 ? "" : "s"}`
      : `OpenCode API key saved (fingerprint ${fingerprint}), but the provider returned no models`);
  } catch (err) { setStatus(`Failed: ${String(err)}`); }
});

document.getElementById("deleteAllCredentialsBtn")?.addEventListener("click", async () => {
  if (!confirm("Delete all saved API credentials? This cannot be undone.")) return;
  try {
    await deleteAllCredentials();
    document.getElementById("googleApiKey").value = "";
    document.getElementById("googleApiKey").placeholder = "AIza...";
    document.getElementById("opencodeApiKey").value = "";
    document.getElementById("opencodeApiKey").placeholder = "sk-...";
    document.getElementById("opencodeApiUrl").value = "";
    setStatus("All API credentials deleted");
  } catch (err) { setStatus(`Failed: ${String(err)}`); }
});

const runBtn = document.getElementById("runBtn");

async function waitForRenderCopy(runId, copyJobId, displayBatch) {
  while (true) {
    const job = await fetchJSON(
      `/api/runs/${encodeURIComponent(runId)}/structured-copy/${encodeURIComponent(copyJobId)}`,
    );
    if (job.status === "completed") {
      setStatus(
        `Run ${displayBatch} provider response was processed and final prompts were assembled on Render. Final prompts will be delivered to the registered local agent when it is online.`,
      );
      return job;
    }
    if (job.status === "failed") {
      const detail = job.error || {};
      const provider = detail.provider || "provider";
      const model = detail.model ? ` ${detail.model}` : "";
      const httpStatus = detail.http_status ? ` (HTTP ${detail.http_status})` : "";
      const providerDetail = detail.error_detail ? ` — ${detail.error_detail}` : "";
      const traceDetail = detail.trace_persistence_error
        ? ` [trace storage failed: ${detail.trace_persistence_error}]`
        : "";
      throw new Error(
        `${provider}${model} failed${httpStatus}: ${detail.error_code || job.progress_code || "copy_generation_failed"}${providerDetail}${traceDetail}`,
      );
    }
    if (job.status === "canceled") {
      throw new Error("Structured copy generation was canceled.");
    }
    const progress = job.progress_code || "generating_copy";
    const progressText = progress === "waiting_for_local_provider"
      ? "waiting for an updated local agent"
      : progress === "calling_provider_local"
        ? "the local agent is calling the provider; Render will validate and assemble the response"
        : `${progress} on Render`;
    setStatus(`Run ${displayBatch}: ${progressText} (${copyJobId}).`);
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
}


async function runPipeline() {
  const selectedPersonas = getPersonaSelection();
  if (!selectedPersonas.length) {
    setStatus("Select at least one persona.");
    return;
  }
  const reuseBackgrounds = Boolean(document.getElementById("reuseBackgrounds")?.checked);
  const reuseVisualPatterns = Boolean(document.getElementById("reuseVisualPatterns")?.checked);
  const backgroundReuseRunId = document.getElementById("backgroundReuseRun")?.value || "";
  const visualPatternReuseRunId = document.getElementById("visualPatternReuseRun")?.value || "";
  if (reuseBackgrounds && !backgroundReuseRunId) {
    setStatus("Select a previous run/batch for background reuse.");
    return;
  }
  if (reuseVisualPatterns && !visualPatternReuseRunId) {
    setStatus("Select a previous run/batch for visual pattern reuse.");
    return;
  }

  const cfg = {
    selected_personas: selectedPersonas,
    language_mode: state.selectedLanguageMode,
    global_formats: [...state.selectedGlobalFormats],
    formats_by_persona: getFormatsByPersona(),
    visual_archetypes_by_format: state.selectedVisualArchetypesByFormat,
    multiplier: Math.max(1, Math.min(20, Number.parseInt(document.getElementById("adMultiplier")?.value || "1", 10) || 1)),
    batch_size: Math.max(1, Math.min(500, Number.parseInt(document.getElementById("batchSize")?.value || "10", 10) || 10)),
    share_background_across_personas: Boolean(document.getElementById("shareBackgroundAcrossPersonas")?.checked),
    reuse_backgrounds_from_run_id: reuseBackgrounds ? backgroundReuseRunId : "",
    reuse_visual_patterns_from_run_id: reuseVisualPatterns ? visualPatternReuseRunId : "",
    generate_images: false,
    server_type: state.currentServerType,
    provider: document.getElementById("llmProvider").value,
    opencode_api_url: document.getElementById("opencodeApiUrl").value.trim(),
    opencode_model: document.getElementById("llmProvider").value === "opencode" ? (modelSelectEl.value || "").trim() : "",
    google_model: document.getElementById("googleModel").value,
    hypothesis: getHypothesisConfig(),
  };

  setStatus("Allocating a Render copy job...");
  if (runBtn) {
    runBtn.disabled = true;
    runBtn.classList.add("is-loading");
  }
  const cancelBtn = document.getElementById("cancelRunBtn");
  if (cancelBtn) {
    cancelBtn.style.display = "inline-block";
    cancelBtn.disabled = false;
    cancelBtn.textContent = "Cancel";
  }
  try {
    // Clear any previous polling interval
    if (state.runPollInterval) {
      clearInterval(state.runPollInterval);
      state.runPollInterval = null;
    }
    const providerName = cfg.provider === "google" ? "google_gemini" : "opencode";
    const pendingSecret = providerName === "google_gemini"
      ? document.getElementById("googleApiKey").value.trim()
      : document.getElementById("opencodeApiKey").value.trim();
    await saveProviderConfig(providerName, {
      ...(providerName === "opencode" ? { api_url: cfg.opencode_api_url } : {}),
      ...(pendingSecret ? { api_key: pendingSecret } : {}),
      default_model: providerName === "google_gemini" ? cfg.google_model : cfg.opencode_model,
    });
    document.getElementById("googleApiKey").value = "";
    document.getElementById("opencodeApiKey").value = "";

    const owner = structuredOwner();
    const envelope = await fetchJSON("/api/runs/allocate-copy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        owner_type: owner.ownerType,
        owner_id: owner.ownerId,
      }),
    });
    activeStructuredRunId = envelope.run_id;
    const queued = await fetchJSON(`/api/runs/${encodeURIComponent(envelope.run_id)}/structured-copy`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        operation_id: `${envelope.run_id}-structured-copy`,
        settings: {
          selected_personas: cfg.selected_personas,
          global_formats: cfg.global_formats,
          formats_by_persona: cfg.formats_by_persona,
          multiplier: cfg.multiplier,
          language_mode: cfg.language_mode,
          provider: providerName,
          model: cfg.opencode_model || cfg.google_model,
          org_id: studioCurrentOrgId || "",
        },
      }),
    });
    setStatus(
      `Run ${envelope.display_batch} is preparing ad copy on Render; the provider request will use the local agent's network. Images are not generated in this step. Job: ${queued.copy_job_id}`,
    );
    await waitForRenderCopy(envelope.run_id, queued.copy_job_id, envelope.display_batch);
    invalidateRuns();
    await loadAndRenderRuns();
  } catch (err) {
    setStatus(`Failed: ${String(err)}`);
  } finally {
    if (cancelBtn) {
      cancelBtn.style.display = "none";
    }
    stopProgressPolling();
    if (runBtn) {
      runBtn.disabled = false;
      runBtn.classList.remove("is-loading");
    }
  }
}


document.getElementById("cancelRunBtn")?.addEventListener("click", async () => {
  const cancelBtn = document.getElementById("cancelRunBtn");
  if (!cancelBtn) return;
  cancelBtn.disabled = true;
  cancelBtn.textContent = "Cancelling...";
  try {
    if (state.runPollInterval) {
      clearInterval(state.runPollInterval);
      state.runPollInterval = null;
    }
    if (!activeStructuredRunId) {
      throw new Error("No active local run is available to cancel.");
    }
    await fetchJSON(`/api/runs/${encodeURIComponent(activeStructuredRunId)}/cancel`, {
      method: "POST",
    });
    setStatus("Cancelling pipeline... will stop after current ad and keep generated results.");
  } catch (err) {
    setStatus(`Cancel failed: ${String(err)}`);
    cancelBtn.disabled = false;
    cancelBtn.textContent = "Cancel";
  }
});


document.getElementById("runBtn")?.addEventListener("click", () => {
  runPipeline().catch((err) => setStatus(String(err)));
});

document.getElementById("closeProductDoc")?.addEventListener("click", () => {
  document.getElementById("productDocEditor")?.classList.add("hidden");
});

document.getElementById("saveProductDoc")?.addEventListener("click", async () => {
  const textarea = document.getElementById("productDocText");
  const saveBtn = document.getElementById("saveProductDoc");
  if (!textarea) return;
  if (saveBtn) saveBtn.disabled = true;
  try {
    await saveProductMasterDoc(textarea.value);
    setStatus("Product Master Doc saved to Render configuration.");
  } catch (err) {
    setStatus(`Failed to save product doc: ${String(err)}`);
  } finally {
    if (saveBtn) saveBtn.disabled = false;
  }
});

document.getElementById("reuseBackgrounds")?.addEventListener("change", (event) => {
  const select = document.getElementById("backgroundReuseRun");
  if (select) {
    select.disabled = !event.target.checked;
    if (!event.target.checked) select.value = "";
  }
  refreshSelect(select);
});

document.getElementById("reuseVisualPatterns")?.addEventListener("change", (event) => {
  const select = document.getElementById("visualPatternReuseRun");
  if (select) {
    select.disabled = !event.target.checked;
    if (!event.target.checked) select.value = "";
  }
  refreshSelect(select);
});

// Input Prompts
const inputPromptConfigKeys = {
  starting_prompt: "starting_prompt",
  "916_conversion": "conversion_916_prompt",
};

document.querySelectorAll(".card-input-prompts .input-prompt-card").forEach((card) => {
  card.addEventListener("click", () => {
    const promptType = card.dataset.promptType;
    const title = card.querySelector("strong").textContent;
    const configKey = inputPromptConfigKeys[promptType];
    if (!configKey) {
      appendLog(`Unknown input prompt type: ${promptType}`);
      return;
    }

    const orgId = studioCurrentOrgId;
    const fetchUrl = orgId
      ? `/api/config/effective?org_id=${encodeURIComponent(orgId)}`
      : "/api/config/effective";
    fetchJSON(fetchUrl)
      .then((data) => {
        const content = data?.config?.[configKey] || "";
        const isOrg = data?.owner_type === "org";
        const saveUrl = isOrg && orgId
          ? `/api/orgs/${orgId}/config`
          : "/api/user/config";
        const saveBodyFn = (text) => ({
          config: { [configKey]: text },
          expected_version: data?.version,
        });
        showPromptFullscreen(title, content, {
          saveUrl: saveUrl,
          saveMethod: "PUT",
          saveBody: saveBodyFn,
          postSave: () => { clearCache("/api/config/effective"); clearCache("/api/config/sources"); },
        });
      })
      .catch((err) => appendLog(`Failed to load ${title}: ${err}`));
  });
});

// Config Files
const configFilePathKeys = {
  "persona_seeds.json": "persona_seeds",
  "dashboard/backend/copy_architecture.json": "copy_architecture",
  "dashboard/backend/copy_prompt_templates.json": "copy_prompt_templates",
  "scripts/prompt_assembler_templates.json": "prompt_assembler_templates",
  "background_variant.json": "background_variant",
  "product_master_doc": "product_master_doc",
};

document.querySelectorAll(".card-files .input-prompt-card").forEach((card) => {
  card.addEventListener("click", () => {
    const filePath = card.dataset.filePath;
    const configKey = card.dataset.configKey || configFilePathKeys[filePath];
    const title = card.querySelector("strong").textContent;
    if (!configKey) {
      appendLog(`Unknown config file: ${filePath || title}`);
      return;
    }

    const orgId = studioCurrentOrgId;
    const fetchUrl = orgId
      ? `/api/config/effective?org_id=${encodeURIComponent(orgId)}`
      : "/api/config/effective";
    fetchJSON(fetchUrl)
      .then((data) => {
        const content = data?.config?.[configKey] || "";
        const isOrg = data?.owner_type === "org";
        const saveUrl = isOrg && orgId
          ? `/api/orgs/${orgId}/config`
          : "/api/user/config";
        const saveBodyFn = (text) => ({
          config: { [configKey]: text },
          expected_version: data?.version,
        });
        showPromptFullscreen(title, content, {
          saveUrl: saveUrl,
          saveMethod: "PUT",
          saveBody: saveBodyFn,
          postSave: () => { clearCache("/api/config/effective"); clearCache("/api/config/sources"); },
        });
      })
      .catch((err) => appendLog(`Failed to load ${title}: ${err}`));
  });
});

// File upload on selection
document.getElementById("productFile")?.addEventListener("change", async (event) => {
  const file = (event.target).files?.[0];
  if (!file) return;
  setStatus(`Uploading ${file.name}...`);
  try {
    const content = await file.text();
    await saveProductMasterDoc(content);
    const textarea = document.getElementById("productDocText");
    if (textarea) textarea.value = content;
    setStatus(`Uploaded ${file.name} to Render configuration`);
    event.target.value = "";
  } catch (err) {
    setStatus(`Upload failed: ${String(err)}`);
  }
});

// Standalone image upload (saves to input/images immediately, refreshes gallery)
document.getElementById("inputImageFiles")?.addEventListener("change", async (event) => {
  const files = [...(event.target.files || [])];
  if (!files.length) return;
  const clearExisting = !!document.getElementById("clearInputImages")?.checked;
  setStatus(`Uploading ${files.length} image${files.length === 1 ? "" : "s"}...`);
  try {
    await ensureStructuredLocal();
    if (clearExisting) {
      const existing = await localDataPlane.listAssets({ kind: "product_image", deviceId: structuredDeviceId });
      await Promise.all(existing.map((item) => localDataPlane.deleteAsset(item.resource_id, {
        deviceId: structuredDeviceId,
      })));
    }
    const saved = await localDataPlane.uploadAssets(files, {
      kind: "product_image",
      deviceId: structuredDeviceId,
    });
    await loadStructuredAssets({ silent: false });
    setStatus(`Uploaded ${saved.length} image${saved.length === 1 ? "" : "s"} to this device`);
    event.target.value = "";
    const clearEl = document.getElementById("clearInputImages");
    if (clearEl) clearEl.checked = false;
  } catch (err) {
    setStatus(`Image upload failed: ${String(err)}`);
  }
});

// Init
initTheme();
enhanceAllSelects();
showRunsSkeletons();

import { initAgentStatus } from "./agents.js";


initAuth().then(async () => {
  initAgentStatus();
  import("./config.js").then(mod => mod.renderConfigPanel());
  import("./auth.js").then(mod => {
    const user = mod.getAuthUser();
    const adminNav = document.getElementById("adminNav");
    const profileBadgeBtn = document.getElementById("profileBadgeBtn");
    if (user && user.is_super_admin) {
      if (adminNav) { adminNav.hidden = false; adminNav.style.display = ""; }
    }
    if (user && user.authenticated) {
      if (profileBadgeBtn) { profileBadgeBtn.hidden = false; profileBadgeBtn.style.display = ""; }
    }
    if (window.location.hash.startsWith("#admin/")) {
      const adminPanel = document.getElementById("adminPanel");
      const profilePanel = document.getElementById("profilePanel");
      const configPanel = document.getElementById("configPanel");
      if (adminPanel) adminPanel.hidden = false;
      if (profilePanel) profilePanel.classList.add("hidden");
      if (configPanel) configPanel.style.display = "none";
      import("./admin.js").then(mod => mod.renderAdminPanel());
    }
  });
  await Promise.all([initDefaults(), loadAndRenderRuns()]);
}).catch((err) => {
  setStatus(String(err));
});

function togglePanel(panelId) {
  const panel = document.getElementById(panelId);
  if (!panel) return;
  const isHidden = panel.classList.contains("hidden");
  // Hide all toggleable panels first
  ["profilePanel", "orgPanel", "configPanel"].forEach(id => {
    const p = document.getElementById(id);
    if (p && id !== panelId) p.classList.add("hidden");
  });
  if (panelId === "adminPanel") return; // admin uses its own toggle
  panel.classList.toggle("hidden");
}

document.getElementById("profileNavBtn")?.addEventListener("click", () => {
  window.location.href = "/profile.html";
});

document.getElementById("profileBadgeBtn")?.addEventListener("click", () => {
  window.location.href = "/profile.html";
});

document.getElementById("adminNav")?.addEventListener("click", () => {
  window.location.href = "/admin.html#admin/overview";
});

window.addEventListener("hashchange", () => {
  if (window.location.hash.startsWith("#admin/")) {
    const panel = document.getElementById("adminPanel");
    if (panel) {
      panel.hidden = false;
      const profilePanel = document.getElementById("profilePanel");
      const configPanel = document.getElementById("configPanel");
      if (profilePanel) profilePanel.classList.add("hidden");
      if (configPanel) configPanel.style.display = "none";
      import("./admin.js").then(mod => mod.renderAdminPanel());
    }
  } else {
    const panel = document.getElementById("adminPanel");
    if (panel && !panel.hidden) {
      panel.hidden = true;
      const profilePanel = document.getElementById("profilePanel");
      const configPanel = document.getElementById("configPanel");
      if (profilePanel) profilePanel.classList.add("hidden");
      if (configPanel) configPanel.style.display = "";
    }
  }
});

const storageInfo = document.getElementById("storageInfo");
if (storageInfo) {
  storageInfo.innerHTML = '<p class="hint">Content is stored on the paired local device. Render retains metadata only.</p>';
}
