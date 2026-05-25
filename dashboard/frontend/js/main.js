import { state, getPersonaSelection, getFormatsByPersona, getHypothesisConfig, loadDefaults } from "./state.js";
import { setStatus, setSelectOptions, appendLog } from "./ui.js";
import { renderPersonas, showPersonaSkeletons, renderGlobalFormats, renderLanguageModes, renderFormatPatterns } from "./personas.js";
import { renderHypothesisUI } from "./hypothesis.js";
import { loadRuns as loadAndRenderRuns, showRunsSkeletons } from "./runs.js";
import { showPromptFullscreen } from "./images.js";
import { stopProgressPolling } from "./chrome.js";
import { initTheme } from "./theme.js";
import { fetchJSON, invalidateRuns } from "./api.js";
import { enhanceAllSelects, refreshSelect } from "./custom-select.js";

const providerSelectEl = document.getElementById("opencodeProvider");
const modelSelectEl = document.getElementById("opencodeModel");
const defaultsInfoEl = document.getElementById("defaultsInfo");

function renderInputImages(images = []) {
  const gallery = document.getElementById("inputImageGallery");
  if (!gallery) return;
  gallery.innerHTML = "";
  if (!images.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = "No stored input images yet.";
    gallery.appendChild(empty);
    return;
  }
  images.forEach((path) => {
    const card = document.createElement("div");
    card.className = "image-card input-image-card";
    card.dataset.aspect = "INPUT_IMAGE";

    const cleanPath = path.replace(/^input\//, "");
    const url = `/input/${cleanPath}`;
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
      window.open(url, "_blank");
    });

    downloadBtn.addEventListener("click", (event) => {
      event.stopPropagation();
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
        await fetchJSON("/api/input-images", {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path }),
        });
        card.remove();
      } catch (err) {
        setStatus(`Failed to delete input image: ${String(err)}`);
        deleteBtn.disabled = false;
      }
    });

    gallery.appendChild(card);
  });
}

function renderProductDocInfo(productDoc) {
  const el = document.getElementById("productDocInfo");
  if (!el) return;
  const doc = productDoc || {};
  const size = Number(doc.size_bytes || 0);
  el.innerHTML = `
    <div class="product-doc-card">
      <strong>Product doc in use</strong>
      <span>${doc.name || "product master doc.txt"}</span>
      <code>${doc.path || "input/docs/product master doc.txt"}</code>
      <small>${doc.exists ? `${(size / 1024).toFixed(1)} KB` : "Missing"}</small>
      <div class="product-doc-actions">
        <button id="openProductDoc" class="ghost-btn" type="button">Open</button>
        <a class="ghost-btn product-doc-download" href="/${doc.path || "input/docs/product master doc.txt"}" download>Download</a>
      </div>
    </div>
  `;
  document.getElementById("openProductDoc")?.addEventListener("click", () => {
    fetchJSON("/api/product-doc").then((doc) => {
      showPromptFullscreen(
        doc.name || "Product Master Doc",
        doc.content || "",
        {
          fetchUrl: "/api/product-doc",
          saveUrl: "/api/product-doc",
          saveBody: (text) => ({ content: text }),
        }
      );
    }).catch((err) => setStatus(`Failed to load product doc: ${String(err)}`));
  });
}

async function openProductDocEditor() {
  const editor = document.getElementById("productDocEditor");
  const textarea = document.getElementById("productDocText");
  if (!editor || !textarea) return;
  const doc = await fetchJSON("/api/product-doc");
  textarea.value = doc.content || "";
  editor.classList.remove("hidden");
  textarea.focus();
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
    renderInputImages(data.input_images || []);
    renderProductDocInfo(data.product_doc);

    const imageCount = (data.input_images || []).length;
    defaultsInfoEl.textContent = `Using defaults: product=${data.default_files.product_info}, mechanism=${data.default_files.playbook}, input/images=${imageCount} file(s)`;

    const opencode = data.opencode || {};
    state.modelsByProvider = opencode.models_by_provider || {};
    document.getElementById("opencodeApiUrl").value = opencode.api_url || "http://127.0.0.1:4090";

    const providers = opencode.providers || Object.keys(state.modelsByProvider);
    const defaultModel = opencode.default_model || "";
    const defaultProvider = defaultModel.includes("/") ? defaultModel.split("/", 1)[0] : (providers[0] || "");

    setSelectOptions(providerSelectEl, providers.length ? providers : [""], defaultProvider);
    renderModelOptions(defaultProvider, defaultModel);
  } catch (err) {
    setStatus(`Failed to load defaults: ${String(err)}`);
  }
}

const runBtn = document.getElementById("runBtn");

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
    opencode_api_url: document.getElementById("opencodeApiUrl").value.trim(),
    opencode_api_key: document.getElementById("opencodeApiKey").value.trim(),
    opencode_provider: (providerSelectEl.value || "").trim(),
    opencode_model: (modelSelectEl.value || "").trim(),
    hypothesis: getHypothesisConfig(),
  };

  const form = new FormData();
  form.append("config", JSON.stringify(cfg));

  const uploads = [
    ["product_info_file", document.getElementById("productFile")],
    ["image_source_file", document.getElementById("imageSourcesFile")],
  ];
  uploads.forEach(([name, input]) => {
    if (input instanceof HTMLInputElement && input.files && input.files[0]) {
      form.append(name, input.files[0]);
    }
  });

  const inputImageFilesEl = document.getElementById("inputImageFiles");
  const clearInputImagesEl = document.getElementById("clearInputImages");
  if (inputImageFilesEl?.files?.length) {
    [...inputImageFilesEl.files].forEach((file) => form.append("input_image_files", file));
  }
  form.append("clear_input_images", clearInputImagesEl?.checked ? "true" : "false");

  setStatus("Running pipeline... this can take time.");
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
    const data = await fetchJSON("/api/runs/execute", { method: "POST", body: form });
    if (cancelBtn) {
      cancelBtn.style.display = "none";
    }
    const fallbackLine = data.copy_generation_failures
      ? `\nCopy fallbacks: ${data.copy_generation_failures} failed ad(s); log: ${data.copy_fallback_log || "run logs"}`
      : "";
    const warningLine = data.copy_generation_warnings
      ? `\nCopy warnings: ${data.copy_generation_warnings}; log: ${data.copy_warning_log || "run logs"}`
      : "";
    const sessionLine = data.copy_session_fallback
      ? `\nSession fallback: product doc attached per request; log: ${data.copy_session_log || "run logs"}`
      : "";
    const noteLine = Array.isArray(data.copy_generation_notes) && data.copy_generation_notes.length
      ? `\nNotes:\n${data.copy_generation_notes.map((note) => `- ${note}`).join("\n")}`
      : "";
    const providerLine = data.opencode_provider ? `\nProvider: ${data.opencode_provider}` : "";
    const modelLine = data.opencode_model ? `\nModel: ${data.opencode_model}` : "";
    setStatus(`Done\nRun: ${data.run_id}\nBatch: ${data.batch}\nLLM mode: ${data.llm_mode}${providerLine}${modelLine}\nCopy source: ${data.copy_source || data.llm_mode}${fallbackLine}${warningLine}${sessionLine}${noteLine}\nPrompts: ${data.prompt_files.length}\nImages: ${data.image_files.length}`);
    fetchJSON("/api/defaults")
      .then((freshDefaults) => renderInputImages(freshDefaults.input_images || []))
      .catch(() => {});
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
    await fetchJSON("/api/runs/cancel-current", { method: "POST" });
    setStatus("Cancelling pipeline... will stop after current ad and keep generated results.");
  } catch (err) {
    setStatus(`Cancel failed: ${String(err)}`);
    cancelBtn.disabled = false;
    cancelBtn.textContent = "Cancel";
  }
});

document.getElementById("serverType")?.addEventListener("change", () => {
  state.currentServerType = "opencode";
  document.getElementById("opencodeApiUrl").value = "http://127.0.0.1:4090";
  initDefaults();
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
    const saved = await fetchJSON("/api/product-doc", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: textarea.value }),
    });
    renderProductDocInfo(saved);
    setStatus("Product doc saved.");
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

if (providerSelectEl) {
  providerSelectEl.addEventListener("change", () => {
    renderModelOptions(providerSelectEl.value, "");
  });
}

// Input Prompts
document.querySelectorAll(".card-input-prompts .input-prompt-card").forEach((card) => {
  card.addEventListener("click", () => {
    const promptType = card.dataset.promptType;
    const title = card.querySelector("strong").textContent;
    fetch(`/api/input-prompt?prompt_type=${encodeURIComponent(promptType)}`)
      .then((r) => r.json())
      .then((data) => {
        showPromptFullscreen(title, data.content || "", {
          fetchUrl: `/api/input-prompt?prompt_type=${encodeURIComponent(promptType)}`,
          saveUrl: "/api/input-prompt",
          saveBody: (text) => ({ prompt_type: promptType, content: text }),
        });
      })
      .catch((err) => appendLog(`Failed to load ${title}: ${err}`));
  });
});

document.querySelectorAll(".card-files .input-prompt-card").forEach((card) => {
  card.addEventListener("click", () => {
    const filePath = card.dataset.filePath;
    const title = card.querySelector("strong").textContent;
    fetch(`/api/prompt-file-content?prompt_path=${encodeURIComponent(filePath)}`)
      .then((r) => r.json())
      .then((data) => {
        showPromptFullscreen(title, data.content || "", {
          fetchUrl: `/api/prompt-file-content?prompt_path=${encodeURIComponent(filePath)}`,
          saveUrl: "/api/prompt-file-content",
          saveBody: (text) => ({ prompt_path: filePath, content: text }),
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
    const text = await file.text();
    await fetchJSON("/api/product-doc", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: text }),
    });
    const doc = await fetchJSON("/api/product-doc");
    renderProductDocInfo(doc);
    setStatus(`Uploaded ${file.name}`);
  } catch (err) {
    setStatus(`Upload failed: ${String(err)}`);
  }
});

// Init
initTheme();
enhanceAllSelects();
showRunsSkeletons();
Promise.all([initDefaults(), loadAndRenderRuns()]).catch((err) => setStatus(String(err)));
