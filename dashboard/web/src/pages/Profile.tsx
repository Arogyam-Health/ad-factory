import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchJSON } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Bento, Tile } from "@/components/Tile";
import { Button } from "@/components/Button";
import { SkeletonLines } from "@/components/Skeleton";

type Org = { org_id: string; name?: string };
type Provider = { provider?: string; config?: { has_secret?: boolean; key_fingerprint?: string } };

export function ProfilePage() {
  const { user, ready } = useAuth();
  const [loading, setLoading] = useState(true);
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [googleKey, setGoogleKey] = useState("");
  const [status, setStatus] = useState("");

  useEffect(() => {
    if (!ready || !user.authenticated) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    Promise.all([
      fetchJSON<{ orgs?: Org[] }>("/api/orgs/me").catch(() => ({ orgs: [] })),
      fetchJSON<Provider[]>("/api/user/provider-config").catch(() => []),
    ]).then(([orgData, providerData]) => {
      if (cancelled) return;
      setOrgs(orgData.orgs || []);
      setProviders(Array.isArray(providerData) ? providerData : []);
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [ready, user.authenticated]);

  if (ready && !user.authenticated) {
    return (
      <div className="page-gate">
        <p className="eyebrow">Press pass</p>
        <h1 style={{ margin: "8px 0 12px" }}>Sign in to view your profile</h1>
        <a className="btn btn-primary" href="/api/auth/google/login">Sign in</a>
      </div>
    );
  }

  return (
    <Bento>
      <Tile span="half" kicker="Identity" title={user.display_name || "User"}>
        {loading ? <SkeletonLines /> : (
          <>
            <p className="hint">{user.email}</p>
            {user.is_super_admin ? <p className="hint">Super admin</p> : null}
          </>
        )}
      </Tile>
      <Tile span="half" kicker="Crew" title="Organizations">
        {loading ? <SkeletonLines /> : orgs.length ? (
          <div className="run-list">
            {orgs.map((org) => (
              <article key={org.org_id} className="run-row">
                <strong>{org.name || org.org_id}</strong>
                <Link to={`/config?org_id=${encodeURIComponent(org.org_id)}`}>Config</Link>
              </article>
            ))}
          </div>
        ) : (
          <p className="hint">No orgs yet. <Link to="/organizations">Create one</Link>.</p>
        )}
      </Tile>
      <Tile span="wide" kicker="Keys" title="Provider credentials">
        {loading ? <SkeletonLines /> : (
          <>
            <div className="run-list" style={{ marginBottom: 16 }}>
              {providers.map((item) => (
                <article key={item.provider} className="run-row">
                  <strong>{item.provider}</strong>
                  <span>{item.config?.has_secret ? `saved · ${item.config.key_fingerprint || "fingerprint"}` : "empty"}</span>
                </article>
              ))}
            </div>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
              <input
                id="googleApiKey"
                type="password"
                value={googleKey}
                onChange={(e) => setGoogleKey(e.target.value)}
                placeholder="Google API key"
                style={{ minHeight: 36, minWidth: 240, background: "var(--surface-0)", border: "1px solid var(--rule)", color: "var(--ink)", padding: "0 10px" }}
              />
              <Button
                variant="primary"
                onClick={async () => {
                  if (!googleKey.trim()) return;
                  try {
                    await fetchJSON("/api/user/provider-config/google_gemini", {
                      method: "PUT",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ api_key: googleKey.trim() }),
                    });
                    setGoogleKey("");
                    setStatus("Google key saved.");
                  } catch (err) {
                    setStatus(String(err));
                  }
                }}
              >
                Save Google key
              </Button>
            </div>
            <p className="hint" style={{ marginTop: 10 }}>{status}</p>
          </>
        )}
      </Tile>
    </Bento>
  );
}
