import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { fetchJSON, peekCache, clearCache } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { asConfigText, CONFIG_KEYS, CONFIG_SECTIONS, JSON_KEYS, KEY_LABELS, readStudioOrg, writeStudioOrg } from "@/lib/config-keys";
import type { ConfigSource, ConfigVersion, EffectiveConfig, StudioPayload } from "@/lib/types";
import { Bento, Tile } from "@/components/Tile";
import { Button } from "@/components/Button";
import { SkeletonLines } from "@/components/Skeleton";
import { Modal } from "@/components/Modal";

function emptyFiles(): Record<string, string> {
  return Object.fromEntries(CONFIG_KEYS.map((key) => [key, ""]));
}

function filesFromConfig(config: Record<string, unknown> | undefined) {
  const next = emptyFiles();
  for (const key of CONFIG_KEYS) next[key] = asConfigText(config?.[key]);
  return next;
}

export function ConfigPage() {
  const { user, ready } = useAuth();
  const [params, setParams] = useSearchParams();
  const orgFromQuery = params.get("org_id") || "";
  const [loading, setLoading] = useState(true);
  const [active, setActive] = useState<string>(CONFIG_KEYS[0]);
  const [files, setFiles] = useState<Record<string, string>>(emptyFiles);
  const [drafts, setDrafts] = useState<Record<string, string>>(emptyFiles);
  const [status, setStatus] = useState("");
  const [source, setSource] = useState(() => orgFromQuery || readStudioOrg());
  const [sources, setSources] = useState<ConfigSource[]>([]);
  const [meta, setMeta] = useState<EffectiveConfig | null>(null);
  const [versions, setVersions] = useState<ConfigVersion[]>([]);
  const [viewVersion, setViewVersion] = useState<{ id: string; files: Record<string, string>; label: string } | null>(null);
  const [mergeOpen, setMergeOpen] = useState(false);
  const [mergeOrg, setMergeOrg] = useState("");
  const [mergeReason, setMergeReason] = useState("");
  const [versionReason, setVersionReason] = useState("");
  const [versionOpen, setVersionOpen] = useState(false);
  const [reload, setReload] = useState(0);

  const guest = ready && !user.authenticated;
  const canEdit = Boolean(meta?.can_edit) && user.authenticated;
  const cachedPublic = peekCache<StudioPayload>("/api/public/studio");

  const sourceLabel = useMemo(() => {
    if (guest) return "generic";
    return meta?.source || source;
  }, [guest, meta, source]);

  useEffect(() => {
    if (orgFromQuery && orgFromQuery !== source) setSource(orgFromQuery);
  }, [orgFromQuery, source]);

  useEffect(() => {
    if (!ready || orgFromQuery) return;
    setSource((current) => {
      const stored = readStudioOrg(user.user_id);
      return stored !== current ? stored : current;
    });
  }, [ready, user.user_id, orgFromQuery]);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;

    async function loadGuest() {
      const cached = cachedPublic?.config ? filesFromConfig(cachedPublic.config) : null;
      if (cached) {
        setFiles(cached);
        setDrafts(cached);
        setLoading(false);
      } else {
        setLoading(true);
      }
      try {
        const data = await fetchJSON<StudioPayload>("/api/public/studio");
        if (cancelled) return;
        const next = filesFromConfig(data.config);
        setFiles(next);
        setDrafts(next);
        setMeta({ source: "generic", can_edit: false, mode: "generic" });
      } catch (err) {
        if (!cancelled) setStatus(String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    async function loadAuthed() {
      const effectiveUrl = source !== "personal"
        ? `/api/config/effective?org_id=${encodeURIComponent(source)}`
        : "/api/config/effective";
      const cached = peekCache<EffectiveConfig>(effectiveUrl);
      if (cached?.config) {
        const next = filesFromConfig(cached.config);
        setFiles(next);
        setDrafts(next);
        setMeta(cached);
        setLoading(false);
      } else {
        setLoading(true);
      }
      try {
        const [effective, srcData] = await Promise.all([
          fetchJSON<EffectiveConfig>(effectiveUrl),
          fetchJSON<{ sources?: ConfigSource[] }>("/api/config/sources").catch(() => ({ sources: [] })),
        ]);
        if (cancelled) return;
        const next = filesFromConfig(effective.config);
        setFiles(next);
        setDrafts(next);
        setMeta(effective);
        setSources(srcData.sources || []);
        if (effective.config_id && effective.can_view_versions) {
          fetchJSON<{ versions?: ConfigVersion[] }>(`/api/config/${effective.config_id}/versions`)
            .then((data) => {
              if (!cancelled) setVersions(data.versions || []);
            })
            .catch(() => setVersions([]));
        } else {
          setVersions([]);
        }
      } catch (err) {
        if (!cancelled) setStatus(String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    if (guest) void loadGuest();
    else void loadAuthed();
    return () => {
      cancelled = true;
    };
  }, [ready, user.authenticated, source, reload]);

  function switchSource(next: string) {
    setSource(next);
    writeStudioOrg(user.user_id, next);
    const nextParams = new URLSearchParams(params);
    if (next === "personal") nextParams.delete("org_id");
    else nextParams.set("org_id", next);
    setParams(nextParams, { replace: true });
  }

  function validateDrafts() {
    for (const key of CONFIG_KEYS) {
      if (!JSON_KEYS.has(key)) continue;
      try {
        JSON.parse(drafts[key] || (key === "persona_seeds" ? "[]" : "{}"));
      } catch {
        throw new Error(`Invalid JSON in ${KEY_LABELS[key]}`);
      }
    }
  }

  async function saveAll() {
    if (!canEdit) {
      setStatus("Sign in to save your own files.");
      return;
    }
    setStatus("Saving…");
    try {
      validateDrafts();
      const path = meta?.owner_type === "org" && meta.org?.org_id
        ? `/api/orgs/${encodeURIComponent(meta.org.org_id)}/config`
        : "/api/user/config";
      const result = await fetchJSON<{ notice?: string; config?: Record<string, unknown> }>(path, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config: drafts, expected_version: meta?.version }),
      });
      clearCache("/api/config/");
      clearCache("/api/defaults");
      const next = { ...drafts };
      if (result.notice && typeof result.config?.copy_prompt_templates === "string") {
        next.copy_prompt_templates = result.config.copy_prompt_templates;
      }
      setFiles(next);
      setDrafts(next);
      setStatus(result.notice || "All files saved.");
      if (meta?.config_id && meta.owner_type === "org") {
        const data = await fetchJSON<{ versions?: ConfigVersion[] }>(
          `/api/config/${meta.config_id}/versions`,
          { noCache: true },
        );
        setVersions(data.versions || []);
      }
    } catch (err) {
      const message = String(err);
      setStatus(message.includes("409") ? "Someone else saved this plate. Reload and try again." : message);
    }
  }

  async function saveVersion() {
    if (!meta?.config_id) return;
    try {
      await fetchJSON(`/api/config/${meta.config_id}/save-version`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: versionReason.trim() || "manual_save" }),
      });
      setVersionOpen(false);
      setVersionReason("");
      setStatus("Version snapshot saved.");
      const data = await fetchJSON<{ versions?: ConfigVersion[] }>(`/api/config/${meta.config_id}/versions`, { noCache: true });
      setVersions(data.versions || []);
    } catch (err) {
      setStatus(String(err));
    }
  }

  async function openVersion(versionId: string) {
    if (!meta?.config_id) return;
    try {
      const version = await fetchJSON<{
        snapshot?: { files?: Record<string, { content?: string }> };
        changed_by_email?: string;
        created_at?: number;
        change_reason?: string;
      }>(`/api/config/${meta.config_id}/versions/${versionId}`);
      const snap: Record<string, string> = {};
      for (const key of CONFIG_KEYS) {
        snap[key] = asConfigText(version.snapshot?.files?.[key]?.content);
      }
      setViewVersion({
        id: versionId,
        files: snap,
        label: `${version.changed_by_email || "unknown"} · ${version.change_reason || "snapshot"}`,
      });
    } catch (err) {
      setStatus(String(err));
    }
  }

  async function deleteVersion(versionId: string) {
    if (!meta?.config_id) return;
    if (!window.confirm("Delete this snapshot? The live plate stays as it is.")) return;
    try {
      await fetchJSON(`/api/config/${meta.config_id}/versions/${encodeURIComponent(versionId)}`, {
        method: "DELETE",
      });
      setStatus("Snapshot deleted.");
      const data = await fetchJSON<{ versions?: ConfigVersion[] }>(`/api/config/${meta.config_id}/versions`, { noCache: true });
      setVersions(data.versions || []);
    } catch (err) {
      setStatus(String(err));
    }
  }

  async function deleteOlderVersions() {
    if (!meta?.config_id) return;
    if (!window.confirm("Delete every snapshot except the newest? The live plate stays as it is.")) return;
    try {
      await fetchJSON(`/api/config/${meta.config_id}/prune-old-versions`, { method: "POST" });
      setStatus("Older snapshots deleted.");
      const data = await fetchJSON<{ versions?: ConfigVersion[] }>(`/api/config/${meta.config_id}/versions`, { noCache: true });
      setVersions(data.versions || []);
    } catch (err) {
      setStatus(String(err));
    }
  }

  async function rollback(versionId: string) {
    if (!meta?.config_id) return;
    const warning = meta.owner_type === "org"
      ? "Rollback config to this version? A snapshot of the current state will be saved automatically."
      : "Rollback config to this version? The live plate will be overwritten.";
    if (!window.confirm(warning)) return;
    try {
      await fetchJSON(`/api/config/${meta.config_id}/rollback/${versionId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "rollback_from_ui" }),
      });
      clearCache("/api/config/");
      setStatus("Config rolled back.");
      setReload((n) => n + 1);
    } catch (err) {
      setStatus(String(err));
    }
  }

  async function copyToOrg() {
    if (!meta?.config_id || !mergeOrg) return;
    try {
      await fetchJSON(`/api/config/${meta.config_id}/copy-to-org`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ org_id: mergeOrg, reason: mergeReason.trim() || "copy_config_to_org" }),
      });
      setMergeOpen(false);
      setStatus("Config copied to org.");
    } catch (err) {
      setStatus(String(err));
    }
  }

  const orgSources = sources.filter((item) => item.type === "org");
  const copyTargets = (meta?.available_orgs || []).filter((org) => org.org_id !== meta?.org?.org_id);

  return (
    <Bento>
      <Tile span="third" kicker="Sources" title="Files on the desk">
        {orgSources.length ? (
          <div className="chips" style={{ marginBottom: 14 }}>
            <button type="button" className={`chip${source === "personal" ? " active" : ""}`} onClick={() => switchSource("personal")}>
              My Config
            </button>
            {orgSources.map((item) => (
              <button
                key={item.org_id}
                type="button"
                className={`chip${source === item.org_id ? " active" : ""}`}
                onClick={() => switchSource(item.org_id || "personal")}
              >
                {item.org_name} {item.config_mode === "shared_org_config" ? "(Shared)" : "(Individual)"}
              </button>
            ))}
          </div>
        ) : null}
        <p className="hint" style={{ marginBottom: 12 }}>
          Source {sourceLabel} · {guest ? "generic rules" : meta?.mode || "personal"} · {canEdit ? "editable" : "read only"}.{" "}
          <Link to="/guide">Operator guide</Link>
        </p>
        {loading ? <SkeletonLines lines={10} /> : (
          <div className="nav">
            {CONFIG_SECTIONS.map((section) => (
              <div key={section.id} className="nav-group">
                <p className={`nav-section${section.id === "business" ? " nav-section-business" : ""}`}>{section.title}</p>
                {section.keys.map((key) => (
                  <button
                    key={key}
                    type="button"
                    className={`nav-link${active === key ? " active" : ""}`}
                    onClick={() => setActive(key)}
                  >
                    <span>{KEY_LABELS[key]}</span>
                    <span className="nav-index">{JSON_KEYS.has(key) ? "JSON" : "TXT"}</span>
                  </button>
                ))}
              </div>
            ))}
          </div>
        )}
      </Tile>
      <Tile span="hero" kicker={active} title={KEY_LABELS[active]}>
        {loading ? <SkeletonLines lines={12} /> : (
          <>
            <textarea
              className="cfg-textarea"
              value={drafts[active] ?? ""}
              readOnly={!canEdit}
              rows={20}
              spellCheck={false}
              onChange={(event) => setDrafts((prev) => ({ ...prev, [active]: event.target.value }))}
            />
            <div className="action-row">
              <Button variant="primary" disabled={!canEdit} onClick={() => void saveAll()}>
                Save all files
              </Button>
              <Button disabled={!canEdit || !meta?.config_id} onClick={() => setVersionOpen(true)}>
                Save version
              </Button>
              <Button disabled={!canEdit || !meta?.can_copy || !copyTargets.length} onClick={() => {
                setMergeOrg(copyTargets[0]?.org_id || "");
                setMergeOpen(true);
              }}>
                Copy to org
              </Button>
              <span className="hint">{status}</span>
            </div>
          </>
        )}
      </Tile>
      <Tile span="wide" kicker="History" title="Version snapshots">
        {!user.authenticated ? (
          <p className="hint">Generic files have no personal history. Sign in to snapshot and roll back your plate.</p>
        ) : (
          <>
            <p className="hint" style={{ marginBottom: 12 }}>
              {meta?.owner_type === "org"
                ? "Shared org files snapshot automatically on each save."
                : "Saving files overwrites the live plate. Click Save version when you want a snapshot."}
            </p>
            {versions.length > 1 && meta?.can_rollback ? (
              <div className="action-row" style={{ marginBottom: 12 }}>
                <Button variant="ghost" onClick={() => void deleteOlderVersions()}>
                  Delete older versions
                </Button>
              </div>
            ) : null}
            {!versions.length ? (
              <p className="hint">No version history yet.</p>
            ) : (
              <div className="data-table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Date</th>
                      <th>Changed by</th>
                      <th>Keys</th>
                      <th>Reason</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {versions.map((version) => (
                      <tr key={version.version_id}>
                        <td>{version.created_at ? new Date(version.created_at * 1000).toLocaleString() : "—"}</td>
                        <td>{version.changed_by_display_name || version.changed_by_email || "—"}</td>
                        <td>{(version.changed_keys || []).map((key) => KEY_LABELS[key] || key).join(", ") || "—"}</td>
                        <td>{version.change_reason || "—"}</td>
                        <td>
                          <Button variant="ghost" onClick={() => void openVersion(version.version_id)}>View</Button>
                          {meta?.can_rollback ? (
                            <>
                              <Button variant="ghost" onClick={() => void rollback(version.version_id)}>Rollback</Button>
                              <Button variant="danger" onClick={() => void deleteVersion(version.version_id)}>Delete</Button>
                            </>
                          ) : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </Tile>
      {versionOpen ? (
        <Modal
          title="Save version snapshot"
          onClose={() => setVersionOpen(false)}
          footer={(
            <>
              <Button variant="primary" onClick={() => void saveVersion()}>Save snapshot</Button>
              <Button variant="ghost" onClick={() => setVersionOpen(false)}>Cancel</Button>
            </>
          )}
        >
          <p className="hint">Snapshots the current files. The live plate does not change.</p>
          <input className="field" value={versionReason} onChange={(e) => setVersionReason(e.target.value)} placeholder="e.g. Before testing new prompts" />
        </Modal>
      ) : null}
      {mergeOpen ? (
        <Modal
          title="Copy config to organization"
          onClose={() => setMergeOpen(false)}
          footer={(
            <>
              <Button variant="primary" onClick={() => void copyToOrg()}>Copy now</Button>
              <Button variant="ghost" onClick={() => setMergeOpen(false)}>Cancel</Button>
            </>
          )}
        >
          <p className="hint">Replaces the target org shared config with this plate.</p>
          <select className="field" value={mergeOrg} onChange={(e) => setMergeOrg(e.target.value)}>
            {copyTargets.map((org) => (
              <option key={org.org_id} value={org.org_id}>{org.name || org.org_id}</option>
            ))}
          </select>
          <input className="field" value={mergeReason} onChange={(e) => setMergeReason(e.target.value)} placeholder="e.g. Promote config to team" />
        </Modal>
      ) : null}
      {viewVersion ? (
        <Modal title={`Snapshot · ${viewVersion.label}`} onClose={() => setViewVersion(null)}>
          {CONFIG_SECTIONS.map((section) => (
            <div key={section.id} style={{ marginBottom: 12 }}>
              <p className="tile-kicker">{section.title}</p>
              <div className="chips">
                {section.keys.map((key) => (
                  <button key={key} type="button" className={`chip${active === key ? " active" : ""}`} onClick={() => setActive(key)}>
                    {KEY_LABELS[key]}
                  </button>
                ))}
              </div>
            </div>
          ))}
          <textarea className="cfg-textarea" readOnly rows={16} value={viewVersion.files[active] || ""} />
        </Modal>
      ) : null}
    </Bento>
  );
}
