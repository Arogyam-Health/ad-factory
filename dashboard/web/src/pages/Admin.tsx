import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
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
  return resp.json();
}

function sectionFromHash() {
  const hash = window.location.hash.replace("#admin/", "").split("?")[0];
  return (SECTIONS as readonly string[]).includes(hash) ? hash : "overview";
}

export function AdminPage() {
  const { user, ready } = useAuth();
  const [section, setSection] = useState(sectionFromHash);
  const [body, setBody] = useState<unknown>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const onHash = () => setSection(sectionFromHash());
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
    setLoading(true);
    setError("");
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
      const path =
        section === "users" ? "/api/admin/users"
        : section === "orgs" ? "/api/admin/orgs"
        : section === "configs" ? "/api/admin/configs"
        : section === "audit" ? "/api/admin/audit-logs"
        : section === "runs" ? "/api/admin/runs"
        : section === "health" ? "/api/admin/health"
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
  }, [section, ready, user.is_super_admin]);

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
        {section === "users" ? <a className="btn btn-ghost" href="/api/admin/exports/users">Export users</a> : null}
        {section === "orgs" ? <a className="btn btn-ghost" href="/api/admin/exports/orgs">Export orgs</a> : null}
        {section === "configs" ? <a className="btn btn-ghost" href="/api/admin/exports/configs">Export configs</a> : null}
        {section === "audit" ? <a className="btn btn-ghost" href="/api/admin/exports/audit-logs">Export audit-logs</a> : null}
        {loading ? <SkeletonLines lines={8} /> : error ? (
          <p style={{ color: "var(--danger)" }}>{error}</p>
        ) : section === "runbook" ? (
          <Runbook />
        ) : section === "readiness" ? (
          <Readiness data={body as ReadinessData} />
        ) : section === "overview" ? (
          <Overview data={body as OverviewData} />
        ) : (
          <pre className="trace-pre">{JSON.stringify(body, null, 2)}</pre>
        )}
        {section === "users" ? (
          <p className="hint" style={{ marginTop: 16 }}>
            Dangerous actions still require typed confirmation: GRANT / REVOKE / REPLACE / DISABLE.
            <Button
              variant="ghost"
              onClick={() => confirmTyped("Grant super admin? Type GRANT to confirm.", "GRANT")}
            >
              Test GRANT prompt
            </Button>
          </p>
        ) : null}
      </Tile>
    </Bento>
  );
}

type OverviewData = {
  stats?: { total_users?: number; total_orgs?: number; total_runs?: number };
  health?: { status?: string; database?: string };
};

function Overview({ data }: { data: OverviewData }) {
  const stats = data?.stats || {};
  return (
    <div className="bento">
      <div className="tile tile-third"><p className="tile-kicker">Users</p><h2>{stats.total_users ?? "—"}</h2></div>
      <div className="tile tile-third"><p className="tile-kicker">Orgs</p><h2>{stats.total_orgs ?? "—"}</h2></div>
      <div className="tile tile-third"><p className="tile-kicker">Runs</p><h2>{stats.total_runs ?? "—"}</h2></div>
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

function Runbook() {
  return (
    <div className="hint">
      <h3>Admin Runbook</h3>
      <p>Overview, users, orgs, configs, audit, readiness. Typed confirmations: GRANT, REVOKE, REPLACE, DISABLE.</p>
      <p>Always prefer disable over delete. Config copy replace_all overwrites the target.</p>
    </div>
  );
}
