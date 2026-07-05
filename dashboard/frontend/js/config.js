import { fetchJSON, clearCache } from "./api.js";
import { getAuthUser, isAuthenticated } from "./auth.js";
import { setStatus } from "./ui.js";
import { getOrgData } from "./org.js";

let currentConfig = null;
let currentVersionData = null;

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

export async function renderConfigPanel() {
  const panel = document.getElementById("configPanel");
  if (!panel) return;

  const user = getAuthUser();
  if (!user.authenticated) {
    panel.innerHTML = `<div class="card"><p class="hint">Login to manage config.</p></div>`;
    return;
  }

  try {
    const data = await fetchJSON("/api/config/effective");
    currentConfig = data;
    renderConfigView(panel, data);
  } catch {
    panel.innerHTML = `<div class="card"><p class="hint">Failed to load config.</p></div>`;
  }
}

function renderConfigView(panel, data) {
  const config = data.config || {};
  const canEdit = data.can_edit === true;
  const canViewVersions = data.can_view_versions === true;
  const canRollback = data.can_rollback === true;
  const canCopy = data.can_copy === true;
  const mode = data.mode || "personal";
  const source = data.source || "generic";
  const configId = data.config_id || null;
  const org = data.org || null;
  const membership = data.membership || null;
  const role = membership ? membership.role : (source === "generic" ? null : "owner");

  const activeTab = getActiveTab();

  panel.innerHTML = `
    <section class="card config-panel">
      <h2>Configuration</h2>

      <div class="config-metadata">
        <div class="config-meta-row">
          <span class="config-meta-label">Source:</span>
          <span class="config-meta-value config-source-${source}">${escapeHtml(source)}</span>
        </div>
        ${org ? `<div class="config-meta-row"><span class="config-meta-label">Org:</span><span class="config-meta-value">${escapeHtml(org.name || "")}</span></div>` : ""}
        ${role ? `<div class="config-meta-row"><span class="config-meta-label">Role:</span><span class="config-meta-value">${escapeHtml(role)}</span></div>` : ""}
        <div class="config-meta-row">
          <span class="config-meta-label">Mode:</span>
          <span class="config-meta-value">${escapeHtml(mode)}</span>
        </div>
        ${configId ? `<div class="config-meta-row"><span class="config-meta-label">Config ID:</span><span class="config-meta-value config-meta-mono">${escapeHtml(configId)}</span></div>` : ""}
        <div class="config-meta-row">
          <span class="config-meta-label">Can Edit:</span>
          <span class="config-meta-value">${canEdit ? "Yes" : "No"}</span>
        </div>
        ${canViewVersions ? `<div class="config-meta-row"><span class="config-meta-label">Versions:</span><span class="config-meta-value"><button class="ghost-btn version-history-btn" type="button">View History</button></span></div>` : ""}
      </div>

      ${!canEdit ? `<p class="config-readonly-notice">Your role cannot edit this config.</p>` : ""}

      <div class="config-tabs">
        ${CONFIG_KEYS.map(k => `
          <button class="config-tab ${activeTab === k ? "active" : ""}" data-config-key="${k}" type="button">
            ${escapeHtml(KEY_LABELS[k] || k)}
          </button>
        `).join("")}
      </div>

      ${CONFIG_KEYS.map(k => {
        const content = config[k] || "";
        const isJson = JSON_KEYS.has(k);
        const showEditor = activeTab === k;
        return `
          <div class="config-editor-pane ${showEditor ? "" : "hidden"}" data-config-key="${k}">
            ${isJson ? `<div class="config-json-indicator">JSON — validate before saving</div>` : ""}
            <textarea class="config-editor-textarea" data-config-key="${k}" spellcheck="false" ${canEdit ? "" : "readonly"}>${escapeHtml(content)}</textarea>
            ${canEdit ? `
              <div class="config-editor-actions">
                ${isJson ? `<span class="config-json-status" id="jsonStatus_${k}"></span>` : ""}
                <button class="ghost-btn config-save-btn" data-config-key="${k}" type="button">Save</button>
              </div>
            ` : ""}
          </div>
        `;
      }).join("")}

      ${canCopy && org ? renderCopyPanel(data) : ""}
    </section>
  `;

  attachConfigHandlers(data);
}

function renderCopyPanel(data) {
  const org = data.org || {};
  const orgId = org.org_id || "";

  return `
    <div class="config-copy-section">
      <h3>Copy Config</h3>
      <div class="copy-form">
        <div class="copy-row">
          <label>Source</label>
          <select id="copySourceType">
            <option value="org">Org Config</option>
            <option value="member">Member Config</option>
          </select>
        </div>
        <div class="copy-row copy-member-row hidden" id="copySourceMemberRow">
          <label>Source Member</label>
          <select id="copySourceMember"><option value="">Select member...</option></select>
        </div>
        <div class="copy-row">
          <label>Target</label>
          <select id="copyTargetType">
            <option value="member">Member Config</option>
            <option value="org">Org Config</option>
          </select>
        </div>
        <div class="copy-row copy-member-row hidden" id="copyTargetMemberRow">
          <label>Target Member</label>
          <select id="copyTargetMember"><option value="">Select member...</option></select>
        </div>
        <div class="copy-row">
          <label>Mode</label>
          <select id="copyMode">
            <option value="replace_all">Replace All</option>
            <option value="merge_missing">Merge Missing</option>
          </select>
        </div>
        <div class="copy-row">
          <label>Reason</label>
          <input id="copyReason" type="text" placeholder="e.g. Copy winning config to member" />
        </div>
        <button id="copyConfigBtn" class="ghost-btn" type="button">Copy Config</button>
      </div>
    </div>
  `;
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function getActiveTab() {
  try {
    const active = document.querySelector(".config-tab.active");
    if (active) return active.dataset.configKey;
  } catch {}
  return CONFIG_KEYS[0];
}

function attachConfigHandlers(data) {
  attachTabHandlers();
  attachSaveHandlers(data);
  attachCopyHandlers(data);
  attachVersionHandlers(data);
}

function attachTabHandlers() {
  document.querySelectorAll(".config-tab").forEach(tab => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".config-tab").forEach(t => t.classList.remove("active"));
      document.querySelectorAll(".config-editor-pane").forEach(p => p.classList.add("hidden"));
      tab.classList.add("active");
      const key = tab.dataset.configKey;
      const pane = document.querySelector(`.config-editor-pane[data-config-key="${key}"]`);
      if (pane) pane.classList.remove("hidden");
    });
  });
}

function attachSaveHandlers(data) {
  if (!data.can_edit) return;

  document.querySelectorAll(".config-save-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const key = btn.dataset.configKey;
      const textarea = document.querySelector(`.config-editor-textarea[data-config-key="${key}"]`);
      if (!textarea) return;
      const content = textarea.value;

      if (JSON_KEYS.has(key)) {
        try {
          JSON.parse(content);
        } catch {
          const statusEl = document.getElementById(`jsonStatus_${key}`);
          if (statusEl) statusEl.textContent = "Invalid JSON";
          return;
        }
      }

      btn.disabled = true;
      try {
        const orgId = data.org ? data.org.org_id : null;
        const ownerType = data.owner_type;

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

        setStatus(`Saved ${KEY_LABELS[key] || key}`);
        clearCache("/api/config/effective");
        await renderConfigPanel();
      } catch (err) {
        setStatus(`Save failed: ${String(err)}`);
      } finally {
        btn.disabled = false;
      }
    });
  });
}

function attachCopyHandlers(data) {
  if (!data.can_copy || !data.org) return;

  const orgId = data.org.org_id;

  loadMemberDropdown(orgId, "copySourceMember");
  loadMemberDropdown(orgId, "copyTargetMember");

  document.getElementById("copySourceType").addEventListener("change", () => {
    const isMember = document.getElementById("copySourceType").value === "member";
    document.getElementById("copySourceMemberRow").classList.toggle("hidden", !isMember);
  });

  document.getElementById("copyTargetType").addEventListener("change", () => {
    const isMember = document.getElementById("copyTargetType").value === "member";
    document.getElementById("copyTargetMemberRow").classList.toggle("hidden", !isMember);
  });

  document.getElementById("copyConfigBtn").addEventListener("click", async () => {
    const sourceType = document.getElementById("copySourceType").value;
    const targetType = document.getElementById("copyTargetType").value;
    const mode = document.getElementById("copyMode").value;
    const reason = document.getElementById("copyReason").value.trim() || "config_copy";

    const body = {
      source_type: sourceType,
      target_type: targetType,
      mode,
      reason,
    };

    if (sourceType === "member") {
      body.source_user_id = document.getElementById("copySourceMember").value;
    }
    if (targetType === "member") {
      body.target_user_id = document.getElementById("copyTargetMember").value;
    }

    const btn = document.getElementById("copyConfigBtn");
    btn.disabled = true;
    try {
      await fetchJSON(`/api/orgs/${orgId}/configs/copy`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setStatus("Config copied successfully!");
      clearCache("/api/config/effective");
      await renderConfigPanel();
    } catch (err) {
      setStatus(`Copy failed: ${String(err)}`);
    } finally {
      btn.disabled = false;
    }
  });
}

function attachVersionHandlers(data) {
  if (!data.can_view_versions || !data.config_id) return;

  document.querySelector(".version-history-btn").addEventListener("click", async () => {
    renderVersionModal(data);
  });
}

async function renderVersionModal(data) {
  const configId = data.config_id;
  const modal = document.createElement("div");
  modal.className = "modal-overlay";
  modal.style.cssText = "position:fixed;inset:0;z-index:1000;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.5);";

  modal.innerHTML = `
    <div class="modal-content" style="background:var(--surface);border:1px solid var(--line);border-radius:12px;max-width:800px;width:90%;max-height:80vh;overflow-y:auto;padding:1.5rem;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;">
        <h2 style="margin:0;">Version History</h2>
        <button class="modal-close-btn ghost-btn" type="button" style="font-size:1.2rem;">✕</button>
      </div>
      <div id="versionListContainer" style="min-height:100px;">
        <p class="hint">Loading versions...</p>
      </div>
    </div>
  `;
  document.body.appendChild(modal);

  modal.querySelector(".modal-close-btn").addEventListener("click", () => modal.remove());
  modal.addEventListener("click", (e) => { if (e.target === modal) modal.remove(); });

  try {
    const versionsData = await fetchJSON(`/api/config/${configId}/versions`);
    const container = document.getElementById("versionListContainer");
    const versions = versionsData.versions || [];
    const canRollback = data.can_rollback === true;

    if (!versions.length) {
      container.innerHTML = `<p class="hint">No version history yet.</p>`;
      return;
    }

    container.innerHTML = `
      <table style="width:100%;border-collapse:collapse;">
        <thead>
          <tr style="border-bottom:1px solid var(--line);">
            <th style="text-align:left;padding:0.4rem;">Date</th>
            <th style="text-align:left;padding:0.4rem;">Changed By</th>
            <th style="text-align:left;padding:0.4rem;">Changed Keys</th>
            <th style="text-align:left;padding:0.4rem;">Reason</th>
            <th style="text-align:left;padding:0.4rem;"></th>
          </tr>
        </thead>
        <tbody>
          ${versions.map(v => `
            <tr style="border-bottom:1px solid var(--line);">
              <td style="padding:0.4rem;">${v.created_at ? new Date(v.created_at * 1000).toLocaleString() : "?"}</td>
              <td style="padding:0.4rem;">${escapeHtml(v.changed_by_email || v.changed_by_user_id || "")}</td>
              <td style="padding:0.4rem;">${(v.changed_keys || []).join(", ") || "—"}</td>
              <td style="padding:0.4rem;">${escapeHtml(v.change_reason || "")}</td>
              <td style="padding:0.4rem;">
                <button class="ghost-btn version-detail-btn" data-version-id="${v.version_id}" data-config-id="${configId}" type="button">View</button>
                ${canRollback ? `<button class="ghost-btn version-rollback-btn" data-version-id="${v.version_id}" data-config-id="${configId}" type="button" style="color:var(--accent-coral);margin-left:0.3rem;">Rollback</button>` : ""}
              </td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    `;

    container.querySelectorAll(".version-detail-btn").forEach(btn => {
      btn.addEventListener("click", async () => {
        const vid = btn.dataset.versionId;
        const cid = btn.dataset.configId;
        try {
          const detail = await fetchJSON(`/api/config/${cid}/versions/${vid}`);
          showVersionSnapshot(detail, canRollback);
        } catch (err) {
          setStatus(`Failed to load version: ${String(err)}`);
        }
      });
    });

    if (canRollback) {
      container.querySelectorAll(".version-rollback-btn").forEach(btn => {
        btn.addEventListener("click", async () => {
          const vid = btn.dataset.versionId;
          const cid = btn.dataset.configId;
          if (!confirm(`Rollback config to this version? A snapshot of current config will be saved.`)) return;
          btn.disabled = true;
          try {
            const result = await fetchJSON(`/api/config/${cid}/rollback/${vid}`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ reason: "rollback_from_ui" }),
            });
            setStatus(`Config rolled back to ${vid}`);
            modal.remove();
            clearCache("/api/config/effective");
            await renderConfigPanel();
          } catch (err) {
            setStatus(`Rollback failed: ${String(err)}`);
          } finally {
            btn.disabled = false;
          }
        });
      });
    }
  } catch (err) {
    const container = document.getElementById("versionListContainer");
    container.innerHTML = `<p class="hint">Failed to load versions: ${escapeHtml(String(err))}</p>`;
  }
}

function showVersionSnapshot(version, canRollback) {
  const snapshot = version.snapshot || {};
  const files = snapshot.files || {};

  const modal = document.createElement("div");
  modal.className = "modal-overlay";
  modal.style.cssText = "position:fixed;inset:0;z-index:1001;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.5);";

  modal.innerHTML = `
    <div class="modal-content" style="background:var(--surface);border:1px solid var(--line);border-radius:12px;max-width:800px;width:90%;max-height:80vh;overflow-y:auto;padding:1.5rem;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;">
        <h2 style="margin:0;">Version Snapshot</h2>
        <button class="modal-close-btn ghost-btn" type="button" style="font-size:1.2rem;">✕</button>
      </div>
      <p class="hint">Changed by: ${escapeHtml(version.changed_by_email || version.changed_by_user_id || "?")} — ${version.created_at ? new Date(version.created_at * 1000).toLocaleString() : "?"}</p>
      <p class="hint">Reason: ${escapeHtml(version.change_reason || "—")}</p>
      <p class="hint">Changed keys: ${(version.changed_keys || []).join(", ") || "—"}</p>

      <div class="version-snapshot-tabs">
        ${CONFIG_KEYS.map(k => {
          const entry = files[k] || {};
          const content = entry.content || "";
          return `
            <button class="snapshot-tab ghost-btn" data-key="${k}" type="button" style="margin:0.2rem;padding:0.3rem 0.6rem;border:1px solid var(--line);border-radius:4px;">
              ${escapeHtml(KEY_LABELS[k] || k)}
            </button>
          `;
        }).join("")}
      </div>

      ${CONFIG_KEYS.map(k => {
        const entry = files[k] || {};
        const content = entry.content || "";
        return `
          <div class="snapshot-pane hidden" data-key="${k}">
            <textarea class="config-editor-textarea" readonly spellcheck="false" style="width:100%;min-height:300px;margin-top:0.5rem;">${escapeHtml(content)}</textarea>
          </div>
        `;
      }).join("")}
    </div>
  `;
  document.body.appendChild(modal);

  modal.querySelector(".modal-close-btn").addEventListener("click", () => modal.remove());
  modal.addEventListener("click", (e) => { if (e.target === modal) modal.remove(); });

  // Show first tab
  const firstTab = modal.querySelector(".snapshot-tab");
  const firstPane = modal.querySelector(".snapshot-pane");
  if (firstTab) firstTab.style.borderColor = "var(--accent)";
  if (firstPane) firstPane.classList.remove("hidden");

  modal.querySelectorAll(".snapshot-tab").forEach(tab => {
    tab.addEventListener("click", () => {
      modal.querySelectorAll(".snapshot-tab").forEach(t => t.style.borderColor = "var(--line)");
      modal.querySelectorAll(".snapshot-pane").forEach(p => p.classList.add("hidden"));
      tab.style.borderColor = "var(--accent)";
      const pane = modal.querySelector(`.snapshot-pane[data-key="${tab.dataset.key}"]`);
      if (pane) pane.classList.remove("hidden");
    });
  });
}

async function loadMemberDropdown(orgId, selectId) {
  try {
    const members = await fetchJSON(`/api/orgs/${orgId}/members`);
    const select = document.getElementById(selectId);
    if (!select) return;
    select.innerHTML = `<option value="">Select member...</option>` + members.map(m =>
      `<option value="${m.user_id}">${escapeHtml(m.email)}</option>`
    ).join("");
  } catch {}
}
