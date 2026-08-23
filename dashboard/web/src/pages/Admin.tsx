import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { peekCache, primeCache } from "@/lib/api";
import { Bento, Tile } from "@/components/Tile";
import { Button } from "@/components/Button";
import { SkeletonLines } from "@/components/Skeleton";

const SECTIONS = [
  "overview",
  "users",
  "individual-users",
  "orgs",
  "configs",
  "config-copy",
  "audit",
  "runs",
  "images",
  "prompts",
  "providers",
  "health",
  "readiness",
  "runbook",
] as const;

function confirmTyped(msg: string, expected: string) {
  const typed = window.prompt(msg);
  return typed !== null && typed.trim() === expected;
}

async function adminFetch<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const resp = await fetch(path, { credentials: "same-origin", ...opts });
  if (resp.status === 401) throw new Error("Authentication required. Please login.");
  if (resp.status === 403) throw new Error("Access denied. Super admin access required.");
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = body.detail || detail;
    } catch {
      /* text */
    }
    throw new Error(detail);
  }
  const data = await resp.json();
  if (!opts.method || opts.method === "GET") primeCache(path, data);
  return data;
}

function sectionFromHash() {
  const hash = window.location.hash.replace("#admin/", "").split("?")[0];
  return (SECTIONS as readonly string[]).includes(hash) ? hash : "overview";
}

function fmtDate(ts?: number) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString();
}

export function AdminPage() {
  const { user, ready } = useAuth();
  const [section, setSection] = useState(sectionFromHash);
  const [body, setBody] = useState<unknown>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const onHash = () => {
      setSection(sectionFromHash());
      setPage(1);
      setSearch("");
    };
    window.addEventListener("hashchange", onHash);
    if (!window.location.hash) window.location.hash = "admin/overview";
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    if (!ready || !user.is_super_admin) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setError("");
    const cachedPath = sectionPath(section, page, search);
    const cached = cachedPath ? peekCache(cachedPath) : undefined;
    if (cached) {
      setBody(cached);
      setLoading(false);
    } else if (section === "overview") {
      const overview = peekCache("/api/admin/overview");
      const stats = peekCache("/api/admin/stats");
      const health = peekCache("/api/admin/health");
      if (overview && stats && health) {
        setBody({ overview, stats, health });
        setLoading(false);
      } else {
        setLoading(true);
      }
    } else {
      setLoading(true);
    }

    const load = async () => {
      if (section === "runbook") {
        setBody("runbook");
        return;
      }
      if (section === "readiness") {
        setBody(await adminFetch("/api/admin/readiness"));
        return;
      }
      if (section === "overview") {
        const [overview, stats, health] = await Promise.all([
          adminFetch("/api/admin/overview"),
          adminFetch("/api/admin/stats"),
          adminFetch("/api/admin/health"),
        ]);
        setBody({ overview, stats, health });
        return;
      }
      if (section === "config-copy") {
        setBody("copy");
        return;
      }
      if (section === "health") {
        setBody(await adminFetch("/api/admin/health"));
        return;
      }
      const params = new URLSearchParams({ page: String(page), per_page: "50" });
      if (search) params.set("search", search);
      const path =
        section === "users" ? `/api/admin/users?${params}`
        : section === "individual-users" ? `/api/admin/individual-users?${params}`
        : section === "orgs" ? `/api/admin/orgs?${params}`
        : section === "configs" ? `/api/admin/configs?${params}`
        : section === "audit" ? `/api/admin/audit-logs?${params}`
        : section === "runs" ? `/api/admin/runs?${params}`
        : section === "images" ? `/api/admin/images?${params}`
        : section === "prompts" ? `/api/admin/prompts?${params}`
        : section === "providers" ? `/api/admin/provider-configs?${params}`
        : `/api/admin/${section}`;
      setBody(await adminFetch(path));
    };
    load()
      .catch((err) => {
        if (!cancelled) setError(String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [section, ready, user.is_super_admin, page, search, reload]);

  if (ready && !user.authenticated) {
    return (
      <div className="page-gate">
        <p className="eyebrow">Make ready</p>
        <h1 style={{ margin: "8px 0 12px" }}>Sign in required</h1>
        <a className="btn btn-primary" href="/api/auth/google/login">Sign in</a>
      </div>
    );
  }

  if (ready && !user.is_super_admin) {
    return (
      <div className="page-gate">
        <p className="eyebrow">Restricted</p>
        <h1 style={{ margin: "8px 0 12px" }}>Access denied</h1>
        <p className="hint">Super admin only.</p>
      </div>
    );
  }

  return (
    <Bento>
      <Tile span="third" kicker="Sections" title="Admin desk">
        <div className="nav">
          {SECTIONS.map((id) => (
            <button
              key={id}
              type="button"
              className={`nav-link${section === id ? " active" : ""}`}
              onClick={() => {
                window.location.hash = `admin/${id}`;
              }}
            >
              {id}
            </button>
          ))}
        </div>
      </Tile>
      <Tile span="hero" kicker={section} title={section.replace(/-/g, " ")}>
        {["users", "orgs", "configs", "audit"].includes(section) ? (
          <a className="btn btn-ghost" href={`/api/admin/exports/${section === "audit" ? "audit-logs" : section}`}>
            Export JSON
          </a>
        ) : null}
        {["users", "orgs", "configs", "audit", "runs", "images", "prompts", "providers", "individual-users"].includes(section) ? (
          <div className="action-row" style={{ margin: "12px 0" }}>
            <input className="field" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search" onKeyDown={(e) => {
              if (e.key === "Enter") setPage(1);
            }} />
            <Button onClick={() => setPage(1)}>Search</Button>
          </div>
        ) : null}
        {loading ? <SkeletonLines lines={8} /> : error ? (
          <p style={{ color: "var(--danger)" }}>{error}</p>
        ) : section === "runbook" ? (
          <Runbook />
        ) : section === "readiness" ? (
          <Readiness data={body as ReadinessData} />
        ) : section === "overview" ? (
          <Overview data={body as OverviewData} />
        ) : section === "config-copy" ? (
          <ConfigCopy onStatus={setError} />
        ) : section === "health" ? (
          <pre className="trace-pre">{JSON.stringify(body, null, 2)}</pre>
        ) : (
          <AdminTable
            section={section}
            data={body as ListPayload}
            currentUserId={user.user_id}
            onReload={() => setReload((n) => n + 1)}
          />
        )}
        {((body as ListPayload | null)?.pages ?? 0) > 1 ? (
          <div className="action-row" style={{ marginTop: 12 }}>
            <Button disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Prev</Button>
            <span className="hint">{page} / {(body as ListPayload).pages ?? 1}</span>
            <Button disabled={page >= ((body as ListPayload).pages ?? 1)} onClick={() => setPage((p) => p + 1)}>Next</Button>
          </div>
        ) : null}
      </Tile>
    </Bento>
  );
}

function sectionPath(section: string, page: number, search: string) {
  if (["runbook", "config-copy", "overview"].includes(section)) return "";
  const params = new URLSearchParams({ page: String(page), per_page: "50" });
  if (search) params.set("search", search);
  if (section === "users") return `/api/admin/users?${params}`;
  if (section === "orgs") return `/api/admin/orgs?${params}`;
  if (section === "readiness") return "/api/admin/readiness";
  return "";
}

type OverviewData = {
  stats?: Record<string, number>;
  overview?: { users?: { new_today?: number; new_this_week?: number }; sessions?: { active?: number } };
  health?: { status?: string; database?: string };
};

function Overview({ data }: { data: OverviewData }) {
  const stats = data?.stats || {};
  return (
    <div className="bento">
      <div className="tile tile-third"><p className="tile-kicker">Users</p><h2>{stats.total_users ?? "—"}</h2></div>
      <div className="tile tile-third"><p className="tile-kicker">Orgs</p><h2>{stats.total_orgs ?? "—"}</h2></div>
      <div className="tile tile-third"><p className="tile-kicker">Runs</p><h2>{stats.total_runs ?? "—"}</h2></div>
      <div className="tile tile-third"><p className="tile-kicker">Configs</p><h2>{stats.total_configs ?? "—"}</h2></div>
      <div className="tile tile-third"><p className="tile-kicker">Sessions</p><h2>{stats.active_sessions ?? data.overview?.sessions?.active ?? "—"}</h2></div>
      <div className="tile tile-third"><p className="tile-kicker">Audit</p><h2>{stats.total_audit_logs ?? "—"}</h2></div>
      <p className="hint">Backend {data.health?.status || "?"} · Database {data.health?.database || "?"}</p>
    </div>
  );
}

type ReadinessData = {
  summary?: { ok?: number; warning?: number; error?: number };
  overall?: string;
  checks?: { key: string; status: string; message?: string }[];
};

function Readiness({ data }: { data: ReadinessData }) {
  return (
    <div>
      <p className="hint">
        {data.overall || "ready"} · {data.summary?.ok || 0} passed · {data.summary?.warning || 0} warnings · {data.summary?.error || 0} errors
      </p>
      <div className="run-list" style={{ marginTop: 16 }}>
        {(data.checks || []).map((check) => (
          <article key={check.key} className="run-row">
            <strong>{check.key}</strong>
            <span>{check.status}</span>
            <span>{check.message}</span>
          </article>
        ))}
      </div>
    </div>
  );
}

function ConfigCopy({ onStatus }: { onStatus: (msg: string) => void }) {
  const [sourceType, setSourceType] = useState("user");
  const [sourceId, setSourceId] = useState("");
  const [targetType, setTargetType] = useState("user");
  const [targetId, setTargetId] = useState("");
  const [mode, setMode] = useState("replace_all");
  const [reason, setReason] = useState("admin_copy_from_dashboard");
  const [result, setResult] = useState("");
  return (
    <div className="stack">
      <label className="hint">Source owner type
        <select className="field" value={sourceType} onChange={(e) => setSourceType(e.target.value)}>
          <option value="user">User</option>
          <option value="org">Org</option>
        </select>
      </label>
      <input className="field" value={sourceId} onChange={(e) => setSourceId(e.target.value)} placeholder="usr_… or org_…" />
      <label className="hint">Target owner type
        <select className="field" value={targetType} onChange={(e) => setTargetType(e.target.value)}>
          <option value="user">User</option>
          <option value="org">Org</option>
        </select>
      </label>
      <input className="field" value={targetId} onChange={(e) => setTargetId(e.target.value)} placeholder="usr_… or org_…" />
      <select className="field" value={mode} onChange={(e) => setMode(e.target.value)}>
        <option value="replace_all">Replace all</option>
        <option value="merge_missing">Merge missing</option>
      </select>
      <input className="field" value={reason} onChange={(e) => setReason(e.target.value)} />
      <Button
        variant="primary"
        onClick={async () => {
          if (mode === "replace_all" && !confirmTyped("This will overwrite target config. Type REPLACE to confirm.", "REPLACE")) return;
          try {
            const data = await adminFetch<{ mode?: string }>("/api/admin/configs/copy", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                source_owner_type: sourceType,
                source_owner_id: sourceId.trim(),
                target_owner_type: targetType,
                target_owner_id: targetId.trim(),
                mode,
                reason,
              }),
            });
            setResult(`Copied (${data.mode})`);
          } catch (err) {
            onStatus(String(err));
          }
        }}
      >
        Copy config
      </Button>
      <p className="hint">{result}</p>
    </div>
  );
}

type ListPayload = { items?: Record<string, unknown>[]; pages?: number; page?: number; total?: number };

function AdminTable({
  section,
  data,
  currentUserId,
  onReload,
}: {
  section: string;
  data: ListPayload;
  currentUserId: string;
  onReload: () => void;
}) {
  const items = data?.items || (Array.isArray(data) ? data as Record<string, unknown>[] : []);
  if (!items.length) return <p className="hint">No rows.</p>;

  async function patchUser(userId: string, payload: Record<string, unknown>) {
    await adminFetch("/api/admin/users/" + encodeURIComponent(userId), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    onReload();
  }

  async function patchOrg(orgId: string, payload: Record<string, unknown>) {
    await adminFetch("/api/admin/orgs/" + encodeURIComponent(orgId), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    onReload();
  }

  return (
    <div className="data-table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            {section === "users" || section === "individual-users" ? (
              <><th>Email</th><th>Name</th><th>Active</th><th>SA</th><th>Created</th><th></th></>
            ) : section === "orgs" ? (
              <><th>Name</th><th>Domain</th><th>Active</th><th>Mode</th><th>Created</th><th></th></>
            ) : section === "configs" ? (
              <><th>ID</th><th>Owner</th><th>Type</th><th>Updated</th></>
            ) : section === "audit" ? (
              <><th>Event</th><th>Actor</th><th>When</th></>
            ) : (
              <><th>Record</th><th>Meta</th></>
            )}
          </tr>
        </thead>
        <tbody>
          {items.map((row, index) => {
            const key = String(row.user_id || row.org_id || row.config_id || row.event_id || row.run_id || index);
            if (section === "users" || section === "individual-users") {
              const uid = String(row.user_id || "");
              return (
                <tr key={key}>
                  <td>{String(row.email || "")}</td>
                  <td>{String(row.display_name || "")}</td>
                  <td>{row.is_active ? "yes" : "no"}</td>
                  <td>{row.is_super_admin ? "yes" : ""}</td>
                  <td>{fmtDate(Number(row.created_at || 0))}</td>
                  <td>
                    {uid && uid !== currentUserId ? (
                      <>
                        <Button
                          variant="ghost"
                          onClick={() => {
                            if (row.is_active && !window.confirm(`Disable user ${row.email}?`)) return;
                            void patchUser(uid, { is_active: !row.is_active, reason: row.is_active ? "disabled_from_admin_ui" : undefined });
                          }}
                        >
                          {row.is_active ? "Disable" : "Enable"}
                        </Button>
                        <Button
                          variant="ghost"
                          onClick={() => {
                            if (row.is_super_admin && !confirmTyped(`Revoke super admin for ${row.email}? Type REVOKE to confirm.`, "REVOKE")) return;
                            if (!row.is_super_admin && !confirmTyped(`Grant super admin to ${row.email}? Type GRANT to confirm.`, "GRANT")) return;
                            void patchUser(uid, { is_super_admin: !row.is_super_admin });
                          }}
                        >
                          {row.is_super_admin ? "Revoke SA" : "Grant SA"}
                        </Button>
                      </>
                    ) : null}
                  </td>
                </tr>
              );
            }
            if (section === "orgs") {
              const oid = String(row.org_id || "");
              return (
                <tr key={key}>
                  <td>{String(row.name || "")}</td>
                  <td>{String(row.domain || "")}</td>
                  <td>{row.is_active !== false ? "yes" : "no"}</td>
                  <td>{String(row.config_mode || "—")}</td>
                  <td>{fmtDate(Number(row.created_at || 0))}</td>
                  <td>
                    <Button
                      variant="ghost"
                      onClick={() => {
                        if (row.is_active !== false && !confirmTyped(`Disable org ${row.name}? Type DISABLE to confirm.`, "DISABLE")) return;
                        void patchOrg(oid, { is_active: row.is_active === false });
                      }}
                    >
                      {row.is_active === false ? "Enable" : "Disable"}
                    </Button>
                  </td>
                </tr>
              );
            }
            if (section === "configs") {
              return (
                <tr key={key}>
                  <td>{String(row.config_id || "")}</td>
                  <td>{String(row.owner_id || "")}</td>
                  <td>{String(row.owner_type || "")}</td>
                  <td>{fmtDate(Number(row.updated_at || row.created_at || 0))}</td>
                </tr>
              );
            }
            if (section === "audit") {
              return (
                <tr key={key}>
                  <td>{String(row.event_type || row.action || "")}</td>
                  <td>{String(row.actor_email || row.actor_user_id || "")}</td>
                  <td>{fmtDate(Number(row.created_at || 0))}</td>
                </tr>
              );
            }
            return (
              <tr key={key}>
                <td>{String(row.run_id || row.image_id || row.prompt_id || row.provider || key)}</td>
                <td>{String(row.status || row.filename || row.owner_id || "")}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Runbook() {
  return (
    <div className="hint stack">
      <h3>Admin Runbook</h3>
      <p>Overview, users, orgs, configs, audit, readiness. Typed confirmations: GRANT, REVOKE, REPLACE, DISABLE.</p>
      <p>Always prefer disable over delete. Config copy replace_all overwrites the target.</p>
      <p>Never reveal provider API keys. Review audit logs after admin actions.</p>
    </div>
  );
}
