import { fetchJSON } from "./api.js";
import { getAuthUser } from "./auth.js";

let currentSection = "overview";
let currentUser = null;

function escapeHtml(v) {
  if (v == null) return "";
  const d = document.createElement("div");
  d.textContent = String(v);
  return d.innerHTML;
}

function formatDate(ts) {
  if (!ts || ts <= 0) return "-";
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch {
    return String(ts);
  }
}

function showLoading(container, msg) {
  container.innerHTML = `<p class="hint">${escapeHtml(msg || "Loading...")}</p>`;
}

function showError(container, err) {
  const detail = err && err.detail ? err.detail : String(err || "Unknown error");
  container.innerHTML = `<div class="card" style="border-color:var(--accent-coral);padding:1rem;"><p style="color:var(--accent-coral);font-weight:600;">Error</p><p>${escapeHtml(detail)}</p></div>`;
}

function showEmpty(container, msg) {
  container.innerHTML = `<p class="hint">${escapeHtml(msg || "No data.")}</p>`;
}

function showTable(container, columns, rows, rowRenderer) {
  if (!rows || !rows.length) {
    showEmpty(container, "No results.");
    return;
  }
  const table = document.createElement("table");
  table.className = "admin-table";
  const thead = document.createElement("thead");
  const tr = document.createElement("tr");
  columns.forEach((c) => {
    const th = document.createElement("th");
    th.textContent = c;
    tr.appendChild(th);
  });
  thead.appendChild(tr);
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  rows.forEach((row, i) => {
    const rtr = document.createElement("tr");
    rowRenderer(rtr, row, i);
    tbody.appendChild(rtr);
  });
  table.appendChild(tbody);
  container.innerHTML = "";
  container.appendChild(table);
}

function paginationBar(page, pages, total, cb) {
  const bar = document.createElement("div");
  bar.className = "admin-pagination";
  const info = document.createElement("span");
  info.className = "hint";
  info.textContent = `Page ${page} of ${pages} (${total} total)`;
  bar.appendChild(info);
  if (page > 1) {
    const prev = document.createElement("button");
    prev.className = "ghost-btn";
    prev.textContent = "Previous";
    prev.addEventListener("click", () => cb(page - 1));
    bar.appendChild(prev);
  }
  if (page < pages) {
    const next = document.createElement("button");
    next.className = "ghost-btn";
    next.textContent = "Next";
    next.addEventListener("click", () => cb(page + 1));
    bar.appendChild(next);
  }
  return bar;
}

function refreshBtn(cb) {
  const btn = document.createElement("button");
  btn.className = "ghost-btn";
  btn.textContent = "Refresh";
  btn.addEventListener("click", cb);
  return btn;
}

async function adminFetch(path, opts = {}) {
  const resp = await fetch(path, { credentials: "same-origin", ...opts });
  if (resp.status === 401) throw { status: 401, detail: "Authentication required. Please login." };
  if (resp.status === 403) throw { status: 403, detail: "Access denied. Super admin access required." };
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = body.detail || detail;
    } catch {}
    throw { status: resp.status, detail };
  }
  return resp.json();
}

function confirmAction(msg) {
  return confirm(msg);
}

async function hashRoute() {
  const hash = window.location.hash.replace("#admin/", "").split("?")[0] || "overview";
  if (hash !== currentSection) {
    currentSection = hash;
    renderAdminPanel();
  }
}

// ─── Section 1: Overview ─────────────────────────────────────────────────

async function renderOverview(container) {
  showLoading(container, "Loading overview...");
  try {
    const [overview, stats, health] = await Promise.all([
      adminFetch("/api/admin/overview"),
      adminFetch("/api/admin/stats"),
      adminFetch("/api/admin/health"),
    ]);
    container.innerHTML = "";
    const header = document.createElement("div");
    header.className = "admin-section-header";
    header.innerHTML = "<h3>Platform Overview</h3>";
    header.appendChild(refreshBtn(() => renderOverview(container)));
    container.appendChild(header);

    const cards = document.createElement("div");
    cards.className = "admin-stat-cards";

    const statGroups = [
      { title: "Users", items: [
        { label: "Total Users", value: stats.total_users },
        { label: "Active Users", value: stats.active_users },
        { label: "Super Admins", value: stats.super_admins },
        { label: "New Today", value: overview.users.new_today },
        { label: "New This Week", value: overview.users.new_this_week },
      ]},
      { title: "Organizations", items: [
        { label: "Total Orgs", value: stats.total_orgs },
        { label: "Active Orgs", value: stats.active_orgs },
        { label: "Active Members", value: stats.total_org_members },
        { label: "Pending Invites", value: stats.pending_invites },
      ]},
      { title: "Content", items: [
        { label: "Total Configs", value: stats.total_configs },
        { label: "Active Configs", value: stats.active_configs },
        { label: "Config Versions", value: stats.total_config_versions },
        { label: "Total Runs", value: stats.total_runs },
        { label: "Total Images", value: stats.total_images },
      ]},
      { title: "System", items: [
        { label: "Total Sessions", value: stats.total_sessions },
        { label: "Active Sessions", value: overview.sessions.active },
        { label: "Audit Logs", value: stats.total_audit_logs },
      ]},
    ];

    statGroups.forEach((group) => {
      const groupEl = document.createElement("div");
      groupEl.className = "admin-stat-group";
      const gTitle = document.createElement("h4");
      gTitle.textContent = group.title;
      groupEl.appendChild(gTitle);
      group.items.forEach((item) => {
        const card = document.createElement("div");
        card.className = "admin-stat-card";
        card.innerHTML = `<span class="admin-stat-value">${escapeHtml(String(item.value))}</span><span class="admin-stat-label">${escapeHtml(item.label)}</span>`;
        groupEl.appendChild(card);
      });
      cards.appendChild(groupEl);
    });
    container.appendChild(cards);

    const healthEl = document.createElement("div");
    healthEl.className = "admin-health-status";
    const statusIcon = health.status === "ok" ? "✓" : "✗";
    const statusClass = health.status === "ok" ? "health-ok" : "health-degraded";
    healthEl.innerHTML = `<h4>System Health</h4><p class="${statusClass}">${statusIcon} Backend: ${escapeHtml(health.status)} | Database: ${escapeHtml(health.database)}</p>`;
    container.appendChild(healthEl);
  } catch (err) {
    showError(container, err);
  }
}

// ─── Section 2: Users ────────────────────────────────────────────────────

async function renderUsers(container, page = 1, search = "") {
  showLoading(container, "Loading users...");
  try {
    const params = new URLSearchParams({ page: String(page), per_page: "50" });
    if (search) params.set("search", search);
    const data = await adminFetch("/api/admin/users?" + params.toString());
    container.innerHTML = "";
    const header = document.createElement("div");
    header.className = "admin-section-header";
    header.innerHTML = "<h3>Users</h3>";

    const searchInput = document.createElement("input");
    searchInput.type = "text";
    searchInput.placeholder = "Search by email, name, or ID...";
    searchInput.value = search;
    searchInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") renderUsers(container, 1, searchInput.value.trim());
    });
    const searchBtn = document.createElement("button");
    searchBtn.className = "ghost-btn";
    searchBtn.textContent = "Search";
    searchBtn.addEventListener("click", () => renderUsers(container, 1, searchInput.value.trim()));

    header.appendChild(searchInput);
    header.appendChild(searchBtn);
    header.appendChild(refreshBtn(() => renderUsers(container, page, search)));
    container.appendChild(header);

    showTable(container,
      ["Email", "Name", "Active", "Super Admin", "Created", "Actions"],
      data.items,
      (tr, u) => {
        tr.innerHTML = `
          <td>${escapeHtml(u.email)}</td>
          <td>${escapeHtml(u.display_name)}</td>
          <td>${u.is_active ? "✓" : "✗"}</td>
          <td>${u.is_super_admin ? "✓" : ""}</td>
          <td>${formatDate(u.created_at)}</td>
          <td class="admin-actions"></td>
        `;
        const actionCell = tr.querySelector(".admin-actions");
        const viewBtn = document.createElement("button");
        viewBtn.className = "ghost-btn";
        viewBtn.textContent = "View";
        viewBtn.addEventListener("click", () => showUserDetail(u.user_id));
        actionCell.appendChild(viewBtn);

        if (u.user_id !== currentUser?.user_id) {
          const disableBtn = document.createElement("button");
          disableBtn.className = "ghost-btn";
          disableBtn.textContent = u.is_active ? "Disable" : "Enable";
          disableBtn.addEventListener("click", async () => {
            if (u.is_active && !confirmAction(`Disable user ${u.email}?`)) return;
            if (!u.is_active && !confirmAction(`Enable user ${u.email}?`)) return;
            try {
              await adminFetch("/api/admin/users/" + encodeURIComponent(u.user_id), {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ is_active: !u.is_active, reason: u.is_active ? "disabled_from_admin_ui" : undefined }),
              });
              renderUsers(container, page, search);
            } catch (err) { showError(container, err); }
          });
          actionCell.appendChild(disableBtn);

          const saBtn = document.createElement("button");
          saBtn.className = "ghost-btn";
          saBtn.textContent = u.is_super_admin ? "Revoke SA" : "Grant SA";
          saBtn.addEventListener("click", async () => {
            if (u.is_super_admin && !confirmAction(`Revoke super admin for ${u.email}?`)) return;
            if (!u.is_super_admin && !confirmAction(`Grant super admin to ${u.email}?`)) return;
            try {
              await adminFetch("/api/admin/users/" + encodeURIComponent(u.user_id), {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ is_super_admin: !u.is_super_admin }),
              });
              renderUsers(container, page, search);
            } catch (err) { showError(container, err); }
          });
          actionCell.appendChild(saBtn);
        }
      }
    );

    if (data.pages > 1) {
      container.appendChild(paginationBar(data.page, data.pages, data.total, (p) => renderUsers(container, p, search)));
    }
  } catch (err) {
    showError(container, err);
  }
}

async function showUserDetail(userId) {
  const panel = document.getElementById("adminPanel");
  const detail = document.createElement("div");
  detail.className = "admin-detail-overlay";
  detail.innerHTML = `<div class="admin-detail-card"><div class="admin-detail-close">✕</div><div id="adminDetailContent"><p class="hint">Loading...</p></div></div>`;
  panel.appendChild(detail);

  detail.querySelector(".admin-detail-close").addEventListener("click", () => detail.remove());
  detail.addEventListener("click", (e) => { if (e.target === detail) detail.remove(); });

  const content = document.getElementById("adminDetailContent");
  try {
    const user = await adminFetch("/api/admin/users/" + encodeURIComponent(userId));
    const sessions = await adminFetch("/api/admin/users/" + encodeURIComponent(userId) + "/sessions");

    content.innerHTML = `
      <h3>User Detail</h3>
      <table class="admin-table">
        <tbody>
          <tr><td>User ID</td><td>${escapeHtml(user.user_id)}</td></tr>
          <tr><td>Email</td><td>${escapeHtml(user.email)}</td></tr>
          <tr><td>Display Name</td><td>${escapeHtml(user.display_name)}</td></tr>
          <tr><td>Active</td><td>${user.is_active ? "✓" : "✗"}</td></tr>
          <tr><td>Super Admin</td><td>${user.is_super_admin ? "✓" : "✗"}</td></tr>
          <tr><td>Platform Admin</td><td>${user.is_platform_admin ? "✓" : "✗"}</td></tr>
          <tr><td>Created</td><td>${formatDate(user.created_at)}</td></tr>
          <tr><td>Updated</td><td>${formatDate(user.updated_at)}</td></tr>
        </tbody>
      </table>
      <h4>Sessions (${sessions.sessions.length})</h4>
    `;

    if (sessions.sessions.length) {
      showTable(content, ["Session ID", "Created", "Expires", "Expired"], sessions.sessions, (tr, s) => {
        tr.innerHTML = `<td>${escapeHtml(s.session_id.slice(0, 12))}...</td><td>${formatDate(s.created_at)}</td><td>${formatDate(s.expires_at)}</td><td>${s.is_expired ? "✓" : ""}</td>`;
      });
    } else {
      const noSess = document.createElement("p");
      noSess.className = "hint";
      noSess.textContent = "No active sessions.";
      content.appendChild(noSess);
    }

    if (userId !== currentUser?.user_id) {
      const revokeBtn = document.createElement("button");
      revokeBtn.className = "ghost-btn";
      revokeBtn.textContent = "Revoke All Sessions";
      revokeBtn.style.marginTop = "0.5rem";
      revokeBtn.addEventListener("click", async () => {
        if (!confirmAction("Revoke all sessions for this user?")) return;
        try {
          await adminFetch("/api/admin/users/" + encodeURIComponent(userId) + "/sessions", { method: "DELETE" });
          detail.remove();
          renderUsers(document.getElementById("adminPanelContent"), 1, "");
        } catch (err) { showError(content, err); }
      });
      content.appendChild(revokeBtn);
    }
  } catch (err) {
    showError(content, err);
  }
}

// ─── Section 3: Individual Users ─────────────────────────────────────────

async function renderIndividualUsers(container, page = 1, search = "") {
  showLoading(container, "Loading individual users...");
  try {
    const params = new URLSearchParams({ page: String(page), per_page: "50" });
    if (search) params.set("search", search);
    const data = await adminFetch("/api/admin/individual-users?" + params.toString());
    container.innerHTML = "";
    const header = document.createElement("div");
    header.className = "admin-section-header";
    header.innerHTML = "<h3>Individual Users</h3><p class=\"hint\">Users with no active organization membership</p>";

    const searchInput = document.createElement("input");
    searchInput.type = "text";
    searchInput.placeholder = "Search...";
    searchInput.value = search;
    searchInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") renderIndividualUsers(container, 1, searchInput.value.trim());
    });
    const searchBtn = document.createElement("button");
    searchBtn.className = "ghost-btn";
    searchBtn.textContent = "Search";
    searchBtn.addEventListener("click", () => renderIndividualUsers(container, 1, searchInput.value.trim()));

    header.appendChild(searchInput);
    header.appendChild(searchBtn);
    header.appendChild(refreshBtn(() => renderIndividualUsers(container, page, search)));
    container.appendChild(header);

    showTable(container,
      ["Email", "Name", "Active", "Super Admin", "Created", "Actions"],
      data.items,
      (tr, u) => {
        tr.innerHTML = `
          <td>${escapeHtml(u.email)}</td>
          <td>${escapeHtml(u.display_name)}</td>
          <td>${u.is_active ? "✓" : "✗"}</td>
          <td>${u.is_super_admin ? "✓" : ""}</td>
          <td>${formatDate(u.created_at)}</td>
          <td class="admin-actions"></td>
        `;
        const ac = tr.querySelector(".admin-actions");
        const vb = document.createElement("button");
        vb.className = "ghost-btn";
        vb.textContent = "View";
        vb.addEventListener("click", () => showUserDetail(u.user_id));
        ac.appendChild(vb);

        if (u.user_id !== currentUser?.user_id) {
          const db = document.createElement("button");
          db.className = "ghost-btn";
          db.textContent = u.is_active ? "Disable" : "Enable";
          db.addEventListener("click", async () => {
            if (u.is_active && !confirmAction(`Disable ${u.email}?`)) return;
            try {
              await adminFetch("/api/admin/users/" + encodeURIComponent(u.user_id), {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ is_active: !u.is_active }),
              });
              renderIndividualUsers(container, page, search);
            } catch (err) { showError(container, err); }
          });
          ac.appendChild(db);
        }
      }
    );

    if (data.pages > 1) {
      container.appendChild(paginationBar(data.page, data.pages, data.total, (p) => renderIndividualUsers(container, p, search)));
    }
  } catch (err) {
    showError(container, err);
  }
}

// ─── Section 4: Organizations ────────────────────────────────────────────

async function renderOrgs(container, page = 1, search = "") {
  showLoading(container, "Loading organizations...");
  try {
    const params = new URLSearchParams({ page: String(page), per_page: "50" });
    if (search) params.set("search", search);
    const data = await adminFetch("/api/admin/orgs?" + params.toString());
    container.innerHTML = "";
    const header = document.createElement("div");
    header.className = "admin-section-header";
    header.innerHTML = "<h3>Organizations</h3>";

    const searchInput = document.createElement("input");
    searchInput.type = "text";
    searchInput.placeholder = "Search by name, domain, or ID...";
    searchInput.value = search;
    searchInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") renderOrgs(container, 1, searchInput.value.trim());
    });
    const searchBtn = document.createElement("button");
    searchBtn.className = "ghost-btn";
    searchBtn.textContent = "Search";
    searchBtn.addEventListener("click", () => renderOrgs(container, 1, searchInput.value.trim()));

    header.appendChild(searchInput);
    header.appendChild(searchBtn);
    header.appendChild(refreshBtn(() => renderOrgs(container, page, search)));
    container.appendChild(header);

    showTable(container,
      ["Name", "Domain", "Active", "Config Mode", "Created", "Actions"],
      data.items,
      (tr, o) => {
        tr.innerHTML = `
          <td>${escapeHtml(o.name)}</td>
          <td>${escapeHtml(o.domain)}</td>
          <td>${o.is_active !== false ? "✓" : "✗"}</td>
          <td>${escapeHtml(o.config_mode || "-")}</td>
          <td>${formatDate(o.created_at)}</td>
          <td class="admin-actions"></td>
        `;
        const ac = tr.querySelector(".admin-actions");
        const vb = document.createElement("button");
        vb.className = "ghost-btn";
        vb.textContent = "View";
        vb.addEventListener("click", () => showOrgDetail(o.org_id));
        ac.appendChild(vb);

        const eb = document.createElement("button");
        eb.className = "ghost-btn";
        eb.textContent = "Edit";
        eb.addEventListener("click", () => showOrgEdit(o));
        ac.appendChild(eb);
      }
    );

    if (data.pages > 1) {
      container.appendChild(paginationBar(data.page, data.pages, data.total, (p) => renderOrgs(container, p, search)));
    }
  } catch (err) {
    showError(container, err);
  }
}

async function showOrgDetail(orgId) {
  const panel = document.getElementById("adminPanel");
  const detail = document.createElement("div");
  detail.className = "admin-detail-overlay";
  detail.innerHTML = `<div class="admin-detail-card admin-detail-card-wide"><div class="admin-detail-close">✕</div><div id="adminDetailContent"><p class="hint">Loading...</p></div></div>`;
  panel.appendChild(detail);

  detail.querySelector(".admin-detail-close").addEventListener("click", () => detail.remove());
  detail.addEventListener("click", (e) => { if (e.target === detail) detail.remove(); });

  const content = document.getElementById("adminDetailContent");
  try {
    const org = await adminFetch("/api/admin/orgs/" + encodeURIComponent(orgId));
    content.innerHTML = `
      <h3>Organization Detail</h3>
      <table class="admin-table">
        <tbody>
          <tr><td>Org ID</td><td>${escapeHtml(org.org.org_id)}</td></tr>
          <tr><td>Name</td><td>${escapeHtml(org.org.name)}</td></tr>
          <tr><td>Domain</td><td>${escapeHtml(org.org.domain)}</td></tr>
          <tr><td>Active</td><td>${org.org.is_active !== false ? "✓" : "✗"}</td></tr>
          <tr><td>Config Mode</td><td>${escapeHtml(org.org.config_mode || "-")}</td></tr>
          <tr><td>Created</td><td>${formatDate(org.org.created_at)}</td></tr>
          <tr><td>Updated</td><td>${formatDate(org.org.updated_at)}</td></tr>
        </tbody>
      </table>
      <h4>Members (${org.members.length})</h4>
    `;
    if (org.members.length) {
      showTable(content, ["User ID", "Email", "Role", "Status", "Joined"], org.members, (tr, m) => {
        tr.innerHTML = `<td>${escapeHtml(m.user_id || "")}</td><td>${escapeHtml(m.email || "")}</td><td>${escapeHtml(m.role)}</td><td>${escapeHtml(m.status)}</td><td>${formatDate(m.joined_at)}</td>`;
      });
    } else {
      const nm = document.createElement("p");
      nm.className = "hint";
      nm.textContent = "No members.";
      content.appendChild(nm);
    }

    const ih = document.createElement("h4");
    ih.textContent = `Invites (${org.invites.length})`;
    content.appendChild(ih);
    if (org.invites.length) {
      showTable(content, ["Email", "Role", "Status", "Created"], org.invites, (tr, i) => {
        tr.innerHTML = `<td>${escapeHtml(i.email)}</td><td>${escapeHtml(i.role)}</td><td>${escapeHtml(i.status)}</td><td>${formatDate(i.created_at)}</td>`;
      });
    } else {
      const ni = document.createElement("p");
      ni.className = "hint";
      ni.textContent = "No invites.";
      content.appendChild(ni);
    }
  } catch (err) {
    showError(content, err);
  }
}

function showOrgEdit(org) {
  const panel = document.getElementById("adminPanel");
  const detail = document.createElement("div");
  detail.className = "admin-detail-overlay";
  detail.innerHTML = `<div class="admin-detail-card"><div class="admin-detail-close">✕</div><div id="adminDetailContent"><p class="hint">Loading...</p></div></div>`;
  panel.appendChild(detail);

  detail.querySelector(".admin-detail-close").addEventListener("click", () => detail.remove());
  detail.addEventListener("click", (e) => { if (e.target === detail) detail.remove(); });

  const content = document.getElementById("adminDetailContent");
  const isActive = org.is_active !== false;
  const configMode = org.config_mode || "shared_org_config";
  content.innerHTML = `
    <h3>Edit Organization</h3>
    <div class="admin-form">
      <label>Name</label>
      <input id="editOrgName" type="text" value="${escapeHtml(org.name)}" />
      <label>Active</label>
      <select id="editOrgActive">
        <option value="true" ${isActive ? "selected" : ""}>Active</option>
        <option value="false" ${!isActive ? "selected" : ""}>Disabled</option>
      </select>
      <label>Config Mode</label>
      <select id="editOrgConfigMode">
        <option value="shared_org_config" ${configMode === "shared_org_config" ? "selected" : ""}>Shared Org Config</option>
        <option value="individual_member_config" ${configMode === "individual_member_config" ? "selected" : ""}>Individual Member Config</option>
      </select>
      <button id="saveOrgEdit" class="ghost-btn" style="margin-top:0.5rem;">Save</button>
    </div>
  `;

  document.getElementById("saveOrgEdit").addEventListener("click", async () => {
    const payload = {
      name: document.getElementById("editOrgName").value.trim(),
      is_active: document.getElementById("editOrgActive").value === "true",
      config_mode: document.getElementById("editOrgConfigMode").value,
    };
    if (!payload.name) return;
    try {
      await adminFetch("/api/admin/orgs/" + encodeURIComponent(org.org_id), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      detail.remove();
      renderOrgs(document.getElementById("adminPanelContent"), 1, "");
    } catch (err) {
      showError(content, err);
    }
  });
}

// ─── Section 5: Configs ──────────────────────────────────────────────────

async function renderConfigs(container, page = 1, ownerType = "") {
  showLoading(container, "Loading configs...");
  try {
    const params = new URLSearchParams({ page: String(page), per_page: "50" });
    if (ownerType) params.set("owner_type", ownerType);
    const data = await adminFetch("/api/admin/configs?" + params.toString());
    container.innerHTML = "";
    const header = document.createElement("div");
    header.className = "admin-section-header";
    header.innerHTML = "<h3>Configs</h3>";

    const filter = document.createElement("select");
    filter.innerHTML = `<option value="">All</option><option value="user" ${ownerType === "user" ? "selected" : ""}>User</option><option value="org" ${ownerType === "org" ? "selected" : ""}>Org</option>`;
    filter.addEventListener("change", () => renderConfigs(container, 1, filter.value));
    header.appendChild(filter);
    header.appendChild(refreshBtn(() => renderConfigs(container, page, ownerType)));
    container.appendChild(header);

    showTable(container,
      ["Config ID", "Owner Type", "Owner ID", "Scope", "Active", "Updated", "Actions"],
      data.items,
      (tr, c) => {
        tr.innerHTML = `
          <td>${escapeHtml(c.config_id || "").slice(0, 16)}...</td>
          <td>${escapeHtml(c.owner_type || "")}</td>
          <td>${escapeHtml((c.owner_id || "")).slice(0, 16)}...</td>
          <td>${escapeHtml(c.config_scope || "")}</td>
          <td>${c.is_active !== false ? "✓" : "✗"}</td>
          <td>${formatDate(c.updated_at)}</td>
          <td class="admin-actions"></td>
        `;
        const ac = tr.querySelector(".admin-actions");
        const vb = document.createElement("button");
        vb.className = "ghost-btn";
        vb.textContent = "View";
        vb.addEventListener("click", () => showConfigDetail(c.config_id, false));
        ac.appendChild(vb);

        const cb = document.createElement("button");
        cb.className = "ghost-btn";
        cb.textContent = "Content";
        cb.addEventListener("click", () => showConfigDetail(c.config_id, true));
        ac.appendChild(cb);
      }
    );

    if (data.pages > 1) {
      container.appendChild(paginationBar(data.page, data.pages, data.total, (p) => renderConfigs(container, p, ownerType)));
    }
  } catch (err) {
    showError(container, err);
  }
}

async function showConfigDetail(configId, includeContent) {
  const panel = document.getElementById("adminPanel");
  const detail = document.createElement("div");
  detail.className = "admin-detail-overlay";
  detail.innerHTML = `<div class="admin-detail-card admin-detail-card-wide"><div class="admin-detail-close">✕</div><div id="adminDetailContent"><p class="hint">Loading...</p></div></div>`;
  panel.appendChild(detail);

  detail.querySelector(".admin-detail-close").addEventListener("click", () => detail.remove());
  detail.addEventListener("click", (e) => { if (e.target === detail) detail.remove(); });

  const content = document.getElementById("adminDetailContent");
  try {
    const params = includeContent ? "?include_content=true" : "";
    const cfg = await adminFetch("/api/admin/configs/" + encodeURIComponent(configId) + params);

    let html = `<h3>Config Detail</h3>
      <table class="admin-table"><tbody>
        <tr><td>Config ID</td><td>${escapeHtml(cfg.config.config_id || "")}</td></tr>
        <tr><td>Owner Type</td><td>${escapeHtml(cfg.config.owner_type || "")}</td></tr>
        <tr><td>Owner ID</td><td>${escapeHtml(cfg.config.owner_id || "")}</td></tr>
        <tr><td>Scope</td><td>${escapeHtml(cfg.config.config_scope || "")}</td></tr>
        <tr><td>Mode</td><td>${escapeHtml(cfg.config.config_mode || "")}</td></tr>
        <tr><td>Source</td><td>${escapeHtml(cfg.config.source || "")}</td></tr>
        <tr><td>Active</td><td>${cfg.config.is_active !== false ? "✓" : "✗"}</td></tr>
        <tr><td>Updated</td><td>${formatDate(cfg.config.updated_at)}</td></tr>
      </tbody></table>`;

    if (includeContent) {
      html += `<p style="color:var(--accent-coral);font-weight:600;">Config content may contain sensitive prompt/business logic. Do not share externally.</p>`;
      const files = cfg.config.files || {};
      const keyLabels = {
        product_master_doc: "Product Master Doc",
        starting_prompt: "Starting Prompt",
        copy_prompt_templates: "Copy Prompt Templates",
        persona_seeds: "Persona Seeds",
        copy_architecture: "Copy Architecture",
        background_variant: "Background Variant",
        prompt_assembler_templates: "Prompt Assembler Templates",
        conversion_916_prompt: "9:16 Conversion Prompt",
      };
      const keys = Object.keys(keyLabels);
      const tabs = document.createElement("div");
      tabs.className = "admin-config-tabs";

      let first = true;
      keys.forEach((key) => {
        const entry = files[key];
        if (!entry || !entry.content) return;
        const tabBtn = document.createElement("button");
        tabBtn.className = "ghost-btn" + (first ? " active" : "");
        tabBtn.textContent = keyLabels[key] || key;
        const tabContent = document.createElement("div");
        tabContent.className = "admin-config-tab-content" + (first ? "" : " hidden");
        const pre = document.createElement("pre");
        pre.className = "admin-config-pre";
        pre.textContent = entry.content;
        tabContent.appendChild(pre);
        tabs.appendChild(tabBtn);
        tabs.appendChild(tabContent);
        tabBtn.addEventListener("click", () => {
          tabs.querySelectorAll(".admin-config-tab-content").forEach((el) => el.classList.add("hidden"));
          tabs.querySelectorAll("button").forEach((b) => b.classList.remove("active"));
          tabContent.classList.remove("hidden");
          tabBtn.classList.add("active");
        });
        first = false;
      });

      if (first) {
        html += `<p class="hint">No config files present.</p>`;
      } else {
        content.innerHTML = html;
        content.appendChild(tabs);
      }
    } else {
      html += `<p class="hint">File content hidden. Use "Content" button to view.</p>`;
    }

    if (!includeContent) content.innerHTML = html;

    // Versions
    const vh = document.createElement("h4");
    vh.textContent = `Versions (${cfg.versions.length})`;
    content.appendChild(vh);
    if (cfg.versions.length) {
      showTable(content, ["Version ID", "Changed By", "Reason", "Changed Keys", "Created"], cfg.versions, (tr, v) => {
        tr.innerHTML = `<td>${escapeHtml(v.version_id || "").slice(0, 12)}...</td><td>${escapeHtml(v.changed_by_email || "")}</td><td>${escapeHtml(v.change_reason || "")}</td><td>${escapeHtml((v.changed_keys || []).join(", "))}</td><td>${formatDate(v.created_at)}</td>`;
      });
    } else {
      const nv = document.createElement("p");
      nv.className = "hint";
      nv.textContent = "No versions.";
      content.appendChild(nv);
    }
  } catch (err) {
    showError(content, err);
  }
}

// ─── Section 6: Config Copy ──────────────────────────────────────────────

function renderConfigCopy(container) {
  container.innerHTML = `
    <h3>Admin Config Copy</h3>
    <div class="admin-form">
      <label>Source Owner Type</label>
      <select id="copySourceType"><option value="user">User</option><option value="org">Org</option></select>
      <label>Source Owner ID</label>
      <input id="copySourceId" type="text" placeholder="usr_... or org_..." />
      <label>Target Owner Type</label>
      <select id="copyTargetType"><option value="user">User</option><option value="org">Org</option></select>
      <label>Target Owner ID</label>
      <input id="copyTargetId" type="text" placeholder="usr_... or org_..." />
      <label>Mode</label>
      <select id="copyMode">
        <option value="replace_all">Replace All</option>
        <option value="merge_missing">Merge Missing</option>
      </select>
      <label>Reason (optional)</label>
      <input id="copyReason" type="text" placeholder="admin_copy_from_dashboard" value="admin_copy_from_dashboard" />
      <button id="runConfigCopy" class="ghost-btn" style="margin-top:0.5rem;">Copy Config</button>
      <div id="configCopyResult" style="margin-top:0.5rem;"></div>
    </div>
  `;

  document.getElementById("runConfigCopy").addEventListener("click", async () => {
    const mode = document.getElementById("copyMode").value;
    if (mode === "replace_all" && !confirmAction("This will overwrite target config. Continue?")) return;

    const payload = {
      source_owner_type: document.getElementById("copySourceType").value,
      source_owner_id: document.getElementById("copySourceId").value.trim(),
      target_owner_type: document.getElementById("copyTargetType").value,
      target_owner_id: document.getElementById("copyTargetId").value.trim(),
      mode,
      reason: document.getElementById("copyReason").value.trim() || "admin_copy_from_dashboard",
    };
    if (!payload.source_owner_id || !payload.target_owner_id) {
      document.getElementById("configCopyResult").innerHTML = `<p style="color:var(--accent-coral);">Source and target IDs required.</p>`;
      return;
    }
    try {
      const result = await adminFetch("/api/admin/configs/copy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      document.getElementById("configCopyResult").innerHTML = `<p style="color:var(--accent-green);">Config copied successfully (mode: ${escapeHtml(result.mode)})</p>`;
    } catch (err) {
      document.getElementById("configCopyResult").innerHTML = `<p style="color:var(--accent-coral);">${escapeHtml(err.detail || String(err))}</p>`;
    }
  });
}

// ─── Section 7: Audit Logs ───────────────────────────────────────────────

async function renderAuditLogs(container, page = 1, filters = {}) {
  showLoading(container, "Loading audit logs...");
  try {
    const params = new URLSearchParams({ page: String(page), per_page: "50" });
    if (filters.event_type) params.set("event_type", filters.event_type);
    if (filters.org_id) params.set("org_id", filters.org_id);
    if (filters.actor_user_id) params.set("actor_user_id", filters.actor_user_id);
    const data = await adminFetch("/api/admin/audit-logs?" + params.toString());
    container.innerHTML = "";
    const header = document.createElement("div");
    header.className = "admin-section-header";
    header.innerHTML = "<h3>Audit Logs</h3>";

    const filterDiv = document.createElement("div");
    filterDiv.className = "admin-filters";
    filterDiv.innerHTML = `
      <input id="auditFilterEvent" type="text" placeholder="Event type..." value="${escapeHtml(filters.event_type || "")}" />
      <input id="auditFilterOrg" type="text" placeholder="Org ID..." value="${escapeHtml(filters.org_id || "")}" />
      <input id="auditFilterActor" type="text" placeholder="Actor user ID..." value="${escapeHtml(filters.actor_user_id || "")}" />
      <button id="auditFilterBtn" class="ghost-btn">Filter</button>
    `;
    header.appendChild(filterDiv);
    header.appendChild(refreshBtn(() => renderAuditLogs(container, page, filters)));
    container.appendChild(header);

    document.getElementById("auditFilterBtn")?.addEventListener("click", () => {
      renderAuditLogs(container, 1, {
        event_type: document.getElementById("auditFilterEvent")?.value.trim() || "",
        org_id: document.getElementById("auditFilterOrg")?.value.trim() || "",
        actor_user_id: document.getElementById("auditFilterActor")?.value.trim() || "",
      });
    });

    showTable(container,
      ["Time", "Event Type", "Actor Email", "Actor", "Org ID", "Target Type", "Target ID", "Metadata"],
      data.items,
      (tr, e) => {
        const metaStr = JSON.stringify(e.metadata || {}).slice(0, 80);
        tr.innerHTML = `
          <td>${formatDate(e.created_at)}</td>
          <td>${escapeHtml(e.event_type)}</td>
          <td>${escapeHtml(e.actor_email || "")}</td>
          <td>${escapeHtml((e.actor_user_id || "").slice(0, 12))}...</td>
          <td>${escapeHtml((e.org_id || "").slice(0, 12))}...</td>
          <td>${escapeHtml(e.target_type || "")}</td>
          <td>${escapeHtml((e.target_id || "").slice(0, 12))}...</td>
          <td class="admin-actions"><button class="ghost-btn meta-toggle">Expand</button></td>
        `;
        tr.querySelector(".meta-toggle").addEventListener("click", () => {
          const existing = tr.querySelector(".admin-meta-expanded");
          if (existing) {
            existing.remove();
            return;
          }
          const td = document.createElement("td");
          td.colSpan = 8;
          td.className = "admin-meta-expanded";
          const pre = document.createElement("pre");
          pre.textContent = JSON.stringify(e.metadata || {}, null, 2);
          td.appendChild(pre);
          tr.appendChild(td);
        });
      }
    );

    if (data.pages > 1) {
      container.appendChild(paginationBar(data.page, data.pages, data.total, (p) => renderAuditLogs(container, p, filters)));
    }
  } catch (err) {
    showError(container, err);
  }
}

// ─── Section 8: Runs ─────────────────────────────────────────────────────

async function renderRuns(container, page = 1, filters = {}) {
  showLoading(container, "Loading runs...");
  try {
    const params = new URLSearchParams({ page: String(page), per_page: "50" });
    if (filters.user_id) params.set("user_id", filters.user_id);
    if (filters.status) params.set("status", filters.status);
    const data = await adminFetch("/api/admin/runs?" + params.toString());
    container.innerHTML = "";
    const header = document.createElement("div");
    header.className = "admin-section-header";
    header.innerHTML = "<h3>Runs</h3>";
    header.appendChild(refreshBtn(() => renderRuns(container, page, filters)));
    container.appendChild(header);

    showTable(container,
      ["Run ID", "User ID", "Status", "Created", "Summary"],
      data.items,
      (tr, r) => {
        const summary = JSON.stringify(r, null, 2).slice(0, 120);
        tr.innerHTML = `
          <td>${escapeHtml((r.run_id || "").slice(0, 12))}...</td>
          <td>${escapeHtml((r.user_id || "").slice(0, 12))}...</td>
          <td>${escapeHtml(r.status || "-")}</td>
          <td>${formatDate(r.created_at)}</td>
          <td class="admin-actions"><button class="ghost-btn view-run-detail">View</button></td>
        `;
        tr.querySelector(".view-run-detail").addEventListener("click", () => {
          const existing = tr.querySelector(".admin-meta-expanded");
          if (existing) {
            existing.remove();
            return;
          }
          const td = document.createElement("td");
          td.colSpan = 5;
          td.className = "admin-meta-expanded";
          const pre = document.createElement("pre");
          pre.textContent = JSON.stringify(r, null, 2);
          td.appendChild(pre);
          tr.appendChild(td);
        });
      }
    );

    if (data.pages > 1) {
      container.appendChild(paginationBar(data.page, data.pages, data.total, (p) => renderRuns(container, p, filters)));
    }
  } catch (err) {
    showError(container, err);
  }
}

// ─── Section 9: Images ───────────────────────────────────────────────────

async function renderImages(container, page = 1, filters = {}) {
  showLoading(container, "Loading images...");
  try {
    const params = new URLSearchParams({ page: String(page), per_page: "50" });
    if (filters.user_id) params.set("user_id", filters.user_id);
    const data = await adminFetch("/api/admin/images?" + params.toString());
    container.innerHTML = "";
    const header = document.createElement("div");
    header.className = "admin-section-header";
    header.innerHTML = "<h3>Images</h3>";
    header.appendChild(refreshBtn(() => renderImages(container, page, filters)));
    container.appendChild(header);

    showTable(container,
      ["Image ID", "Thumbnail", "Run ID", "User ID", "Created", "Actions"],
      data.items,
      (tr, img) => {
        const hasUrl = img.url || img.storage_path || img.cloudinary_url;
        tr.innerHTML = `
          <td>${escapeHtml((img.image_id || img._id || "").toString().slice(0, 12))}...</td>
          <td>${hasUrl ? `<a href="${escapeHtml(hasUrl)}" target="_blank" rel="noopener">View</a>` : "-"}</td>
          <td>${escapeHtml((img.run_id || "").slice(0, 12))}...</td>
          <td>${escapeHtml((img.user_id || "").slice(0, 12))}...</td>
          <td>${formatDate(img.created_at)}</td>
          <td>${hasUrl ? `<a href="${escapeHtml(hasUrl)}" target="_blank" rel="noopener" class="ghost-btn">Open</a>` : "-"}</td>
        `;
      }
    );

    if (data.pages > 1) {
      container.appendChild(paginationBar(data.page, data.pages, data.total, (p) => renderImages(container, p, filters)));
    }
  } catch (err) {
    showError(container, err);
  }
}

// ─── Section 10: Prompts ─────────────────────────────────────────────────

async function renderPrompts(container, page = 1, filters = {}) {
  showLoading(container, "Loading prompts...");
  try {
    const params = new URLSearchParams({ page: String(page), per_page: "50" });
    if (filters.user_id) params.set("user_id", filters.user_id);
    const data = await adminFetch("/api/admin/prompts?" + params.toString());
    container.innerHTML = "";
    const header = document.createElement("div");
    header.className = "admin-section-header";
    header.innerHTML = "<h3>Prompts</h3>";
    header.appendChild(refreshBtn(() => renderPrompts(container, page, filters)));
    container.appendChild(header);

    showTable(container,
      ["Prompt ID", "User ID", "Run ID", "Model", "Created", "Summary"],
      data.items,
      (tr, p) => {
        const model = p.model || p.provider || "-";
        const summary = (p.content || p.prompt || "").toString().slice(0, 80);
        tr.innerHTML = `
          <td>${escapeHtml((p.prompt_id || p._id || "").toString().slice(0, 12))}...</td>
          <td>${escapeHtml((p.user_id || "").slice(0, 12))}...</td>
          <td>${escapeHtml((p.run_id || "").slice(0, 12))}...</td>
          <td>${escapeHtml(model)}</td>
          <td>${formatDate(p.created_at)}</td>
          <td>${escapeHtml(summary)}...</td>
        `;
      }
    );

    if (data.pages > 1) {
      container.appendChild(paginationBar(data.page, data.pages, data.total, (p) => renderPrompts(container, p, filters)));
    }
  } catch (err) {
    showError(container, err);
  }
}

// ─── Section 11: Provider Configs ────────────────────────────────────────

async function renderProviderConfigs(container, page = 1, provider = "") {
  showLoading(container, "Loading provider configs...");
  try {
    const params = new URLSearchParams({ page: String(page), per_page: "50" });
    if (provider) params.set("provider", provider);
    const data = await adminFetch("/api/admin/provider-configs?" + params.toString());
    container.innerHTML = "";
    const header = document.createElement("div");
    header.className = "admin-section-header";
    header.innerHTML = "<h3>Provider Configs</h3>";
    const filter = document.createElement("select");
    filter.innerHTML = `<option value="">All</option><option value="opencode" ${provider === "opencode" ? "selected" : ""}>OpenCode</option><option value="google_gemini" ${provider === "google_gemini" ? "selected" : ""}>Google Gemini</option>`;
    filter.addEventListener("change", () => renderProviderConfigs(container, 1, filter.value));
    header.appendChild(filter);
    header.appendChild(refreshBtn(() => renderProviderConfigs(container, page, provider)));
    container.appendChild(header);

    showTable(container,
      ["Provider", "Owner Type", "Owner ID", "Configured", "Masked Key", "Updated"],
      data.items,
      (tr, pc) => {
        tr.innerHTML = `
          <td>${escapeHtml(pc.provider)}</td>
          <td>${escapeHtml(pc.owner_type)}</td>
          <td>${escapeHtml((pc.owner_id || "").slice(0, 16))}...</td>
          <td>${pc.configured ? "✓" : "✗"}</td>
          <td>${escapeHtml(pc.masked_key || "-")}</td>
          <td>${formatDate(pc.updated_at)}</td>
        `;
      }
    );

    if (data.pages > 1) {
      container.appendChild(paginationBar(data.page, data.pages, data.total, (p) => renderProviderConfigs(container, p, provider)));
    }
  } catch (err) {
    showError(container, err);
  }
}

// ─── Section 12: Health ──────────────────────────────────────────────────

async function renderHealth(container) {
  showLoading(container, "Checking health...");
  try {
    const [health, stats] = await Promise.all([
      adminFetch("/api/admin/health"),
      adminFetch("/api/admin/stats"),
    ]);
    container.innerHTML = "";
    const header = document.createElement("div");
    header.className = "admin-section-header";
    header.innerHTML = "<h3>Health & System Info</h3>";
    header.appendChild(refreshBtn(() => renderHealth(container)));
    container.appendChild(header);

    const cards = document.createElement("div");
    cards.className = "admin-stat-cards";

    const healthStatus = health.status === "ok" ? "✓ Operational" : "✗ Degraded";
    const dbStatus = health.database === "connected" ? "✓ Connected" : "✗ Disconnected";
    const healthGroup = document.createElement("div");
    healthGroup.className = "admin-stat-group";
    healthGroup.innerHTML = `<h4>System Health</h4>
      <div class="admin-stat-card"><span class="admin-stat-value">${escapeHtml(healthStatus)}</span><span class="admin-stat-label">Backend</span></div>
      <div class="admin-stat-card"><span class="admin-stat-value">${escapeHtml(dbStatus)}</span><span class="admin-stat-label">Database</span></div>`;
    cards.appendChild(healthGroup);

    const countsGroup = document.createElement("div");
    countsGroup.className = "admin-stat-group";
    countsGroup.innerHTML = `<h4>Counts</h4>
      <div class="admin-stat-card"><span class="admin-stat-value">${stats.active_users}</span><span class="admin-stat-label">Active Users</span></div>
      <div class="admin-stat-card"><span class="admin-stat-value">${stats.active_orgs}</span><span class="admin-stat-label">Active Orgs</span></div>
      <div class="admin-stat-card"><span class="admin-stat-value">${stats.active_configs}</span><span class="admin-stat-label">Active Configs</span></div>
      <div class="admin-stat-card"><span class="admin-stat-value">${stats.total_runs}</span><span class="admin-stat-label">Total Runs</span></div>
      <div class="admin-stat-card"><span class="admin-stat-value">${stats.total_images}</span><span class="admin-stat-label">Total Images</span></div>`;
    cards.appendChild(countsGroup);
    container.appendChild(cards);
  } catch (err) {
    showError(container, err);
  }
}

// ─── Router ──────────────────────────────────────────────────────────────

export async function renderAdminPanel() {
  const panel = document.getElementById("adminPanel");
  const nav = document.getElementById("adminNav");
  if (!panel) return;

  currentUser = getAuthUser();

  if (!currentUser || !currentUser.is_super_admin) {
    panel.innerHTML = `<div class="card"><p style="color:var(--accent-coral);font-weight:600;">Super admin access required</p></div>`;
    if (nav) nav.style.display = "none";
    return;
  }

  if (nav) nav.style.display = "inline-block";

  const hash = window.location.hash.replace("#admin/", "").split("?")[0] || "overview";
  currentSection = hash;

  panel.innerHTML = `<div class="admin-layout"><div class="admin-sidebar"></div><div class="admin-main" id="adminPanelContent"></div></div>`;

  const sidebar = panel.querySelector(".admin-sidebar");
  const content = document.getElementById("adminPanelContent");

  const navItems = [
    { id: "overview", label: "Overview" },
    { id: "users", label: "Users" },
    { id: "individual-users", label: "Individual Users" },
    { id: "orgs", label: "Organizations" },
    { id: "configs", label: "Configs" },
    { id: "config-copy", label: "Config Copy" },
    { id: "audit", label: "Audit Logs" },
    { id: "runs", label: "Runs" },
    { id: "images", label: "Images" },
    { id: "prompts", label: "Prompts" },
    { id: "providers", label: "Provider Configs" },
    { id: "health", label: "Health" },
  ];

  navItems.forEach((item) => {
    const btn = document.createElement("button");
    btn.className = "admin-sidebar-item" + (item.id === currentSection ? " active" : "");
    btn.textContent = item.label;
    btn.addEventListener("click", () => {
      window.location.hash = "admin/" + item.id;
      renderAdminPanel();
    });
    sidebar.appendChild(btn);
  });

  document.querySelectorAll(".admin-nav-btn").forEach((b) => b.classList.remove("active"));
  const activeNavBtn = document.querySelector("[data-admin-section=\"" + currentSection + "\"]");
  if (activeNavBtn) activeNavBtn.classList.add("active");

  switch (currentSection) {
    case "overview":
      await renderOverview(content);
      break;
    case "users":
      await renderUsers(content);
      break;
    case "individual-users":
      await renderIndividualUsers(content);
      break;
    case "orgs":
      await renderOrgs(content);
      break;
    case "configs":
      await renderConfigs(content);
      break;
    case "config-copy":
      renderConfigCopy(content);
      break;
    case "audit":
      await renderAuditLogs(content);
      break;
    case "runs":
      await renderRuns(content);
      break;
    case "images":
      await renderImages(content);
      break;
    case "prompts":
      await renderPrompts(content);
      break;
    case "providers":
      await renderProviderConfigs(content);
      break;
    case "health":
      await renderHealth(content);
      break;
    default:
      content.innerHTML = `<p class="hint">Unknown section.</p>`;
  }
}
