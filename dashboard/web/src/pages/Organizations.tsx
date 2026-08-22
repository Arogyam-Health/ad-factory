import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchJSON, peekCache, clearCache } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Membership, Org, OrgInvite, OrgMember } from "@/lib/types";
import { Bento, Tile } from "@/components/Tile";
import { Button } from "@/components/Button";
import { SkeletonLines } from "@/components/Skeleton";

type OrgsPayload = { orgs?: Org[]; memberships?: Membership[]; default_org?: Org };

function fmtDate(ts?: number) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleDateString();
}

function permSummary(perms?: Record<string, boolean>) {
  if (!perms) return "Generate ads";
  const labels = [];
  if (perms.can_edit_org_config) labels.push("Edit config");
  if (perms.can_manage_org) labels.push("Manage");
  if (perms.can_invite_members) labels.push("Invite");
  return labels.join(", ") || "Generate ads";
}

export function OrganizationsPage() {
  const { user, ready } = useAuth();
  const [loading, setLoading] = useState(true);
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [name, setName] = useState("");
  const [status, setStatus] = useState("");
  const [openId, setOpenId] = useState("");
  const [detail, setDetail] = useState<Record<string, { members: OrgMember[]; invites: OrgInvite[] }>>({});
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("creator");
  const [inviteMsg, setInviteMsg] = useState("");
  const [inviteUrl, setInviteUrl] = useState("");

  async function load(background = false) {
    if (!background) setLoading(true);
    try {
      const data = await fetchJSON<OrgsPayload>("/api/orgs/me");
      setOrgs(data.orgs || []);
      setMemberships(data.memberships || []);
    } catch (err) {
      setStatus(String(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!ready) return;
    if (!user.authenticated) {
      setLoading(false);
      return;
    }
    const cached = peekCache<OrgsPayload>("/api/orgs/me");
    if (cached) {
      setOrgs(cached.orgs || []);
      setMemberships(cached.memberships || []);
      setLoading(false);
    }
    void load(Boolean(cached));
  }, [ready, user.authenticated]);

  async function expand(org: Org) {
    const next = openId === org.org_id ? "" : org.org_id;
    setOpenId(next);
    if (!next || detail[org.org_id]) return;
    const [members, invData] = await Promise.all([
      fetchJSON<OrgMember[]>(`/api/orgs/${org.org_id}/members`).catch(() => []),
      org.permissions?.can_invite_members
        ? fetchJSON<{ invites?: OrgInvite[] }>(`/api/orgs/${org.org_id}/invites`).catch(() => ({ invites: [] }))
        : Promise.resolve({ invites: [] }),
    ]);
    setDetail((prev) => ({ ...prev, [org.org_id]: { members, invites: invData.invites || [] } }));
  }

  async function sendInvite(orgId: string) {
    if (!inviteEmail.trim()) {
      setInviteMsg("Enter an email.");
      return;
    }
    setInviteMsg("Sending…");
    setInviteUrl("");
    try {
      const data = await fetchJSON<{
        email_sent?: boolean;
        email_provider?: string;
        email_error?: string;
        invite_url?: string;
      }>(`/api/orgs/${orgId}/invites`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: inviteEmail.trim(), role: inviteRole }),
      });
      clearCache(`/api/orgs/${orgId}/invites`);
      setInviteEmail("");
      if (data.email_sent) {
        setInviteMsg(`Invite sent via ${data.email_provider || "mail"}.`);
      } else {
        setInviteMsg(data.email_error || "Email is not configured — share this link.");
        setInviteUrl(data.invite_url || "");
      }
      const invData = await fetchJSON<{ invites?: OrgInvite[] }>(`/api/orgs/${orgId}/invites`, { noCache: true });
      setDetail((prev) => ({
        ...prev,
        [orgId]: { members: prev[orgId]?.members || [], invites: invData.invites || [] },
      }));
    } catch (err) {
      setInviteMsg(String(err));
    }
  }

  if (ready && !user.authenticated) {
    return (
      <Bento>
        <Tile span="wide" kicker="Floor" title="Teams and organizations">
          <p className="hint">
            Guests can look around. Creating orgs, inviting members, and fetching a team config
            need a signed-in account. The generic studio files stay available on Config.
          </p>
          <div className="action-row" style={{ marginTop: 16 }}>
            <a className="btn btn-primary" href="/api/auth/google/login">Sign in to manage teams</a>
            <Link className="btn btn-ghost" to="/config">Browse generic files</Link>
          </div>
        </Tile>
      </Bento>
    );
  }

  return (
    <Bento>
      <Tile span="wide" kicker="Floor" title="Your organizations">
        <div className="stat-row">
          <div><p className="tile-kicker">Organizations</p><strong>{orgs.length}</strong></div>
          <div><p className="tile-kicker">Memberships</p><strong>{memberships.length}</strong></div>
        </div>
        {loading ? <SkeletonLines lines={6} /> : orgs.length ? (
          <div className="run-list">
            {orgs.map((org) => {
              const role = memberships.find((m) => m.org_id === org.org_id)?.role || "member";
              const open = openId === org.org_id;
              const info = detail[org.org_id];
              const pending = (info?.invites || []).filter((inv) => inv.status === "pending");
              return (
                <article key={org.org_id} className="org-card">
                  <div className="org-card-head">
                    <button type="button" className="org-expand" onClick={() => void expand(org)}>
                      <strong>{org.name || "Untitled"}</strong>
                      <span>{role}</span>
                      <span>{org.config_mode === "shared_org_config" ? "Shared" : "Individual"}</span>
                    </button>
                    <div className="action-row">
                      <Link className="btn btn-ghost" to={`/config?org_id=${encodeURIComponent(org.org_id)}`}>Fetch config</Link>
                      <Link className="btn btn-ghost" to="/">Studio</Link>
                      {role === "owner" ? (
                        <Button
                          variant="danger"
                          onClick={() => {
                            const typed = window.prompt(`Type the org name "${org.name || ""}" to confirm deletion:`);
                            if (typed !== (org.name || "")) {
                              if (typed !== null) window.alert("Name did not match. Deletion cancelled.");
                              return;
                            }
                            void fetchJSON(`/api/orgs/${org.org_id}`, { method: "DELETE" })
                              .then(() => {
                                clearCache("/api/orgs");
                                return load();
                              })
                              .catch((err) => setStatus(String(err)));
                          }}
                        >
                          Delete
                        </Button>
                      ) : null}
                    </div>
                  </div>
                  {open ? (
                    <div className="org-detail">
                      <p className="hint">Permissions: {permSummary(org.permissions)}</p>
                      {org.permissions?.can_invite_members ? (
                        <div className="action-row" style={{ margin: "12px 0" }}>
                          <input className="field" type="email" value={inviteEmail} onChange={(e) => setInviteEmail(e.target.value)} placeholder="colleague@company.com" />
                          <select className="field field-narrow" value={inviteRole} onChange={(e) => setInviteRole(e.target.value)}>
                            <option value="creator">Creator</option>
                            <option value="config_admin">Config Admin</option>
                          </select>
                          <Button variant="primary" onClick={() => void sendInvite(org.org_id)}>Send invite</Button>
                        </div>
                      ) : null}
                      {inviteMsg ? <p className="hint">{inviteMsg}</p> : null}
                      {inviteUrl ? (
                        <div className="action-row">
                          <input className="field" readOnly value={inviteUrl} />
                          <Button variant="ghost" onClick={() => void navigator.clipboard.writeText(inviteUrl)}>Copy link</Button>
                        </div>
                      ) : null}
                      {pending.length ? (
                        <div className="data-table-wrap" style={{ marginTop: 12 }}>
                          <table className="data-table">
                            <thead><tr><th>Pending</th><th>Role</th><th>Expires</th><th></th></tr></thead>
                            <tbody>
                              {pending.map((inv) => (
                                <tr key={inv.invite_id}>
                                  <td>{inv.email}</td>
                                  <td>{inv.role}</td>
                                  <td>{fmtDate(inv.expires_at)}</td>
                                  <td>
                                    <Button
                                      variant="ghost"
                                      onClick={() => {
                                        void fetchJSON(`/api/orgs/${org.org_id}/invites/${inv.invite_id}`, { method: "DELETE" })
                                          .then(() => {
                                            clearCache(`/api/orgs/${org.org_id}/invites`);
                                            setDetail((prev) => ({
                                              ...prev,
                                              [org.org_id]: {
                                                members: prev[org.org_id]?.members || [],
                                                invites: (prev[org.org_id]?.invites || []).filter((item) => item.invite_id !== inv.invite_id),
                                              },
                                            }));
                                          });
                                      }}
                                    >
                                      Revoke
                                    </Button>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      ) : null}
                      {(info?.members || []).length ? (
                        <div className="data-table-wrap" style={{ marginTop: 12 }}>
                          <table className="data-table">
                            <thead>
                              <tr>
                                <th>Member</th>
                                <th>Role</th>
                                <th>Joined</th>
                                <th>Permissions</th>
                                {role === "owner" ? <th></th> : null}
                              </tr>
                            </thead>
                            <tbody>
                              {info.members.map((member) => (
                                <tr key={member.user_id}>
                                  <td>
                                    <strong>{member.display_name || member.email}</strong>
                                    <div className="hint">{member.email}</div>
                                  </td>
                                  <td>{member.role}</td>
                                  <td>{fmtDate(member.joined_at)}</td>
                                  <td>{permSummary(member.permissions)}</td>
                                  {role === "owner" && member.role !== "owner" ? (
                                    <td>
                                      <select
                                        className="field field-narrow"
                                        defaultValue={member.role}
                                        onChange={(event) => {
                                          const nextRole = event.target.value;
                                          if (nextRole === "owner" && !window.confirm("Make this member the owner? You will become Config Admin.")) {
                                            event.target.value = member.role || "creator";
                                            return;
                                          }
                                          void fetchJSON(`/api/orgs/${org.org_id}/members/${member.user_id}/role`, {
                                            method: "PATCH",
                                            headers: { "Content-Type": "application/json" },
                                            body: JSON.stringify({ role: nextRole }),
                                          }).then(() => {
                                            clearCache(`/api/orgs/${org.org_id}/members`);
                                            void load(true);
                                          });
                                        }}
                                      >
                                        <option value="creator">Creator</option>
                                        <option value="config_admin">Config Admin</option>
                                        <option value="owner">Owner</option>
                                      </select>
                                      <Button
                                        variant="ghost"
                                        onClick={() => {
                                          if (!window.confirm(`Remove ${member.email} from organization?`)) return;
                                          void fetchJSON(`/api/orgs/${org.org_id}/members/${member.user_id}`, { method: "DELETE" })
                                            .then(() => {
                                              clearCache(`/api/orgs/${org.org_id}/members`);
                                              void load(true);
                                            });
                                        }}
                                      >
                                        Remove
                                      </Button>
                                    </td>
                                  ) : role === "owner" ? <td></td> : null}
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      ) : (
                        <p className="hint">Loading members…</p>
                      )}
                    </div>
                  ) : null}
                </article>
              );
            })}
          </div>
        ) : (
          <p className="hint">No organizations yet. Cut a new one below.</p>
        )}
      </Tile>
      <Tile span="half" kicker="New plate" title="Create organization">
        <input
          className="field"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Night shift"
        />
        <Button
          variant="primary"
          onClick={async () => {
            if (!name.trim()) {
              setStatus("Enter a name.");
              return;
            }
            try {
              await fetchJSON("/api/orgs", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name: name.trim() }),
              });
              clearCache("/api/orgs");
              setName("");
              setStatus("Organization created.");
              await load();
            } catch (err) {
              setStatus(String(err));
            }
          }}
        >
          Create
        </Button>
        <p className="hint" style={{ marginTop: 12 }}>{status}</p>
      </Tile>
    </Bento>
  );
}
