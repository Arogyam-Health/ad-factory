import { fetchJSON, clearCache } from "./api.js";
import { getAuthUser, isAuthenticated } from "./auth.js";
import { setStatus, showGlobalLoading, hideGlobalLoading } from "./ui.js";

let orgData = null;
let pendingInvites = [];

export function getOrgData() {
  return orgData;
}

export async function loadOrgData() {
  if (!isAuthenticated()) return null;
  try {
    const data = await fetchJSON("/api/orgs/me");
    orgData = data;
    return data;
  } catch {
    orgData = null;
    return null;
  }
}

export async function renderOrgPanel() {
  const panel = document.getElementById("orgPanel");
  if (!panel) return;

  const user = getAuthUser();
  if (!user.authenticated) {
    panel.innerHTML = `<div class="card"><p class="hint">Login to manage your organization.</p></div>`;
    return;
  }

  try {
    const data = await loadOrgData();
    if (!data || !data.orgs || !data.orgs.length) {
      renderCreateOrg(panel);
      return;
    }
    renderOrgView(panel, data);
  } catch {
    panel.innerHTML = `<div class="card"><p class="hint">Failed to load organization data.</p></div>`;
  }
}

function renderCreateOrg(panel) {
  panel.innerHTML = `
    <section class="card">
      <h2>Organization</h2>
      <div class="org-create-form">
        <label for="orgNameInput">Organization Name</label>
        <div class="inline-row">
          <input id="orgNameInput" type="text" placeholder="e.g. Acme Corp" />
          <button id="createOrgBtn" class="ghost-btn" type="button">Create Organization</button>
        </div>
      </div>
    </section>
  `;

  const btn = document.getElementById("createOrgBtn");
  if (btn) {
    btn.addEventListener("click", async () => {
      const name = document.getElementById("orgNameInput").value.trim();
      if (!name) { setStatus("Enter an organization name."); return; }
      btn.disabled = true;
      try {
        await fetchJSON("/api/orgs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
        setStatus("Organization created!");
        clearCache("/api/orgs");
        await renderOrgPanel();
      } catch (err) {
        setStatus(`Failed: ${String(err)}`);
      } finally {
        btn.disabled = false;
      }
    });
  }
}

async function renderOrgView(panel, data) {
  const defaultOrg = data.default_org;
  if (!defaultOrg) {
    panel.innerHTML = `<div class="card"><p class="hint">No default organization found.</p></div>`;
    return;
  }

  const orgId = defaultOrg.org_id;
  const membership = data.memberships && data.memberships[0];
  const role = membership ? membership.role : "creator";
  const perms = defaultOrg.permissions || {};

  // Load members data
  let members = [];
  let invites = [];
  try {
    members = await fetchJSON(`/api/orgs/${orgId}/members`);
  } catch {}
  try {
    if (perms.can_invite_members) {
      const invData = await fetchJSON(`/api/orgs/${orgId}/invites`);
      invites = invData.invites || [];
    }
  } catch {}

  panel.innerHTML = `
    <section class="card">
      <div class="org-header">
        <h2>${escapeHtml(defaultOrg.name || "Organization")}</h2>
        <span class="org-domain">${escapeHtml(defaultOrg.domain || "")}</span>
        <span class="org-role-badge role-${role}">${escapeHtml(role)}</span>
        <span class="org-mode-badge">${defaultOrg.config_mode === "shared_org_config" ? "Shared Config" : "Individual Config"}</span>
      </div>

      <div class="org-stats">
        <span><strong>${members.length}</strong> member${members.length !== 1 ? "s" : ""}</span>
        <span>Config: <strong>${defaultOrg.config_mode === "shared_org_config" ? "Shared" : "Individual"}</strong></span>
      </div>

      ${perms.can_manage_org ? renderInviteForm(orgId) : ""}
      ${perms.can_manage_org && invites.length ? renderPendingInvites(orgId, invites) : ""}
      ${members.length ? renderMembersTable(orgId, members, role) : ""}
    </section>
  `;

  attachInviteHandlers(orgId, role);
  attachMemberHandlers(orgId, role);
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function renderInviteForm(orgId) {
  return `
    <div class="org-invite-form">
      <h3>Invite Member</h3>
      <div class="inline-row">
        <input id="inviteEmail" type="email" placeholder="colleague@company.com" />
        <select id="inviteRole">
          <option value="creator">Creator</option>
          <option value="config_admin">Config Admin</option>
        </select>
        <button id="sendInviteBtn" class="ghost-btn" type="button">Send Invite</button>
      </div>
      <div id="inviteResult" class="invite-result"></div>
    </div>
  `;
}

function renderPendingInvites(orgId, invites) {
  const pending = invites.filter(i => i.status === "pending");
  if (!pending.length) return "";
  return `
    <div class="org-pending-invites">
      <h3>Pending Invites</h3>
      <table class="invites-table">
        <thead><tr><th>Email</th><th>Role</th><th>Expires</th><th></th></tr></thead>
        <tbody>
          ${pending.map(inv => `
            <tr>
              <td>${escapeHtml(inv.email)}</td>
              <td>${escapeHtml(inv.role)}</td>
              <td>${inv.expires_at ? new Date(inv.expires_at * 1000).toLocaleDateString() : "?"}</td>
              <td><button class="ghost-btn revoke-invite-btn" data-invite-id="${inv.invite_id}" data-org-id="${orgId}" type="button" style="color:var(--accent-coral)">Revoke</button></td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderMembersTable(orgId, members, currentRole) {
  return `
    <div class="org-members">
      <h3>Members</h3>
      <table class="members-table">
        <thead><tr><th>Email</th><th>Role</th><th>Joined</th><th>Permissions</th>${currentRole === "owner" ? "<th></th>" : ""}</tr></thead>
        <tbody>
          ${members.map(m => `
            <tr>
              <td>${escapeHtml(m.email)}</td>
              <td class="role-${m.role}">${escapeHtml(m.role)}</td>
              <td>${m.joined_at ? new Date(m.joined_at * 1000).toLocaleDateString() : "?"}</td>
              <td class="perm-summary">${permissionSummary(m.permissions)}</td>
              ${currentRole === "owner" && m.role !== "owner" ? `
                <td class="member-actions">
                  <select class="member-role-select" data-org-id="${orgId}" data-user-id="${m.user_id}" data-current-role="${m.role}">
                    <option value="creator" ${m.role === "creator" ? "selected" : ""}>Creator</option>
                    <option value="config_admin" ${m.role === "config_admin" ? "selected" : ""}>Config Admin</option>
                    <option value="owner">Owner</option>
                  </select>
                  <button class="ghost-btn remove-member-btn" data-org-id="${orgId}" data-user-id="${m.user_id}" data-email="${escapeHtml(m.email)}" type="button" style="color:var(--accent-coral)">Remove</button>
                </td>
              ` : ""}
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function permissionSummary(perms) {
  if (!perms) return "";
  const labels = [];
  if (perms.can_edit_org_config) labels.push("edit config");
  if (perms.can_manage_org) labels.push("manage");
  if (perms.can_invite_members) labels.push("invite");
  return labels.join(", ") || "generate ads";
}

function attachInviteHandlers(orgId) {
  const sendBtn = document.getElementById("sendInviteBtn");
  if (!sendBtn) return;
  sendBtn.addEventListener("click", async () => {
    const email = document.getElementById("inviteEmail").value.trim();
    const role = document.getElementById("inviteRole").value;
    if (!email) { setStatus("Enter an email address."); return; }
    sendBtn.disabled = true;
    const resultDiv = document.getElementById("inviteResult");
    try {
      const data = await fetchJSON(`/api/orgs/${orgId}/invites`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, role }),
      });
      resultDiv.innerHTML = `
        <p class="invite-success">Invite sent to ${escapeHtml(email)}</p>
        <p>Invite link: <a href="${escapeHtml(data.invite_url)}" target="_blank">${escapeHtml(data.invite_url)}</a></p>
        ${data.email_sent ? "<p>Email sent.</p>" : "<p>Email not configured. Share the link manually.</p>"}
      `;
      document.getElementById("inviteEmail").value = "";
      clearCache(`/api/orgs/${orgId}/invites`);
      await renderOrgPanel();
    } catch (err) {
      resultDiv.innerHTML = `<p class="invite-error">${escapeHtml(String(err))}</p>`;
    } finally {
      sendBtn.disabled = false;
    }
  });
}

function attachMemberHandlers(orgId, currentRole) {
  if (currentRole !== "owner") return;

  document.querySelectorAll(".member-role-select").forEach(sel => {
    sel.addEventListener("change", async () => {
      const userId = sel.dataset.userId;
      const newRole = sel.value;
      if (newRole === "owner" && !confirm("Make this member the owner? You will become Config Admin.")) {
        sel.value = sel.dataset.currentRole;
        return;
      }
      sel.disabled = true;
      try {
        await fetchJSON(`/api/orgs/${orgId}/members/${userId}/role`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role: newRole }),
        });
        setStatus(`Member role updated to ${newRole}`);
        clearCache(`/api/orgs/${orgId}/members`);
        await renderOrgPanel();
      } catch (err) {
        setStatus(`Failed: ${String(err)}`);
        sel.value = sel.dataset.currentRole;
      } finally {
        sel.disabled = false;
      }
    });
  });

  document.querySelectorAll(".remove-member-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const userId = btn.dataset.userId;
      const email = btn.dataset.email;
      if (!confirm(`Remove ${email} from organization?`)) return;
      btn.disabled = true;
      try {
        await fetchJSON(`/api/orgs/${orgId}/members/${userId}`, { method: "DELETE" });
        setStatus(`Removed ${email} from organization`);
        clearCache(`/api/orgs/${orgId}/members`);
        await renderOrgPanel();
      } catch (err) {
        setStatus(`Failed: ${String(err)}`);
      } finally {
        btn.disabled = false;
      }
    });
  });

  document.querySelectorAll(".revoke-invite-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const inviteId = btn.dataset.inviteId;
      const oid = btn.dataset.orgId;
      btn.disabled = true;
      try {
        await fetchJSON(`/api/orgs/${oid}/invites/${inviteId}`, { method: "DELETE" });
        setStatus("Invite revoked");
        clearCache(`/api/orgs/${oid}/invites`);
        await renderOrgPanel();
      } catch (err) {
        setStatus(`Failed: ${String(err)}`);
      } finally {
        btn.disabled = false;
      }
    });
  });
}
