import { appendLog } from "./ui.js";
import { fetchJSON, invalidateRuns } from "./api.js";
import { localDataPlane } from "./local-data-plane.js";
import { getAuthUser } from "./auth.js";
import {
  exactOnImageCopyLines,
  replaceExactOnImageCopy,
} from "./prompt-copy.js";

const expandedPromptRunIds = new Set();

async function loadLocalPrompts(run) {
  await localDataPlane.ensurePaired({
    ownerType: run.owner_type || "user",
    ownerId: run.owner_id || getAuthUser()?.user_id,
    deviceId: run.device_id,
    agentId: run.agent_id,
  });
  const items = await localDataPlane.listPrompts(run.run_id, run.device_id);
  return {
    prompts: await Promise.all(items.map(async (item) => {
      const content = await localDataPlane.promptContent(item.prompt_id, run.device_id);
      return {
        ...item,
        prompt_file: item.prompt_file || `${item.display_name || item.prompt_id}.txt`,
        full_content: content,
        copy_lines: exactOnImageCopyLines(content),
      };
    })),
  };
}

export function buildPromptEditor(run, container, promptsData) {
  const promptsByPath = new Map();
  if (Array.isArray(promptsData)) {
    promptsData.forEach((d) => {
      const key = d.file_path || "";
      if (key) promptsByPath.set(key, d);
    });
  }

  const loadBtn = document.createElement("button");
  loadBtn.type = "button";
  loadBtn.className = "ghost-btn";
  loadBtn.textContent = "Load editable copy";
  const loadHint = document.createElement("div");
  loadHint.className = "hint";
  loadHint.textContent = "Lazy loaded to keep dashboard fast.";
  container.append(loadBtn, loadHint);

  loadBtn.onclick = () => {
    loadBtn.disabled = true;
    loadHint.textContent = "Loading editable on-image copy...";
    loadLocalPrompts(run)
      .then((data) => {
        const prompts = data.prompts || [];
        if (!prompts.length) {
          expandedPromptRunIds.delete(run.run_id);
          loadHint.textContent = "No prompts found for this run.";
          loadBtn.disabled = false;
          return;
        }
        expandedPromptRunIds.add(run.run_id);
        loadBtn.remove();
        loadHint.remove();

        const controls = document.createElement("div");
        controls.className = "prompt-controls";

        const selectAllBtn = mkBtn("Select all");
        const clearBtn = mkBtn("Clear selection");
        const exportCopyBtn = mkBtn("EXPORT ON-IMAGE COPY");
        const importCopyBtn = mkBtn("IMPORT EXCEL & UPDATE PROMPTS");
        const generate45Btn = mkBtn("Generate 4:5 (Gemini/ChatGPT)");
        const generate916Btn = mkBtn("Generate 9:16 (Gemini/ChatGPT) from 4:5 images");

        const importFileEl = document.createElement("input");
        importFileEl.type = "file";
        importFileEl.accept = ".xlsx";
        importFileEl.style.display = "none";

        exportCopyBtn.onclick = async () => {
          exportCopyBtn.disabled = true;
          try {
            appendLog(`Exporting EXACT ON-IMAGE COPY to XLSX for ${run.run_id}...`);
            const blob = await localDataPlane.exportPrompts(run.run_id, run.device_id);
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = `on-image-copy-${run.run_id}.xlsx`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
            appendLog(`Export ready: ${run.run_id}`);
          } catch (err) {
            appendLog(String(err));
          } finally {
            exportCopyBtn.disabled = false;
          }
        };

        importCopyBtn.onclick = () => importFileEl.click();
        importFileEl.onchange = async () => {
          const file = importFileEl.files && importFileEl.files[0];
          if (!file) return;
          const previewEl = document.createElement("pre");
          previewEl.className = "status";
          previewEl.style.marginTop = "10px";
          importCopyBtn.disabled = true;
          try {
            appendLog("Importing XLSX to the authoritative local device...");
            const data = await localDataPlane.importPrompts(run.run_id, file, run.device_id);
            previewEl.textContent = `Updated ${data.updated || 0} immutable prompt version(s).`;
            container.appendChild(previewEl);
            appendLog(`Import applied. Updated ${data.updated || 0} prompt(s).`);
            import("./runs.js").then((m) => m.loadRuns());
          } catch (err) {
            appendLog(String(err));
          } finally {
            importCopyBtn.disabled = false;
            importFileEl.value = "";
          }
        };

        controls.append(selectAllBtn, clearBtn, exportCopyBtn, importCopyBtn, generate45Btn, generate916Btn);
        container.appendChild(controls);

        const editorList = document.createElement("div");
        editorList.className = "prompt-editor-list";
        container.appendChild(editorList);

        const items = [];
        prompts.forEach((prompt) => {
          const card = buildPromptCard(prompt, run, items, promptsByPath);
          editorList.appendChild(card);
        });

        selectAllBtn.onclick = () => items.forEach((it) => { it.checkbox.checked = true; });
        clearBtn.onclick = () => items.forEach((it) => { it.checkbox.checked = false; });

        generate45Btn.onclick = async () => {
          const selected = items.filter((it) => it.checkbox.checked && it.promptId).map((it) => it.promptId);
          if (!selected.length) { appendLog("Select at least one prompt."); return; }

          const engine = await showEngineSelector("4:5");
          if (!engine) return;

          generate45Btn.disabled = true;
          const engineLabel = engine === "chatgpt" ? "ChatGPT" : "Gemini";
          appendLog(`Generating 4:5 images in ${engineLabel} for ${selected.length} selected prompt(s) from ${run.run_id}...`);
          try {
            const data = await fetchJSON(`/api/runs/${encodeURIComponent(run.run_id)}/image-generation`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                operation_id: `prompt-images-${run.run_id}-45-${Date.now()}`,
                engine,
                mode: "45",
              }),
            });
            appendLog(`Queued 4:5 generation in ${engineLabel}: ${data.job_id}`);
            import("./runs.js").then((m) => m.loadRuns());
          } catch (err) {
            appendLog(String(err));
          } finally {
            generate45Btn.disabled = false;
          }
        };

        generate916Btn.onclick = async () => {
          const selected = items.filter((it) => it.checkbox.checked && it.promptId).map((it) => it.promptId);
          if (!selected.length) { appendLog("Select at least one prompt."); return; }
          const engine = await showEngineSelector("9:16");
          if (!engine) return;

          generate916Btn.disabled = true;
          const engineLabel = engine === "chatgpt" ? "ChatGPT" : "Gemini";
          appendLog(`Generating 9:16 in ${engineLabel} from selected 4:5 image references for ${selected.length} prompt(s)...`);
          try {
            const data = await fetchJSON(`/api/runs/${encodeURIComponent(run.run_id)}/image-generation`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                operation_id: `prompt-images-${run.run_id}-916-${Date.now()}`,
                engine,
                mode: "916",
              }),
            });
            appendLog(`Queued 9:16 generation in ${engineLabel}: ${data.job_id}`);
            import("./runs.js").then((m) => m.loadRuns());
          } catch (err) {
            appendLog(String(err));
          } finally {
            generate916Btn.disabled = false;
          }
        };
      })
      .catch((err) => {
        const message = `Could not load editable copy: ${String(err)}`;
        if (loadHint.isConnected) {
          loadHint.textContent = message;
          loadBtn.disabled = false;
        } else {
          const errorHint = document.createElement("div");
          errorHint.className = "hint";
          errorHint.textContent = message;
          container.appendChild(errorHint);
        }
      });
  };
  if (expandedPromptRunIds.has(run.run_id)) {
    queueMicrotask(() => loadBtn.click());
  }
}

function mkBtn(text) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = text;
  return b;
}

function buildPromptCard(prompt, run, items, promptsByPath) {
  const card = document.createElement("div");
  card.className = "prompt-editor";

  const top = document.createElement("div");
  top.className = "prompt-editor-top";
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = true;

  const link = document.createElement("a");
  link.href = "#";
  link.textContent = prompt.prompt_file;
  link.onclick = async (event) => {
    event.preventDefault();
    try {
      const content = await localDataPlane.promptContent(prompt.prompt_id, run.device_id);
      const url = URL.createObjectURL(new Blob([content], { type: "text/plain" }));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = prompt.prompt_file || `${prompt.prompt_id}.txt`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      appendLog(`Prompt download failed: ${String(error)}`);
    }
  };

  const inlineControls = document.createElement("span");
  inlineControls.className = "prompt-inline-controls";
  const editBtn = document.createElement("button");
  editBtn.type = "button";
  editBtn.className = "ghost-btn prompt-edit-btn";
  editBtn.textContent = "\u270f\ufe0f";
  editBtn.title = "Edit prompt text";
  const deleteBtn = document.createElement("button");
  deleteBtn.type = "button";
  deleteBtn.className = "ghost-btn prompt-delete-btn";
  deleteBtn.textContent = "\u{1F5D1}\uFE0F";
  deleteBtn.title = "Delete prompt file";
  inlineControls.append(editBtn, deleteBtn);
  top.append(checkbox, link, inlineControls);
  card.appendChild(top);

  const linesDisplay = document.createElement("div");
  linesDisplay.className = "prompt-lines-display";
  const copyLines = prompt.copy_lines || [];
  if (!copyLines.length) {
    const empty = document.createElement("div");
    empty.className = "hint";
    empty.textContent = "No editable EXACT ON-IMAGE COPY block found in this prompt.";
    linesDisplay.appendChild(empty);
  } else {
    copyLines.forEach((line) => {
      const row = document.createElement("div");
      row.className = "prompt-line-display";
      const label = document.createElement("span");
      label.className = "prompt-line-label";
      label.textContent = line.label + ": ";
      const value = document.createElement("span");
      value.className = "prompt-line-value";
      value.textContent = line.value || "(empty)";
      row.append(label, value);
      linesDisplay.appendChild(row);
    });
  }
  card.appendChild(linesDisplay);

  const editForm = document.createElement("div");
  editForm.className = "prompt-edit-form";
  editForm.style.display = "none";
  copyLines.forEach((line) => {
    const row = document.createElement("div");
    row.className = "prompt-line";
    const label = document.createElement("label");
    label.textContent = line.label;
    const textarea = document.createElement("textarea");
    textarea.value = line.value || "";
    textarea.rows = 2;
    row.append(label, textarea);
    editForm.appendChild(row);
  });

  const editActions = document.createElement("div");
  editActions.className = "prompt-edit-actions";
  editActions.style.display = "none";
  const saveBtn = mkBtn("\ud83d\udcbe Save");
  saveBtn.className = "ghost-btn";
  const cancelBtn = mkBtn("\u2715 Cancel");
  cancelBtn.className = "ghost-btn";
  editActions.append(saveBtn, cancelBtn);
  card.appendChild(editActions);
  card.appendChild(editForm);

  let editing = false;
  const hasEditableCopy = copyLines.length > 0;
  editBtn.disabled = !hasEditableCopy;
  editBtn.title = hasEditableCopy ? "Edit prompt text" : "No editable copy block found";

  editBtn.onclick = () => {
    if (!hasEditableCopy) return;
    editing = true;
    linesDisplay.style.display = "none";
    editForm.style.display = "";
    editActions.style.display = "";
    top.classList.add("editing");
  };
  cancelBtn.onclick = () => {
    editing = false;
    linesDisplay.style.display = "";
    editForm.style.display = "none";
    editActions.style.display = "none";
    top.classList.remove("editing");
  };

  deleteBtn.onclick = async () => {
    if (!confirm(`Delete prompt file "${prompt.prompt_file}"? This cannot be undone.`)) return;
    deleteBtn.disabled = true;
    try {
      await localDataPlane.deletePrompt(prompt.prompt_id, run.device_id);
      await fetchJSON(
        `/api/runs/${encodeURIComponent(run.run_id)}/prompts/${encodeURIComponent(prompt.prompt_id)}`,
        { method: "DELETE" },
      );
      card.remove();
      appendLog(`Deleted prompt: ${prompt.prompt_file}`);
      invalidateRuns();
    } catch (err) {
      appendLog(`Delete error: ${String(err)}`);
      deleteBtn.disabled = false;
    }
  };

  saveBtn.onclick = async () => {
    const lineRows = editForm.querySelectorAll(".prompt-line");
    const editedCopy = [...lineRows].map((row) => ({
      label: row.querySelector("label").textContent,
      value: row.querySelector("textarea").value,
    }));
    const updatedContent = replaceExactOnImageCopy(
      prompt.full_content,
      editedCopy,
    );
    saveBtn.disabled = true;
    try {
      const result = await localDataPlane.putPrompt(
        prompt.prompt_id,
        run.run_id,
        updatedContent,
        prompt.resource_version,
        run.device_id,
      );
      prompt.resource_version = result.version;
      prompt.full_content = updatedContent;
      try {
        await fetchJSON(
          `/api/runs/${encodeURIComponent(run.run_id)}/prompts/${encodeURIComponent(prompt.prompt_id)}`,
          {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              sha256: result.sha256,
              resource_version: result.version,
            }),
          },
        );
      } catch {
        // Local content is authoritative; Mongo metadata is reconciled on the next listing.
      }
      appendLog(`Saved edits to: ${prompt.prompt_file}`);
      editing = false;
      linesDisplay.style.display = "";
      editForm.style.display = "none";
      editActions.style.display = "none";
      top.classList.remove("editing");
      const displayValues = linesDisplay.querySelectorAll(".prompt-line-value");
      const editTextareas = editForm.querySelectorAll("textarea");
      editTextareas.forEach((ta, i) => {
        if (displayValues[i]) displayValues[i].textContent = ta.value || "(empty)";
      });
      invalidateRuns();
    } catch (err) {
      appendLog(`Edit error: ${String(err)}`);
      saveBtn.disabled = false;
    }
  };

  items.push({
    promptFile: prompt.prompt_file,
    promptId: prompt.prompt_id || "",
    personaNumber: prompt.persona_number,
    checkbox,
  });
  return card;
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
