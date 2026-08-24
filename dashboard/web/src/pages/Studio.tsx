import { useEffect, useMemo, useRef, useState } from "react";
import { fetchJSON, peekCache, invalidateRuns, clearCache } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { asConfigText, catalogConcepts, CONFIG_KEYS, KEY_HINTS, KEY_LABELS, readStudioOrg, writeStudioOrg } from "@/lib/config-keys";
import { localDataPlane } from "@/lib/local-data-plane.js";
import type { ConfigSource, EffectiveConfig, OpencodeCatalog, Persona, ProviderSafe, Run, StudioPayload } from "@/lib/types";
import { Bento, Tile } from "@/components/Tile";
import { Button } from "@/components/Button";
import { Skeleton, SkeletonLines } from "@/components/Skeleton";
import { FileViewer } from "@/components/FileViewer";
import { FileField } from "@/components/FileField";
import { ReferenceCompose, ReferenceDesk, ReferenceFlow } from "@/pages/studio/ReferencePanel";
import { RunWorkspace } from "@/pages/studio/RunWorkspace";
import { BatchSelect } from "@/pages/studio/BatchSelect";
import { LazyAsset } from "@/pages/studio/LazyAsset";
import { displayRunStatus } from "@/lib/run-status";
import { DownloadKindDialog } from "@/components/DownloadKindDialog";

const FORMATS = ["HERO", "BA", "TEST", "FEAT", "UGC"] as const;
const LANGUAGES = ["ALL", "EN", "HI", "HINGLISH"] as const;
const DEFAULT_OPENCODE_MODEL = "opencode/big-pickle";
const RUNS_PER_PAGE = 5;

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function pairingStatus(err?: unknown) {
  const host = window.location.hostname;
  const onPublicSite = host.endsWith(".onrender.com") || (host !== "localhost" && host !== "127.0.0.1");
  if (onPublicSite) {
    return "This tab cannot read local files yet. Click Pair local agent and allow local network access if Chrome asks.";
  }
  return err ? String(err) : "Start the local agent on this machine.";
}

async function queueRunImages(
  runId: string,
  mode: "45" | "both" | "916",
  engine: string,
  agentId: string,
  deviceId: string,
) {
  return fetchJSON<{ job_id?: string }>(`/api/runs/${encodeURIComponent(runId)}/image-generation`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      operation_id: `${runId}-images-${mode}-${Date.now()}`,
      engine,
      mode,
      agent_id: agentId,
      device_id: deviceId,
    }),
  });
}

export function StudioPage() {
  const { user, ready } = useAuth();
  const [loading, setLoading] = useState(() => !(
    peekCache<StudioPayload>("/api/defaults")
    || peekCache<StudioPayload>("/api/public/studio")
  ));
  const [studio, setStudio] = useState<StudioPayload | null>(
    peekCache<StudioPayload>("/api/defaults") ?? peekCache<StudioPayload>("/api/public/studio") ?? null,
  );
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
  const [agentId, setAgentId] = useState("");
  const [assets, setAssets] = useState<{ resource_id: string; url?: string; filename?: string; version?: number }[]>([]);
  const [assetBusy, setAssetBusy] = useState(false);
  const [hypType, setHypType] = useState("none");
  const [hypVariant, setHypVariant] = useState("");
  const [provider, setProvider] = useState("opencode");
  const [opencodeUrl, setOpencodeUrl] = useState("");
  const [opencodeKey, setOpencodeKey] = useState("");
  const [googleKey, setGoogleKey] = useState("");
  const [model, setModel] = useState(DEFAULT_OPENCODE_MODEL);
  const [models, setModels] = useState<string[]>([DEFAULT_OPENCODE_MODEL]);
  const [keySaved, setKeySaved] = useState(false);
  const [keyHint, setKeyHint] = useState("OpenCode API key");
  const [googleSaved, setGoogleSaved] = useState(false);
  const [googleHint, setGoogleHint] = useState("Google API key");
  const [savingKey, setSavingKey] = useState(false);
  const [multiplier, setMultiplier] = useState(1);
  const [batchSize, setBatchSize] = useState(10);
  const [shareBg, setShareBg] = useState(false);
  const [reuseBg, setReuseBg] = useState("");
  const [reusePattern, setReusePattern] = useState("");
  const [copyJobId, setCopyJobId] = useState("");
  const [activeRunId, setActiveRunId] = useState("");
  const [viewer, setViewer] = useState("");
  const [configMeta, setConfigMeta] = useState<EffectiveConfig | null>(null);
  const [orgId, setOrgId] = useState(() => readStudioOrg());
  const [sources, setSources] = useState<ConfigSource[]>([]);
  const [pickedRuns, setPickedRuns] = useState<Set<string>>(new Set());
  const [openRunId, setOpenRunId] = useState("");
  const [runPage, setRunPage] = useState(0);
  const [paired, setPaired] = useState(false);
  const [deskTick, setDeskTick] = useState(0);
  const [batchBusy, setBatchBusy] = useState("");
  const [imageEngine, setImageEngine] = useState(() => (
    localStorage.getItem("adFactoryImageEngine") === "gemini" ? "gemini" : "chatgpt"
  ));
  const [selectedConcept, setSelectedConcept] = useState(
    () => localStorage.getItem("adFactorySelectedConcept") || "",
  );
  const [downloadPrompt, setDownloadPrompt] = useState(false);
  const newestRunRef = useRef("");

  useEffect(() => {
    localStorage.setItem("adFactoryFlowMode", flow);
  }, [flow]);
  useEffect(() => {
    localStorage.setItem("adFactoryImageEngine", imageEngine);
  }, [imageEngine]);
  useEffect(() => {
    localStorage.setItem("adFactorySelectedConcept", selectedConcept);
  }, [selectedConcept]);

  useEffect(() => {
    if (!ready) return;
    setOrgId(readStudioOrg(user.user_id));
  }, [ready, user.user_id]);

  function selectOrg(next: string) {
    setOrgId(next);
    writeStudioOrg(user.user_id, next);
  }

  function applyProviderState(next: {
    url: string;
    model: string;
    models: string[];
    keySaved: boolean;
    keyHint: string;
    googleSaved: boolean;
    googleHint: string;
  }) {
    if (next.url) setOpencodeUrl(next.url);
    setModels(next.models);
    setModel(next.model);
    setKeySaved(next.keySaved);
    setKeyHint(next.keyHint);
    setGoogleSaved(next.googleSaved);
    setGoogleHint(next.googleHint);
  }

  async function loadProviders() {
    const [list, catalog] = await Promise.all([
      fetchJSON<ProviderSafe[]>("/api/user/provider-config", { noCache: true }).catch(() => []),
      fetchJSON<OpencodeCatalog>("/api/user/provider-config/opencode/catalog", { noCache: true }).catch(() => ({} as OpencodeCatalog)),
    ]);
    const opencode = (Array.isArray(list) ? list : []).find((item) => item.provider === "opencode");
    const google = (Array.isArray(list) ? list : []).find((item) => item.provider === "google_gemini");
    const catalogModels = Object.values(catalog.models_by_provider || {}).flat();
    const nextModels = [...new Set([
      DEFAULT_OPENCODE_MODEL,
      opencode?.config?.default_model || "",
      catalog.default_model || "",
      ...catalogModels,
    ].filter(Boolean))];
    return {
      url: opencode?.config?.api_url || catalog.api_url || "",
      model: opencode?.config?.default_model || catalog.default_model || DEFAULT_OPENCODE_MODEL,
      models: nextModels.length ? nextModels : [DEFAULT_OPENCODE_MODEL],
      keySaved: Boolean(opencode?.config?.has_secret),
      keyHint: opencode?.config?.has_secret
        ? `Saved key · ${opencode.config?.key_fingerprint || "on this account"}`
        : "OpenCode API key",
      googleSaved: Boolean(google?.config?.has_secret),
      googleHint: google?.config?.has_secret
        ? `Saved key · ${google.config?.key_fingerprint || "on this account"}`
        : "Google API key",
    };
  }

  async function saveProviderKeys() {
    if (!user.authenticated) {
      setStatus("Sign in to save provider keys.");
      return;
    }
    setSavingKey(true);
    try {
      const providerName = provider === "google" ? "google_gemini" : "opencode";
      const pendingSecret = providerName === "google_gemini" ? googleKey.trim() : opencodeKey.trim();
      await fetchJSON(`/api/user/provider-config/${providerName}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...(providerName === "opencode" ? { api_url: opencodeUrl.trim() || "https://opencode.ai/zen/v1" } : {}),
          ...(pendingSecret ? { api_key: pendingSecret } : {}),
          ...(model.trim() ? { default_model: model.trim() } : { default_model: DEFAULT_OPENCODE_MODEL }),
        }),
      });
      setOpencodeKey("");
      setGoogleKey("");
      clearCache("/api/user/provider-config");
      applyProviderState(await loadProviders());
      setStatus("Provider settings saved.");
    } catch (err) {
      setStatus(String(err));
    } finally {
      setSavingKey(false);
    }
  }

  useEffect(() => {
    if (!ready) return;
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
      const defaultsUrl = orgId && orgId !== "personal"
        ? `/api/defaults?org_id=${encodeURIComponent(orgId)}`
        : "/api/defaults";
      const runUrl = `/api/runs?flow=${flow}`;
      const effectiveUrl = orgId !== "personal"
        ? `/api/config/effective?org_id=${encodeURIComponent(orgId)}`
        : "/api/config/effective";

      function applyPersonas(defaults: StudioPayload | null, personaRows?: Persona[]) {
        const nextPersonas = (personaRows || defaults?.personas || [])
          .map((p) => ({ number: Number(p.number), name: String(p.name || `Persona ${p.number}`) }))
          .filter((p) => p.number);
        if (nextPersonas.length) setPersonas(nextPersonas);
        if (defaults) {
          setStudio((prev) => ({
            ...defaults,
            config: prev?.config || defaults.config,
            personas: nextPersonas.length ? nextPersonas : defaults.personas,
          }));
          if (defaults.batch_size) setBatchSize(defaults.batch_size);
        }
        return nextPersonas;
      }

      const cachedDefaults = peekCache<StudioPayload>(defaultsUrl) || peekCache<StudioPayload>("/api/public/studio");
      const cachedPersonas = peekCache<{ personas?: Persona[] }>(personaUrl);
      const cachedRuns = peekCache<{ runs?: Run[] }>(runUrl);
      const cachedEffective = peekCache<EffectiveConfig>(effectiveUrl);
      if (cachedDefaults || cachedPersonas?.personas?.length) {
        applyPersonas(cachedDefaults || null, cachedPersonas?.personas);
        setLoading(false);
      }
      if (cachedRuns?.runs) setRuns(cachedRuns.runs);
      if (cachedEffective) {
        setConfigMeta(cachedEffective);
        if (cachedEffective.config) {
          setStudio((prev) => (prev ? { ...prev, config: cachedEffective.config } : prev));
        }
      }

      if (user.authenticated && user.user_id) {
        const owners = [
          ...(orgId && orgId !== "personal" ? [{ ownerType: "org" as const, ownerId: orgId }] : []),
          { ownerType: "user" as const, ownerId: user.user_id },
        ];
        const restored = localDataPlane.restoreStoredSession(owners);
        if (restored) {
          setDeviceId(restored.deviceId);
          setAgentId(restored.agentId);
          setPaired(true);
        }
        fetchJSON<{ sources?: ConfigSource[] }>("/api/config/sources")
          .then((data) => { if (!cancelled) setSources(data.sources || []); })
          .catch(() => undefined);
        void loadProviders().then((next) => {
          if (!cancelled) applyProviderState(next);
        });
      }

      try {
        if (user.authenticated) {
          const [defaults, personasData, runData] = await Promise.all([
            fetchJSON<StudioPayload>(defaultsUrl),
            fetchJSON<{ personas?: Persona[] }>(personaUrl).catch(() => ({ personas: [] })),
            fetchJSON<{ runs?: Run[] }>(runUrl),
          ]);
          if (cancelled) return;
          applyPersonas(defaults, personasData.personas);
          setRuns(runData.runs || []);
          setStatus((prev) => (prev.startsWith("Error: 401") ? "Plate is idle." : prev));
          setLoading(false);
          void fetchJSON<EffectiveConfig>(effectiveUrl)
            .catch(() => ({ config: {} } as EffectiveConfig))
            .then((effective) => {
              if (cancelled) return;
              setConfigMeta(effective);
              setStudio((prev) => (
                prev
                  ? { ...prev, config: effective.config || prev.config }
                  : { ...defaults, config: effective.config || defaults.config }
              ));
            });
        } else {
          const data = await fetchJSON<StudioPayload>("/api/public/studio");
          if (cancelled) return;
          setStudio(data);
          setPersonas(data.personas || []);
          setConfigMeta({ can_edit: false, source: "generic", mode: "generic" });
          setRuns([]);
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
  }, [ready, flow, user.authenticated, user.user_id, orgId]);

  const selectedCount = selected.size;
  const openRun = runs.find((run) => run.run_id === openRunId) || null;
  const runPages = Math.max(1, Math.ceil(runs.length / RUNS_PER_PAGE));
  const safeRunPage = Math.min(runPage, runPages - 1);
  const pageRuns = runs.slice(safeRunPage * RUNS_PER_PAGE, safeRunPage * RUNS_PER_PAGE + RUNS_PER_PAGE);
  const formatList = useMemo(() => FORMATS.filter((fmt) => formats.has(fmt)), [formats]);

  useEffect(() => {
    const newest = runs[0]?.run_id || "";
    if (newest && newest !== newestRunRef.current) {
      newestRunRef.current = newest;
      setRunPage(0);
    }
    if (runPage > runPages - 1) setRunPage(Math.max(0, runPages - 1));
  }, [runs, runPage, runPages]);
  const hypVars = studio?.hypothesis?.variables || {};
  const hypOptions = hypVars[hypType]?.options || [];

  useEffect(() => {
    const variables = studio?.hypothesis?.variables;
    if (!variables || !Object.keys(variables).length) return;
    if (!variables[hypType]) {
      setHypType("none");
      setHypVariant("");
      return;
    }
    const options = variables[hypType]?.options || [];
    if (hypVariant && !options.some((opt) => opt.id === hypVariant)) {
      setHypVariant("");
    }
  }, [studio?.hypothesis?.variables, hypType, hypVariant]);

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
          default_model: model.trim() || DEFAULT_OPENCODE_MODEL,
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
            selected_concept: selectedConcept,
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
      setOpenRunId(envelope.run_id);
      setRunPage(0);
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

  function openRunRow(run: Run) {
    const id = run.run_id || "";
    setOpenRunId(id);
    setActiveRunId(id);
  }

  async function pairLocalAgent() {
    if (!user.authenticated || !user.user_id) {
      setStatus("Sign in before pairing the local agent.");
      return { ok: false, deviceId: "", agentId: "" };
    }
    setStatus("Pairing this tab with the local agent…");
    try {
      const info = await localDataPlane.discover();
      const liveId = info.device_id || "";
      if (liveId) setDeviceId(liveId);
      const owners = [
        ...(orgId && orgId !== "personal" ? [{ ownerType: "org" as const, ownerId: orgId }] : []),
        { ownerType: "user" as const, ownerId: user.user_id },
      ];
      let lastError: unknown;
      for (const owner of owners) {
        try {
          const next = await localDataPlane.ensurePaired({ ...owner, deviceId: liveId });
          const id = next.info.device_id || liveId;
          const nextAgent = next.agent?.agent_id || "";
          setDeviceId(id);
          setAgentId(nextAgent);
          setPaired(true);
          setStatus(`Paired ${id.slice(0, 8)}. Local files are available in this tab.`);
          return { ok: true, deviceId: id, agentId: nextAgent };
        } catch (err) {
          lastError = err;
        }
      }
      setPaired(false);
      setStatus(pairingStatus(lastError));
      return { ok: false, deviceId: "", agentId: "" };
    } catch (err) {
      setPaired(false);
      setStatus(pairingStatus(err));
      return { ok: false, deviceId: "", agentId: "" };
    }
  }

  async function generateSelected(mode: "45" | "both") {
    const ids = [...pickedRuns];
    if (!ids.length) {
      setStatus("Select batches first.");
      return;
    }
    const live = paired
      ? { ok: true, deviceId, agentId }
      : await pairLocalAgent();
    if (!live.ok) return;
    setBatchBusy(mode);
    try {
      for (const id of ids) {
        await queueRunImages(id, mode, imageEngine, live.agentId, live.deviceId);
      }
      invalidateRuns();
      const label = mode === "both" ? "4:5 + 9:16" : "4:5";
      setStatus(`Queued ${label} for ${ids.length} batch${ids.length === 1 ? "" : "es"}.`);
      const data = await fetchJSON<{ runs?: Run[] }>(`/api/runs?flow=${flow}`, { noCache: true });
      setRuns(data.runs || []);
    } catch (err) {
      setStatus(String(err));
    } finally {
      setBatchBusy("");
    }
  }

  async function downloadSelected(includeRaw: boolean) {
    const ids = [...pickedRuns];
    if (!ids.length) {
      setStatus("Select batches first.");
      return;
    }
    const live = paired && deviceId
      ? { ok: true, deviceId, agentId }
      : await pairLocalAgent();
    if (!live.ok || !live.deviceId) return;
    setBatchBusy("download");
    try {
      for (const id of ids) {
        const run = runs.find((item) => item.run_id === id);
        const blob = await localDataPlane.downloadRun(id, live.deviceId, { includeRaw });
        triggerDownload(blob, `${run?.display_batch || id}.zip`);
      }
      setDeskTick((value) => value + 1);
      setStatus(`Downloaded ${ids.length} batch${ids.length === 1 ? "" : "es"} (${includeRaw ? "cropped + raw" : "cropped only"}).`);
    } catch (err) {
      setStatus(String(err));
    } finally {
      setBatchBusy("");
    }
  }

  async function cancelSelected() {
    const ids = [...pickedRuns];
    if (!ids.length && activeRunId) ids.push(activeRunId);
    if (!ids.length) {
      setStatus("Select a run to cancel.");
      return;
    }
    setBatchBusy("cancel");
    try {
      let canceled = 0;
      for (const id of ids) {
        await fetchJSON(`/api/runs/${encodeURIComponent(id)}/cancel`, { method: "POST" });
        canceled += 1;
      }
      invalidateRuns();
      setStatus(`Cancel requested for ${canceled} run${canceled === 1 ? "" : "s"}.`);
      const data = await fetchJSON<{ runs?: Run[] }>(`/api/runs?flow=${flow}`, { noCache: true });
      setRuns(data.runs || []);
      setDeskTick((value) => value + 1);
    } catch (err) {
      setStatus(String(err));
    } finally {
      setBatchBusy("");
    }
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
          <OrgConfigChips orgId={orgId} sources={orgSources} onSelect={selectOrg} />
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
                {paired ? `Paired ${deviceId.slice(0, 8)} · ` : deviceId ? `Agent ${deviceId.slice(0, 8)} reachable · ` : ""}{status}
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
                    <option value="">None</option>
                    {hypOptions.map((opt) => <option key={opt.id} value={opt.id}>{opt.label}</option>)}
                  </select>
                </label>
              ) : (
                <p className="hint">No hypothesis style selected. Ads generate normally.</p>
              )}
              <label className="hint">
                Concept
                <select className="field" value={selectedConcept} onChange={(e) => setSelectedConcept(e.target.value)}>
                  <option value="">None</option>
                  {catalogConcepts(studio).map((item) => (
                    <option key={item.id} value={item.id}>{item.label}</option>
                  ))}
                </select>
              </label>
              <label className="hint">
                LLM provider
                <select className="field" value={provider} onChange={(e) => setProvider(e.target.value)}>
                  <option value="opencode">OpenCode</option>
                  <option value="google">Google Gemini</option>
                </select>
              </label>
              <form
                className="action-row"
                style={{ marginBottom: 12 }}
                onSubmit={(event) => {
                  event.preventDefault();
                  void saveProviderKeys();
                }}
              >
                {provider === "opencode" ? (
                  <>
                    <input className="field" value={opencodeUrl} onChange={(e) => setOpencodeUrl(e.target.value)} placeholder="https://opencode.ai/zen/v1" />
                    <input className="field" type="password" value={opencodeKey} onChange={(e) => setOpencodeKey(e.target.value)} placeholder={keyHint} autoComplete="off" />
                  </>
                ) : (
                  <input id="googleApiKey" className="field" type="password" value={googleKey} onChange={(e) => setGoogleKey(e.target.value)} placeholder={googleHint} autoComplete="off" />
                )}
                <label className="hint">
                  Model
                  <select className="field" value={models.includes(model) ? model : DEFAULT_OPENCODE_MODEL} onChange={(e) => setModel(e.target.value)}>
                    {models.map((item) => (
                      <option key={item} value={item}>{item}</option>
                    ))}
                  </select>
                </label>
                <Button type="submit" disabled={savingKey || !user.authenticated}>
                  {savingKey ? "Saving…" : "Save API key"}
                </Button>
                <span className="hint">{keySaved || googleSaved ? "Saved key stays on this account. Type a new one only to replace it." : "Save before running a plate."}</span>
              </form>
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
                disabled={!paired || assetBusy}
                emptyHint={
                  paired
                    ? "No file chosen"
                    : deviceId
                      ? "Agent is on this machine, but this tab is not paired to it"
                      : "Start the local agent on this machine"
                }
                onFiles={async (files, input) => {
                  if (!files?.length || !deviceId || !paired) return;
                  setAssetBusy(true);
                  try {
                    await localDataPlane.uploadAssets(files, { kind: "product_image", deviceId });
                    const items = await localDataPlane.listAssets({ kind: "product_image", deviceId });
                    setAssets(items.slice(0, 12));
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
                    <LazyAsset
                      key={item.resource_id}
                      resourceId={item.resource_id}
                      deviceId={deviceId}
                      version={item.version}
                      alt={item.filename || "Input image"}
                    />
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
        {orgSources.length ? (
          <OrgConfigChips orgId={orgId} sources={orgSources} onSelect={selectOrg} />
        ) : null}
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
          <BatchSelect runs={runs} picked={pickedRuns} onChange={setPickedRuns} />
          <Button variant="ghost" disabled={!user.authenticated} onClick={() => void pairLocalAgent()}>
            {paired ? "Paired" : "Pair local agent"}
          </Button>
          <label className="toolbar-field">
            <span>Image engine</span>
            <select
              className="field"
              value={imageEngine}
              onChange={(e) => setImageEngine(e.target.value === "gemini" ? "gemini" : "chatgpt")}
            >
              <option value="chatgpt">ChatGPT</option>
              <option value="gemini">Gemini</option>
            </select>
          </label>
          <Button disabled={Boolean(batchBusy) || !pickedRuns.size} onClick={() => void generateSelected("45")}>
            {batchBusy === "45" ? "Queuing…" : "Generate 4:5"}
          </Button>
          <Button disabled={Boolean(batchBusy) || !pickedRuns.size} onClick={() => void generateSelected("both")}>
            {batchBusy === "both" ? "Queuing…" : "Generate 4:5 + 9:16"}
          </Button>
          <Button
            variant="ghost"
            disabled={Boolean(batchBusy) || (!pickedRuns.size && !activeRunId)}
            onClick={() => void cancelSelected()}
          >
            {batchBusy === "cancel" ? "Cancelling…" : "Cancel run"}
          </Button>
          <Button
            disabled={Boolean(batchBusy) || !pickedRuns.size}
            onClick={() => {
              if (!pickedRuns.size) {
                setStatus("Select batches first.");
                return;
              }
              setDownloadPrompt(true);
            }}
          >
            {batchBusy === "download" ? "Downloading…" : "Download batches"}
          </Button>
          <Button variant="ghost" onClick={async () => {
            const data = await fetchJSON<{ runs?: Run[] }>(`/api/runs?flow=${flow}`, { noCache: true });
            setRuns(data.runs || []);
            setDeskTick((value) => value + 1);
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
          <>
          <div className="run-list">
            {pageRuns.map((run) => (
              <article
                key={run.run_id}
                className={`run-row${openRunId === run.run_id ? " active" : ""}`}
                role="button"
                tabIndex={0}
                onClick={() => openRunRow(run)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    openRunRow(run);
                  }
                }}
              >
                <label onClick={(event) => event.stopPropagation()}>
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
                <strong className="run-open">{run.display_batch || run.run_id}</strong>
                <span>{displayRunStatus(run)}</span>
                <span>{run.prompt_count ?? 0} prompts</span>
                <span>{run.image_count ?? 0} images</span>
              </article>
            ))}
          </div>
          {runs.length > RUNS_PER_PAGE ? (
            <div className="run-pager">
              <Button
                variant="ghost"
                disabled={safeRunPage <= 0}
                onClick={() => setRunPage((page) => Math.max(0, page - 1))}
              >
                ← Prev
              </Button>
              <span className="hint">Page {safeRunPage + 1} of {runPages}</span>
              <Button
                variant="ghost"
                disabled={safeRunPage >= runPages - 1}
                onClick={() => setRunPage((page) => Math.min(runPages - 1, page + 1))}
              >
                Next →
              </Button>
            </div>
          ) : null}
          {openRun ? (
            <RunWorkspace
              run={openRun}
              deviceId={deviceId}
              agentId={agentId}
              paired={paired}
              refreshToken={deskTick}
              onPair={pairLocalAgent}
              onClose={() => setOpenRunId("")}
              onStatus={setStatus}
              onRefresh={async () => {
                const data = await fetchJSON<{ runs?: Run[] }>(`/api/runs?flow=${flow}`, { noCache: true });
                setRuns(data.runs || []);
              }}
            />
          ) : null}
          </>
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
      {downloadPrompt ? (
        <DownloadKindDialog
          title={`Download ${pickedRuns.size} batch${pickedRuns.size === 1 ? "" : "es"}`}
          onClose={() => setDownloadPrompt(false)}
          onChoose={(includeRaw) => {
            setDownloadPrompt(false);
            void downloadSelected(includeRaw);
          }}
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
      selectedConcept={selectedConcept}
      setSelectedConcept={setSelectedConcept}
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

function OrgConfigChips({
  orgId,
  sources,
  onSelect,
}: {
  orgId: string;
  sources: ConfigSource[];
  onSelect: (next: string) => void;
}) {
  return (
    <div className="chips" style={{ marginBottom: 16 }}>
      <button
        type="button"
        className={`chip${orgId === "personal" ? " active" : ""}`}
        onClick={() => onSelect("personal")}
      >
        My config
      </button>
      {sources.map((item) => (
        <button
          key={item.org_id}
          type="button"
          className={`chip${orgId === item.org_id ? " active" : ""}`}
          onClick={() => onSelect(item.org_id || "personal")}
        >
          {item.org_name}
        </button>
      ))}
    </div>
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
