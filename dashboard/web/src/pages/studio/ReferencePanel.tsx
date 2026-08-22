import { useEffect, useState } from "react";
import { fetchJSON, invalidateRuns } from "@/lib/api";
import { asConfigText } from "@/lib/config-keys";
import { localDataPlane } from "@/lib/local-data-plane.js";
import type { Persona, Run, StudioPayload } from "@/lib/types";
import { Button } from "@/components/Button";
import { FileViewer } from "@/components/FileViewer";

type Asset = { resource_id: string; url?: string; filename?: string; version?: number };

type Props = {
  authenticated: boolean;
  userId: string;
  deviceId: string;
  personas: Persona[];
  selected: Set<number>;
  togglePersona: (n: number) => void;
  language: string;
  setLanguage: (value: string) => void;
  studio: StudioPayload | null;
  setStatus: (value: string) => void;
  onRuns: (runs: Run[]) => void;
};

const LANGUAGES = ["ALL", "EN", "HI", "HINGLISH"];

export function ReferencePanel({
  authenticated,
  userId,
  deviceId,
  personas,
  selected,
  togglePersona,
  language,
  setLanguage,
  studio,
  setStatus,
  onRuns,
}: Props) {
  const [refs, setRefs] = useState<Asset[]>([]);
  const [products, setProducts] = useState<Asset[]>([]);
  const [pickedRefs, setPickedRefs] = useState<Set<string>>(new Set());
  const [pickedProducts, setPickedProducts] = useState<Set<string>>(new Set());
  const [comments, setComments] = useState<Record<string, string>>({});
  const [engine, setEngine] = useState("chatgpt");
  const [make916, setMake916] = useState(true);
  const [busy, setBusy] = useState(false);
  const [viewer, setViewer] = useState<string>("");
  const [jobId, setJobId] = useState("");
  const [runId, setRunId] = useState("");

  const jobCount = selected.size * Math.max(pickedRefs.size, 0) * (language === "ALL" ? 3 : 1);

  useEffect(() => {
    if (!deviceId) return;
    let cancelled = false;
    Promise.all([
      localDataPlane.listAssets({ kind: "reference_image", deviceId }),
      localDataPlane.listAssets({ kind: "product_image", deviceId }),
    ]).then(async ([refItems, productItems]) => {
      if (cancelled) return;
      const withUrls = async (items: Asset[]) => Promise.all(
        items.slice(0, 24).map(async (item) => ({
          ...item,
          url: await localDataPlane.assetObjectUrl(item.resource_id, deviceId, item.version).catch(() => ""),
        })),
      );
      setRefs(await withUrls(refItems));
      setProducts(await withUrls(productItems));
    }).catch((err) => setStatus(String(err)));
    return () => {
      cancelled = true;
    };
  }, [deviceId, setStatus]);

  async function refreshKind(kind: "reference_image" | "product_image") {
    const items = await localDataPlane.listAssets({ kind, deviceId });
    const withUrls = await Promise.all(
      items.slice(0, 24).map(async (item) => ({
        ...item,
        url: await localDataPlane.assetObjectUrl(item.resource_id, deviceId, item.version).catch(() => ""),
      })),
    );
    if (kind === "reference_image") setRefs(withUrls);
    else setProducts(withUrls);
  }

  async function startReference() {
    if (!authenticated) {
      setStatus("Sign in before sending a reference plate.");
      return;
    }
    if (!selected.size || !pickedRefs.size || !pickedProducts.size) {
      setStatus("Pick personas, reference images, and product assets.");
      return;
    }
    const starting = asConfigText(studio?.config?.reference_starting_prompt);
    const productDoc = asConfigText(studio?.config?.reference_product_master_doc);
    const personasText = asConfigText(studio?.config?.persona_seeds);
    const conversion = asConfigText(studio?.config?.conversion_916_prompt);
    if (!starting.trim() || !productDoc.trim() || !personasText.trim()) {
      setStatus("Generic/reference files are empty. Open Config and fill the reference docs.");
      return;
    }
    setBusy(true);
    setStatus("Preparing reference run…");
    try {
      const envelope = await localDataPlane.allocateLocalRun({
        ownerType: "user",
        ownerId: userId,
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
          persona_ids: [...selected].map(String),
          language_mode: language,
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
      invalidateRuns();
      const data = await fetchJSON<{ runs?: Run[] }>("/api/runs?flow=reference", { noCache: true });
      onRuns(data.runs || []);
      setStatus(`Reference plate ${envelope.display_batch || envelope.run_id} queued.`);
    } catch (err) {
      setStatus(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="chips" style={{ marginBottom: 12 }}>
        {LANGUAGES.map((mode) => (
          <button key={mode} type="button" className={`chip${language === mode ? " active" : ""}`} onClick={() => setLanguage(mode)}>
            {mode}
          </button>
        ))}
      </div>
      <p className="hint" style={{ marginBottom: 12 }}>{jobCount} jobs · personas × selected references × language</p>
      <div className="persona-grid" style={{ marginBottom: 16 }}>
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
      </div>
      <label className="hint">
        Reference library
        <input
          type="file"
          multiple
          accept="image/png,image/jpeg,image/webp"
          disabled={!deviceId}
          style={{ display: "block", margin: "8px 0 12px" }}
          onChange={async (event) => {
            const files = event.target.files;
            if (!files?.length || !deviceId) return;
            await localDataPlane.uploadAssets(files, { kind: "reference_image", deviceId });
            await refreshKind("reference_image");
            event.target.value = "";
          }}
        />
      </label>
      <div className="asset-strip">
        {refs.map((item) => (
          <button
            key={item.resource_id}
            type="button"
            className={`asset-thumb${pickedRefs.has(item.resource_id) ? " active" : ""}`}
            onClick={() => setPickedRefs((prev) => {
              const next = new Set(prev);
              if (next.has(item.resource_id)) next.delete(item.resource_id);
              else next.add(item.resource_id);
              return next;
            })}
          >
            {item.url ? <img src={item.url} alt={item.filename || "Reference"} /> : <span>ref</span>}
          </button>
        ))}
      </div>
      {[...pickedRefs].slice(0, 1).map((id) => (
        <textarea
          key={id}
          className="cfg-textarea"
          rows={3}
          placeholder="Optional comment for the selected reference"
          value={comments[id] || ""}
          onChange={(e) => setComments((prev) => ({ ...prev, [id]: e.target.value }))}
        />
      ))}
      <label className="hint" style={{ display: "block", marginTop: 12 }}>
        Product assets
        <input
          type="file"
          multiple
          accept="image/*"
          disabled={!deviceId}
          style={{ display: "block", margin: "8px 0 12px" }}
          onChange={async (event) => {
            const files = event.target.files;
            if (!files?.length || !deviceId) return;
            await localDataPlane.uploadAssets(files, { kind: "product_image", deviceId });
            await refreshKind("product_image");
            event.target.value = "";
          }}
        />
      </label>
      <div className="asset-strip">
        {products.map((item) => (
          <button
            key={item.resource_id}
            type="button"
            className={`asset-thumb${pickedProducts.has(item.resource_id) ? " active" : ""}`}
            onClick={() => setPickedProducts((prev) => {
              const next = new Set(prev);
              if (next.has(item.resource_id)) next.delete(item.resource_id);
              else next.add(item.resource_id);
              return next;
            })}
          >
            {item.url ? <img src={item.url} alt={item.filename || "Product"} /> : <span>pack</span>}
          </button>
        ))}
      </div>
      <div className="action-row" style={{ margin: "14px 0" }}>
        <Button variant="ghost" onClick={() => setViewer("persona_seeds")}>View persona seed</Button>
        <Button variant="ghost" onClick={() => setViewer("reference_starting_prompt")}>Reference starting prompt</Button>
        <Button variant="ghost" onClick={() => setViewer("reference_product_master_doc")}>Product document</Button>
        <Button variant="ghost" onClick={() => setViewer("conversion_916_prompt")}>9:16 conversion</Button>
      </div>
      <label className="hint">
        Image engine
        <select className="field" value={engine} onChange={(e) => setEngine(e.target.value)}>
          <option value="chatgpt">ChatGPT</option>
          <option value="gemini">Gemini</option>
        </select>
      </label>
      <label className="toggle-row">
        <input type="checkbox" checked={make916} onChange={(e) => setMake916(e.target.checked)} />
        Create 9:16 after 4:5
      </label>
      <div className="action-row">
        <Button variant="primary" disabled={busy || !authenticated} onClick={() => void startReference()}>
          {busy ? "Queuing…" : "Run reference flow"}
        </Button>
        <Button
          variant="ghost"
          disabled={!jobId}
          onClick={async () => {
            try {
              await fetchJSON(`/api/agents/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });
              setStatus(`Cancel requested for ${runId}.`);
            } catch (err) {
              setStatus(String(err));
            }
          }}
        >
          Cancel
        </Button>
      </div>
      {viewer ? (
        <FileViewer configKey={viewer} value={studio?.config?.[viewer]} onClose={() => setViewer("")} />
      ) : null}
    </>
  );
}
