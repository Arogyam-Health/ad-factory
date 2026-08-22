import { useEffect, useMemo, useState } from "react";
import { fetchJSON, clearCache } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Bento, Tile } from "@/components/Tile";
import { Button } from "@/components/Button";
import { SkeletonLines } from "@/components/Skeleton";

const CONFIG_KEYS = [
  "product_master_doc",
  "starting_prompt",
  "copy_prompt_templates",
  "persona_seeds",
  "copy_architecture",
  "background_variant",
  "prompt_assembler_templates",
  "conversion_916_prompt",
  "reference_starting_prompt",
  "reference_product_master_doc",
] as const;

const KEY_LABELS: Record<string, string> = {
  product_master_doc: "Product Master Doc",
  starting_prompt: "Starting Prompt",
  copy_prompt_templates: "Copy Prompt Templates",
  persona_seeds: "Persona Seeds",
  copy_architecture: "Copy Architecture",
  background_variant: "Background Variant",
  prompt_assembler_templates: "Prompt Assembler Templates",
  conversion_916_prompt: "9:16 Conversion Prompt",
  reference_starting_prompt: "Reference Starting Prompt",
  reference_product_master_doc: "Reference Product Doc",
};

export function ConfigPage() {
  const { user, ready } = useAuth();
  const orgFromQuery = useMemo(() => new URLSearchParams(window.location.search).get("org_id") || "", []);
  const [loading, setLoading] = useState(true);
  const [active, setActive] = useState<string>(CONFIG_KEYS[0]);
  const [files, setFiles] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState("");
  const [source] = useState(orgFromQuery || "personal");

  useEffect(() => {
    if (!ready) return;
    if (!user.authenticated) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    const url = source !== "personal" ? `/api/config/effective?org_id=${encodeURIComponent(source)}` : "/api/config/effective";
    fetchJSON<{ config?: Record<string, string> }>(url, { cache: "no-store" })
      .then((data) => {
        if (cancelled) return;
        const next = data.config || {};
        setFiles(next);
        setDraft(String(next[active] ?? ""));
      })
      .catch((err) => setStatus(String(err)))
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [ready, user.authenticated, source, active]);

  async function save() {
    setStatus("Saving…");
    try {
      const effectiveUrl = source !== "personal"
        ? `/api/config/effective?org_id=${encodeURIComponent(source)}`
        : "/api/config/effective";
      const effective = await fetchJSON<{ version?: number; owner_type?: string }>(effectiveUrl);
      const path = source !== "personal" && effective.owner_type === "org"
        ? `/api/orgs/${encodeURIComponent(source)}/config`
        : "/api/user/config";
      await fetchJSON(path, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          config: { [active]: draft },
          expected_version: effective.version,
        }),
      });
      clearCache("/api/config/effective");
      setStatus("Plate saved.");
    } catch (err) {
      setStatus(String(err));
    }
  }

  if (ready && !user.authenticated) {
    return (
      <div className="page-gate">
        <p className="eyebrow">Locked form</p>
        <h1 style={{ margin: "8px 0 12px" }}>Sign in to manage configuration</h1>
        <a className="btn btn-primary" href="/api/auth/google/login">Sign in</a>
      </div>
    );
  }

  return (
    <Bento>
      <Tile span="third" kicker="Sources" title="Files on the desk">
        {loading ? <SkeletonLines lines={8} /> : (
          <div className="nav">
            {CONFIG_KEYS.map((key) => (
              <button
                key={key}
                type="button"
                className={`nav-link${active === key ? " active" : ""}`}
                onClick={() => {
                  setActive(key);
                  setDraft(String(files[key] ?? ""));
                }}
              >
                {KEY_LABELS[key]}
              </button>
            ))}
          </div>
        )}
      </Tile>
      <Tile span="hero" kicker={active} title={KEY_LABELS[active]}>
        {orgFromQuery ? <p className="hint">Opened from Teams · org {orgFromQuery}</p> : null}
        {loading ? <SkeletonLines lines={10} /> : (
          <>
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              rows={18}
              style={{
                width: "100%",
                background: "var(--surface-0)",
                border: "1px solid var(--rule)",
                color: "var(--ink)",
                padding: 12,
                fontFamily: "var(--font-mono)",
                fontSize: 13,
                resize: "vertical",
              }}
            />
            <div style={{ display: "flex", gap: 10, marginTop: 14, alignItems: "center" }}>
              <Button variant="primary" onClick={() => void save()}>Save file</Button>
              <span className="hint">{status}</span>
            </div>
          </>
        )}
      </Tile>
    </Bento>
  );
}
