import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { fetchJSON, peekCache, invalidateRuns, clearCache, primeCache } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { catalogConcepts, catalogLanguageModes, CONFIG_SECTIONS, KEY_HINTS, KEY_LABELS, readStudioOrg, summarizeConfigValue, writeStudioOrg } from "@/lib/config-keys";
import { localDataPlane } from "@/lib/local-data-plane.js";
import type { ConfigSource, EffectiveConfig, FormatOption, OpencodeCatalog, Persona, ProviderSafe, Run, StudioPayload } from "@/lib/types";
import { Bento, Tile } from "@/components/Tile";
import { Button } from "@/components/Button";
import { ListPager } from "@/components/ListPager";
import { Skeleton, SkeletonLines } from "@/components/Skeleton";
import { FileViewer } from "@/components/FileViewer";
import { FileField } from "@/components/FileField";
import { RunTerminal, type TerminalLine } from "@/components/RunTerminal";
import { ReferenceCompose, ReferenceDesk, ReferenceFlow } from "@/pages/studio/ReferencePanel";
import { RunWorkspace } from "@/pages/studio/RunWorkspace";
import { BatchSelect } from "@/pages/studio/BatchSelect";
import { LazyAsset } from "@/pages/studio/LazyAsset";
import { copyFailureDetail, displayRunStatus, imageFailureDetail } from "@/lib/run-status";
import { filterRunsByFlow, isActiveRun, isReferenceRun, mergeRunLists } from "@/lib/run-flow";
import { readStudioSession, writeStudioSession } from "@/lib/studio-session";
import { DownloadKindDialog } from "@/components/DownloadKindDialog";

const FALLBACK_FORMATS: FormatOption[] = [
  { id: "HERO", label: "HERO" },
  { id: "BA", label: "BA" },
  { id: "TEST", label: "TEST" },
  { id: "FEAT", label: "FEAT" },
  { id: "UGC", label: "UGC" },
];

function catalogFormats(studio: StudioPayload | null): FormatOption[] {
  const items = (studio?.formats || []).map((item) => (
    typeof item === "string"
      ? { id: item, label: item }
      : { id: String(item.id || "").toUpperCase(), label: item.label || item.id }
  )).filter((item) => item.id);
  return items.length ? items : FALLBACK_FORMATS;
}
const DEFAULT_OPENCODE_MODEL = "opencode/big-pickle";
const RUNS_PER_PAGE = 5;
const PICKED_PRODUCTS_KEY = "adFactoryPickedProducts";
const PRODUCT_ASSET_LIMIT = 48;

type ProductAsset = { resource_id: string; url?: string; filename?: string; version?: number };
type FlowKind = "structured" | "reference";

function restorePairing() {
  try {
    return localDataPlane.restoreStoredSession() || null;
  } catch {
    return null;
  }
}

function readPickedProducts(): string[] | null {
  try {
    const raw = localStorage.getItem(PICKED_PRODUCTS_KEY);
    if (raw == null) return null;
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.map(String) : null;
  } catch {
    return null;
  }
}

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
  productAssetIds: string[] = [],
) {
  if (mode !== "916" && !productAssetIds.length) {
    throw new Error("Select at least one input image to send to the image model.");
  }
  return fetchJSON<{ job_id?: string }>(`/api/runs/${encodeURIComponent(runId)}/image-generation`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      operation_id: `${runId}-images-${mode}-${Date.now()}`,
      engine,
      mode,
      agent_id: agentId,
      device_id: deviceId,
      ...(productAssetIds.length ? { product_asset_ids: productAssetIds } : {}),
    }),
  });
}

export function StudioPage() {
  const { user, ready } = useAuth();
  const { pathname } = useLocation();
  const studioVisible = pathname === "/";
  const restoredPair = useMemo(() => restorePairing(), []);
  const savedSession = useMemo(() => readStudioSession(), []);
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
  const [personaFormats, setPersonaFormats] = useState<Record<number, string[]>>({});
  const [patterns, setPatterns] = useState<Record<string, string>>({});
  const [language, setLanguage] = useState("EN");
  const [flow, setFlow] = useState<"structured" | "reference">(
    () => (localStorage.getItem("adFactoryFlowMode") === "reference" ? "reference" : "structured"),
  );
  const [structuredRuns, setStructuredRuns] = useState<Run[]>(() => savedSession.structuredRuns);
  const [referenceRuns, setReferenceRuns] = useState<Run[]>(() => savedSession.referenceRuns);
  const [status, setStatus] = useState(savedSession.status || "Plate is idle.");
  const [logLines, setLogLines] = useState<TerminalLine[]>(() => savedSession.logLines);
  const [busy, setBusy] = useState(false);
  const [deviceId, setDeviceId] = useState(restoredPair?.deviceId || "");
  const [agentId, setAgentId] = useState(restoredPair?.agentId || "");
  const [assets, setAssets] = useState<ProductAsset[]>([]);
  const [pickedProducts, setPickedProducts] = useState<Set<string>>(new Set());
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
  const [copyJobId, setCopyJobId] = useState(() => savedSession.copyJobId);
  const [activeRunId, setActiveRunId] = useState(() => savedSession.activeRunId);
  const [viewer, setViewer] = useState("");
  const [plateTick, setPlateTick] = useState(0);
  const [configMeta, setConfigMeta] = useState<EffectiveConfig | null>(null);
  const [orgId, setOrgId] = useState(() => readStudioOrg());
  const [sources, setSources] = useState<ConfigSource[]>([]);
  const [pickedRuns, setPickedRuns] = useState<Set<string>>(new Set());
  const [openRunByFlow, setOpenRunByFlow] = useState({
    structured: localStorage.getItem("adFactoryCopyPipeline") || "",
    reference: localStorage.getItem("adFactoryReferencePipeline") || "",
  });
  const [runPage, setRunPage] = useState(0);
  const [paired, setPaired] = useState(() => Boolean(restoredPair?.deviceId));
  const [deskTick, setDeskTick] = useState(0);
  const [batchBusy, setBatchBusy] = useState("");
  const [imageEngine, setImageEngine] = useState(() => (
    localStorage.getItem("adFactoryImageEngine") === "gemini" ? "gemini" : "chatgpt"
  ));
  const [copyBrowserEngine, setCopyBrowserEngine] = useState(() => (
    localStorage.getItem("adFactoryCopyBrowserEngine") === "gemini" ? "gemini" : "chatgpt"
  ));
  const [selectedConcept, setSelectedConcept] = useState(
    () => localStorage.getItem("adFactorySelectedConcept") || "",
  );
  const [downloadPrompt, setDownloadPrompt] = useState(false);
  const newestRunRef = useRef("");
  const runsPanelRef = useRef<HTMLDivElement>(null);
  const logIdRef = useRef(savedSession.logId);
  const lastPollLineRef = useRef<Record<string, string>>({});
  const pollIdsRef = useRef<string[]>([]);
  const runs = flow === "reference" ? referenceRuns : structuredRuns;
  const openRunId = openRunByFlow[flow];

  const appendLog = useCallback((text: string, level: TerminalLine["level"] = "info") => {
    const next = String(text || "").trim();
    if (!next) return;
    setLogLines((prev) => {
      const last = prev[prev.length - 1];
      if (last?.text === next && last.level === level) return prev;
      logIdRef.current += 1;
      return [...prev, { id: logIdRef.current, at: Date.now(), level, text: next }].slice(-80);
    });
    setStatus((prev) => (prev === next ? prev : next));
  }, []);

  function setFlowOpenRun(target: FlowKind, id: string) {
    setOpenRunByFlow((prev) => ({ ...prev, [target]: id }));
    if (target === "structured" && id) localStorage.setItem("adFactoryCopyPipeline", id);
    if (target === "reference" && id) localStorage.setItem("adFactoryReferencePipeline", id);
  }

  function writeRunList(target: FlowKind, rows: Run[], allowEmpty = false) {
    const filtered = filterRunsByFlow(rows, target);
    const setter = target === "reference" ? setReferenceRuns : setStructuredRuns;
    setter((prev) => {
      if (!filtered.length && prev.length && !allowEmpty) return prev;
      return filtered;
    });
    if (filtered.length || allowEmpty) {
      primeCache(`/api/runs?flow=${target}`, { runs: filtered });
    }
  }

  async function loadFlowRuns(target: FlowKind, allowEmpty = true) {
    const url = `/api/runs?flow=${target}`;
    const data = await fetchJSON<{ runs?: Run[] }>(url, { noCache: true });
    let next = data.runs || [];
    const remembered = target === "reference" ? savedSession.referenceRuns : savedSession.structuredRuns;
    if (remembered.length) next = mergeRunLists(next, remembered);
    if (paired && deviceId) {
      try {
        const local = await localDataPlane.listRuns(deviceId);
        const mapped = local.map((item) => ({
          run_id: item.run_id,
          display_batch: item.display_batch,
          flow_type: item.flow_type,
          created_at: item.created_at,
          status: item.status || "queued",
          prompt_count: item.prompt_count || 0,
          image_count: item.image_count || 0,
        }));
        next = mergeRunLists(next, filterRunsByFlow(mapped, target));
      } catch {
        /* local inventory is optional */
      }
    }
    writeRunList(target, next, allowEmpty && !remembered.length);
    return next;
  }

  useEffect(() => {
    writeStudioSession({
      structuredRuns,
      referenceRuns,
      copyJobId,
      activeRunId,
      logLines,
      logId: logIdRef.current,
      status,
    });
  }, [structuredRuns, referenceRuns, copyJobId, activeRunId, logLines, status]);
  useEffect(() => {
    localStorage.setItem("adFactoryFlowMode", flow);
  }, [flow]);
  useEffect(() => {
    localStorage.setItem("adFactoryImageEngine", imageEngine);
  }, [imageEngine]);
  useEffect(() => {
    localStorage.setItem("adFactoryCopyBrowserEngine", copyBrowserEngine);
  }, [copyBrowserEngine]);
  useEffect(() => {
    localStorage.setItem("adFactorySelectedConcept", selectedConcept);
  }, [selectedConcept]);
  useEffect(() => {
    if (!assets.length && !pickedProducts.size) return;
    localStorage.setItem(PICKED_PRODUCTS_KEY, JSON.stringify([...pickedProducts]));
  }, [pickedProducts, assets.length]);
  useEffect(() => {
    if (!paired || !deviceId || !localDataPlane.session(deviceId)) return;
    let cancelled = false;
    setAssetBusy(true);
    void localDataPlane.listAssets({ kind: "product_image", deviceId })
      .then((items) => {
        if (cancelled) return;
        const next = items.slice(0, PRODUCT_ASSET_LIMIT);
        setAssets(next);
        setPickedProducts((prev) => {
          const available = new Set(next.map((item) => item.resource_id));
          if (prev.size) return new Set([...prev].filter((id) => available.has(id)));
          const stored = readPickedProducts();
          if (stored) return new Set(stored.filter((id) => available.has(id)));
          return available;
        });
      })
      .catch((err) => {
        if (!cancelled) setStatus(String(err));
      })
      .finally(() => {
        if (!cancelled) setAssetBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [paired, deviceId]);

  useEffect(() => {
    if (!ready) return;
    setOrgId(readStudioOrg(user.user_id));
  }, [ready, user.user_id]);

  const pairRef = useRef<(opts?: { silent?: boolean }) => Promise<{ ok: boolean; deviceId: string; agentId: string }>>(
    async () => ({ ok: false, deviceId: "", agentId: "" }),
  );

  useEffect(() => {
    if (!studioVisible || !user.authenticated || !user.user_id) return;
    let cancelled = false;
    let timer = 0;
    async function connect() {
      const live = await pairRef.current({ silent: true });
      if (cancelled) return;
      if (!live.ok) timer = window.setTimeout(connect, 15000);
    }
    void connect();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [studioVisible, user.authenticated, user.user_id, orgId]);

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
      const structuredUrl = "/api/runs?flow=structured";
      const referenceUrl = "/api/runs?flow=reference";
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
      const cachedStructured = peekCache<{ runs?: Run[] }>(structuredUrl);
      const cachedReference = peekCache<{ runs?: Run[] }>(referenceUrl);
      const cachedEffective = peekCache<EffectiveConfig>(effectiveUrl);
      if (cachedDefaults || cachedPersonas?.personas?.length) {
        applyPersonas(cachedDefaults || null, cachedPersonas?.personas);
        setLoading(false);
      }
      if (cachedStructured?.runs) writeRunList("structured", cachedStructured.runs);
      if (cachedReference?.runs) writeRunList("reference", cachedReference.runs);
      if (cachedRuns?.runs) writeRunList(flow, cachedRuns.runs);
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
          const [defaults, personasData] = await Promise.all([
            fetchJSON<StudioPayload>(defaultsUrl),
            fetchJSON<{ personas?: Persona[] }>(personaUrl).catch(() => ({ personas: [] })),
          ]);
          if (cancelled) return;
          applyPersonas(defaults, personasData.personas);
          await Promise.all([
            loadFlowRuns("structured", false),
            loadFlowRuns("reference", false),
          ]);
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
  }, [ready, flow, user.authenticated, user.user_id, orgId, plateTick]);

  const selectedCount = selected.size;
  const sortedRuns = useMemo(
    () => [...runs].sort((a, b) => Number(b.created_at || 0) - Number(a.created_at || 0)),
    [runs],
  );
  const openRun = sortedRuns.find((run) => run.run_id === openRunId)
    || (openRunId
      ? {
          run_id: openRunId,
          display_batch: openRunId,
          prompt_count: 0,
          image_count: 0,
          status: "running",
          copy_generation: { status: "queued" },
        }
      : null);
  const runPages = Math.max(1, Math.ceil(sortedRuns.length / RUNS_PER_PAGE));
  const safeRunPage = Math.min(runPage, runPages - 1);
  const pageRuns = sortedRuns.slice(safeRunPage * RUNS_PER_PAGE, safeRunPage * RUNS_PER_PAGE + RUNS_PER_PAGE);
  const formatOptions = useMemo(() => catalogFormats(studio), [studio]);
  const languageModes = useMemo(() => catalogLanguageModes(studio), [studio]);
  const formatList = useMemo(
    () => formatOptions.filter((item) => formats.has(item.id)).map((item) => item.id),
    [formatOptions, formats],
  );

  useEffect(() => {
    const ids = new Set(formatOptions.map((item) => item.id));
    setFormats((prev) => {
      const next = new Set([...prev].filter((id) => ids.has(id)));
      if (!next.size && formatOptions[0]) next.add(formatOptions[0].id);
      if (next.size === prev.size && [...next].every((id) => prev.has(id))) return prev;
      return next;
    });
  }, [formatOptions]);

  useEffect(() => {
    const ids = new Set(languageModes.map((item) => item.id));
    if (ids.size && !ids.has(language)) {
      setLanguage(languageModes[0]?.id || "EN");
    }
  }, [languageModes, language]);

  useEffect(() => {
    const newest = sortedRuns[0]?.run_id || "";
    if (newest && newest !== newestRunRef.current) {
      newestRunRef.current = newest;
      setRunPage(0);
    }
    if (runPage > runPages - 1) setRunPage(Math.max(0, runPages - 1));
  }, [sortedRuns, runPage, runPages]);

  useEffect(() => {
    if (!copyJobId || !activeRunId) return;
    let cancelled = false;
    let timer = 0;
    let failures = 0;
    async function poll() {
      try {
        const job = await fetchJSON<{ status?: string }>(
          `/api/runs/${encodeURIComponent(activeRunId)}/structured-copy/${encodeURIComponent(copyJobId)}`,
          { noCache: true },
        );
        if (cancelled) return;
        failures = 0;
        if (["completed", "failed", "canceled"].includes(String(job.status || ""))) {
          await loadFlowRuns("structured", true);
          if (cancelled) return;
          setFlowOpenRun("structured", activeRunId);
          setRunPage(0);
          setDeskTick((value) => value + 1);
          appendLog(`Structured copy ${job.status} for ${activeRunId}.`, job.status === "failed" ? "error" : "info");
          return;
        }
      } catch {
        if (cancelled) return;
        failures += 1;
        if (failures >= 20) return;
      }
      timer = window.setTimeout(() => {
        void poll();
      }, 3000);
    }
    void poll();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [copyJobId, activeRunId]);

  useEffect(() => {
    pollIdsRef.current = [...structuredRuns, ...referenceRuns]
      .filter(isActiveRun)
      .map((run) => run.run_id || "")
      .filter(Boolean);
  }, [structuredRuns, referenceRuns]);

  useEffect(() => {
    if (!studioVisible) return;
    let cancelled = false;
    let timer = 0;
    async function tick() {
      const ids = pollIdsRef.current;
      for (const id of ids) {
        try {
          const live = await fetchJSON<Run>(`/api/runs/${encodeURIComponent(id)}`, { noCache: true });
          if (cancelled) return;
          const target: FlowKind = isReferenceRun(live) ? "reference" : "structured";
          const setter = target === "reference" ? setReferenceRuns : setStructuredRuns;
          setter((prev) => prev.map((row) => (row.run_id === id ? { ...row, ...live } : row)));
          const err = imageFailureDetail(live) || copyFailureDetail(live);
          const key = `${displayRunStatus(live)}:${err}`;
          if (lastPollLineRef.current[id] === key) continue;
          lastPollLineRef.current[id] = key;
          appendLog(
            err
              ? `${live.display_batch || id}: ${displayRunStatus(live)} — ${err}`
              : `${live.display_batch || id}: ${displayRunStatus(live)}`,
            err ? "error" : "info",
          );
        } catch {
          /* keep last known row */
        }
      }
      timer = window.setTimeout(() => {
        void tick();
      }, 4000);
    }
    void tick();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [studioVisible]);
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

  function formatsForPersona(n: number) {
    if (personaFormats[n]) return personaFormats[n];
    if (selected.has(n)) return formatList;
    return [];
  }

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
      else if (next.size < 8) next.add(fmt);
      setPersonaFormats((current) => {
        if (!selected.size) return current;
        const updated = { ...current };
        for (const number of selected) {
          const existing = new Set(current[number] ?? [...prev]);
          if (next.has(fmt)) existing.add(fmt);
          else existing.delete(fmt);
          updated[number] = [...existing];
        }
        return updated;
      });
      return next;
    });
  }

  function toggleProduct(id: string) {
    setPickedProducts((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function removeProduct(item: ProductAsset) {
    if (!deviceId || !window.confirm(`Remove ${item.filename || "this image"}?`)) return;
    setAssetBusy(true);
    try {
      await localDataPlane.deleteAsset(item.resource_id, { deviceId });
      setAssets((prev) => prev.filter((asset) => asset.resource_id !== item.resource_id));
      setPickedProducts((prev) => {
        const next = new Set(prev);
        next.delete(item.resource_id);
        return next;
      });
      setStatus(`Removed ${item.filename || "image"}.`);
    } catch (err) {
      setStatus(String(err));
    } finally {
      setAssetBusy(false);
    }
  }

  function togglePersonaFormat(n: number, fmt: string) {
    setPersonaFormats((current) => {
      const existing = new Set(current[n] ?? formatList);
      if (existing.has(fmt)) existing.delete(fmt);
      else if (existing.size < 8) existing.add(fmt);
      return { ...current, [n]: [...existing] };
    });
  }

  async function startStructured() {
    if (!user.authenticated) {
      setStatus("Sign in before sending a plate.");
      return;
    }
    const formatsByPersona: Record<string, string[]> = {};
    const usedFormats = new Set<string>();
    for (const n of selected) {
      const list = formatsForPersona(n);
      if (!list.length) continue;
      formatsByPersona[String(n)] = list;
      for (const fmt of list) usedFormats.add(fmt);
    }
    const globalForRequest = formatList.length ? formatList : [...usedFormats];
    if (!selectedCount || !globalForRequest.length) {
      setStatus("Pick at least one persona and one format.");
      return;
    }
    setBusy(true);
    setStatus("Allocating copy plate…");
    try {
      const isBrowser = provider === "browser";
      if (isBrowser) {
        const live = localDataPlane.session(deviceId)
          ? { ok: true, deviceId, agentId }
          : await pairLocalAgent();
        if (!live.ok) return;
      } else {
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
      }
      const providerName = isBrowser ? "browser" : provider === "google" ? "google_gemini" : "opencode";
      const modelName = isBrowser ? copyBrowserEngine : model;
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
            global_formats: globalForRequest,
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
            model: modelName,
            org_id: orgId !== "personal" ? orgId : "",
          },
        }),
      });
      localStorage.setItem("adFactoryCopyPipeline", envelope.run_id);
      setActiveRunId(envelope.run_id);
      setFlowOpenRun("structured", envelope.run_id);
      setRunPage(0);
      setCopyJobId(queued.copy_job_id || "");
      setStructuredRuns((prev) => {
        if (prev.some((run) => run.run_id === envelope.run_id)) return prev;
        return [
          {
            run_id: envelope.run_id,
            display_batch: envelope.display_batch || envelope.run_id,
            created_at: Date.now() / 1000,
            flow_type: "structured",
            prompt_count: 0,
            image_count: 0,
            status: "running",
            copy_generation: { status: "queued" },
          },
          ...prev,
        ];
      });
      invalidateRuns();
      appendLog(`Plate ${envelope.display_batch || envelope.run_id} is on press.`);
      await loadFlowRuns("structured", true);
      setFlowOpenRun("structured", envelope.run_id);
      setRunPage(0);
      requestAnimationFrame(() => {
        runsPanelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    } catch (err) {
      appendLog(String(err), "error");
    } finally {
      setBusy(false);
    }
  }

  const orgSources = sources.filter((item) => item.type === "org");
  const canEditFiles = Boolean(configMeta?.can_edit) && user.authenticated;
  const configOrgId = configMeta?.owner_type === "org"
    ? (configMeta.org?.org_id || (orgId !== "personal" ? orgId : ""))
    : "";

  function applySavedFile(key: string, text: string, result?: { notice?: string; config?: Record<string, unknown> }) {
    setStudio((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        config: {
          ...prev.config,
          [key]: text,
          ...(result?.notice && typeof result?.config?.copy_prompt_templates === "string"
            ? { copy_prompt_templates: result.config.copy_prompt_templates }
            : {}),
        },
      };
    });
    setConfigMeta((prev) => (prev ? { ...prev, version: (prev.version ?? 0) + 1 } : prev));
    setStatus(result?.notice || `${KEY_LABELS[key] || key} saved.`);
    if (key === "ad_formats" || key === "ad_languages" || key === "copy_prompt_templates") {
      setPlateTick((value) => value + 1);
    }
  }

  function openRunRow(run: Run) {
    const id = run.run_id || "";
    setFlowOpenRun(isReferenceRun(run) ? "reference" : "structured", id);
    if (!isReferenceRun(run)) setActiveRunId(id);
  }

  async function pairLocalAgent(opts?: { silent?: boolean }) {
    const silent = Boolean(opts?.silent);
    if (!user.authenticated || !user.user_id) {
      if (!silent) setStatus("Sign in before pairing the local agent.");
      return { ok: false, deviceId: "", agentId: "" };
    }
    const owners = [
      ...(orgId && orgId !== "personal" ? [{ ownerType: "org" as const, ownerId: orgId }] : []),
      { ownerType: "user" as const, ownerId: user.user_id },
    ];
    const restored = localDataPlane.restoreStoredSession(owners);
    if (restored?.deviceId && localDataPlane.session(restored.deviceId)) {
      setDeviceId(restored.deviceId);
      setAgentId(restored.agentId);
      setPaired(true);
      return { ok: true, deviceId: restored.deviceId, agentId: restored.agentId };
    }
    if (!silent) setStatus("Pairing this tab with the local agent…");
    try {
      const info = await localDataPlane.discover();
      const liveId = info.device_id || restored?.deviceId || "";
      if (liveId) setDeviceId(liveId);
      let lastError: unknown;
      for (const owner of owners) {
        try {
          const next = await localDataPlane.ensurePaired({ ...owner, deviceId: liveId });
          const id = next.info.device_id || liveId;
          const nextAgent = next.agent?.agent_id || "";
          setDeviceId(id);
          setAgentId(nextAgent);
          setPaired(true);
          appendLog(`Paired ${id.slice(0, 8)}. Local files are available in this tab.`);
          return { ok: true, deviceId: id, agentId: nextAgent };
        } catch (err) {
          lastError = err;
        }
      }
      setPaired(false);
      if (!silent) setStatus(pairingStatus(lastError));
      return { ok: false, deviceId: "", agentId: "" };
    } catch (err) {
      setPaired(false);
      if (!silent) setStatus(pairingStatus(err));
      return { ok: false, deviceId: "", agentId: "" };
    }
  }
  pairRef.current = pairLocalAgent;

  async function generateSelected(mode: "45" | "both") {
    const ids = [...pickedRuns];
    if (!ids.length) {
      setStatus("Select batches first.");
      return;
    }
    if (!pickedProducts.size) {
      setStatus("Select at least one input image to send to the image model.");
      return;
    }
    const live = localDataPlane.session(deviceId)
      ? { ok: true, deviceId, agentId }
      : await pairLocalAgent();
    if (!live.ok) return;
    setBatchBusy(mode);
    try {
      for (const id of ids) {
        await queueRunImages(id, mode, imageEngine, live.agentId, live.deviceId, [...pickedProducts]);
      }
      invalidateRuns();
      const label = mode === "both" ? "4:5 + 9:16" : "4:5";
      appendLog(`Queued ${label} for ${ids.length} batch${ids.length === 1 ? "" : "es"}.`);
      await loadFlowRuns(flow, true);
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
    const live = localDataPlane.session(deviceId)
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
    if (!ids.length && openRunId) ids.push(openRunId);
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
      appendLog(`Cancel requested for ${canceled} run${canceled === 1 ? "" : "s"}.`, "warning");
      await loadFlowRuns(flow, true);
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
              {languageModes.map((mode) => (
                <button key={mode.id} type="button" className={`chip${language === mode.id ? " active" : ""}`} onClick={() => setLanguage(mode.id)}>
                  {mode.label || mode.id}
                </button>
              ))}
            </div>
            <p className="tile-kicker">Global formats</p>
            <p className="hint" style={{ marginBottom: 8 }}>Applies to selected personas only. Click a format on a card to change only that persona.</p>
            <div className="chips" style={{ marginBottom: 18 }}>
              {formatOptions.map((fmt) => (
                <button key={fmt.id} type="button" className={`chip${formats.has(fmt.id) ? " active" : ""}`} onClick={() => toggleFormat(fmt.id)}>
                  {fmt.label || fmt.id}
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
                      <option value="llm_decide">Leave it to the image model</option>
                      {(studio?.format_patterns?.[fmt] || []).map((item) => (
                        <option key={item.id} value={item.id}>{item.label || item.id}</option>
                      ))}
                    </select>
                  </label>
                ))}
              </div>
            ) : null}
            {loading ? <SkeletonGridLite /> : (
              <div className="persona-board">
                <div className="persona-grid">
                  {personas.map((persona) => {
                    const personaFormatSet = new Set(formatsForPersona(persona.number));
                    return (
                      <div
                        key={persona.number}
                        role="button"
                        tabIndex={0}
                        className={`persona-card${selected.has(persona.number) ? " active" : ""}`}
                        onClick={() => togglePersona(persona.number)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            togglePersona(persona.number);
                          }
                        }}
                      >
                        <div className="persona-card-head">
                          <span className="persona-num">P{String(persona.number).padStart(2, "0")}</span>
                          <span>{persona.name}</span>
                        </div>
                        <div className="persona-formats" onClick={(event) => event.stopPropagation()}>
                          {formatOptions.map((fmt) => (
                            <button
                              key={fmt.id}
                              type="button"
                              className={`chip chip-mini${personaFormatSet.has(fmt.id) ? " active" : ""}`}
                              onClick={() => togglePersonaFormat(persona.number, fmt.id)}
                            >
                              {fmt.label || fmt.id}
                            </button>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                  {!personas.length ? <p className="hint">No personas on this plate yet.</p> : null}
                </div>
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
                {selectedCount} persona{selectedCount === 1 ? "" : "s"} · {[...new Set([...selected].flatMap((n) => formatsForPersona(n)))].join(" / ") || "no formats"} · {language}
              </p>
              <p className="hint" style={{ margin: "14px 0 18px" }}>
                {paired ? `Paired ${deviceId.slice(0, 8)} · ` : deviceId ? `Agent ${deviceId.slice(0, 8)} reachable · ` : ""}{status}
              </p>
              <RunTerminal lines={logLines} />
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
                  <option value="browser">Browser automation</option>
                </select>
              </label>
              {provider === "browser" ? (
                <>
                  <label className="hint">
                    Browser engine
                    <select
                      className="field"
                      value={copyBrowserEngine}
                      onChange={(e) => setCopyBrowserEngine(e.target.value === "gemini" ? "gemini" : "chatgpt")}
                    >
                      <option value="chatgpt">ChatGPT</option>
                      <option value="gemini">Gemini</option>
                    </select>
                  </label>
                  <p className="hint">
                    Uses the model already selected in your Chrome CDP tab. Pair the local agent first. Product context is sent once, then ads are requested in that chat by Ads per LLM call.
                  </p>
                </>
              ) : (
              <form
                className="provider-keys"
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
                <div className="provider-key-row">
                  <label className="provider-key-model">
                    <span>Model</span>
                    <select className="field" value={models.includes(model) ? model : DEFAULT_OPENCODE_MODEL} onChange={(e) => setModel(e.target.value)}>
                      {models.map((item) => (
                        <option key={item} value={item}>{item}</option>
                      ))}
                    </select>
                  </label>
                  <Button type="submit" className="provider-key-save" disabled={savingKey || !user.authenticated}>
                    {savingKey ? "Saving…" : "Save API key"}
                  </Button>
                </div>
                <p className="hint">
                  {keySaved || googleSaved ? "Saved key stays on this account. Type a new one only to replace it." : "Save before running a plate."}
                </p>
              </form>
              )}
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
                  {sortedRuns.map((run) => <option key={run.run_id} value={run.run_id}>{run.display_batch || run.run_id}</option>)}
                </select>
              </label>
              <label className="hint">
                Reuse visual patterns from run
                <select className="field" value={reusePattern} onChange={(e) => setReusePattern(e.target.value)}>
                  <option value="">None</option>
                  {sortedRuns.map((run) => <option key={`p-${run.run_id}`} value={run.run_id}>{run.display_batch || run.run_id}</option>)}
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
                    const known = new Set(assets.map((item) => item.resource_id));
                    await localDataPlane.uploadAssets(files, { kind: "product_image", deviceId });
                    const items = (await localDataPlane.listAssets({ kind: "product_image", deviceId }))
                      .slice(0, PRODUCT_ASSET_LIMIT);
                    const uploaded = items
                      .map((item) => item.resource_id)
                      .filter((id) => !known.has(id));
                    setAssets(items);
                    setPickedProducts((prev) => {
                      const available = new Set(items.map((item) => item.resource_id));
                      const next = new Set([...prev].filter((id) => available.has(id)));
                      for (const id of uploaded) next.add(id);
                      return next;
                    });
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
                <>
                  <p className="hint" style={{ margin: "8px 0 6px" }}>
                    Check the images to send to the image model. Unchecked images stay stored.
                  </p>
                  <div className="action-row" style={{ marginBottom: 8 }}>
                    <span className="hint">{assets.length} stored · {pickedProducts.size} selected</span>
                    <Button
                      variant="ghost"
                      disabled={!assets.length}
                      onClick={() => setPickedProducts(new Set(assets.map((item) => item.resource_id)))}
                    >
                      Select all
                    </Button>
                    <Button
                      variant="ghost"
                      disabled={!pickedProducts.size}
                      onClick={() => setPickedProducts(new Set())}
                    >
                      Select none
                    </Button>
                  </div>
                  <div className="asset-strip" style={{ marginBottom: 16 }}>
                    {assets.map((item) => {
                      const selectedImage = pickedProducts.has(item.resource_id);
                      return (
                        <article
                          key={item.resource_id}
                          className={`asset-card${selectedImage ? " selected" : ""}`}
                        >
                          <label className="asset-card-check">
                            <input
                              type="checkbox"
                              checked={selectedImage}
                              onChange={() => toggleProduct(item.resource_id)}
                              aria-label={`Send ${item.filename || "image"} to the image model`}
                            />
                          </label>
                          <button
                            type="button"
                            className="asset-thumb"
                            title={item.filename || item.resource_id}
                            onClick={() => toggleProduct(item.resource_id)}
                          >
                            <LazyAsset
                              resourceId={item.resource_id}
                              deviceId={deviceId}
                              version={item.version}
                              alt={item.filename || "Input image"}
                            />
                          </button>
                          <button
                            type="button"
                            className="asset-card-remove"
                            aria-label={`Remove ${item.filename || "image"}`}
                            disabled={assetBusy}
                            onClick={() => void removeProduct(item)}
                          >
                            ×
                          </button>
                        </article>
                      );
                    })}
                  </div>
                </>
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
          <p className="hint" style={{ margin: "14px 0 0" }}>{status}</p>
          <RunTerminal lines={logLines} />
        </Tile>
      )}

      <Tile span="wide" kicker="03 · Copy desk" title="Plate files">
        {orgSources.length ? (
          <OrgConfigChips orgId={orgId} sources={orgSources} onSelect={selectOrg} />
        ) : null}
        <p className="hint" style={{ marginBottom: 12 }}>
          {canEditFiles
            ? "Open a file to edit it. Save writes to the selected Mongo config, same as the Config desk. "
            : "Open a file to read it. Sign in to edit your own plate. "}
          <Link to="/guide">Operator guide</Link>
        </p>
        <div className="file-card-grid">
          {CONFIG_SECTIONS[0].keys.map((key) => (
            <button key={key} type="button" className="file-card" onClick={() => setViewer(key)}>
              <strong>{KEY_LABELS[key]}</strong>
              <span>{KEY_HINTS[key]}</span>
              <em>{summarizeConfigValue(studio?.config?.[key])}</em>
            </button>
          ))}
        </div>
      </Tile>

      <Tile span="wide" kicker="03 · Hypothesis" title="Hypothesis styles">
        <p className="hint" style={{ marginBottom: 12 }}>
          These files feed the Hypothesis and Style menus. They are not mixed with plate files.
        </p>
        <div className="file-card-grid">
          {CONFIG_SECTIONS[1].keys.map((key) => (
            <button key={key} type="button" className="file-card" onClick={() => setViewer(key)}>
              <strong>{KEY_LABELS[key]}</strong>
              <span>{KEY_HINTS[key]}</span>
              <em>{summarizeConfigValue(studio?.config?.[key])}</em>
            </button>
          ))}
        </div>
      </Tile>

      <Tile span="wide" className="tile-business" kicker="03 · Business" title="Business rules">
        <p className="hint" style={{ marginBottom: 12 }}>
          This brand&apos;s lock. A new business edits these first. Image proof bar and headline bans
          live in Prompt Assembler Templates (`proof_bar_text`, `headline_bans`). Persona seed field
          names are mapped in Ad Languages (`_persona_source_map`).
        </p>
        <div className="file-card-grid">
          {CONFIG_SECTIONS[2].keys.map((key) => (
            <button key={key} type="button" className="file-card file-card-business" onClick={() => setViewer(key)}>
              <strong>{KEY_LABELS[key]}</strong>
              <span>{KEY_HINTS[key]}</span>
              <em>{summarizeConfigValue(studio?.config?.[key])}</em>
            </button>
          ))}
        </div>
      </Tile>

      <div ref={runsPanelRef} className="bento-wide">
      <Tile span="wide" kicker="04 · Dry proofs" title="Recent runs">
        <div className="action-row" style={{ marginBottom: 12 }}>
          <BatchSelect runs={sortedRuns} picked={pickedRuns} onChange={setPickedRuns} />
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
            disabled={Boolean(batchBusy) || (!pickedRuns.size && !openRunId)}
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
            writeRunList(flow, data.runs || [], true);
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
                const setter = flow === "reference" ? setReferenceRuns : setStructuredRuns;
                setter((prev) => prev.filter((run) => !pickedRuns.has(run.run_id || "")));
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
                setStructuredRuns([]);
                setReferenceRuns([]);
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
        ) : (sortedRuns.length || openRun) ? (
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
                {imageFailureDetail(run) ? <span>Image failed: {imageFailureDetail(run)}</span> : null}
                {copyFailureDetail(run) ? <span>Copy failed: {copyFailureDetail(run)}</span> : null}
              </article>
            ))}
          </div>
          <ListPager
            page={safeRunPage}
            pageCount={runPages}
            onPage={setRunPage}
            summary={`${sortedRuns.length} runs`}
          />
          {openRun ? (
            <RunWorkspace
              run={openRun}
              deviceId={deviceId}
              agentId={agentId}
              paired={paired}
              productAssetIds={[...pickedProducts]}
              refreshToken={deskTick}
              onPair={pairLocalAgent}
              onClose={() => setFlowOpenRun(flow, "")}
              onStatus={appendLog}
              onRefresh={async () => {
                const data = await fetchJSON<{ runs?: Run[] }>(`/api/runs?flow=${flow}`, { noCache: true });
                writeRunList(flow, data.runs || [], true);
              }}
            />
          ) : null}
          </>
        ) : (
          <p className="hint">No runs in this flow yet. The stage stays empty until a plate lands.</p>
        )}
      </Tile>
      </div>
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

  return (
    <ReferenceFlow
      authenticated={user.authenticated}
      userId={user.user_id}
      orgId={orgId}
      paired={paired}
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
      setStatus={appendLog}
      onRuns={(rows) => writeRunList("reference", rows, true)}
      onOpenRun={(id) => setFlowOpenRun("reference", id)}
      onStubRun={(run) => setReferenceRuns((prev) => mergeRunLists([run], prev))}
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
