import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode, type WheelEvent } from "react";
import { fetchJSON, invalidateRuns } from "@/lib/api";
import { asConfigText, catalogConcepts, catalogLanguageModes } from "@/lib/config-keys";
import { localDataPlane } from "@/lib/local-data-plane.js";
import type { Persona, Run, StudioPayload } from "@/lib/types";
import { Button } from "@/components/Button";
import { FileField } from "@/components/FileField";
import { LazyAsset } from "@/pages/studio/LazyAsset";
import { imageFailureDetail } from "@/lib/run-status";

type Asset = { resource_id: string; url?: string; filename?: string; version?: number };

type ReferenceProps = {
  authenticated: boolean;
  userId: string;
  deviceId: string;
  personas: Persona[];
  selected: Set<number>;
  togglePersona: (n: number) => void;
  language: string;
  setLanguage: (value: string) => void;
  selectedConcept: string;
  setSelectedConcept: (value: string) => void;
  studio: StudioPayload | null;
  status: string;
  setStatus: (value: string) => void;
  onRuns: (runs: Run[]) => void;
  onOpenRun?: (runId: string) => void;
  onStubRun?: (run: Run) => void;
  canEditFiles?: boolean;
  configVersion?: number;
  configOwnerType?: string;
  configOrgId?: string;
  onConfigSaved?: (key: string, text: string) => void;
};

type ReferenceApi = ReferenceProps & {
  refs: Asset[];
  products: Asset[];
  pickedRefs: Set<string>;
  pickedProducts: Set<string>;
  comments: Record<string, string>;
  setComments: (value: Record<string, string> | ((prev: Record<string, string>) => Record<string, string>)) => void;
  engine: string;
  setEngine: (value: string) => void;
  make916: boolean;
  setMake916: (value: boolean) => void;
  busy: boolean;
  jobId: string;
  runId: string;
  jobCount: number;
  toggleRef: (id: string) => void;
  toggleProduct: (id: string) => void;
  uploadKind: (kind: "reference_image" | "product_image", files: FileList | null) => Promise<void>;
  startReference: () => Promise<void>;
  cancelReference: () => Promise<void>;
};

const ReferenceCtx = createContext<ReferenceApi | null>(null);

function useReference() {
  const ctx = useContext(ReferenceCtx);
  if (!ctx) throw new Error("Reference pane must sit inside ReferenceFlow");
  return ctx;
}

function SwipeLibrary({
  label,
  className = "",
  children,
}: {
  label: string;
  className?: string;
  children: ReactNode;
}) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const [canPrev, setCanPrev] = useState(false);
  const [canNext, setCanNext] = useState(false);
  const itemCount = Array.isArray(children) ? children.length : 0;

  function updateNav() {
    const el = scrollerRef.current;
    if (!el) return;
    setCanPrev(el.scrollLeft > 2);
    setCanNext(el.scrollLeft + el.clientWidth < el.scrollWidth - 2);
  }

  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    updateNav();
    el.addEventListener("scroll", updateNav, { passive: true });
    const observer = new ResizeObserver(updateNav);
    observer.observe(el);
    return () => {
      el.removeEventListener("scroll", updateNav);
      observer.disconnect();
    };
  }, [itemCount]);

  function scrollByDir(dir: -1 | 1) {
    const el = scrollerRef.current;
    if (!el) return;
    const card = el.querySelector<HTMLElement>(".reference-slide");
    const delta = (card?.getBoundingClientRect().width || 240) + 12;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollBy({ left: dir * delta, behavior: reduced ? "auto" : "smooth" });
  }

  function onWheel(event: WheelEvent<HTMLDivElement>) {
    const el = event.currentTarget;
    if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
    const max = el.scrollWidth - el.clientWidth;
    if (max <= 0) return;
    const next = el.scrollLeft + event.deltaY;
    if ((next <= 0 && el.scrollLeft <= 0) || (next >= max && el.scrollLeft >= max)) return;
    el.scrollLeft += event.deltaY;
    event.preventDefault();
  }

  return (
    <div className={`reference-rail${className ? ` ${className}` : ""}`}>
      <div
        ref={scrollerRef}
        className="reference-library"
        tabIndex={0}
        role="region"
        aria-label={label}
        onWheel={onWheel}
      >
        {children}
      </div>
      {canPrev || canNext ? (
        <div className="reference-rail-nav">
          <button
            type="button"
            className="reference-rail-btn"
            aria-label={`Previous ${label}`}
            disabled={!canPrev}
            onClick={() => scrollByDir(-1)}
          >
            ‹
          </button>
          <button
            type="button"
            className="reference-rail-btn"
            aria-label={`Next ${label}`}
            disabled={!canNext}
            onClick={() => scrollByDir(1)}
          >
            ›
          </button>
        </div>
      ) : null}
    </div>
  );
}

export function ReferenceFlow({ children, ...props }: ReferenceProps & { children: ReactNode }) {
  const [refs, setRefs] = useState<Asset[]>([]);
  const [products, setProducts] = useState<Asset[]>([]);
  const [pickedRefs, setPickedRefs] = useState<Set<string>>(new Set());
  const [pickedProducts, setPickedProducts] = useState<Set<string>>(new Set());
  const [comments, setComments] = useState<Record<string, string>>({});
  const [engine, setEngine] = useState("chatgpt");
  const [make916, setMake916] = useState(true);
  const [busy, setBusy] = useState(false);
  const [jobId, setJobId] = useState("");
  const [runId, setRunId] = useState(() => localStorage.getItem("adFactoryReferencePipeline") || "");

  const languageCount = catalogLanguageModes(props.studio).find((item) => item.id === props.language)?.languages?.length || 1;
  const jobCount = props.selected.size * Math.max(pickedRefs.size, 0) * languageCount;

  useEffect(() => {
    if (!props.deviceId) return;
    let cancelled = false;
    Promise.all([
      localDataPlane.listAssets({ kind: "reference_image", deviceId: props.deviceId }),
      localDataPlane.listAssets({ kind: "product_image", deviceId: props.deviceId }),
    ]).then(([refItems, productItems]) => {
      if (cancelled) return;
      setRefs(refItems.slice(0, 48));
      setProducts(productItems.slice(0, 48));
    }).catch((err) => props.setStatus(String(err)));
    return () => {
      cancelled = true;
    };
  }, [props.deviceId, props.setStatus]);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    let timer = 0;
    async function poll() {
      try {
        const live = await fetchJSON<Run>(`/api/runs/${encodeURIComponent(runId)}`, { noCache: true });
        if (cancelled) return;
        const status = String(live.status || live.image_generation?.status || "");
        const err = imageFailureDetail(live);
        if (err) props.setStatus(`${live.display_batch || runId} failed: ${err}`);
        if (["completed", "failed", "canceled"].includes(status)) {
          const data = await fetchJSON<{ runs?: Run[] }>("/api/runs?flow=reference", { noCache: true });
          if (!cancelled) props.onRuns(data.runs || []);
          return;
        }
      } catch {
        /* keep polling */
      }
      timer = window.setTimeout(() => {
        void poll();
      }, 4000);
    }
    void poll();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [runId]);

  async function refreshKind(kind: "reference_image" | "product_image") {
    const items = await localDataPlane.listAssets({ kind, deviceId: props.deviceId });
    if (kind === "reference_image") setRefs(items.slice(0, 48));
    else setProducts(items.slice(0, 48));
  }

  const value = useMemo<ReferenceApi>(() => ({
    ...props,
    refs,
    products,
    pickedRefs,
    pickedProducts,
    comments,
    setComments,
    engine,
    setEngine,
    make916,
    setMake916,
    busy,
    jobId,
    runId,
    jobCount,
    toggleRef(id) {
      setPickedRefs((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
      });
    },
    toggleProduct(id) {
      setPickedProducts((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
      });
    },
    async uploadKind(kind, files) {
      if (!props.deviceId) return;
      if (files?.length) {
        await localDataPlane.uploadAssets(files, { kind, deviceId: props.deviceId });
      }
      await refreshKind(kind);
    },
    async startReference() {
      if (!props.authenticated) {
        props.setStatus("Sign in before sending a reference plate.");
        return;
      }
      if (!props.selected.size) {
        props.setStatus("Select at least one persona.");
        return;
      }
      if (!pickedRefs.size) {
        props.setStatus("Select at least one reference image.");
        return;
      }
      if (!pickedProducts.size) {
        props.setStatus("Select at least one product image.");
        return;
      }
      const starting = asConfigText(props.studio?.config?.reference_starting_prompt);
      const productDoc = asConfigText(props.studio?.config?.reference_product_master_doc);
      const personasText = asConfigText(props.studio?.config?.persona_seeds);
      const conversion = asConfigText(props.studio?.config?.conversion_916_prompt);
      if (!starting.trim()) {
        props.setStatus("Reference starting prompt is empty. Open Config and fill it.");
        return;
      }
      if (!productDoc.trim()) {
        props.setStatus("Reference product document is empty. Open Config and fill it.");
        return;
      }
      if (!personasText.trim()) {
        props.setStatus("Persona seeds are empty. Open Config and fill them.");
        return;
      }
      setBusy(true);
      props.setStatus("Preparing reference run…");
      try {
        const envelope = await localDataPlane.allocateLocalRun({
          ownerType: "user",
          ownerId: props.userId,
          flowType: "reference",
          settings: { engine, generate_916: make916 },
        });
        const productDocument = await localDataPlane.putText(
          "documents",
          `${envelope.run_id}-reference-product-document`,
          productDoc,
          { deviceId: envelope.device_id, operationId: `${envelope.run_id}-product-document`, runId: envelope.run_id, role: "reference_product_document" },
        );
        const startingPrompt = await localDataPlane.putText(
          "configs",
          `${envelope.run_id}-reference-starting-prompt`,
          starting,
          { deviceId: envelope.device_id, operationId: `${envelope.run_id}-starting-prompt`, runId: envelope.run_id, role: "reference_starting_prompt" },
        );
        const personaConfig = await localDataPlane.putText(
          "configs",
          `${envelope.run_id}-reference-personas`,
          personasText,
          { deviceId: envelope.device_id, operationId: `${envelope.run_id}-persona-config`, runId: envelope.run_id, role: "reference_persona_config" },
        );
        let conversionPrompt = null;
        if (make916 && conversion.trim()) {
          conversionPrompt = await localDataPlane.putText(
            "configs",
            `${envelope.run_id}-reference-conversion-916`,
            conversion,
            { deviceId: envelope.device_id, operationId: `${envelope.run_id}-conversion-916`, runId: envelope.run_id, role: "conversion_prompt" },
          );
        }
        const referenceDeclarations = [];
        for (const item of refs.filter((ref) => pickedRefs.has(ref.resource_id))) {
          const declaration: Record<string, unknown> = { resource_id: item.resource_id, version: item.version };
          const comment = comments[item.resource_id]?.trim();
          if (comment) {
            const saved = await localDataPlane.putText(
              "configs",
              `${envelope.run_id}-reference-comment-${item.resource_id}`,
              comment,
              { deviceId: envelope.device_id, operationId: `${envelope.run_id}-comment-${item.resource_id}` },
            );
            declaration.comment_resource_id = saved.resource_id;
            declaration.comment_version = saved.version;
          }
          referenceDeclarations.push(declaration);
        }
        await localDataPlane.putText(
          "configs",
          `${envelope.run_id}-reference-settings`,
          JSON.stringify({
            references: referenceDeclarations,
            products: products.filter((item) => pickedProducts.has(item.resource_id)).map((item) => ({
              resource_id: item.resource_id,
              version: item.version,
            })),
            persona_ids: [...props.selected].map(String),
            language_mode: props.language,
            selected_concept: props.selectedConcept,
            ...(props.selectedConcept
              ? {
                  creative_concept: catalogConcepts(props.studio).find((item) => item.id === props.selectedConcept) || null,
                }
              : {}),
            product_document: { resource_id: productDocument.resource_id, version: productDocument.version },
            starting_prompt: { resource_id: startingPrompt.resource_id, version: startingPrompt.version },
            persona_config: { resource_id: personaConfig.resource_id, version: personaConfig.version },
            ...(conversionPrompt ? { conversion_prompt: { resource_id: conversionPrompt.resource_id, version: conversionPrompt.version } } : {}),
          }),
          { deviceId: envelope.device_id, operationId: `${envelope.run_id}-settings`, runId: envelope.run_id, role: "reference_settings" },
        );
        const queued = await fetchJSON<{ job_id?: string }>(`/api/runs/${encodeURIComponent(envelope.run_id)}/reference-generation`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            operation_id: `${envelope.run_id}-reference-generation`,
            engine,
            mode: make916 ? "both" : "45",
          }),
        });
        setRunId(envelope.run_id);
        setJobId(queued.job_id || "");
        localStorage.setItem("adFactoryReferencePipeline", envelope.run_id);
        props.onOpenRun?.(envelope.run_id);
        props.onStubRun?.({
          run_id: envelope.run_id,
          display_batch: envelope.display_batch || envelope.run_id,
          flow_type: "reference",
          created_at: Date.now() / 1000,
          prompt_count: 0,
          image_count: 0,
          status: "queued",
          image_generation: { status: "queued", job_id: queued.job_id || "" },
        });
        invalidateRuns();
        const data = await fetchJSON<{ runs?: Run[] }>("/api/runs?flow=reference", { noCache: true });
        props.onRuns(data.runs || []);
        props.setStatus(`Reference plate ${envelope.display_batch || envelope.run_id} queued.`);
      } catch (err) {
        props.setStatus(String(err));
      } finally {
        setBusy(false);
      }
    },
    async cancelReference() {
      try {
        if (runId) {
          await fetchJSON(`/api/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" });
        } else if (jobId) {
          await fetchJSON(`/api/agents/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });
        } else {
          props.setStatus("No active reference run to cancel.");
          return;
        }
        invalidateRuns();
        props.setStatus(`Cancel requested for ${runId || jobId}.`);
      } catch (err) {
        props.setStatus(String(err));
      }
    },
  }), [
    props,
    refs,
    products,
    pickedRefs,
    pickedProducts,
    comments,
    engine,
    make916,
    busy,
    jobId,
    runId,
    jobCount,
  ]);

  return <ReferenceCtx.Provider value={value}>{children}</ReferenceCtx.Provider>;
}

function ConceptSelect({
  value,
  onChange,
  studio,
}: {
  value: string;
  onChange: (value: string) => void;
  studio: StudioPayload | null;
}) {
  return (
    <label className="hint" style={{ display: "block", marginBottom: 12 }}>
      Concept
      <select className="field" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">None</option>
        {catalogConcepts(studio).map((item) => (
          <option key={item.id} value={item.id}>{item.label}</option>
        ))}
      </select>
    </label>
  );
}

export function ReferenceCompose() {
  const {
    language,
    setLanguage,
    selectedConcept,
    setSelectedConcept,
    studio,
    personas,
    selected,
    togglePersona,
    jobCount,
  } = useReference();
  return (
    <>
      <div className="chips" style={{ marginBottom: 12 }}>
        {catalogLanguageModes(studio).map((mode) => (
          <button key={mode.id} type="button" className={`chip${language === mode.id ? " active" : ""}`} onClick={() => setLanguage(mode.id)}>
            {mode.label || mode.id}
          </button>
        ))}
      </div>
      <ConceptSelect value={selectedConcept} onChange={setSelectedConcept} studio={studio} />
      <p className="hint" style={{ marginBottom: 12 }}>{jobCount} jobs · personas × selected references × language</p>
      <div className="persona-board">
        <div className="persona-grid">
          {personas.map((persona) => (
            <button
              key={persona.number}
              type="button"
              className={`persona-card${selected.has(persona.number) ? " active" : ""}`}
              onClick={() => togglePersona(persona.number)}
            >
              <span className="persona-card-head">
                <span className="persona-num">P{String(persona.number).padStart(2, "0")}</span>
                <span>{persona.name}</span>
              </span>
            </button>
          ))}
          {!personas.length ? <p className="hint">No personas on this plate yet.</p> : null}
        </div>
      </div>
    </>
  );
}

export function ReferenceDesk() {
  const ctx = useReference();
  return (
    <>
      <p className="hint" style={{ marginBottom: 14 }}>{ctx.status}</p>
      <label className="hint">Reference library</label>
      <FileField
        id="referenceLibraryFiles"
        label="Choose references"
        multiple
        accept="image/png,image/jpeg,image/webp"
        disabled={!ctx.deviceId}
        emptyHint={ctx.deviceId ? "No file chosen" : "Pair the local agent first"}
        onFiles={(files, input) => {
          void ctx.uploadKind("reference_image", files);
          input.value = "";
        }}
      />
      <p className="hint">{ctx.refs.length} stored · {ctx.pickedRefs.size} selected</p>
      <SwipeLibrary label="Reference images">
        {ctx.refs.map((item, index) => (
          <article
            key={item.resource_id}
            className={`reference-slide${ctx.pickedRefs.has(item.resource_id) ? " selected" : ""}`}
          >
            <label className="reference-select">
              <input
                type="checkbox"
                checked={ctx.pickedRefs.has(item.resource_id)}
                onChange={() => ctx.toggleRef(item.resource_id)}
              />
              Use
            </label>
            <button
              type="button"
              className="reference-thumb"
              onClick={() => ctx.toggleRef(item.resource_id)}
            >
              <LazyAsset
                resourceId={item.resource_id}
                deviceId={ctx.deviceId}
                version={item.version}
                alt={item.filename || "Reference"}
              />
            </button>
            <div className="reference-slide-body">
              <strong>{index + 1}. {item.filename || item.resource_id}</strong>
              <textarea
                rows={3}
                placeholder="Optional instruction for only this reference image…"
                value={ctx.comments[item.resource_id] || ""}
                onChange={(e) => ctx.setComments((prev) => ({ ...prev, [item.resource_id]: e.target.value }))}
              />
              <div className="reference-slide-actions">
                <Button
                  variant="ghost"
                  onClick={async () => {
                    const url = await localDataPlane.assetObjectUrl(item.resource_id, ctx.deviceId, item.version).catch(() => "");
                    if (url) window.open(url, "_blank", "noopener");
                  }}
                >
                  Open
                </Button>
                <Button
                  variant="ghost"
                  onClick={async () => {
                    if (!window.confirm(`Remove ${item.filename || item.resource_id}?`)) return;
                    await localDataPlane.deleteAsset(item.resource_id, { deviceId: ctx.deviceId });
                    ctx.setComments((prev) => {
                      const next = { ...prev };
                      delete next[item.resource_id];
                      return next;
                    });
                    if (ctx.pickedRefs.has(item.resource_id)) ctx.toggleRef(item.resource_id);
                    await ctx.uploadKind("reference_image", null);
                  }}
                >
                  Remove
                </Button>
              </div>
            </div>
          </article>
        ))}
      </SwipeLibrary>
      <label className="hint" style={{ display: "block", marginTop: 12 }}>Product assets</label>
      <FileField
        id="referenceProductFiles"
        label="Choose products"
        multiple
        accept="image/*"
        disabled={!ctx.deviceId}
        emptyHint={ctx.deviceId ? "No file chosen" : "Pair the local agent first"}
        onFiles={(files, input) => {
          void ctx.uploadKind("product_image", files);
          input.value = "";
        }}
      />
      <p className="hint">{ctx.products.length} stored · {ctx.pickedProducts.size} selected</p>
      <SwipeLibrary label="Product assets" className="product-library">
        {ctx.products.map((item, index) => (
          <article
            key={item.resource_id}
            className={`reference-slide${ctx.pickedProducts.has(item.resource_id) ? " selected" : ""}`}
          >
            <label className="reference-select">
              <input
                type="checkbox"
                checked={ctx.pickedProducts.has(item.resource_id)}
                onChange={() => ctx.toggleProduct(item.resource_id)}
              />
              Use
            </label>
            <button type="button" className="reference-thumb" onClick={() => ctx.toggleProduct(item.resource_id)}>
              <LazyAsset
                resourceId={item.resource_id}
                deviceId={ctx.deviceId}
                version={item.version}
                alt={item.filename || "Product"}
              />
            </button>
            <div className="reference-slide-body">
              <strong>{index + 1}. {item.filename || item.resource_id}</strong>
              <div className="reference-slide-actions">
                <Button
                  variant="ghost"
                  onClick={async () => {
                    const url = await localDataPlane.assetObjectUrl(item.resource_id, ctx.deviceId, item.version).catch(() => "");
                    if (url) window.open(url, "_blank", "noopener");
                  }}
                >
                  Open
                </Button>
                <Button
                  variant="ghost"
                  onClick={async () => {
                    if (!window.confirm(`Remove ${item.filename || item.resource_id}?`)) return;
                    await localDataPlane.deleteAsset(item.resource_id, { deviceId: ctx.deviceId });
                    if (ctx.pickedProducts.has(item.resource_id)) ctx.toggleProduct(item.resource_id);
                    await ctx.uploadKind("product_image", null);
                  }}
                >
                  Remove
                </Button>
              </div>
            </div>
          </article>
        ))}
      </SwipeLibrary>
      <ConceptSelect value={ctx.selectedConcept} onChange={ctx.setSelectedConcept} studio={ctx.studio} />
      <div className="action-row">
        <label className="toolbar-field">
          <span>Image engine</span>
          <select className="field" value={ctx.engine} onChange={(e) => ctx.setEngine(e.target.value)}>
            <option value="chatgpt">ChatGPT</option>
            <option value="gemini">Gemini</option>
          </select>
        </label>
        <label className="toggle-row">
          <input type="checkbox" checked={ctx.make916} onChange={(e) => ctx.setMake916(e.target.checked)} />
          Create 9:16 after 4:5
        </label>
        <Button variant="primary" disabled={ctx.busy || !ctx.authenticated} onClick={() => void ctx.startReference()}>
          {ctx.busy ? "Queuing…" : "Run reference flow"}
        </Button>
        <Button variant="ghost" disabled={!ctx.runId && !ctx.jobId} onClick={() => void ctx.cancelReference()}>
          Cancel run
        </Button>
      </div>
    </>
  );
}
