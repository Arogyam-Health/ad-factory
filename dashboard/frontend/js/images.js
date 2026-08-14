import { appendLog } from "./ui.js";
import { fetchJSON, invalidateRuns } from "./api.js";
import { localDataPlane } from "./local-data-plane.js";

function isReferenceRun(run) {
  const flow = String(run?.flow_type || "");
  return flow === "reference" || flow === "reference_image";
}

function imageUrl(item, path) {
  const directUrl = item?.url || item?.local_path || item?.file_path || path;
  if (/^(?:blob:|http:\/\/(?:127\.0\.0\.1|localhost):)/i.test(directUrl)) {
    return directUrl;
  }
  return "";
}

export function buildImageGallery(run, imagesData) {
  const activeImageFiles = run.image_files || [];
  const queuedImageFiles = run.regeneration_queue_files || [];
  const hasActive = activeImageFiles.length > 0;
  const hasQueued = queuedImageFiles.length > 0;
  if (!hasActive && !hasQueued) return null;
  const imageItemsByPath = new Map((run.image_items || []).map((item) => [item.path, item]));
  const queueItemsByPath = new Map((run.regeneration_queue_items || []).map((item) => [item.path, item]));

  const imageByPath = new Map();
  if (Array.isArray(imagesData)) {
    imagesData.forEach((d) => {
      const key = d.file_path || d.local_path || "";
      if (key) imageByPath.set(key, d);
    });
  }

  const selectedItems = new Map();

  const gal = document.createElement("div");
  gal.className = "image-gallery";

  const galHeader = document.createElement("div");
  galHeader.className = "gallery-header";
  galHeader.innerHTML = `<strong>Generated Images (${activeImageFiles.length})</strong>`;
  gal.appendChild(galHeader);

  const regenBar = document.createElement("div");
  regenBar.className = "regeneration-toolbar";

  const selectedCount = document.createElement("span");
  selectedCount.className = "regeneration-count";
  selectedCount.textContent = "0 selected";

  const selectVisibleBtn = document.createElement("button");
  selectVisibleBtn.type = "button";
  selectVisibleBtn.className = "ghost-btn";
  selectVisibleBtn.textContent = "Select visible";

  const clearSelectionBtn = document.createElement("button");
  clearSelectionBtn.type = "button";
  clearSelectionBtn.className = "ghost-btn";
  clearSelectionBtn.textContent = "Clear";

  const markBtn = document.createElement("button");
  markBtn.type = "button";
  markBtn.className = "ghost-btn";
  markBtn.textContent = "Move to to_be_regenerated";

  const regenerateNowBtn = document.createElement("button");
  regenerateNowBtn.type = "button";
  regenerateNowBtn.className = "ghost-btn";
  regenerateNowBtn.textContent = "Regenerate selected";

  const reviseCommentedBtn = document.createElement("button");
  reviseCommentedBtn.type = "button";
  reviseCommentedBtn.className = "ghost-btn revise-commented-btn";
  reviseCommentedBtn.textContent = "Revise all commented";

  regenBar.append(selectedCount, selectVisibleBtn, clearSelectionBtn, markBtn, regenerateNowBtn, reviseCommentedBtn);
  gal.appendChild(regenBar);

  const allCount = activeImageFiles.length;
  const ar45 = activeImageFiles.filter((f) => f.includes("/4_5/")).length;
  const ar916 = activeImageFiles.filter((f) => f.includes("/9_16/")).length;

  if (ar45 > 0 && ar916 > 0) {
    const filterBar = document.createElement("div");
    filterBar.className = "gallery-filters";
    [{ label: `All (${allCount})`, value: "" }, { label: `4:5 (${ar45})`, value: "4_5" }, { label: `9:16 (${ar916})`, value: "9_16" }].forEach((f) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `gallery-filter ${f.value === "" ? "active" : ""}`;
      btn.textContent = f.label;
      btn.dataset.filter = f.value;
      btn.onclick = () => {
        filterBar.querySelectorAll(".gallery-filter").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        gal.querySelectorAll(".image-card").forEach((c) => {
          c.style.display = !f.value || c.dataset.aspect.includes(f.value) ? "" : "none";
        });
      };
      filterBar.appendChild(btn);
    });
    gal.appendChild(filterBar);
  }

  const grid = document.createElement("div");
  grid.className = "image-grid";

  function updateSelectedCount() {
    selectedCount.textContent = `${selectedItems.size} selected`;
    const hasSelection = selectedItems.size > 0;
    const allLocal = Array.from(selectedItems.values()).every((item) => item.is_local && item.output_id);
    markBtn.disabled = !hasSelection || !allLocal;
    regenerateNowBtn.disabled = !hasSelection || !allLocal;
    const title = allLocal ? "Local images use immutable output versions on this device." : "Legacy cloud content is unavailable.";
    markBtn.title = title;
    regenerateNowBtn.title = title;
  }

  async function archiveSelectedImages() {
    const imageFiles = Array.from(selectedItems.values()).map((item) => item.path);
    if (!imageFiles.length) {
      appendLog("Select at least one image.");
      return null;
    }
    const localItems = Array.from(selectedItems.values()).filter((item) => item.is_local);
    if (localItems.length !== selectedItems.size) {
      throw new Error("Selected legacy images are unavailable on the authoritative local device.");
    }
    await Promise.all(localItems.map((item) => localDataPlane.outputAction(
      item.output_id,
      "archive",
      run.device_id,
    )));
    return { moved: localItems.map((item) => ({ archived_file: item.path })) };
  }

  selectVisibleBtn.addEventListener("click", () => {
    grid.querySelectorAll(".image-card").forEach((card) => {
      if (card.style.display === "none") return;
      const checkbox = card.querySelector(".image-select-checkbox");
      if (!checkbox) return;
      checkbox.checked = true;
      selectedItems.set(card.dataset.path, imageItemsByPath.get(card.dataset.path) || { path: card.dataset.path });
      card.classList.add("selected-for-regeneration");
    });
    updateSelectedCount();
  });

  clearSelectionBtn.addEventListener("click", () => {
    selectedItems.clear();
    grid.querySelectorAll(".image-select-checkbox").forEach((checkbox) => { checkbox.checked = false; });
    grid.querySelectorAll(".image-card").forEach((card) => card.classList.remove("selected-for-regeneration"));
    updateSelectedCount();
  });

  reviseCommentedBtn.addEventListener("click", async () => {
    reviseCommentedBtn.disabled = true;
    try {
      const { submitAllRevisions } = await import("./image-comments.js");
      await submitAllRevisions(run.run_id);
    } finally {
      reviseCommentedBtn.disabled = false;
    }
  });

  markBtn.addEventListener("click", async () => {
    if (!selectedItems.size) { appendLog("Select at least one image."); return; }
    if (!confirm(`Move ${selectedItems.size} selected image(s) to to_be_regenerated?`)) return;
    markBtn.disabled = true;
    try {
      const data = await archiveSelectedImages();
      appendLog(`Moved ${data?.moved?.length || 0} image(s) to to_be_regenerated.`);
      invalidateRuns();
      import("./runs.js").then((m) => m.loadRuns());
    } catch (err) {
      appendLog(`Move error: ${String(err)}`);
    } finally {
      markBtn.disabled = false;
      updateSelectedCount();
    }
  });

  regenerateNowBtn.addEventListener("click", async () => {
    if (!selectedItems.size) { appendLog("Select at least one image."); return; }
    const engine = await showEngineSelector("selected");
    if (!engine) return;
    if (!confirm(`Move and regenerate ${selectedItems.size} selected image(s)?`)) return;
    regenerateNowBtn.disabled = true;
    markBtn.disabled = true;
    try {
      await archiveSelectedImages();
      const localSelection = Array.from(selectedItems.values()).filter((item) => item.is_local);
      await Promise.all(localSelection.map((item) => localDataPlane.outputAction(
        item.output_id,
        "regenerate",
        run.device_id,
        { engine },
      )));
      appendLog(`Queued ${localSelection.length} local output regeneration(s).`);
      selectedItems.clear();
      invalidateRuns();
      import("./runs.js").then((m) => m.refreshStructuredLocalOutputs());
    } catch (err) {
      appendLog(`Regeneration error: ${String(err)}`);
    } finally {
      regenerateNowBtn.disabled = false;
      updateSelectedCount();
    }
  });

  activeImageFiles.forEach((path) => {
    const imageItem = imageItemsByPath.get(path) || { path, prompt_file: "", regenerate_prompt_file: "", prompt_excerpt: "" };
    const dbItem = imageByPath.get(path);
    Object.assign(imageItem, dbItem);
    const card = document.createElement("div");
    card.className = "image-card";
    card.dataset.path = path;
    card.dataset.outputId = imageItem.output_id || "";
    if (imageItem.metadata?.regenerated) card.classList.add("image-card-regenerated");

    const is916 = imageItem.aspect_ratio === "9:16" || path.includes("/9_16/");
    const arLabel = is916 ? "9:16" : "4:5";
    card.dataset.aspect = is916 ? "9_16" : "4_5";
    card.dataset.aspectLabel = arLabel;

    const url = imageUrl(imageItem, path);

    const imgWrap = document.createElement("div");
    imgWrap.className = "image-wrap";

    const selectLabel = document.createElement("label");
    selectLabel.className = "image-select-label";
    const selectCheckbox = document.createElement("input");
    selectCheckbox.type = "checkbox";
    selectCheckbox.className = "image-select-checkbox";
    const selectText = document.createElement("span");
    selectText.textContent = "Regenerate";
    selectLabel.append(selectCheckbox, selectText);
    imgWrap.appendChild(selectLabel);

    const img = document.createElement("img");
    img.className = "gallery-thumb";
    img.loading = "lazy";
    img.src = url;
    img.alt = path.split("/").pop() || "generated image";
    img.title = arLabel;
    imgWrap.appendChild(img);

    const imgDeleteBtn = document.createElement("button");
    imgDeleteBtn.type = "button";
    imgDeleteBtn.className = "image-delete-btn";
    imgDeleteBtn.textContent = "\u2715";
    imgDeleteBtn.title = "Delete this image";
    imgWrap.appendChild(imgDeleteBtn);

    const imgDlBtn = document.createElement("button");
    imgDlBtn.type = "button";
    imgDlBtn.className = "image-download-btn";
    imgDlBtn.textContent = "\u2B07";
    imgDlBtn.title = "Download image with metadata";
    imgWrap.appendChild(imgDlBtn);

    const imgReplaceBtn = document.createElement("button");
    imgReplaceBtn.type = "button";
    imgReplaceBtn.className = "image-replace-btn";
    imgReplaceBtn.textContent = "↻";
    imgReplaceBtn.title = "Replace this image";
    imgWrap.appendChild(imgReplaceBtn);

    const replaceInput = document.createElement("input");
    replaceInput.type = "file";
    replaceInput.accept = "image/png,image/jpeg,image/webp";
    replaceInput.className = "hidden-file-input";
    card.appendChild(replaceInput);

    const badge = document.createElement("span");
    badge.className = `aspect-badge ${is916 ? "ar-916" : "ar-45"}`;
    badge.textContent = arLabel;
    card.appendChild(badge);
    card.appendChild(imgWrap);

    const fname = document.createElement("div");
    fname.className = "image-filename";
    fname.textContent = imageItem.display_name || path.split("/").pop() || path;
    fname.title = path;
    card.appendChild(fname);

    if (isReferenceRun(run)) {
      const meta = document.createElement("div");
      meta.className = "image-output-meta";
      const version = imageItem.resource_version || imageItem.version || "";
      meta.textContent = version ? `${arLabel} · output v${version}` : arLabel;
      card.appendChild(meta);
    } else {
    const promptBox = document.createElement("details");
    promptBox.className = "image-prompt-box";
    const promptSummary = document.createElement("summary");
    promptSummary.textContent = imageItem.prompt_file ? `Prompt: ${imageItem.prompt_file.split("/").pop()}` : "Prompt: not mapped";
    promptBox.appendChild(promptSummary);

    if (imageItem.mapping_status) {
      const mappingNote = document.createElement("div");
      mappingNote.className = "prompt-mapping-note";
      mappingNote.textContent = imageItem.mapping_status;
      promptBox.appendChild(mappingNote);
    }

    if (imageItem.prompt_file) {
      const promptName = document.createElement("div");
      promptName.className = "image-prompt-name";
      promptName.textContent = imageItem.prompt_file;
      promptBox.appendChild(promptName);
    }

    const promptFullscreenBtn = document.createElement("button");
    promptFullscreenBtn.type = "button";
    promptFullscreenBtn.className = "prompt-fullscreen-btn";
    promptFullscreenBtn.textContent = "Fullscreen prompt";
    promptFullscreenBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      const localPromptOptions = imageItem.is_local && imageItem.prompt_id ? {
        loadContent: () => localDataPlane.promptContent(imageItem.prompt_id, run.device_id, imageItem.prompt_version || imageItem.version),
        onSave: async (text) => {
          const prompts = await localDataPlane.listPrompts(run.run_id, run.device_id);
          const prompt = prompts.find((item) => item.prompt_id === imageItem.prompt_id);
          if (!prompt) throw new Error("Local prompt is unavailable");
          await localDataPlane.putPrompt(
            imageItem.prompt_id,
            run.run_id,
            text,
            prompt.resource_version,
            run.device_id,
          );
        },
      } : null;
      showPromptFullscreen(
        imageItem.prompt_file || "Prompt",
        imageItem.prompt_excerpt || "No prompt text available for this image.",
        localPromptOptions || {}
      );
    });
    promptBox.appendChild(promptFullscreenBtn);

    const promptPre = document.createElement("pre");
    promptPre.textContent = imageItem.prompt_excerpt || "No prompt text available for this image.";
    promptBox.appendChild(promptPre);
    card.appendChild(promptBox);
    }

    selectCheckbox.addEventListener("change", () => {
      if (selectCheckbox.checked) {
        selectedItems.set(path, imageItem);
        card.classList.add("selected-for-regeneration");
      } else {
        selectedItems.delete(path);
        card.classList.remove("selected-for-regeneration");
      }
      updateSelectedCount();
    });

    card.addEventListener("click", (event) => {
      if (event.target.closest("button") || event.target.closest("input") || event.target.closest("details") || event.target.closest("label")) return;
      window.open(url, "_blank");
    });

    imgDeleteBtn.addEventListener("click", async (event) => {
      event.stopPropagation();
      if (!confirm(`Delete image "${path.split("/").pop()}"?`)) return;
      imgDeleteBtn.disabled = true;
      try {
        if (!imageItem.is_local || !imageItem.output_id) {
          throw new Error("Legacy image content is unavailable on the authoritative local device.");
        }
        await localDataPlane.deleteOutput(imageItem.output_id, run.device_id);
        try {
          await fetchJSON(
            `/api/runs/${encodeURIComponent(run.run_id)}/images/${encodeURIComponent(imageItem.output_id)}`,
            { method: "DELETE" },
          );
        } catch {
          // Local deletion is authoritative; Mongo reconciliation follows via the outbox.
        }
        appendLog(`Deleted image: ${path.split("/").pop()}`);
        card.remove();
        invalidateRuns();
        import("./runs.js").then((module) => module.refreshStructuredLocalOutputs());
      } catch (err) {
        appendLog(`Delete error: ${String(err)}`);
        imgDeleteBtn.disabled = false;
      }
    });

    imgDlBtn.addEventListener("click", async (event) => {
      event.stopPropagation();
      if (!imageItem.is_local || !url) {
        appendLog("Legacy image download is unavailable; reconnect its authoritative local device.");
        return;
      }
      const a = document.createElement("a");
      a.href = url;
      a.download = "";
      document.body.appendChild(a);
      a.click();
      a.remove();
    });

    imgReplaceBtn.addEventListener("pointerdown", (event) => {
      event.stopPropagation();
    });

    imgReplaceBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      event.preventDefault();
      replaceInput.click();
    });

    replaceInput.addEventListener("change", async () => {
      const file = replaceInput.files?.[0];
      if (!file) return;
      imgReplaceBtn.disabled = true;
      try {
        if (!imageItem.is_local || !imageItem.output_id) {
          throw new Error("Legacy image replacement is unavailable on the authoritative local device.");
        }
        await localDataPlane.replaceOutput(imageItem.output_id, file, run.device_id);
        appendLog(`Replaced image: ${path.split("/").pop()}`);
        invalidateRuns();
        // The refresh owns every object URL, so it swaps in the new version without
        // revoking one that this card is still displaying.
        await import("./runs.js").then((module) => module.refreshStructuredLocalOutputs());
      } catch (err) {
        appendLog(`Replace error: ${String(err)}`);
      } finally {
        replaceInput.value = "";
        imgReplaceBtn.disabled = false;
      }
    });

    grid.appendChild(card);
  });

  gal.appendChild(grid);
  updateSelectedCount();

  if (hasQueued) {
    const queueSelectedItems = new Map();

    const queueSection = document.createElement("div");
    queueSection.className = "image-gallery regeneration-queue-section";

    const queueHeader = document.createElement("div");
    queueHeader.className = "gallery-header";
    queueHeader.innerHTML = `<strong>To Be Regenerated (${queuedImageFiles.length})</strong>`;
    queueSection.appendChild(queueHeader);

    const queueToolbar = document.createElement("div");
    queueToolbar.className = "regeneration-toolbar";

    const queueSelectedCount = document.createElement("span");
    queueSelectedCount.className = "regeneration-count";
    queueSelectedCount.textContent = "0 selected";

    const queueSelectAll = document.createElement("button");
    queueSelectAll.type = "button";
    queueSelectAll.className = "ghost-btn";
    queueSelectAll.textContent = "Select all";

    const queueClearBtn = document.createElement("button");
    queueClearBtn.type = "button";
    queueClearBtn.className = "ghost-btn";
    queueClearBtn.textContent = "Clear";

    const regenBtn = document.createElement("button");
    regenBtn.type = "button";
    regenBtn.className = "ghost-btn";
    regenBtn.textContent = "Regenerate selected";
    regenBtn.disabled = true;

    const restoreBtn = document.createElement("button");
    restoreBtn.type = "button";
    restoreBtn.className = "ghost-btn";
    restoreBtn.textContent = "Restore selected";
    restoreBtn.disabled = true;

    queueToolbar.append(queueSelectedCount, queueSelectAll, queueClearBtn, regenBtn, restoreBtn);
    queueSection.appendChild(queueToolbar);

    function updateQueueSelectedCount() {
      queueSelectedCount.textContent = `${queueSelectedItems.size} selected`;
      const allLocal = Array.from(queueSelectedItems.values())
        .every((item) => item.is_local && item.output_id);
      regenBtn.disabled = queueSelectedItems.size === 0 || !allLocal;
      restoreBtn.disabled = queueSelectedItems.size === 0 || !allLocal;
    }

    const queueGrid = document.createElement("div");
    queueGrid.className = "image-grid";

    queueSelectAll.addEventListener("click", () => {
      queueGrid.querySelectorAll(".image-card").forEach((card) => {
        const checkbox = card.querySelector(".queue-select-checkbox");
        if (checkbox && !card.style.display) {
          checkbox.checked = true;
          queueSelectedItems.set(card.dataset.path, queueItemsByPath.get(card.dataset.path) || { path: card.dataset.path });
          card.classList.add("selected-for-regeneration");
        }
      });
      updateQueueSelectedCount();
    });

    queueClearBtn.addEventListener("click", () => {
      queueSelectedItems.clear();
      queueGrid.querySelectorAll(".queue-select-checkbox").forEach((c) => { c.checked = false; });
      queueGrid.querySelectorAll(".image-card").forEach((card) => card.classList.remove("selected-for-regeneration"));
      updateQueueSelectedCount();
    });

    regenBtn.addEventListener("click", async () => {
      if (!queueSelectedItems.size) return;
      const engine = await showEngineSelector("4:5 & 9:16");
      if (!engine) return;
      regenBtn.disabled = true;
      try {
        const localItems = Array.from(queueSelectedItems.values()).filter((item) => item.is_local);
        if (localItems.length !== queueSelectedItems.size) {
          throw new Error("Legacy queued images are unavailable on the authoritative local device.");
        }
        await Promise.all(localItems.map((item) => localDataPlane.outputAction(
          item.output_id,
          "regenerate",
          run.device_id,
          { engine },
        )));
        appendLog(`Queued ${localItems.length} local image regeneration(s).`);
        invalidateRuns();
        import("./runs.js").then((m) => m.refreshStructuredLocalOutputs());
      } catch (err) {
        appendLog(`Queue regeneration error: ${String(err)}`);
      } finally {
        updateQueueSelectedCount();
      }
    });

    restoreBtn.addEventListener("click", async () => {
      if (!queueSelectedItems.size) return;
      if (!confirm(`Restore ${queueSelectedItems.size} image(s) from the regeneration queue back to their original location?`)) return;
      restoreBtn.disabled = true;
      try {
        const localItems = Array.from(queueSelectedItems.values()).filter((item) => item.is_local);
        if (localItems.length !== queueSelectedItems.size) {
          throw new Error("Legacy queued images are unavailable on the authoritative local device.");
        }
        await Promise.all(localItems.map((item) => localDataPlane.outputAction(
          item.output_id,
          "restore",
          run.device_id,
        )));
        appendLog(`Restored ${localItems.length} local image(s).`);
        invalidateRuns();
        import("./runs.js").then((m) => m.refreshStructuredLocalOutputs());
      } catch (err) {
        appendLog(`Restore error: ${String(err)}`);
      } finally {
        updateQueueSelectedCount();
      }
    });

    queuedImageFiles.forEach((path) => {
      const queueItem = queueItemsByPath.get(path) || { path, prompt_file: "", prompt_excerpt: "", is_queued: true };
      const dbItem = imageByPath.get(path);
      Object.assign(queueItem, dbItem);
      const card = document.createElement("div");
      card.className = "image-card regeneration-queue-card";
      card.dataset.path = path;
      card.dataset.outputId = queueItem.output_id || "";
      if (queueItem.metadata?.regenerated) card.classList.add("image-card-regenerated");

      const is916 = path.includes("/9_16/");
      const arLabel = is916 ? "9:16" : "4:5";
      card.dataset.aspect = is916 ? "9_16" : "4_5";

      const url = imageUrl(queueItem, path);

      const imgWrap = document.createElement("div");
      imgWrap.className = "image-wrap";

      const selectLabel = document.createElement("label");
      selectLabel.className = "image-select-label";
      const selectCheckbox = document.createElement("input");
      selectCheckbox.type = "checkbox";
      selectCheckbox.className = "queue-select-checkbox";
      const selectText = document.createElement("span");
      selectText.textContent = "Restore";
      selectLabel.append(selectCheckbox, selectText);
      imgWrap.appendChild(selectLabel);

      const img = document.createElement("img");
      img.className = "gallery-thumb";
      img.loading = "lazy";
      img.src = url;
      img.alt = path.split("/").pop() || "queued image";
      img.title = `${arLabel} - queued for regeneration`;
      imgWrap.appendChild(img);

      const queueDeleteBtn = document.createElement("button");
      queueDeleteBtn.type = "button";
      queueDeleteBtn.className = "image-delete-btn";
      queueDeleteBtn.textContent = "\u2715";
      queueDeleteBtn.title = "Delete this queued image";
      imgWrap.appendChild(queueDeleteBtn);

      const badge = document.createElement("span");
      badge.className = `aspect-badge ${is916 ? "ar-916" : "ar-45"}`;
      badge.textContent = arLabel;
      card.appendChild(badge);
      if (queueItem.metadata?.regenerated) {
        const regenDot = document.createElement("span");
        regenDot.className = "regenerated-dot";
        regenDot.title = "Regenerated image pending review";
        card.appendChild(regenDot);
      }
      card.appendChild(imgWrap);

      const fname = document.createElement("div");
      fname.className = "image-filename";
      fname.textContent = queueItem.display_name || path.split("/").pop() || path;
      fname.title = path;
      card.appendChild(fname);

      if (isReferenceRun(run)) {
        const meta = document.createElement("div");
        meta.className = "image-output-meta";
        const version = queueItem.resource_version || queueItem.version || "";
        meta.textContent = version ? `${arLabel} · output v${version}` : arLabel;
        card.appendChild(meta);
      } else {
      const promptBox = document.createElement("details");
      promptBox.className = "image-prompt-box";
      const promptSummary = document.createElement("summary");
      promptSummary.textContent = queueItem.prompt_file ? `Prompt: ${queueItem.prompt_file.split("/").pop()}` : "Prompt: not mapped";
      promptBox.appendChild(promptSummary);
      if (queueItem.mapping_status) {
        const mappingNote = document.createElement("div");
        mappingNote.className = "prompt-mapping-note";
        mappingNote.textContent = queueItem.mapping_status;
        promptBox.appendChild(mappingNote);
      }
      const promptFullscreenBtn = document.createElement("button");
      promptFullscreenBtn.type = "button";
      promptFullscreenBtn.className = "prompt-fullscreen-btn";
      promptFullscreenBtn.textContent = "Fullscreen prompt";
      promptFullscreenBtn.addEventListener("click", (event) => {
        event.stopPropagation();
        const localPromptOptions = queueItem.is_local && queueItem.prompt_id ? {
          loadContent: () => localDataPlane.promptContent(queueItem.prompt_id, run.device_id, queueItem.prompt_version || queueItem.version),
          onSave: async (text) => {
            const prompts = await localDataPlane.listPrompts(run.run_id, run.device_id);
            const prompt = prompts.find((item) => item.prompt_id === queueItem.prompt_id);
            if (!prompt) throw new Error("Local prompt is unavailable");
            await localDataPlane.putPrompt(
              queueItem.prompt_id,
              run.run_id,
              text,
              prompt.resource_version,
              run.device_id,
            );
          },
        } : null;
        showPromptFullscreen(
          queueItem.prompt_file || "Prompt",
          queueItem.prompt_excerpt || "No prompt text available.",
          localPromptOptions || {}
        );
      });
      promptBox.appendChild(promptFullscreenBtn);
      const promptPre = document.createElement("pre");
      promptPre.textContent = queueItem.prompt_excerpt || "No prompt text available.";
      promptBox.appendChild(promptPre);
      card.appendChild(promptBox);
      }

      selectCheckbox.addEventListener("change", () => {
        if (selectCheckbox.checked) {
          queueSelectedItems.set(path, queueItem);
          card.classList.add("selected-for-regeneration");
        } else {
          queueSelectedItems.delete(path);
          card.classList.remove("selected-for-regeneration");
        }
        updateQueueSelectedCount();
      });

      queueDeleteBtn.addEventListener("click", async (event) => {
        event.stopPropagation();
        if (!confirm(`Delete queued image "${path.split("/").pop()}"?`)) return;
        queueDeleteBtn.disabled = true;
        try {
          if (!queueItem.is_local || !queueItem.output_id) {
            throw new Error("Legacy queued image is unavailable on the authoritative local device.");
          }
          await localDataPlane.deleteOutput(queueItem.output_id, run.device_id);
          queueSelectedItems.delete(path);
          appendLog(`Deleted queued image: ${path.split("/").pop()}`);
          card.remove();
          updateQueueSelectedCount();
          invalidateRuns();
        } catch (err) {
          appendLog(`Delete queued image error: ${String(err)}`);
          queueDeleteBtn.disabled = false;
        }
      });

      card.addEventListener("click", (event) => {
        if (event.target.closest("button") || event.target.closest("input") || event.target.closest("details") || event.target.closest("label")) return;
        window.open(url, "_blank");
      });

      queueGrid.appendChild(card);
    });

    queueSection.appendChild(queueGrid);
    gal.appendChild(queueSection);
  }

  return gal;
}

export function showPromptFullscreen(title, promptText, opts = {}) {
  const { fetchUrl, saveUrl, saveBody, onSave, loadContent: loadLocalContent } = opts;
  const overlay = document.createElement("div");
  overlay.className = "prompt-fullscreen-overlay";
  overlay.innerHTML = `
    <div class="prompt-fullscreen-modal">
      <div class="prompt-fullscreen-header">
        <strong></strong>
        <div class="prompt-fullscreen-actions">
          <button type="button" class="prompt-fullscreen-btn save-btn">Save</button>
          <button type="button" class="prompt-fullscreen-btn cancel-btn">Cancel</button>
          <button type="button" class="prompt-fullscreen-close">X</button>
        </div>
      </div>
      <textarea class="prompt-fullscreen-textarea"></textarea>
    </div>
  `;
  const strong = overlay.querySelector("strong");
  const textarea = overlay.querySelector("textarea");
  const saveBtn = overlay.querySelector(".save-btn");
  const cancelBtn = overlay.querySelector(".cancel-btn");
  const closeBtn = overlay.querySelector(".prompt-fullscreen-close");
  strong.textContent = title;
  document.body.appendChild(overlay);

  async function loadContent() {
    if (loadLocalContent) {
      try {
        textarea.value = await loadLocalContent();
      } catch (err) {
        appendLog(`Failed to load content: ${String(err)}`);
      }
    } else if (fetchUrl) {
      try {
        const data = await fetchJSON(fetchUrl);
        textarea.value = data.content || "";
      } catch (err) {
        appendLog(`Failed to load content: ${String(err)}`);
      }
    } else {
      textarea.value = promptText;
    }
  }

  loadContent().then(() => textarea.focus());

  saveBtn.addEventListener("click", async () => {
    if (!saveUrl && !onSave) return;
    saveBtn.disabled = true;
    try {
      if (onSave) {
        await onSave(textarea.value);
      } else {
        const body = saveBody ? saveBody(textarea.value) : { content: textarea.value };
        const method = opts.saveMethod || "POST";
        await fetchJSON(saveUrl, {
          method: method,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      }
      appendLog("Saved.");
      if (opts.postSave) opts.postSave();
      close();
    } catch (err) {
      appendLog(`Save error: ${String(err)}`);
    } finally {
      saveBtn.disabled = false;
    }
  });

  const close = () => overlay.remove();
  cancelBtn.addEventListener("click", close);
  closeBtn.addEventListener("click", close);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) close();
  });
  const onKey = (event) => {
    if (event.key === "Escape") {
      close();
      document.removeEventListener("keydown", onKey);
    }
  };
  document.addEventListener("keydown", onKey);
}

function showEngineSelector(aspectLabel = "4:5") {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "engine-selector-overlay";
    overlay.innerHTML = `
      <div class="engine-selector-modal">
        <h3>Select Image Generation Engine</h3>
        <p>Choose which engine to use for regenerating ${aspectLabel} images:</p>
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
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) {
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
  });
}
