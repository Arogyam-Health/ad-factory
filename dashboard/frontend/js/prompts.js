import { appendLog } from "./ui.js";
import { state } from "./state.js";
import { fetchJSON, invalidateRuns } from "./api.js";

export function buildPromptEditor(run, container) {
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
    fetchJSON(`/api/runs/${run.run_id}/prompt-copies`)
      .then((data) => {
        const prompts = data.prompts || [];
        if (!prompts.length) {
          loadHint.textContent = "No prompts found for this run.";
          loadBtn.disabled = false;
          return;
        }
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
            const res = await fetch(`/api/runs/${run.run_id}/export-on-image-copy`);
            if (!res.ok) {
              appendLog(`Export failed: ${await res.text() || res.statusText}`);
              return;
            }
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            const cd = res.headers.get("Content-Disposition");
            const fnMatch = cd && cd.match(/filename="([^"]+)"/);
            a.download = fnMatch ? fnMatch[1] : `on-image-copy-${run.run_id}.xlsx`;
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
            appendLog("Importing XLSX and generating preview...");
            const fd = new FormData();
            fd.append("file", file);
            fd.append("confirm", "false");
            const res = await fetch(`/api/runs/${run.run_id}/import-on-image-copy`, { method: "POST", body: fd });
            let data = null;
            try { data = await res.json(); } catch { data = { detail: await res.text() }; }
            if (!res.ok) {
              appendLog("Import validation failed");
              previewEl.textContent = JSON.stringify(data.detail || data, null, 2);
              container.appendChild(previewEl);
              return;
            }
            previewEl.textContent =
              `Preview ready for ${data.changed_rows_count} rows (skipped ${data.skipped_rows}):\n` +
              (data.items || []).slice(0, 30).map((x) => `- ${x.prompt_id}: ${x.old_copy} => ${x.new_copy}`).join("\n") +
              ((data.items || []).length > 30 ? `\n... (${(data.items || []).length - 30} more)` : "");
            container.appendChild(previewEl);
            if (!window.confirm(`Preview generated.\nApply changes for ${data.changed_rows_count} rows?`)) {
              appendLog("Import canceled (no overwrite applied).");
              return;
            }
            appendLog("Applying XLSX changes (exact-block overwrite only)...");
            const fd2 = new FormData();
            fd2.append("file", file);
            fd2.append("confirm", "true");
            const res2 = await fetch(`/api/runs/${run.run_id}/import-on-image-copy`, { method: "POST", body: fd2 });
            let data2 = null;
            try { data2 = await res2.json(); } catch { data2 = { detail: await res2.text() }; }
            if (!res2.ok) {
              appendLog("Import apply failed.");
              previewEl.textContent = JSON.stringify(data2.detail || data2, null, 2);
              return;
            }
            appendLog(`Import applied. Updated ${data2.changed_rows_count} rows. Skipped ${data2.skipped_rows}.`);
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
          const card = buildPromptCard(prompt, run, items);
          editorList.appendChild(card);
        });

        selectAllBtn.onclick = () => items.forEach((it) => { it.checkbox.checked = true; });
        clearBtn.onclick = () => items.forEach((it) => { it.checkbox.checked = false; });

        generate45Btn.onclick = async () => {
          const selected = items.filter((it) => it.checkbox.checked).map((it) => it.promptFile);
          if (!selected.length) { appendLog("Select at least one prompt."); return; }

          const engine = await showEngineSelector("4:5");
          if (!engine) return;

          generate45Btn.disabled = true;
          const engineLabel = engine === "chatgpt" ? "ChatGPT" : "Gemini";
          appendLog(`Generating 4:5 images in ${engineLabel} for ${selected.length} selected prompt(s) from ${run.run_id}...`);
          try {
            const data = await fetchJSON(`/api/runs/${run.run_id}/generate-images-45`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ prompt_files: selected, headless: state.headlessModeEnabled, engine }),
            });
            const batchKey = data.batch || data.run_id || "";
            if (batchKey && state.headlessModeEnabled) {
              import("./chrome.js").then((m) => m.startProgressPolling(batchKey));
            }
            appendLog(`Done. Generated 4:5 in ${engineLabel} for selected prompts: ${selected.length}`);
            import("./runs.js").then((m) => m.loadRuns());
          } catch (err) {
            appendLog(String(err));
          } finally {
            generate45Btn.disabled = false;
          }
        };

        generate916Btn.onclick = async () => {
          const selected = items.filter((it) => it.checkbox.checked).map((it) => it.promptFile);
          if (!selected.length) { appendLog("Select at least one prompt."); return; }
          const engine = await showEngineSelector("9:16");
          if (!engine) return;

          generate916Btn.disabled = true;
          const engineLabel = engine === "chatgpt" ? "ChatGPT" : "Gemini";
          appendLog(`Generating 9:16 in ${engineLabel} from selected 4:5 image references for ${selected.length} prompt(s)...`);
          try {
            const data = await fetchJSON(`/api/runs/${run.run_id}/generate-images-916-from-45`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ prompt_files: selected, headless: state.headlessModeEnabled, engine }),
            });
            const batchKey = data.batch || data.run_id || "";
            if (batchKey && state.headlessModeEnabled) {
              import("./chrome.js").then((m) => m.startProgressPolling(batchKey));
            }
            appendLog(`Done. Generated 9:16 in ${engineLabel} from selected 4:5 refs`);
            import("./runs.js").then((m) => m.loadRuns());
          } catch (err) {
            appendLog(String(err));
          } finally {
            generate916Btn.disabled = false;
          }
        };
      })
      .catch((err) => {
        loadHint.textContent = `Could not load editable copy: ${String(err)}`;
        loadBtn.disabled = false;
      });
  };
}

function mkBtn(text) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = text;
  return b;
}

function buildPromptCard(prompt, run, items) {
  const card = document.createElement("div");
  card.className = "prompt-editor";

  const top = document.createElement("div");
  top.className = "prompt-editor-top";
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = true;

  const link = document.createElement("a");
  link.href = prompt.review_url;
  link.target = "_blank";
  link.textContent = prompt.prompt_file;

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
      await fetchJSON(`/api/runs/${run.run_id}/delete-prompt`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt_file: prompt.prompt_file }),
      });
      appendLog(`Deleted prompt: ${prompt.prompt_file}`);
      card.remove();
      invalidateRuns();
    } catch (err) {
      appendLog(`Delete error: ${String(err)}`);
      deleteBtn.disabled = false;
    }
  };

  saveBtn.onclick = async () => {
    const lineRows = editForm.querySelectorAll(".prompt-line");
    const newText = [...lineRows].map((row) => {
      const label = row.querySelector("label").textContent;
      const value = row.querySelector("textarea").value;
      return `- ${label}: ${value}`;
    }).join("\n");
    if (!newText.trim()) { appendLog("Prompt text cannot be empty."); return; }
    saveBtn.disabled = true;
    try {
      await fetchJSON(`/api/runs/${run.run_id}/edit-prompt`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt_file: prompt.prompt_file, text: newText }),
      });
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

  items.push({ promptFile: prompt.prompt_file, personaNumber: prompt.persona_number, checkbox });
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
