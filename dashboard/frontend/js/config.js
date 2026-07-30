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
let availableSources = [];
let currentSource = "personal";

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

  // Load available sources
  try {
    const srcData = await fetchJSON("/api/config/sources");
    availableSources = srcData.sources || [];
  } catch {
    availableSources = [{ type: "personal", label: "My Config", has_custom: false }];
  }

  // Check for ?org_id= query param (from "Fetch Config" button on Teams page)
  const params = new URLSearchParams(window.location.search);
  const requestedOrgId = params.get("org_id");

  if (requestedOrgId) {
    currentSource = requestedOrgId;
  } else {
    currentSource = "personal";
  }

  await loadConfigForSource(currentSource);
  renderConfigPage();
}

async function loadConfigForSource(sourceId) {
  try {
    if (sourceId === "personal") {
      currentData = await fetchJSON("/api/config/effective");
    } else {
      currentData = await fetchJSON(`/api/config/effective?org_id=${encodeURIComponent(sourceId)}`);
    }
  } catch {
    currentData = null;
  }
}

function renderConfigPage() {
  const data = currentData;
  if (!data) {
    document.getElementById("cfgEditors").innerHTML = `
      <div class="cfg-empty">
        <span class="cfg-empty-icon">&#9888;</span>
        <strong>Failed to load config</strong>
        <p style="color:var(--muted);margin-top:0.5rem">Refresh the page to try again.</p>
      </div>`;
    return;
  }

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
  const rawAvailableOrgs = data.available_orgs || [];
  const availableOrgs = ownerType === "org" && org
    ? rawAvailableOrgs.filter(o => o.org_id !== org.org_id)
    : rawAvailableOrgs;

  // ── Source selector ───────────────────────────────────────────────
  const metaEl = document.getElementById("cfgMeta");
  const orgSources = availableSources.filter(s => s.type === "org");
  const currentLabel = org
    ? `${org.name || "Org"} (${source === "org_shared" ? "Shared" : "Personal"})`
    : "My Config";

  let sourceSelectorHtml = "";
  if (orgSources.length || availableSources.length > 1) {
    sourceSelectorHtml = `
      <div style="display:flex;align-items:center;gap:0.75rem;margin-bottom:1.25rem;padding:0.75rem 1rem;background:var(--surface-2);border-radius:var(--radius-sm);flex-wrap:wrap">
        <span style="font-size:0.82rem;font-weight:600;color:var(--muted);white-space:nowrap">Config Source:</span>
        <div style="display:flex;gap:0.3rem;flex-wrap:wrap" id="cfgSourceButtons">
          <button class="cfg-source-btn ${!org ? "active" : ""}" data-source="personal" type="button" style="padding:0.35rem 0.75rem;border-radius:var(--radius-sm);border:1px solid var(--line);background:${!org ? "var(--primary-muted)" : "transparent"};color:${!org ? "var(--primary)" : "var(--muted)"};font-size:0.8rem;font-weight:500;font-family:var(--font-body);cursor:pointer;transition:all 0.15s">My Config</button>
          ${orgSources.map(s => `
            <button class="cfg-source-btn ${org && org.org_id === s.org_id ? "active" : ""}" data-source="${esc(s.org_id)}" type="button" style="padding:0.35rem 0.75rem;border-radius:var(--radius-sm);border:1px solid var(--line);background:${org && org.org_id === s.org_id ? "var(--primary-muted)" : "transparent"};color:${org && org.org_id === s.org_id ? "var(--primary)" : "var(--muted)"};font-size:0.8rem;font-weight:500;font-family:var(--font-body);cursor:pointer;transition:all 0.15s">${esc(s.org_name)} ${s.config_mode === "shared_org_config" ? "(Shared)" : "(Individual)"}</button>
          `).join("")}
        </div>
      </div>`;
  }

  metaEl.innerHTML = sourceSelectorHtml + `
    <span class="cfg-meta-item"><strong>Source:</strong> ${esc(source)}</span>
    <span class="cfg-meta-item"><strong>Mode:</strong> ${esc(mode)}</span>
    ${org ? `<span class="cfg-meta-item"><strong>Org:</strong> ${esc(org.name || "")}</span>` : ""}
    <span class="cfg-meta-item"><strong>Can Edit:</strong> ${canEdit ? "Yes" : "No"}</span>
    ${configId ? `<span class="cfg-meta-item"><strong>Config ID:</strong> <code style="font-size:0.75rem;background:var(--surface-2);padding:1px 6px;border-radius:4px">${esc(configId)}</code></span>` : ""}
  `;

  // Wire source buttons
  metaEl.querySelectorAll(".cfg-source-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const src = btn.dataset.source;
      currentSource = src;
      // Update URL without reload
      const url = new URL(window.location);
      if (src === "personal") {
        url.searchParams.delete("org_id");
      } else {
        url.searchParams.set("org_id", src);
      }
      history.replaceState(null, "", url);
      await loadConfigForSource(src);
      renderConfigPage();
    });
  });

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
  if (canCopy && availableOrgs.length) {
    actionsHtml += `<button class="cfg-merge-btn" id="cfgMergeBtn" type="button">&#10132; Copy to Org</button>`;
  }
  actionsEl.innerHTML = actionsHtml;

  if (canEdit) {
    document.getElementById("cfgSaveAllBtn")?.addEventListener("click", saveAllConfigs);
    document.getElementById("cfgSaveVersionBtn")?.addEventListener("click", openSaveVersionModal);
  }
  if (canCopy && availableOrgs.length) {
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
    const nextConfig = {};
    for (const ta of textareas) {
      const key = ta.dataset.key;
      nextConfig[key] = ta.value;
    }

    if (ownerType === "org" && orgId) {
      await fetchJSON(`/api/orgs/${orgId}/config`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config: nextConfig }),
      });
    } else {
      await fetchJSON("/api/user/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config: nextConfig }),
      });
    }
    status("All configs saved", "success");
    clearCache("/api/config/effective");
    await loadConfigForSource(currentSource);
    renderConfigPage();
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
  const orgs = currentData?.available_orgs || [];
  if (!orgs.length) return;

  const single = orgs.length === 1;

  const modal = document.createElement("div");
  modal.className = "modal-overlay";
  modal.innerHTML = `
    <div class="modal-box">
      <div class="modal-header">
        <h2>Copy Config to Organization</h2>
        <button class="modal-close" type="button">&times;</button>
      </div>
      <p style="color:var(--muted);font-size:0.88rem;margin-bottom:1rem">
        This will copy the current configuration into the target org's shared config,
        replacing its current values.
      </p>
      ${single ? `<input type="hidden" id="mergeOrgId" value="${esc(orgs[0].org_id)}" />` : `
      <label style="font-size:0.82rem;font-weight:600;display:block;margin-bottom:0.4rem">Target Organization</label>
      <select id="mergeOrgId" style="width:100%;padding:0.5rem;border:1px solid var(--line);border-radius:var(--radius-sm);background:var(--surface);color:var(--ink);font-size:0.88rem;margin-bottom:1rem">
        ${orgs.map(o => `<option value="${esc(o.org_id)}">${esc(o.name)}</option>`).join("")}
      </select>
      `}
      <label style="font-size:0.82rem;font-weight:600;display:block;margin-bottom:0.4rem">Reason (optional)</label>
      <input class="cfg-version-reason-input" id="mergeReasonInput" type="text"
        placeholder="e.g. Promote config to team" />
      <div style="display:flex;gap:0.5rem;margin-top:1.25rem">
        <button class="cfg-merge-btn" id="mergeConfirmBtn" type="button" style="background:var(--accent-amber);color:#fff">${single ? "Copy Now" : "Copy to Selected Org"}</button>
        <button class="modal-close ghost-btn" type="button" style="font-size:0.85rem">Cancel</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);

  const close = () => modal.remove();
  modal.querySelectorAll(".modal-close").forEach(el => el.addEventListener("click", close));
  modal.addEventListener("click", e => { if (e.target === modal) close(); });

  document.getElementById("mergeConfirmBtn").addEventListener("click", async () => {
    const orgId = document.getElementById("mergeOrgId").value;
    if (!orgId) { status("Select an organization", "error"); return; }
    const reason = document.getElementById("mergeReasonInput").value.trim() || "copy_config_to_org";
    const btn = document.getElementById("mergeConfirmBtn");
    btn.disabled = true;
    btn.textContent = "Copying…";

    try {
      const configId = currentData?.config_id;
      if (!configId) throw new Error("No config ID available");

      await fetchJSON(`/api/config/${configId}/copy-to-org`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ org_id: orgId, reason }),
      });
      status("Config copied to org", "success");
      close();
    } catch (err) {
      status(`Copy failed: ${String(err)}`, "error");
      btn.disabled = false;
      btn.textContent = "Copy to Selected Org";
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
    await loadConfigForSource(currentSource);
    renderConfigPage();
  } catch (err) {
    status(`Rollback failed: ${String(err)}`, "error");
  }
}

// ── Legacy support: renderConfigPanel for index.html ────────────────

export async function renderConfigPanel() {
  const panel = document.getElementById("configPanel");
  if (!panel) return;

  try {
    const data = await fetchJSON("/api/config/effective");
    const config = data.config || {};
    const source = data.source || "generic";
    const canEdit = data.can_edit !== false;

    panel.innerHTML = `
      <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.75rem">
          <h3 style="margin:0;font-size:0.95rem;font-weight:600">Config Files <span style="font-size:0.72rem;color:var(--muted);font-weight:400">(${esc(source)})</span></h3>
          <a href="/config.html" style="font-size:0.78rem;color:var(--primary);text-decoration:none;font-weight:500">Open Full Editor &rarr;</a>
        </div>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:0.5rem">
          ${CONFIG_KEYS.map(k => `
            <div style="padding:0.5rem 0.75rem;border:1px solid var(--line);border-radius:var(--radius-sm);font-size:0.8rem">
              <div style="font-weight:600;color:var(--ink);margin-bottom:0.2rem">${esc(KEY_LABELS[k] || k)}</div>
              <div style="color:var(--muted);font-size:0.72rem;max-height:2.4em;overflow:hidden;text-overflow:ellipsis">${esc((config[k] || "").slice(0, 60) || "empty")}</div>
            </div>
          `).join("")}
        </div>
      </div>`;
  } catch {
    panel.innerHTML = `<div class="card"><p class="hint">Failed to load config. <a href="/config.html" style="color:var(--primary)">Open Config Editor</a></p></div>`;
  }
}
