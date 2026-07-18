import { fetchJSON, invalidateRuns } from "./api.js";
import { state } from "./state.js";
import { appendLog } from "./ui.js";

const $ = (id) => document.getElementById(id);
const selectedPersonas = new Set();
const selectedFiles = new Map();
let activeRunId = "";
let statusTimer = null;
let objectUrls = [];

function injectUI() {
  if ($("referenceFlowPanel")) return;
  if (!document.querySelector('link[href="/reference-flow.css"]')) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "/reference-flow.css";
    document.head.appendChild(link);
  }

  const hero = document.querySelector(".hero");
  const structured = document.querySelector(".layout-columns");
  const runs = document.querySelector(".card-runs");
  if (!hero || !structured || !runs) return;
  structured.id = "structuredFlowPanel";

  const nav = document.createElement("nav");
  nav.className = "flow-mode-switch";
  nav.setAttribute("aria-label", "Generation flow");
  nav.innerHTML = `
    <button id="structuredFlowTab" class="flow-mode-tab active" type="button">Structured Flow</button>
    <button id="referenceFlowTab" class="flow-mode-tab" type="button">Reference Image Flow</button>
  `;
  hero.insertAdjacentElement("afterend", nav);

  const panel = document.createElement("div");
  panel.id = "referenceFlowPanel";
  panel.className = "reference-flow-panel hidden";
  panel.innerHTML = `
    <section class="card reference-intro-card">
      <div><p class="eyebrow">Reference-led generation</p><h2>Reference Image Flow</h2>
      <p class="hint">The model receives only the selected persona, product document, product images, and one reference image per job.</p></div>
      <div class="reference-flow-equation"><strong id="referenceJobCount">0 jobs</strong><span>personas × reference images</span></div>
    </section>
    <div class="reference-flow-grid">
      <section class="card"><h2>1) Select Personas</h2><p class="hint">Every reference is generated for each selected persona, persona by persona.</p><div id="referencePersonaList" class="reference-persona-grid"></div></section>
      <section class="card"><h2>2) Upload Reference Images</h2><label>Choose a folder</label>
        <input id="referenceImageFiles" type="file" multiple webkitdirectory directory accept="image/png,image/jpeg,image/webp" />
        <div class="reference-upload-actions"><button id="referenceChooseFiles" class="ghost-btn" type="button">Choose individual files</button><span id="referenceImageSummary" class="hint">No reference images selected.</span></div>
        <input id="referenceIndividualFiles" class="hidden" type="file" multiple accept="image/png,image/jpeg,image/webp" />
        <div id="referencePreviewGrid" class="reference-preview-grid"></div>
      </section>
      <section class="card"><h2>3) Product Inputs</h2><label>Optional product document override</label><input id="referenceProductFile" type="file" />
        <label>Optional product images to add</label><input id="referenceProductImages" type="file" multiple accept="image/*" />
        <label class="toggle-label execution-toggle"><input id="referenceClearProductImages" type="checkbox" /><span>Clear existing product images before adding these</span></label>
        <p class="hint">Empty inputs reuse the current product document and all images in <code>input/images</code>.</p>
      </section>
      <section class="card reference-execution-card"><h2>4) Generate</h2><label>Image engine</label><select id="referenceEngine"><option value="gemini">Gemini</option><option value="chatgpt">ChatGPT</option></select>
        <label class="toggle-label execution-toggle"><input id="referenceGenerate916" type="checkbox" checked /><span>Create 9:16 versions after 4:5</span></label>
        <p class="hint">4:5 is generated first with the protected safe zone, then the existing 9:16 conversion flow runs.</p>
        <button id="referenceRunBtn" type="button">Run Reference Flow</button><button id="referenceCancelBtn" class="ghost-btn" type="button" disabled>Cancel Run</button>
        <div class="reference-progress-shell"><div id="referenceProgressBar" class="reference-progress-bar"></div></div><pre id="referenceStatus" class="status"></pre>
      </section>
    </div>`;
  runs.insertAdjacentElement("beforebegin", panel);
}

function setFlow(mode) {
  const reference = mode === "reference";
  $("structuredFlowTab")?.classList.toggle("active", !reference);
  $("referenceFlowTab")?.classList.toggle("active", reference);
  $("structuredFlowPanel")?.classList.toggle("hidden", reference);
  $("referenceFlowPanel")?.classList.toggle("hidden", !reference);
  localStorage.setItem("adFactoryFlowMode", reference ? "reference" : "structured");
}

function fileKey(file) { return `${file.webkitRelativePath || file.name}:${file.size}:${file.lastModified}`; }
function clearObjectUrls() { objectUrls.forEach(URL.revokeObjectURL); objectUrls = []; }
function updateJobCount() {
  const count = selectedPersonas.size * selectedFiles.size;
  if ($("referenceJobCount")) $("referenceJobCount").textContent = `${count} job${count === 1 ? "" : "s"}`;
}
function renderFiles() {
  clearObjectUrls();
  const files = [...selectedFiles.values()];
  $("referenceImageSummary").textContent = files.length ? `${files.length} reference image${files.length === 1 ? "" : "s"} selected.` : "No reference images selected.";
  const grid = $("referencePreviewGrid");
  grid.innerHTML = "";
  files.slice(0, 60).forEach((file) => {
    const item = document.createElement("div"); item.className = "reference-preview-item";
    const img = document.createElement("img"); const url = URL.createObjectURL(file); objectUrls.push(url); img.src = url; img.alt = file.name; img.loading = "lazy";
    const label = document.createElement("span"); label.textContent = file.webkitRelativePath || file.name;
    item.append(img, label); grid.appendChild(item);
  });
  if (files.length > 60) { const more = document.createElement("div"); more.className = "reference-preview-more"; more.textContent = `+${files.length - 60} more`; grid.appendChild(more); }
  updateJobCount();
}
function addFiles(list) { [...(list || [])].forEach((file) => { if (file.type.startsWith("image/")) selectedFiles.set(fileKey(file), file); }); renderFiles(); }

function renderPersonas() {
  const root = $("referencePersonaList");
  if (!root || !state.defaultData?.personas) return;
  root.innerHTML = "";
  state.defaultData.personas.forEach((persona) => {
    const card = document.createElement("label"); card.className = "reference-persona-card";
    const checkbox = document.createElement("input"); checkbox.type = "checkbox";
    const copy = document.createElement("span"); copy.innerHTML = `<strong>${persona.number}. ${persona.name}</strong><small>${persona.core_pattern || persona.description || ""}</small>`;
    checkbox.addEventListener("change", () => { checkbox.checked ? selectedPersonas.add(persona.number) : selectedPersonas.delete(persona.number); card.classList.toggle("selected", checkbox.checked); updateJobCount(); });
    card.append(checkbox, copy); root.appendChild(card);
  });
}
async function waitForDefaults() {
  for (let i = 0; i < 100; i += 1) { if (state.defaultData?.personas?.length) { renderPersonas(); return; } await new Promise((r) => setTimeout(r, 100)); }
  $("referencePersonaList").textContent = "Could not load persona seeds.";
}
function showStatus(payload) {
  $("referenceStatus").textContent = JSON.stringify(payload, null, 2);
  const completed = Number(payload.completed_jobs || 0), total = Number(payload.total_jobs || 0);
  let percent = total ? Math.min(90, Math.round((completed / total) * 90)) : 0;
  if (payload.phase === "9:16 conversion") percent = 94;
  if (payload.status === "completed") percent = 100;
  $("referenceProgressBar").style.width = `${percent}%`;
}
function stopPolling() { if (statusTimer) clearInterval(statusTimer); statusTimer = null; }
async function pollStatus() {
  if (!activeRunId) return;
  try {
    const data = await fetchJSON(`/api/runs/${activeRunId}/reference-status?t=${Date.now()}`);
    showStatus(data);
    if (["completed", "error", "cancelled"].includes(data.status)) {
      stopPolling(); $("referenceRunBtn").disabled = false; $("referenceCancelBtn").disabled = true; appendLog(data.message || `Reference flow ${data.status}.`);
      if (data.status === "completed") { invalidateRuns(); const { loadRuns } = await import("./runs.js"); await loadRuns(); }
    }
  } catch (error) { appendLog(`Reference status error: ${String(error)}`); }
}
async function startRun() {
  const files = [...selectedFiles.values()];
  if (!selectedPersonas.size) return appendLog("Select at least one persona for the reference flow.");
  if (!files.length) return appendLog("Upload at least one reference image.");
  const form = new FormData();
  form.append("config", JSON.stringify({ selected_personas: [...selectedPersonas], engine: $("referenceEngine").value, generate_916: $("referenceGenerate916").checked, headless: state.headlessModeEnabled }));
  files.forEach((file) => form.append("reference_image_files", file, file.name));
  const productFile = $("referenceProductFile").files?.[0]; if (productFile) form.append("product_info_file", productFile, productFile.name);
  [...($("referenceProductImages").files || [])].forEach((file) => form.append("input_image_files", file, file.name));
  form.append("clear_input_images", String($("referenceClearProductImages").checked));
  $("referenceRunBtn").disabled = true; $("referenceCancelBtn").disabled = true; $("referenceProgressBar").style.width = "2%"; $("referenceStatus").textContent = "Uploading and starting...";
  try {
    const data = await fetchJSON("/api/runs/execute-reference", { method: "POST", body: form });
    activeRunId = data.run_id; $("referenceCancelBtn").disabled = false; showStatus(data); appendLog(`Reference flow started: ${data.run_id}, ${data.total_jobs} 4:5 jobs.`);
    stopPolling(); statusTimer = setInterval(pollStatus, 2000); await pollStatus();
  } catch (error) { $("referenceRunBtn").disabled = false; $("referenceCancelBtn").disabled = true; $("referenceStatus").textContent = String(error); appendLog(`Reference flow failed to start: ${String(error)}`); }
}
async function cancelRun() {
  if (!activeRunId) return; $("referenceCancelBtn").disabled = true;
  try { await fetchJSON(`/api/runs/${activeRunId}/cancel`, { method: "POST" }); appendLog(`Cancellation requested for ${activeRunId}.`); }
  catch (error) { appendLog(`Cancel error: ${String(error)}`); }
}

injectUI();
$("structuredFlowTab")?.addEventListener("click", () => setFlow("structured"));
$("referenceFlowTab")?.addEventListener("click", () => setFlow("reference"));
$("referenceImageFiles")?.addEventListener("change", (e) => addFiles(e.target.files));
$("referenceIndividualFiles")?.addEventListener("change", (e) => addFiles(e.target.files));
$("referenceChooseFiles")?.addEventListener("click", () => $("referenceIndividualFiles")?.click());
$("referenceRunBtn")?.addEventListener("click", startRun);
$("referenceCancelBtn")?.addEventListener("click", cancelRun);
setFlow(localStorage.getItem("adFactoryFlowMode") === "reference" ? "reference" : "structured");
waitForDefaults();
