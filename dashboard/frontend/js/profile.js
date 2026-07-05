import { fetchJSON, clearCache } from "./api.js";
import { getAuthUser, isAuthenticated } from "./auth.js";
import { setStatus, showGlobalLoading, hideGlobalLoading } from "./ui.js";

export async function renderProfilePanel() {
  const panel = document.getElementById("profilePanel");
  if (!panel) return;

  const user = getAuthUser();
  if (!user.authenticated) {
    panel.innerHTML = `<div class="profile-empty"><div class="profile-empty-icon">&#128100;</div><p>Sign in to view your profile.</p></div>`;
    return;
  }

  showGlobalLoading("Loading profile...");
  try {
    const [orgData, providerConfigs] = await Promise.all([
      fetchJSON("/api/orgs/me").catch(() => null),
      fetchJSON("/api/user/provider-config").catch(() => []),
    ]);
    renderProfile(panel, user, orgData, providerConfigs);
  } catch (err) {
    panel.innerHTML = `<div class="profile-empty"><p>Failed to load profile data.</p></div>`;
  } finally {
    hideGlobalLoading();
  }
}

async function renderProfile(panel, user, orgData, providerConfigs) {
  panel.innerHTML = "";
  const container = document.createElement("div");
  container.className = "profile-container";

  // ── User Card ──────────────────────────────────────────────────────
  const userCard = document.createElement("div");
  userCard.className = "profile-user-card";
  const avatarUrl = user.avatar_url || "";
  const initials = (user.display_name || user.email || "?").charAt(0).toUpperCase();
  userCard.innerHTML = `
    <div class="profile-avatar-wrap">
      ${avatarUrl ? `<img class="profile-avatar" src="${escapeHtml(avatarUrl)}" alt="" />` : `<div class="profile-avatar profile-avatar-fallback">${escapeHtml(initials)}</div>`}
      ${user.is_super_admin ? '<span class="profile-sa-badge">Admin</span>' : ""}
    </div>
    <div class="profile-user-info">
      <h2 class="profile-user-name">${escapeHtml(user.display_name || "User")}</h2>
      <p class="profile-user-email">${escapeHtml(user.email || "")}</p>
      <div class="profile-user-meta">
        <span class="profile-meta-item">ID: <code>${escapeHtml(user.user_id || "")}</code></span>
      </div>
    </div>
  `;
  container.appendChild(userCard);

  // ── Organizations ──────────────────────────────────────────────────
  const orgsSection = document.createElement("div");
  orgsSection.className = "profile-section";

  if (!orgData || !orgData.orgs || !orgData.orgs.length) {
    orgsSection.innerHTML = `
      <div class="profile-section-header"><h3>Organizations</h3></div>
      <div class="profile-org-create">
        <label for="profileOrgName">Organization Name</label>
        <div class="inline-row">
          <input id="profileOrgName" type="text" placeholder="e.g. Acme Corp" />
          <button id="profileCreateOrgBtn" class="ghost-btn" type="button">Create</button>
        </div>
      </div>
    `;
    container.appendChild(orgsSection);
    attachCreateOrgHandler();
    finishRender(panel, container, user);
    return;
  }

  const defaultOrg = orgData.default_org;
  const memberships = orgData.memberships || [];
  const role = memberships[0]?.role || "creator";
  const perms = defaultOrg?.permissions || {};

  const orgHeader = document.createElement("div");
  orgHeader.className = "profile-section-header";
  orgHeader.innerHTML = `<h3>Organizations</h3><span class="profile-org-count">${orgData.orgs.length} org${orgData.orgs.length !== 1 ? "s" : ""}</span>`;
  orgsSection.appendChild(orgHeader);

  const orgCards = document.createElement("div");
  orgCards.className = "profile-org-cards";
  for (const org of orgData.orgs) {
    const memberCount = (orgData.memberships || []).filter(m => m.org_id === org.org_id).length;
    orgCards.appendChild(createOrgCard(org, role, memberCount));
  }
  orgsSection.appendChild(orgCards);

  container.appendChild(orgsSection);

  // Fetch members/invites for default org
  let members = [];
  let invites = [];
  if (defaultOrg) {
    const orgId = defaultOrg.org_id;
    try {
      members = await fetchJSON(`/api/orgs/${orgId}/members`).catch(() => []);
      if (perms.can_invite_members) {
        const invData = await fetchJSON(`/api/orgs/${orgId}/invites`).catch(() => ({ invites: [] }));
        invites = invData.invites || [];
      }
    } catch {}
    if (perms.can_invite_members) {
      orgsSection.appendChild(createInviteSection(orgId, invites));
    }
    if (members.length) {
      orgsSection.appendChild(createMembersSection(orgId, members, role));
    }
  }

  // ── Provider Configs ───────────────────────────────────────────────
  if (providerConfigs.length) {
    const pcSection = document.createElement("div");
    pcSection.className = "profile-section";
    pcSection.innerHTML = `<div class="profile-section-header"><h3>Provider Configs</h3></div>`;
    const pcList = document.createElement("div");
    pcList.className = "profile-pc-list";
    for (const pc of providerConfigs) {
      const card = document.createElement("div");
      card.className = "profile-pc-card";
      const configured = pc.config?.api_key ? "Configured" : "Not configured";
      card.innerHTML = `
        <strong>${escapeHtml(pc.provider || "Unknown")}</strong>
        <span class="${pc.config?.api_key ? "profile-pc-ok" : "profile-pc-missing"}">${configured}</span>
      `;
      pcList.appendChild(card);
    }
    pcSection.appendChild(pcList);
    container.appendChild(pcSection);
  }

  finishRender(panel, container, user);
}

function createOrgCard(org, role, memberCount) {
  const card = document.createElement("div");
  card.className = "profile-org-card";
  const isDefault = role === "owner";
  const modeLabel = org.config_mode === "shared_org_config" ? "Shared Config" : "Individual Config";
  card.innerHTML = `
    <div class="profile-org-card-top">
      <strong class="profile-org-name">${escapeHtml(org.name || "Unnamed")}</strong>
      ${isDefault ? '<span class="profile-org-owner-badge">Owner</span>' : ""}
    </div>
    <div class="profile-org-card-meta">
      <span>${escapeHtml(org.domain || "")}</span>
      <span>${memberCount} member${memberCount !== 1 ? "s" : ""}</span>
      <span class="profile-mode-badge">${modeLabel}</span>
    </div>
  `;
  card.addEventListener("click", () => {
    document.getElementById("orgPanel")?.classList.toggle("hidden");
  });
  return card;
}

function createInviteSection(orgId, invites) {
  const pending = invites.filter(i => i.status === "pending");
  const wrap = document.createElement("div");
  wrap.className = "profile-subsection";
  wrap.innerHTML = `
    <div class="profile-subsection-header">
      <h4>Invite Member</h4>
    </div>
    <div class="inline-row">
      <input id="profileInviteEmail" type="email" placeholder="colleague@company.com" />
      <select id="profileInviteRole">
        <option value="creator">Creator</option>
        <option value="config_admin">Config Admin</option>
      </select>
      <button id="profileSendInviteBtn" class="ghost-btn" type="button">Send</button>
    </div>
    <div id="profileInviteResult" class="invite-result"></div>
    ${pending.length ? `
      <div class="profile-pending-wrap">
        <h4>Pending Invites (${pending.length})</h4>
        <table class="invites-table">
          <thead><tr><th>Email</th><th>Role</th><th>Expires</th><th></th></tr></thead>
          <tbody>
            ${pending.map(inv => `
              <tr>
                <td>${escapeHtml(inv.email)}</td>
                <td>${escapeHtml(inv.role)}</td>
                <td>${inv.expires_at ? new Date(inv.expires_at * 1000).toLocaleDateString() : "?"}</td>
                <td><button class="ghost-btn profile-revoke-btn" data-invite-id="${inv.invite_id}" data-org-id="${orgId}" type="button" style="color:var(--accent-coral)">Revoke</button></td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    ` : ""}
  `;
  setTimeout(() => attachInviteHandlers(orgId), 0);
  return wrap;
}

function createMembersSection(orgId, members, currentRole) {
  const wrap = document.createElement("div");
  wrap.className = "profile-subsection";
  wrap.innerHTML = `
    <div class="profile-subsection-header">
      <h4>Members (${members.length})</h4>
    </div>
    <table class="members-table">
      <thead><tr><th>Email</th><th>Role</th><th>Joined</th><th>Permissions</th>${currentRole === "owner" ? "<th></th>" : ""}</tr></thead>
      <tbody>
        ${members.map(m => `
          <tr>
            <td>${escapeHtml(m.email)}</td>
            <td><span class="role-badge role-${m.role}">${escapeHtml(m.role)}</span></td>
            <td>${m.joined_at ? new Date(m.joined_at * 1000).toLocaleDateString() : "?"}</td>
            <td class="perm-summary">${permissionSummary(m.permissions)}</td>
            ${currentRole === "owner" && m.role !== "owner" ? `
              <td class="member-actions">
                <select class="member-role-select" data-org-id="${orgId}" data-user-id="${m.user_id}" data-current-role="${m.role}">
                  <option value="creator" ${m.role === "creator" ? "selected" : ""}>Creator</option>
                  <option value="config_admin" ${m.role === "config_admin" ? "selected" : ""}>Config Admin</option>
                </select>
                <button class="ghost-btn remove-member-btn" data-org-id="${orgId}" data-user-id="${m.user_id}" data-email="${escapeHtml(m.email)}" type="button" style="color:var(--accent-coral)">Remove</button>
              </td>
            ` : ""}
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
  setTimeout(() => attachMemberHandlers(orgId, currentRole), 0);
  return wrap;
}

function permissionSummary(perms) {
  if (!perms) return "";
  const labels = [];
  if (perms.can_edit_org_config) labels.push("edit config");
  if (perms.can_manage_org) labels.push("manage");
  if (perms.can_invite_members) labels.push("invite");
  return labels.join(", ") || "generate ads";
}

function attachCreateOrgHandler() {
  const btn = document.getElementById("profileCreateOrgBtn");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const name = document.getElementById("profileOrgName")?.value.trim();
    if (!name) { setStatus("Enter an organization name."); return; }
    btn.disabled = true;
    try {
      await fetchJSON("/api/orgs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
      setStatus("Organization created!");
      clearCache("/api/orgs");
      await renderProfilePanel();
    } catch (err) {
      setStatus(`Failed: ${String(err)}`);
    } finally {
      btn.disabled = false;
    }
  });
}

function attachInviteHandlers(orgId) {
  const sendBtn = document.getElementById("profileSendInviteBtn");
  if (!sendBtn) return;
  sendBtn.addEventListener("click", async () => {
    const email = document.getElementById("profileInviteEmail")?.value.trim();
    const role = document.getElementById("profileInviteRole")?.value;
    if (!email) { setStatus("Enter an email address."); return; }
    sendBtn.disabled = true;
    const resultDiv = document.getElementById("profileInviteResult");
    try {
      const data = await fetchJSON(`/api/orgs/${orgId}/invites`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, role }),
      });
      if (resultDiv) {
        resultDiv.innerHTML = `
          <p class="invite-success">Invite sent to ${escapeHtml(email)}</p>
          <p class="invite-link">Link: <a href="${escapeHtml(data.invite_url)}" target="_blank">share</a>
          ${data.email_sent ? "&nbsp;(email sent)" : "&nbsp;(share manually)"}</p>
        `;
      }
      const emailInput = document.getElementById("profileInviteEmail");
      if (emailInput) emailInput.value = "";
      clearCache(`/api/orgs/${orgId}/invites`);
      await renderProfilePanel();
    } catch (err) {
      if (resultDiv) resultDiv.innerHTML = `<p class="invite-error">${escapeHtml(String(err))}</p>`;
    } finally {
      sendBtn.disabled = false;
    }
  });

  document.querySelectorAll(".profile-revoke-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const inviteId = btn.dataset.inviteId;
      const oid = btn.dataset.orgId;
      btn.disabled = true;
      try {
        await fetchJSON(`/api/orgs/${oid}/invites/${inviteId}`, { method: "DELETE" });
        setStatus("Invite revoked");
        clearCache(`/api/orgs/${oid}/invites`);
        await renderProfilePanel();
      } catch (err) {
        setStatus(`Failed: ${String(err)}`);
      } finally {
        btn.disabled = false;
      }
    });
  });
}

function attachMemberHandlers(orgId, currentRole) {
  if (currentRole !== "owner") return;
  document.querySelectorAll(".member-role-select").forEach(sel => {
    sel.addEventListener("change", async () => {
      const userId = sel.dataset.userId;
      const newRole = sel.value;
      sel.disabled = true;
      try {
        await fetchJSON(`/api/orgs/${orgId}/members/${userId}/role`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role: newRole }),
        });
        setStatus(`Member role updated to ${newRole}`);
        clearCache(`/api/orgs/${orgId}/members`);
        await renderProfilePanel();
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
        setStatus(`Removed ${email}`);
        clearCache(`/api/orgs/${orgId}/members`);
        await renderProfilePanel();
      } catch (err) {
        setStatus(`Failed: ${String(err)}`);
      } finally {
        btn.disabled = false;
      }
    });
  });
}

function finishRender(panel, container, user) {
  // Config & admin links
  const links = document.createElement("div");
  links.className = "profile-links";
  links.innerHTML = `
    <button class="ghost-btn" id="profileOpenConfig" type="button">&#9881; Edit Config</button>
    ${user.is_super_admin ? '<button class="ghost-btn" id="profileOpenAdmin" type="button">&#128736; Admin Dashboard</button>' : ""}
  `;
  container.appendChild(links);
  panel.appendChild(container);

  document.getElementById("profileOpenConfig")?.addEventListener("click", () => {
    const configPanel = document.getElementById("configPanel");
    if (configPanel) {
      configPanel.classList.toggle("hidden");
      configPanel.scrollIntoView({ behavior: "smooth" });
    }
  });
  document.getElementById("profileOpenAdmin")?.addEventListener("click", () => {
    window.location.hash = "admin/overview";
  });
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}
