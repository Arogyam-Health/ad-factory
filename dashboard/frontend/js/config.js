import { fetchJSON, clearCache } from "./api.js";
import { getAuthUser, isAuthenticated } from "./auth.js";

const CONFIG_KEYS = [
  "product_master_doc",
  "starting_prompt",
  "copy_prompt_templates",
  "persona_seeds",
  "copy_architecture",
  "background_variant",
  "prompt_assembler_templates",
  "conversion_916_prompt",
];

const JSON_KEYS = new Set([
  "copy_prompt_templates",
  "persona_seeds",
  "copy_architecture",
  "background_variant",
  "prompt_assembler_templates",
]);

const KEY_LABELS = {
  product_master_doc: "Product Master Doc",
  starting_prompt: "Starting Prompt",
  copy_prompt_templates: "Copy Prompt Templates",
  persona_seeds: "Persona Seeds",
  copy_architecture: "Copy Architecture",
  background_variant: "Background Variant",
  prompt_assembler_templates: "Prompt Assembler Templates",
  conversion_916_prompt: "9:16 Conversion Prompt",
};

let currentData = null;
let activeKey = CONFIG_KEYS[0];

function esc(s) {
  const d = document.createElement("div");
  d.textContent = String(s ?? "");
  return d.innerHTML;
}

function status(msg, type = "") {
  const el = document.getElementById("cfgStatus");
  if (!el) return;
  el.textContent = msg;
  el.className = "cfg-status" + (type ? ` ${type}` : "");
  if (type) setTimeout(() => { el.textContent = ""; el.className = "cfg-status"; }, 4000);
}

// ── Main render ───────────────────────────────────────────────────

export async function initConfigPage() {
  const user = getAuthUser();
  if (!user || !user.authenticated) {
    document.getElementById("cfgEditors").innerHTML = `
      <div class="cfg-empty">
        <span class="cfg-empty-icon">&#128274;</span>
        <strong>Sign in to manage configuration</strong>
        <p style="color:var(--muted);margin-top:0.5rem">You need to be logged in to view and edit your config.</p>
      </div>`;
    return;
  }

  try {
    currentData = await fetchJSON("/api/config/effective");
    renderConfigPage();
  } catch {
    document.getElementById("cfgEditors").innerHTML = `
      <div class="cfg-empty">
        <span class="cfg-empty-icon">&#9888;</span>
        <strong>Failed to load config</strong>
        <p style="color:var(--muted);margin-top:0.5rem">Refresh the page to try again.</p>
      </div>`;
  }
}

function renderConfigPage() {
  const data = currentData;
  if (!data) return;

  const config  = data.config || {};
  const canEdit = data.can_edit === true;
  const mode    = data.mode || "personal";
  const source  = data.source || "generic";
  const org     = data.org || null;
  const configId = data.config_id || null;
  const canViewVersions = data.can_view_versions === true;
  const canRollback     = data.can_rollback === true;
  const canCopy         = data.can_copy === true;
  const ownerType       = data.owner_type || "user";

  // ── Meta ──────────────────────────────────────────────────────────
  const metaEl = document.getElementById("cfgMeta");
  metaEl.innerHTML = `
    <span class="cfg-meta-item"><strong>Source:</strong> ${esc(source)}</span>
    <span class="cfg-meta-item"><strong>Mode:</strong> ${esc(mode)}</span>
    ${org ? `<span class="cfg-meta-item"><strong>Org:</strong> ${esc(org.name || "")}</span>` : ""}
    <span class="cfg-meta-item"><strong>Can Edit:</strong> ${canEdit ? "Yes" : "No"}</span>
    ${configId ? `<span class="cfg-meta-item"><strong>Config ID:</strong> <code style="font-size:0.75rem;background:var(--surface-2);padding:1px 6px;border-radius:4px">${esc(configId)}</code></span>` : ""}
  `;

  // ── Tabs ──────────────────────────────────────────────────────────
  const tabsEl = document.getElementById("cfgTabs");
  tabsEl.innerHTML = CONFIG_KEYS.map(k => `
    <button class="cfg-tab ${activeKey === k ? "active" : ""}" data-key="${k}" type="button">
      ${esc(KEY_LABELS[k] || k)}
    </button>
  `).join("");

  tabsEl.querySelectorAll(".cfg-tab").forEach(tab => {
    tab.addEventListener("click", () => {
      activeKey = tab.dataset.key;
      tabsEl.querySelectorAll(".cfg-tab").forEach(t => t.classList.remove("active"));
      tab.classList.add("active");
      renderEditors();
    });
  });

  // ── Editors ───────────────────────────────────────────────────────
  renderEditors();

  // ── Action bar ────────────────────────────────────────────────────
  const actionsEl = document.getElementById("cfgActions");
  actionsEl.style.display = "";

  let actionsHtml = "";
  if (canEdit) {
    actionsHtml += `<button class="cfg-save-btn" id="cfgSaveAllBtn" type="button">Save Changes</button>`;
    actionsHtml += `<button class="cfg-save-version-btn" id="cfgSaveVersionBtn" type="button">&#128190; Save Version</button>`;
  }
  if (canCopy && org && ownerType === "user") {
    actionsHtml += `<button class="cfg-merge-btn" id="cfgMergeBtn" type="button">&#10132; Merge to Org</button>`;
  }
  actionsEl.innerHTML = actionsHtml;

  if (canEdit) {
    document.getElementById("cfgSaveAllBtn")?.addEventListener("click", saveAllConfigs);
    document.getElementById("cfgSaveVersionBtn")?.addEventListener("click", openSaveVersionModal);
  }
  if (canCopy && org && ownerType === "user") {
    document.getElementById("cfgMergeBtn")?.addEventListener("click", openMergeModal);
  }

  // ── Version history ───────────────────────────────────────────────
  if (canViewVersions && configId) {
    loadVersionHistory(configId, canRollback);
  }
}

function renderEditors() {
  const config  = currentData?.config || {};
  const canEdit = currentData?.can_edit === true;
  const editorsEl = document.getElementById("cfgEditors");

  editorsEl.innerHTML = CONFIG_KEYS.map(k => {
    const content = config[k] || "";
    const isJson = JSON_KEYS.has(k);
    const isActive = activeKey === k;
    return `
      <div class="cfg-editor-pane ${isActive ? "active" : ""}" data-key="${k}">
        ${isJson ? `<span class="cfg-json-badge">JSON</span>` : ""}
        <textarea class="cfg-textarea" data-key="${k}" spellcheck="false" ${canEdit ? "" : "readonly"}>${esc(content)}</textarea>
      </div>
    `;
  }).join("");
}

// ── Save all configs ───────────────────────────────────────────────

async function saveAllConfigs() {
  const btn = document.getElementById("cfgSaveAllBtn");
  if (!btn) return;

  const ownerType = currentData?.owner_type || "user";
  const orgId     = currentData?.org?.org_id;
  const textareas = document.querySelectorAll(".cfg-textarea");

  for (const ta of textareas) {
    const key = ta.dataset.key;
    if (JSON_KEYS.has(key)) {
      try { JSON.parse(ta.value); }
      catch { status(`Invalid JSON in ${KEY_LABELS[key] || key}`, "error"); ta.focus(); return; }
    }
  }

  btn.disabled = true;
  btn.textContent = "Saving…";

  try {
    for (const ta of textareas) {
      const key = ta.dataset.key;
      const content = ta.value;

      if (ownerType === "org" && orgId) {
        await fetchJSON(`/api/orgs/${orgId}/config`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ config: { [key]: content } }),
        });
      } else {
        await fetchJSON("/api/user/config", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ [key]: content }),
        });
      }
    }
    status("All configs saved", "success");
    clearCache("/api/config/effective");
    currentData = await fetchJSON("/api/config/effective");
  } catch (err) {
    status(`Save failed: ${String(err)}`, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Save Changes";
  }
}

// ── Save Version modal ─────────────────────────────────────────────

function openSaveVersionModal() {
  const modal = document.createElement("div");
  modal.className = "modal-overlay";
  modal.innerHTML = `
    <div class="modal-box">
      <div class="modal-header">
        <h2>Save Version Snapshot</h2>
        <button class="modal-close" type="button">&times;</button>
      </div>
      <p style="color:var(--muted);font-size:0.88rem;margin-bottom:1rem">
        This will snapshot the current state of your config as a named version.
        No changes will be made to the config itself.
      </p>
      <label style="font-size:0.82rem;font-weight:600;display:block;margin-bottom:0.4rem">Reason (optional)</label>
      <input class="cfg-version-reason-input" id="versionReasonInput" type="text"
        placeholder="e.g. Before testing new prompts" />
      <div style="display:flex;gap:0.5rem;margin-top:1.25rem">
        <button class="cfg-save-btn" id="versionSaveConfirmBtn" type="button">Save Snapshot</button>
        <button class="modal-close ghost-btn" type="button" style="font-size:0.85rem">Cancel</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);

  const close = () => modal.remove();
  modal.querySelector(".modal-close").addEventListener("click", close);
  modal.addEventListener("click", e => { if (e.target === modal) close(); });

  document.getElementById("versionSaveConfirmBtn").addEventListener("click", async () => {
    const reason = document.getElementById("versionReasonInput").value.trim() || "manual_save";
    const btn = document.getElementById("versionSaveConfirmBtn");
    btn.disabled = true;
    btn.textContent = "Saving…";

    try {
      const configId = currentData?.config_id;
      if (!configId) throw new Error("No config ID available");

      await fetchJSON(`/api/config/${configId}/save-version`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason }),
      });
      status("Version snapshot saved", "success");
      close();
      if (configId) loadVersionHistory(configId, currentData?.can_rollback === true);
    } catch (err) {
      status(`Failed to save version: ${String(err)}`, "error");
      btn.disabled = false;
      btn.textContent = "Save Snapshot";
    }
  });
}

// ── Merge to Org modal ─────────────────────────────────────────────

function openMergeModal() {
  const org = currentData?.org;
  if (!org) return;

  const modal = document.createElement("div");
  modal.className = "modal-overlay";
  modal.innerHTML = `
    <div class="modal-box">
      <div class="modal-header">
        <h2>Merge Config to Organization</h2>
        <button class="modal-close" type="button">&times;</button>
      </div>
      <p style="color:var(--muted);font-size:0.88rem;margin-bottom:1rem">
        This will copy your current personal configuration into the shared org config
        (<strong>${esc(org.name || "")}</strong>), replacing the org's current values.
      </p>
      <label style="font-size:0.82rem;font-weight:600;display:block;margin-bottom:0.4rem">Reason (optional)</label>
      <input class="cfg-version-reason-input" id="mergeReasonInput" type="text"
        placeholder="e.g. Promote winning config to team" />
      <div style="display:flex;gap:0.5rem;margin-top:1.25rem">
        <button class="cfg-merge-btn" id="mergeConfirmBtn" type="button" style="background:var(--accent-amber);color:#fff">Merge Now</button>
        <button class="modal-close ghost-btn" type="button" style="font-size:0.85rem">Cancel</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);

  const close = () => modal.remove();
  modal.querySelector(".modal-close").addEventListener("click", close);
  modal.addEventListener("click", e => { if (e.target === modal) close(); });

  document.getElementById("mergeConfirmBtn").addEventListener("click", async () => {
    const reason = document.getElementById("mergeReasonInput").value.trim() || "merge_individual_to_org";
    const btn = document.getElementById("mergeConfirmBtn");
    btn.disabled = true;
    btn.textContent = "Merging…";

    try {
      const configId = currentData?.config_id;
      if (!configId) throw new Error("No config ID available");

      await fetchJSON(`/api/config/${configId}/merge-to-org`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ org_id: org.org_id, reason }),
      });
      status("Config merged to org", "success");
      close();
    } catch (err) {
      status(`Merge failed: ${String(err)}`, "error");
      btn.disabled = false;
      btn.textContent = "Merge Now";
    }
  });
}

// ── Version History ────────────────────────────────────────────────

async function loadVersionHistory(configId, canRollback) {
  const section = document.getElementById("cfgVersionSection");
  const listEl  = document.getElementById("cfgVersionList");
  section.style.display = "";
  listEl.innerHTML = `<p style="color:var(--muted);font-size:0.82rem">Loading version history…</p>`;

  try {
    const data = await fetchJSON(`/api/config/${configId}/versions`);
    const versions = data.versions || [];

    if (!versions.length) {
      listEl.innerHTML = `<p style="color:var(--muted);font-size:0.85rem">No version history yet. Click "Save Version" to create your first snapshot.</p>`;
      return;
    }

    listEl.innerHTML = `
      <table class="cfg-version-table">
        <thead>
          <tr>
            <th>Date</th>
            <th>Changed By</th>
            <th>Keys</th>
            <th>Reason</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${versions.map(v => `
            <tr>
              <td style="white-space:nowrap">${v.created_at ? new Date(v.created_at * 1000).toLocaleString() : "—"}</td>
              <td>${esc(v.changed_by_email || v.changed_by_user_id || "—")}</td>
              <td>
                <div class="cfg-version-keys">
                  ${(v.changed_keys || []).map(k => `<span class="cfg-version-key-tag">${esc(KEY_LABELS[k] || k)}</span>`).join("")}
                </div>
              </td>
              <td class="cfg-version-reason">${esc(v.change_reason || "—")}</td>
              <td>
                <div class="cfg-version-actions">
                  <button class="ghost-btn cfg-ver-view" data-vid="${esc(v.version_id)}" type="button" style="font-size:0.78rem">View</button>
                  ${canRollback ? `<button class="ghost-btn cfg-ver-rollback" data-vid="${esc(v.version_id)}" type="button" style="font-size:0.78rem;color:var(--accent-coral)">Rollback</button>` : ""}
                </div>
              </td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    `;

    listEl.querySelectorAll(".cfg-ver-view").forEach(btn => {
      btn.addEventListener("click", () => viewVersion(configId, btn.dataset.vid, canRollback));
    });

    if (canRollback) {
      listEl.querySelectorAll(".cfg-ver-rollback").forEach(btn => {
        btn.addEventListener("click", () => rollbackVersion(configId, btn.dataset.vid));
      });
    }
  } catch (err) {
    listEl.innerHTML = `<p style="color:var(--accent-coral);font-size:0.85rem">Failed to load versions: ${esc(String(err))}</p>`;
  }
}

async function viewVersion(configId, versionId, canRollback) {
  try {
    const version = await fetchJSON(`/api/config/${configId}/versions/${versionId}`);
    const snapshot = version.snapshot || {};
    const files = snapshot.files || {};

    const modal = document.createElement("div");
    modal.className = "modal-overlay";

    let tabsHtml = CONFIG_KEYS.map(k => {
      const hasContent = files[k]?.content;
      return `<button class="cfg-tab cfg-snap-tab" data-key="${k}" type="button" ${hasContent ? "" : "disabled style='opacity:0.4'"}>${esc(KEY_LABELS[k] || k)}</button>`;
    }).join("");

    let panesHtml = CONFIG_KEYS.map((k, i) => {
      const content = files[k]?.content || "";
      return `<div class="cfg-editor-pane ${i === 0 ? "active" : ""}" data-key="${k}">
        <textarea class="cfg-textarea" readonly spellcheck="false">${esc(content)}</textarea>
      </div>`;
    }).join("");

    modal.innerHTML = `
      <div class="modal-box">
        <div class="modal-header">
          <h2>Version Snapshot</h2>
          <button class="modal-close" type="button">&times;</button>
        </div>
        <p style="color:var(--muted);font-size:0.82rem;margin-bottom:0.5rem">
          By ${esc(version.changed_by_email || "?")} — ${version.created_at ? new Date(version.created_at * 1000).toLocaleString() : "?"}
        </p>
        <p style="color:var(--muted);font-size:0.82rem;margin-bottom:1rem">
          Reason: ${esc(version.change_reason || "—")}
        </p>
        <div class="cfg-tabs" style="margin-bottom:0.75rem">${tabsHtml}</div>
        ${panesHtml}
      </div>
    `;
    document.body.appendChild(modal);

    const close = () => modal.remove();
    modal.querySelector(".modal-close").addEventListener("click", close);
    modal.addEventListener("click", e => { if (e.target === modal) close(); });

    modal.querySelectorAll(".cfg-snap-tab").forEach(tab => {
      tab.addEventListener("click", () => {
        modal.querySelectorAll(".cfg-snap-tab").forEach(t => t.classList.remove("active"));
        modal.querySelectorAll(".cfg-editor-pane").forEach(p => p.classList.remove("active"));
        tab.classList.add("active");
        const pane = modal.querySelector(`.cfg-editor-pane[data-key="${tab.dataset.key}"]`);
        if (pane) pane.classList.add("active");
      });
    });
  } catch (err) {
    status(`Failed to load version: ${String(err)}`, "error");
  }
}

async function rollbackVersion(configId, versionId) {
  if (!confirm("Rollback config to this version? A snapshot of the current state will be saved automatically.")) return;

  try {
    await fetchJSON(`/api/config/${configId}/rollback/${versionId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: "rollback_from_ui" }),
    });
    status("Config rolled back", "success");
    clearCache("/api/config/effective");
    currentData = await fetchJSON("/api/config/effective");
    renderConfigPage();
  } catch (err) {
    status(`Rollback failed: ${String(err)}`, "error");
  }
}

// ── Legacy support: renderConfigPanel for index.html (hidden) ──────

export async function renderConfigPanel() {
  const panel = document.getElementById("configPanel");
  if (!panel) return;
  panel.innerHTML = `<div class="card"><p class="hint">Config has moved to <a href="/config.html" style="color:var(--primary)">/config.html</a></p></div>`;
}
