import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchJSON, clearCache } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Bento, Tile } from "@/components/Tile";
import { Button } from "@/components/Button";
import { SkeletonLines } from "@/components/Skeleton";

type Org = { org_id: string; name?: string; config_mode?: string };
type Membership = { org_id: string; role?: string };

export function OrganizationsPage() {
  const { user, ready } = useAuth();
  const [loading, setLoading] = useState(true);
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [name, setName] = useState("");
  const [status, setStatus] = useState("");

  async function load() {
    setLoading(true);
    try {
      const data = await fetchJSON<{ orgs?: Org[]; memberships?: Membership[] }>("/api/orgs/me");
      setOrgs(data.orgs || []);
      setMemberships(data.memberships || []);
    } catch (err) {
      setStatus(String(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (ready && user.authenticated) void load();
    if (ready && !user.authenticated) setLoading(false);
  }, [ready, user.authenticated]);

  if (ready && !user.authenticated) {
    return (
      <div className="page-gate">
        <p className="eyebrow">Crew sheet</p>
        <h1 style={{ margin: "8px 0 12px" }}>Sign in to manage teams</h1>
        <a className="btn btn-primary" href="/api/auth/google/login">Sign in</a>
      </div>
    );
  }

  return (
    <Bento>
      <Tile span="wide" kicker="Floor" title="Your organizations">
        {loading ? <SkeletonLines lines={6} /> : orgs.length ? (
          <div className="run-list">
            {orgs.map((org) => {
              const role = memberships.find((m) => m.org_id === org.org_id)?.role || "member";
              return (
                <article key={org.org_id} className="run-row">
                  <strong>{org.name || "Untitled"}</strong>
                  <span>{role}</span>
                  <span>{org.config_mode === "shared_org_config" ? "Shared" : "Individual"}</span>
                  <Link to={`/config?org_id=${encodeURIComponent(org.org_id)}`}>Fetch config</Link>
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
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Night shift"
          style={{
            width: "100%",
            minHeight: 40,
            marginBottom: 12,
            background: "var(--surface-0)",
            border: "1px solid var(--rule)",
            color: "var(--ink)",
            padding: "0 12px",
          }}
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
