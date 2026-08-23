import { useEffect, useMemo, useState } from "react";
import { fetchJSON, peekCache, invalidateRuns, clearCache } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { asConfigText, CONFIG_KEYS, KEY_HINTS, KEY_LABELS, studioOrgKey } from "@/lib/config-keys";
import { localDataPlane } from "@/lib/local-data-plane.js";
import type { ConfigSource, EffectiveConfig, Persona, Run, StudioPayload } from "@/lib/types";
import { Bento, Tile } from "@/components/Tile";
import { Button } from "@/components/Button";
import { Skeleton, SkeletonLines } from "@/components/Skeleton";
import { FileViewer } from "@/components/FileViewer";
import { FileField } from "@/components/FileField";
import { ReferenceCompose, ReferenceDesk, ReferenceFlow } from "@/pages/studio/ReferencePanel";

const FORMATS = ["HERO", "BA", "TEST", "FEAT", "UGC"] as const;
const LANGUAGES = ["ALL", "EN", "HI", "HINGLISH"] as const;

export function StudioPage() {
  const { user } = useAuth();
  const [loading, setLoading] = useState(true);
  const [studio, setStudio] = useState<StudioPayload | null>(peekCache<StudioPayload>("/api/public/studio") ?? null);
  const [personas, setPersonas] = useState<Persona[]>(studio?.personas || []);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [formats, setFormats] = useState<Set<string>>(new Set(["HERO"]));
  const [patterns, setPatterns] = useState<Record<string, string>>({});
  const [language, setLanguage] = useState("EN");
  const [flow, setFlow] = useState<"structured" | "reference">(
    () => (localStorage.getItem("adFactoryFlowMode") === "reference" ? "reference" : "structured"),
  );
  const [runs, setRuns] = useState<Run[]>([]);
  const [status, setStatus] = useState("Plate is idle.");
  const [busy, setBusy] = useState(false);
  const [deviceId, setDeviceId] = useState("");
  const [assets, setAssets] = useState<{ resource_id: string; url?: string; filename?: string }[]>([]);
  const [assetBusy, setAssetBusy] = useState(false);
  const [hypType, setHypType] = useState("none");
  const [hypVariant, setHypVariant] = useState("");
  const [provider, setProvider] = useState("opencode");
  const [opencodeUrl, setOpencodeUrl] = useState("");
  const [opencodeKey, setOpencodeKey] = useState("");
  const [googleKey, setGoogleKey] = useState("");
  const [model, setModel] = useState("");
  const [multiplier, setMultiplier] = useState(1);
  const [batchSize, setBatchSize] = useState(10);
  const [shareBg, setShareBg] = useState(false);
  const [reuseBg, setReuseBg] = useState("");
  const [reusePattern, setReusePattern] = useState("");
  const [copyJobId, setCopyJobId] = useState("");
  const [activeRunId, setActiveRunId] = useState("");
  const [viewer, setViewer] = useState("");
  const [configMeta, setConfigMeta] = useState<EffectiveConfig | null>(null);
  const [orgId, setOrgId] = useState(() => (
    user.user_id ? localStorage.getItem(studioOrgKey(user.user_id)) || "personal" : "personal"
  ));
  const [sources, setSources] = useState<ConfigSource[]>([]);
  const [pickedRuns, setPickedRuns] = useState<Set<string>>(new Set());

  useEffect(() => {
    localStorage.setItem("adFactoryFlowMode", flow);
  }, [flow]);

  useEffect(() => {
    if (user.user_id) {
      const stored = localStorage.getItem(studioOrgKey(user.user_id)) || "personal";
      setOrgId(stored);
    }
  }, [user.user_id]);

  useEffect(() => {
    let cancelled = false;
    const publicCached = peekCache<StudioPayload>("/api/public/studio");
    if (publicCached?.personas?.length && !user.authenticated) {
      setStudio(publicCached);
      setPersonas(publicCached.personas);
      setLoading(false);
    }

    async function load() {
      const personaUrl = orgId && orgId !== "personal"
        ? `/api/config/persona-summary?org_id=${encodeURIComponent(orgId)}`
        : "/api/config/persona-summary";
      const runUrl = `/api/runs?flow=${flow}`;

      if (user.authenticated && user.user_id) {
        localDataPlane
          .ensurePaired({
            ownerType: orgId && orgId !== "personal" ? "org" : "user",
            ownerId: orgId && orgId !== "personal" ? orgId : user.user_id,
          })
          .then(async (paired) => {
            if (cancelled) return;
            const id = paired.info.device_id || "";
            setDeviceId(id);
            const items = await localDataPlane.listAssets({ kind: "product_image", deviceId: id });
            const withUrls = await Promise.all(
              items.slice(0, 12).map(async (item) => ({
                ...item,
                url: await localDataPlane.assetObjectUrl(item.resource_id, id, item.version).catch(() => ""),
              })),
            );
            if (!cancelled) setAssets(withUrls);
          })
          .catch((err) => {
            if (!cancelled) setStatus(String(err));
          });
        fetchJSON<{ sources?: ConfigSource[] }>("/api/config/sources")
          .then((data) => { if (!cancelled) setSources(data.sources || []); })
          .catch(() => undefined);
      }

      const cachedRuns = peekCache<{ runs?: Run[] }>(runUrl);
      if (cachedRuns?.runs) setRuns(cachedRuns.runs);

      try {
        if (user.authenticated) {
          const [defaults, personasData, runData, effective] = await Promise.all([
            fetchJSON<StudioPayload>("/api/defaults"),
            fetchJSON<{ personas?: Persona[] }>(personaUrl).catch(() => ({ personas: [] })),
            fetchJSON<{ runs?: Run[] }>(runUrl),
            fetchJSON<EffectiveConfig>(
              orgId !== "personal" ? `/api/config/effective?org_id=${encodeURIComponent(orgId)}` : "/api/config/effective",
            ).catch(() => ({ config: {} } as EffectiveConfig)),
          ]);
          if (cancelled) return;
          const nextPersonas = (personasData.personas || defaults.personas || [])
            .map((p) => ({ number: Number(p.number), name: String(p.name || `Persona ${p.number}`) }))
            .filter((p) => p.number);
          setPersonas(nextPersonas);
          setStudio({ ...defaults, config: effective.config || defaults.config, personas: nextPersonas });
          setConfigMeta(effective);
          setRuns(runData.runs || []);
          if (defaults.batch_size) setBatchSize(defaults.batch_size);
        } else {
          const data = await fetchJSON<StudioPayload>("/api/public/studio");
          if (cancelled) return;
          setStudio(data);
          setPersonas(data.personas || []);
          setConfigMeta({ can_edit: false, source: "generic", mode: "generic" });
          const runData = await fetchJSON<{ runs?: Run[] }>(runUrl).catch(() => ({ runs: [] }));
          setRuns(runData.runs || []);
        }
      } catch (err) {
        if (!cancelled) setStatus(String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [flow, user.authenticated, user.user_id, orgId]);

  const selectedCount = selected.size;
  const formatList = useMemo(() => FORMATS.filter((fmt) => formats.has(fmt)), [formats]);
  const hypVars = studio?.hypothesis?.variables || {};
  const hypOptions = hypVars[hypType]?.options || [];

  function togglePersona(n: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(n)) next.delete(n);
      else next.add(n);
      return next;
    });
  }

  function toggleFormat(fmt: string) {
    setFormats((prev) => {
      const next = new Set(prev);
      if (next.has(fmt)) next.delete(fmt);
      else next.add(fmt);
      return next;
    });
  }

  async function startStructured() {
    if (!user.authenticated) {
      setStatus("Sign in before sending a plate.");
      return;
    }
    if (!selectedCount || !formatList.length) {
      setStatus("Pick at least one persona and one format.");
      return;
    }
    setBusy(true);
    setStatus("Allocating copy plate…");
    try {
      const formatsByPersona: Record<string, string[]> = {};
      for (const n of selected) formatsByPersona[String(n)] = formatList;
      const providerName = provider === "google" ? "google_gemini" : "opencode";
      const pendingSecret = providerName === "google_gemini" ? googleKey.trim() : opencodeKey.trim();
      await fetchJSON(`/api/user/provider-config/${providerName}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...(providerName === "opencode" && opencodeUrl.trim() ? { api_url: opencodeUrl.trim() } : {}),
          ...(pendingSecret ? { api_key: pendingSecret } : {}),
          ...(model.trim() ? { default_model: model.trim() } : {}),
        }),
      }).catch(() => undefined);
      setGoogleKey("");
      setOpencodeKey("");
      const envelope = await fetchJSON<{ run_id: string; display_batch?: string }>("/api/runs/allocate-copy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          owner_type: orgId && orgId !== "personal" ? "org" : "user",
          owner_id: orgId && orgId !== "personal" ? orgId : user.user_id,
        }),
      });
      const queued = await fetchJSON<{ copy_job_id?: string }>(`/api/runs/${encodeURIComponent(envelope.run_id)}/structured-copy`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          operation_id: `${envelope.run_id}-structured-copy`,
          settings: {
            selected_personas: [...selected],
            global_formats: formatList,
            formats_by_persona: formatsByPersona,
            multiplier,
            batch_size: batchSize,
            share_background_across_personas: shareBg,
            reuse_backgrounds_from_run_id: reuseBg,
            reuse_visual_patterns_from_run_id: reusePattern,
            hypothesis: { type: hypType, variant: hypVariant },
            visual_archetypes_by_format: patterns,
            language_mode: language,
            provider: providerName,
            model,
            org_id: orgId !== "personal" ? orgId : "",
          },
        }),
      });
      localStorage.setItem("adFactoryCopyPipeline", envelope.run_id);
      setActiveRunId(envelope.run_id);
      setCopyJobId(queued.copy_job_id || "");
      invalidateRuns();
      setStatus(`Plate ${envelope.display_batch || envelope.run_id} is on press.`);
      const data = await fetchJSON<{ runs?: Run[] }>(`/api/runs?flow=${flow}`, { noCache: true });
      setRuns(data.runs || []);
    } catch (err) {
      setStatus(String(err));
    } finally {
      setBusy(false);
    }
  }

  const orgSources = sources.filter((item) => item.type === "org");
  const canEditFiles = Boolean(configMeta?.can_edit) && user.authenticated;
  const configOrgId = configMeta?.owner_type === "org"
    ? (configMeta.org?.org_id || (orgId !== "personal" ? orgId : ""))
    : "";

  function applySavedFile(key: string, text: string) {
    setStudio((prev) => (prev ? { ...prev, config: { ...prev.config, [key]: text } } : prev));
    setConfigMeta((prev) => (prev ? { ...prev, version: (prev.version ?? 0) + 1 } : prev));
    setStatus(`${KEY_LABELS[key] || key} saved.`);
  }

  const studioBody = (
    <Bento>
      <Tile span="hero" kicker="01 · Composition" title="Personas and formats">
        <div className="chips" style={{ marginBottom: 16 }}>
          <button type="button" className={`chip${flow === "structured" ? " active" : ""}`} onClick={() => setFlow("structured")}>
            Structured
          </button>
          <button type="button" className={`chip${flow === "reference" ? " active" : ""}`} onClick={() => setFlow("reference")}>
            Reference
          </button>
        </div>
        {orgSources.length ? (
          <div className="chips" style={{ marginBottom: 16 }}>
            <button
              type="button"
              className={`chip${orgId === "personal" ? " active" : ""}`}
              onClick={() => {
                setOrgId("personal");
                if (user.user_id) localStorage.setItem(studioOrgKey(user.user_id), "personal");
              }}
            >
              My config
            </button>
            {orgSources.map((item) => (
              <button
                key={item.org_id}
                type="button"
                className={`chip${orgId === item.org_id ? " active" : ""}`}
                onClick={() => {
                  const next = item.org_id || "personal";
                  setOrgId(next);
                  if (user.user_id) localStorage.setItem(studioOrgKey(user.user_id), next);
                }}
              >
                {item.org_name}
              </button>
            ))}
          </div>
        ) : null}

        {flow === "reference" ? (
          loading ? <SkeletonGridLite /> : <ReferenceCompose />
        ) : (
          <>
            <p className="tile-kicker">Language</p>
            <div className="chips" style={{ marginBottom: 18 }}>
              {LANGUAGES.map((mode) => (
                <button key={mode} type="button" className={`chip${language === mode ? " active" : ""}`} onClick={() => setLanguage(mode)}>
                  {mode}
                </button>
              ))}
            </div>
            <p className="tile-kicker">Formats on selected personas</p>
            <div className="chips" style={{ marginBottom: 18 }}>
              {FORMATS.map((fmt) => (
                <button key={fmt} type="button" className={`chip${formats.has(fmt) ? " active" : ""}`} onClick={() => toggleFormat(fmt)}>
                  {fmt}
                </button>
              ))}
            </div>
            {formatList.length ? (
              <div className="pattern-grid">
                {formatList.map((fmt) => (
                  <label key={fmt} className="hint">
                    {fmt} pattern
                    <select className="field" value={patterns[fmt] || ""} onChange={(e) => setPatterns((prev) => ({ ...prev, [fmt]: e.target.value }))}>
                      <option value="">Auto rotate</option>
                      {(studio?.format_patterns?.[fmt] || []).map((item) => (
                        <option key={item.id} value={item.id}>{item.label || item.id}</option>
                      ))}
                    </select>
                  </label>
                ))}
              </div>
            ) : null}
            {loading ? <SkeletonGridLite /> : (
              <div className="persona-grid">
                {personas.map((persona) => (
                  <button
                    key={persona.number}
                    type="button"
                    className={`persona-card${selected.has(persona.number) ? " active" : ""}`}
                    onClick={() => togglePersona(persona.number)}
                  >
                    <span className="persona-num">P{String(persona.number).padStart(2, "0")}</span>
                    <span>{persona.name}</span>
                  </button>
                ))}
                {!personas.length ? <p className="hint">No personas on this plate yet.</p> : null}
              </div>
            )}
          </>
        )}
      </Tile>

      {flow === "structured" ? (
        <Tile span="side" kicker="02 · Make ready" title="Send to press">
          {loading ? <SkeletonLines lines={5} /> : (
            <>
              <p className="hint">
                {selectedCount} persona{selectedCount === 1 ? "" : "s"} · {formatList.join(" / ") || "no formats"} · {language}
              </p>
              <p className="hint" style={{ margin: "14px 0 18px" }}>
                {deviceId ? `Paired ${deviceId.slice(0, 8)} · ` : ""}{status}
              </p>
              <label className="hint">
                Hypothesis
                <select className="field" value={hypType} onChange={(e) => { setHypType(e.target.value); setHypVariant(""); }}>
                  {Object.entries(hypVars).map(([key, defn]) => (
                    <option key={key} value={key}>{defn.label || key}</option>
                  ))}
                </select>
              </label>
              {hypOptions.length ? (
                <label className="hint">
                  Style
                  <select className="field" value={hypVariant} onChange={(e) => setHypVariant(e.target.value)}>
                    {hypOptions.map((opt) => <option key={opt.id} value={opt.id}>{opt.label}</option>)}
                  </select>
                </label>
              ) : (
                <p className="hint">No hypothesis style selected. Ads generate normally.</p>
              )}
              <label className="hint">
                LLM provider
                <select className="field" value={provider} onChange={(e) => setProvider(e.target.value)}>
                  <option value="opencode">OpenCode</option>
                  <option value="google">Google Gemini</option>
                </select>
              </label>
              {provider === "opencode" ? (
                <>
                  <input className="field" value={opencodeUrl} onChange={(e) => setOpencodeUrl(e.target.value)} placeholder="OpenCode API URL" />
                  <input className="field" type="password" value={opencodeKey} onChange={(e) => setOpencodeKey(e.target.value)} placeholder="OpenCode API key" autoComplete="off" />
                </>
              ) : (
                <input id="googleApiKey" className="field" type="password" value={googleKey} onChange={(e) => setGoogleKey(e.target.value)} placeholder="Google API key" autoComplete="off" />
              )}
              <input className="field" value={model} onChange={(e) => setModel(e.target.value)} placeholder="Model (optional)" />
              <label className="hint">
                Ad multiplier
                <input className="field" type="number" min={1} max={20} value={multiplier} onChange={(e) => setMultiplier(Number(e.target.value) || 1)} />
              </label>
              <label className="hint">
                Ads per LLM call
                <input className="field" type="number" min={1} max={500} value={batchSize} onChange={(e) => setBatchSize(Number(e.target.value) || 10)} />
              </label>
              <label className="toggle-row">
                <input type="checkbox" checked={shareBg} onChange={(e) => setShareBg(e.target.checked)} />
                Keep same background across personas
              </label>
              <label className="hint">
                Reuse backgrounds from run
                <select className="field" value={reuseBg} onChange={(e) => setReuseBg(e.target.value)}>
                  <option value="">None</option>
                  {runs.map((run) => <option key={run.run_id} value={run.run_id}>{run.display_batch || run.run_id}</option>)}
                </select>
              </label>
              <label className="hint">
                Reuse visual patterns from run
                <select className="field" value={reusePattern} onChange={(e) => setReusePattern(e.target.value)}>
                  <option value="">None</option>
                  {runs.map((run) => <option key={`p-${run.run_id}`} value={run.run_id}>{run.display_batch || run.run_id}</option>)}
                </select>
              </label>
              <label className="hint" style={{ display: "block", marginBottom: 4 }}>
                Input images
              </label>
              <FileField
                id="inputImageFiles"
                label="Choose images"
                multiple
                accept="image/*"
                disabled={!deviceId || assetBusy}
                emptyHint={deviceId ? "No file chosen" : "Pair the local agent first"}
                onFiles={async (files, input) => {
                  if (!files?.length || !deviceId) return;
                  setAssetBusy(true);
                  try {
                    await localDataPlane.uploadAssets(files, { kind: "product_image", deviceId });
                    const items = await localDataPlane.listAssets({ kind: "product_image", deviceId });
                    const withUrls = await Promise.all(
                      items.slice(0, 12).map(async (item) => ({
                        ...item,
                        url: await localDataPlane.assetObjectUrl(item.resource_id, deviceId, item.version).catch(() => ""),
                      })),
                    );
                    setAssets(withUrls);
                    setStatus(`Stored ${files.length} image${files.length === 1 ? "" : "s"} on this device.`);
                  } catch (err) {
                    setStatus(String(err));
                  } finally {
                    setAssetBusy(false);
                    input.value = "";
                  }
                }}
              />
              {assets.length ? (
                <div className="asset-strip" style={{ marginBottom: 16 }}>
                  {assets.map((item) => (
                    <img key={item.resource_id} src={item.url} alt={item.filename || "Input image"} />
                  ))}
                </div>
              ) : assetBusy ? (
                <Skeleton className="skel-block" />
              ) : null}
              <div className="action-row">
                <Button variant="primary" disabled={busy || !user.authenticated} onClick={() => void startStructured()}>
                  {busy ? "On press…" : "Run structured plate"}
                </Button>
                <Button
                  variant="ghost"
                  disabled={!activeRunId}
                  onClick={async () => {
                    try {
                      await fetchJSON(`/api/runs/${encodeURIComponent(activeRunId)}/cancel`, { method: "POST" });
                      setStatus(`Cancel requested for ${activeRunId}.`);
                    } catch (err) {
                      setStatus(String(err));
                    }
                  }}
                >
                  Cancel
                </Button>
              </div>
              {copyJobId ? <p className="hint">Copy job {copyJobId}</p> : null}
            </>
          )}
        </Tile>
      ) : (
        <Tile span="side" kicker="02 · Reference desk" title="Context">
          {loading ? <SkeletonLines lines={8} /> : <ReferenceDesk />}
        </Tile>
      )}

      <Tile span="wide" kicker="03 · Copy desk" title="Config files on this plate">
        <p className="hint" style={{ marginBottom: 12 }}>
          {canEditFiles
            ? "Open a file to edit it. Save writes to the selected Mongo config, same as the Config desk."
            : "Open a file to read it. Sign in to edit your own plate."}
        </p>
        <div className="file-card-grid">
          {CONFIG_KEYS.map((key) => (
            <button key={key} type="button" className="file-card" onClick={() => setViewer(key)}>
              <strong>{KEY_LABELS[key]}</strong>
              <span>{KEY_HINTS[key]}</span>
              <em>{asConfigText(studio?.config?.[key]).slice(0, 72) || "empty"}</em>
            </button>
          ))}
        </div>
      </Tile>

      <Tile span="wide" kicker="04 · Dry proofs" title="Recent runs">
        <div className="action-row" style={{ marginBottom: 12 }}>
          <Button variant="ghost" onClick={async () => {
            const data = await fetchJSON<{ runs?: Run[] }>(`/api/runs?flow=${flow}`, { noCache: true });
            setRuns(data.runs || []);
          }}>Refresh</Button>
          <Button
            variant="ghost"
            disabled={!pickedRuns.size || !user.authenticated}
            onClick={async () => {
              if (!window.confirm("Delete selected runs?")) return;
              try {
                await fetchJSON("/api/runs/bulk-delete", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ run_ids: [...pickedRuns] }),
                });
                clearCache("/api/runs");
                setRuns((prev) => prev.filter((run) => !pickedRuns.has(run.run_id || "")));
                setPickedRuns(new Set());
              } catch (err) {
                setStatus(String(err));
              }
            }}
          >
            Delete selected
          </Button>
          <Button
            variant="danger"
            disabled={!user.authenticated}
            onClick={async () => {
              const typed = window.prompt("Delete every run for this account? Type PURGE to confirm:");
              if (typed !== "PURGE") return;
              try {
                await fetchJSON("/api/runs/purge-all", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ confirm: "PURGE" }),
                });
                invalidateRuns();
                setRuns([]);
              } catch (err) {
                setStatus(String(err));
              }
            }}
          >
            Delete all
          </Button>
        </div>
        {loading ? (
          <div style={{ display: "grid", gap: 10 }}>
            <Skeleton className="skel-block" />
            <Skeleton className="skel-block" />
          </div>
        ) : runs.length ? (
          <div className="run-list">
            {runs.slice(0, 12).map((run) => (
              <article key={run.run_id} className="run-row">
                <label>
                  <input
                    type="checkbox"
                    checked={pickedRuns.has(run.run_id || "")}
                    onChange={() => {
                      const id = run.run_id || "";
                      setPickedRuns((prev) => {
                        const next = new Set(prev);
                        if (next.has(id)) next.delete(id);
                        else next.add(id);
                        return next;
                      });
                    }}
                  />
                </label>
                <strong>{run.display_batch || run.run_id}</strong>
                <span>{run.status || "unknown"}</span>
                <span>{run.prompt_count ?? 0} prompts</span>
                <span>{run.image_count ?? 0} images</span>
              </article>
            ))}
          </div>
        ) : (
          <p className="hint">No runs in this flow yet. The stage stays empty until a plate lands.</p>
        )}
      </Tile>
      {viewer ? (
        <FileViewer
          configKey={viewer}
          value={studio?.config?.[viewer]}
          canEdit={canEditFiles}
          version={configMeta?.version}
          ownerType={configMeta?.owner_type}
          orgId={configOrgId}
          onClose={() => setViewer("")}
          onSaved={applySavedFile}
        />
      ) : null}
    </Bento>
  );

  if (flow !== "reference") return studioBody;
  return (
    <ReferenceFlow
      authenticated={user.authenticated}
      userId={user.user_id}
      deviceId={deviceId}
      personas={personas}
      selected={selected}
      togglePersona={togglePersona}
      language={language}
      setLanguage={setLanguage}
      studio={studio}
      status={status}
      setStatus={setStatus}
      onRuns={setRuns}
      canEditFiles={canEditFiles}
      configVersion={configMeta?.version}
      configOwnerType={configMeta?.owner_type}
      configOrgId={configOrgId}
      onConfigSaved={applySavedFile}
    >
      {studioBody}
    </ReferenceFlow>
  );
}

function SkeletonGridLite() {
  return (
    <div className="persona-grid" aria-busy="true">
      {Array.from({ length: 8 }, (_, i) => (
        <Skeleton key={i} className="skel-card" />
      ))}
    </div>
  );
}
