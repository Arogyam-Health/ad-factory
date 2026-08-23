import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchJSON, peekCache, clearCache } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Org, ProviderSafe } from "@/lib/types";
import { Bento, Tile } from "@/components/Tile";
import { Button } from "@/components/Button";
import { SkeletonLines } from "@/components/Skeleton";

export function ProfilePage() {
  const { user, ready } = useAuth();
  const [loading, setLoading] = useState(true);
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [providers, setProviders] = useState<ProviderSafe[]>([]);
  const [googleKey, setGoogleKey] = useState("");
  const [opencodeUrl, setOpencodeUrl] = useState("");
  const [opencodeKey, setOpencodeKey] = useState("");
  const [status, setStatus] = useState("");

  function applyProviders(next: ProviderSafe[]) {
    setProviders(next);
    const opencode = next.find((item) => item.provider === "opencode");
    if (opencode?.config?.api_url) setOpencodeUrl(opencode.config.api_url);
  }

  const opencode = providers.find((item) => item.provider === "opencode");
  const google = providers.find((item) => item.provider === "google_gemini");
  const opencodeHint = opencode?.config?.has_secret
    ? `Saved key · ${opencode.config.key_fingerprint || "on this account"}`
    : "OpenCode API key";
  const googleHint = google?.config?.has_secret
    ? `Saved key · ${google.config.key_fingerprint || "on this account"}`
    : "Google API key";

  useEffect(() => {
    if (!ready) return;
    if (!user.authenticated) {
      setLoading(false);
      return;
    }
    const cachedOrgs = peekCache<{ orgs?: Org[] }>("/api/orgs/me");
    const cachedProviders = peekCache<ProviderSafe[]>("/api/user/provider-config");
    if (cachedOrgs || cachedProviders) {
      setOrgs(cachedOrgs?.orgs || []);
      applyProviders(Array.isArray(cachedProviders) ? cachedProviders : []);
      setLoading(false);
    }
    Promise.all([
      fetchJSON<{ orgs?: Org[] }>("/api/orgs/me").catch(() => ({ orgs: [] })),
      fetchJSON<ProviderSafe[]>("/api/user/provider-config", { noCache: true }).catch(() => []),
    ]).then(([orgData, providerData]) => {
      setOrgs(orgData.orgs || []);
      applyProviders(Array.isArray(providerData) ? providerData : []);
    }).finally(() => setLoading(false));
  }, [ready, user.authenticated]);

  if (ready && !user.authenticated) {
    return (
      <Bento>
        <Tile span="half" kicker="Press pass" title="Guest">
          <p className="hint">You can browse every generic file and rule. Provider keys and saved orgs appear after sign-in.</p>
          <a className="btn btn-primary" href="/api/auth/google/login" style={{ marginTop: 14 }}>Sign in</a>
        </Tile>
        <Tile span="half" kicker="Generic plate" title="What you can see now">
          <p className="hint">Studio personas, formats, hypothesis variables, and the ten config files — all from the system generic plate.</p>
          <Link className="btn btn-ghost" to="/config" style={{ marginTop: 14 }}>Open generic files</Link>
        </Tile>
      </Bento>
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
                  <span>{item.config?.default_model || item.config?.api_url || ""}</span>
                </article>
              ))}
            </div>
            <form
              className="action-row"
              onSubmit={async (event) => {
                event.preventDefault();
                if (!googleKey.trim()) return;
                try {
                  await fetchJSON("/api/user/provider-config/google_gemini", {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ api_key: googleKey.trim() }),
                  });
                  setGoogleKey("");
                  clearCache("/api/user/provider-config");
                  applyProviders(await fetchJSON<ProviderSafe[]>("/api/user/provider-config", { noCache: true }));
                  setStatus("Google key saved.");
                } catch (err) {
                  setStatus(String(err));
                }
              }}
            >
              <input id="googleApiKey" className="field" type="password" value={googleKey} onChange={(e) => setGoogleKey(e.target.value)} placeholder={googleHint} autoComplete="off" />
              <Button type="submit" variant="primary">
                Save Google key
              </Button>
            </form>
            <form
              className="action-row"
              style={{ marginTop: 10 }}
              onSubmit={async (event) => {
                event.preventDefault();
                try {
                  await fetchJSON("/api/user/provider-config/opencode", {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                      api_url: opencodeUrl.trim() || "https://opencode.ai/zen/v1",
                      ...(opencodeKey.trim() ? { api_key: opencodeKey.trim() } : {}),
                      default_model: opencode?.config?.default_model || "opencode/big-pickle",
                    }),
                  });
                  setOpencodeKey("");
                  clearCache("/api/user/provider-config");
                  applyProviders(await fetchJSON<ProviderSafe[]>("/api/user/provider-config", { noCache: true }));
                  setStatus("OpenCode settings saved.");
                } catch (err) {
                  setStatus(String(err));
                }
              }}
            >
              <input className="field" value={opencodeUrl} onChange={(e) => setOpencodeUrl(e.target.value)} placeholder="https://opencode.ai/zen/v1" />
              <input className="field" type="password" value={opencodeKey} onChange={(e) => setOpencodeKey(e.target.value)} placeholder={opencodeHint} autoComplete="off" />
              <Button type="submit">
                Save OpenCode
              </Button>
            </form>
            <p className="hint" style={{ marginTop: 10 }}>{status}</p>
          </>
        )}
      </Tile>
    </Bento>
  );
}
