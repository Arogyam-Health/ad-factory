import { useEffect, useState } from "react";
import { fetchJSON, invalidateRuns } from "@/lib/api";
import { localDataPlane } from "@/lib/local-data-plane.js";
import { exactOnImageCopyLines, replaceExactOnImageCopy } from "@/lib/prompt-copy.js";
import type { Run } from "@/lib/types";
import { Button } from "@/components/Button";

type PromptRow = {
  prompt_id?: string;
  format?: string;
  persona?: string;
  persona_name?: string;
  display_name?: string;
  language?: string;
  status?: string;
  text?: string;
  version?: number;
  resource_version?: number;
};

type OutputRow = {
  output_id?: string;
  resource_id?: string;
  artifact_id?: string;
  aspect_ratio?: string;
  filename?: string;
  display_name?: string;
  url?: string;
  version?: number;
  current_version?: number;
  resource_version?: number;
};

type CopyLine = { label: string; value: string };

function outputKey(item: OutputRow) {
  return item.output_id || item.resource_id || item.artifact_id || "";
}

function mergeOutputs(local: OutputRow[], meta: OutputRow[]) {
  const byKey = new Map<string, OutputRow>();
  for (const item of [...meta, ...local]) {
    const key = outputKey(item);
    if (!key) continue;
    byKey.set(key, { ...byKey.get(key), ...item });
  }
  return [...byKey.values()];
}

export function RunWorkspace({
  run,
  deviceId,
  agentId,
  onClose,
  onStatus,
  onRefresh,
}: {
  run: Run;
  deviceId: string;
  agentId: string;
  onClose: () => void;
  onStatus: (value: string) => void;
  onRefresh: () => Promise<void>;
}) {
  const runId = run.run_id || "";
  const [prompts, setPrompts] = useState<PromptRow[]>([]);
  const [outputs, setOutputs] = useState<OutputRow[]>([]);
  const [engine, setEngine] = useState("chatgpt");
  const [busy, setBusy] = useState("");
  const [comment, setComment] = useState("");
  const [picked, setPicked] = useState("");
  const [editingId, setEditingId] = useState("");
  const [copyLines, setCopyLines] = useState<CopyLine[]>([]);
  const [copySource, setCopySource] = useState("");
  const [copyVersion, setCopyVersion] = useState(0);
  const [copyError, setCopyError] = useState("");

  useEffect(() => {
    let cancelled = false;
    async function load() {
      const [metaPrompts, metaImages] = await Promise.all([
        fetchJSON<{ prompts?: PromptRow[] }>(`/api/runs/${encodeURIComponent(runId)}/prompts`).catch(() => ({ prompts: [] })),
        fetchJSON<{ images?: OutputRow[] }>(`/api/runs/${encodeURIComponent(runId)}/images`).catch(() => ({ images: [] })),
      ]);
      if (cancelled) return;
      let nextPrompts = metaPrompts.prompts || [];
      let nextOutputs = metaImages.images || [];
      const localDevice = deviceId || run.device_id || "";
      if (localDevice) {
        try {
          const [localPrompts, localOutputs] = await Promise.all([
            localDataPlane.listPrompts(runId, localDevice).catch(() => []),
            localDataPlane.listOutputs(runId, localDevice).catch(() => []),
          ]);
          if (cancelled) return;
          if (localPrompts.length) {
            nextPrompts = await Promise.all(
              localPrompts.slice(0, 40).map(async (item) => ({
                ...item,
                persona: item.persona || item.persona_name || item.display_name,
                version: item.resource_version || item.version || 0,
                text: item.prompt_id
                  ? await localDataPlane.promptContent(item.prompt_id, localDevice, item.resource_version || item.version).catch(() => "")
                  : "",
              })),
            );
          }
          nextOutputs = mergeOutputs(localOutputs, metaImages.images || []);
          nextOutputs = await Promise.all(
            nextOutputs.map(async (item) => {
              const version = item.current_version || item.version || item.resource_version;
              const id = item.output_id || item.resource_id || "";
              return {
                ...item,
                url: id
                  ? await localDataPlane.outputObjectUrl(id, localDevice, version).catch(() => "")
                  : "",
              };
            }),
          );
        } catch {
          nextOutputs = metaImages.images || [];
        }
      }
      if (!cancelled) {
        setPrompts(nextPrompts);
        setOutputs(nextOutputs);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [runId, deviceId, run.device_id, busy]);

  async function queueImages(mode: "45" | "both" | "916") {
    if (!runId) return;
    setBusy(mode);
    try {
      const queued = await fetchJSON<{ job_id?: string }>(`/api/runs/${encodeURIComponent(runId)}/image-generation`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          operation_id: `${runId}-images-${mode}-${Date.now()}`,
          engine,
          mode,
          agent_id: agentId,
          device_id: deviceId || run.device_id || "",
        }),
      });
      invalidateRuns();
      onStatus(`Queued ${mode === "both" ? "4:5 + 9:16" : mode === "916" ? "9:16" : "4:5"} for ${run.display_batch || runId}. Job ${queued.job_id || ""}.`);
      await onRefresh();
    } catch (err) {
      onStatus(String(err));
    } finally {
      setBusy("");
    }
  }

  async function revisePicked() {
    const outputId = picked;
    if (!outputId || !comment.trim()) {
      onStatus("Select an image and write a revision comment.");
      return;
    }
    const localDevice = deviceId || run.device_id || "";
    if (!localDevice) {
      onStatus("Pair this dashboard with the local agent before revising.");
      return;
    }
    setBusy("revision");
    try {
      await localDataPlane.outputAction(outputId, "revisions", localDevice, {
        comment: comment.trim(),
        engine,
      });
      onStatus(`Revision queued for ${run.display_batch || runId}.`);
      setComment("");
    } catch (err) {
      onStatus(String(err));
    } finally {
      setBusy("");
    }
  }

  async function openCopyEditor(prompt: PromptRow) {
    const id = prompt.prompt_id || "";
    const localDevice = deviceId || run.device_id || "";
    setCopyError("");
    if (editingId === id) {
      setEditingId("");
      return;
    }
    let text = prompt.text || "";
    if (!text && id && localDevice) {
      text = await localDataPlane.promptContent(id, localDevice, prompt.resource_version || prompt.version).catch(() => "");
    }
    const lines = exactOnImageCopyLines(text);
    if (!text) {
      setCopyError("Pair the local agent to load this prompt’s on-image copy.");
      setCopyLines([]);
      setCopySource("");
    } else if (!lines.length) {
      setCopyError("No EXACT ON-IMAGE COPY block in this prompt.");
      setCopyLines([]);
      setCopySource(text);
    } else {
      setCopyLines(lines);
      setCopySource(text);
    }
    setCopyVersion(prompt.resource_version || prompt.version || 0);
    setEditingId(id);
  }

  async function saveCopy() {
    const id = editingId;
    const localDevice = deviceId || run.device_id || "";
    if (!id || !localDevice) {
      onStatus("Pair the local agent before saving on-image copy.");
      return;
    }
    setBusy("copy");
    try {
      const next = replaceExactOnImageCopy(copySource, copyLines);
      const saved = await localDataPlane.putPrompt(id, runId, next, copyVersion, localDevice);
      setCopySource(next);
      setCopyVersion(saved.version || copyVersion + 1);
      setPrompts((prev) => prev.map((item) => (
        item.prompt_id === id
          ? { ...item, text: next, version: saved.version || copyVersion + 1 }
          : item
      )));
      onStatus(`Saved on-image copy for ${run.display_batch || runId}.`);
    } catch (err) {
      onStatus(String(err));
    } finally {
      setBusy("");
    }
  }

  const shownCount = outputs.length || run.image_count || 0;
  const imageHint = outputs.length
    ? ""
    : (run.image_count || 0) > 0
      ? `${run.image_count} images are recorded on this run, but this tab cannot read the local files yet. Restart the local agent with --api-base http://127.0.0.1:4090 if it is talking to another dashboard.`
      : "No generated images yet. Use Generate 4:5 after this tab is paired with the local agent.";

  return (
    <section className="run-workspace">
      <div className="action-row" style={{ marginBottom: 12 }}>
        <strong>{run.display_batch || runId}</strong>
        <span className="hint">{run.status || "unknown"} · {run.prompt_count ?? prompts.length} prompts · {shownCount} images</span>
        <Button variant="ghost" onClick={onClose}>Close run</Button>
      </div>
      <label className="hint">
        Image engine
        <select className="field" value={engine} onChange={(e) => setEngine(e.target.value)}>
          <option value="chatgpt">ChatGPT</option>
          <option value="gemini">Gemini</option>
        </select>
      </label>
      <div className="action-row" style={{ marginBottom: 16 }}>
        <Button variant="primary" disabled={Boolean(busy)} onClick={() => void queueImages("45")}>
          {busy === "45" ? "Queuing…" : "Generate 4:5"}
        </Button>
        <Button disabled={Boolean(busy)} onClick={() => void queueImages("both")}>
          {busy === "both" ? "Queuing…" : "Generate 4:5 + 9:16"}
        </Button>
        <Button disabled={Boolean(busy)} onClick={() => void queueImages("916")}>
          {busy === "916" ? "Queuing…" : "Generate 9:16"}
        </Button>
      </div>
      <p className="hint" style={{ marginBottom: 10 }}>
        {prompts.length ? `${prompts.length} prompts on this plate.` : "No prompts yet. Run structured copy first."}
      </p>
      {prompts.length ? (
        <div className="run-list" style={{ marginBottom: 16 }}>
          {prompts.slice(0, 16).map((prompt, index) => {
            const id = prompt.prompt_id || String(index);
            return (
              <div key={id}>
                <article className="run-row prompt-row">
                  <strong>{prompt.persona || prompt.display_name || prompt.prompt_id || `Prompt ${index + 1}`}</strong>
                  <span>{prompt.format || "—"}</span>
                  <span>{prompt.language || "—"}</span>
                  <span>{prompt.status || "ready"}</span>
                  <Button variant="ghost" onClick={() => void openCopyEditor(prompt)}>
                    {editingId === id ? "Close copy" : "Edit on-image copy"}
                  </Button>
                </article>
                {editingId === id ? (
                  <div className="copy-editor">
                    {copyError ? <p className="hint">{copyError}</p> : null}
                    {copyLines.map((line, lineIndex) => (
                      <label key={`${line.label}-${lineIndex}`} className="copy-line">
                        <span>{line.label}</span>
                        <input
                          className="field"
                          value={line.value}
                          onChange={(e) => {
                            const value = e.target.value;
                            setCopyLines((prev) => prev.map((item, i) => (i === lineIndex ? { ...item, value } : item)));
                          }}
                        />
                      </label>
                    ))}
                    {copyLines.length ? (
                      <Button disabled={busy === "copy"} onClick={() => void saveCopy()}>
                        {busy === "copy" ? "Saving…" : "Save on-image copy"}
                      </Button>
                    ) : null}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : null}
      {outputs.length ? (
        <div className="output-grid">
          {outputs.map((item, index) => {
            const id = item.output_id || item.resource_id || item.artifact_id || String(index);
            return (
              <button
                key={id}
                type="button"
                className={`output-card${picked === id ? " active" : ""}`}
                onClick={() => setPicked(id)}
              >
                {item.url ? <img src={item.url} alt={item.display_name || item.filename || id} /> : <span>{item.aspect_ratio || item.display_name || "image"}</span>}
              </button>
            );
          })}
        </div>
      ) : (
        <p className="hint">{imageHint}</p>
      )}
      <label className="hint" style={{ marginTop: 14 }}>
        Revision comment
        <textarea
          className="cfg-textarea"
          rows={3}
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder={picked ? "Describe the change for the selected image" : "Select an image, then describe the revision"}
        />
      </label>
      <Button disabled={Boolean(busy) || !picked} onClick={() => void revisePicked()}>
        {busy === "revision" ? "Queuing…" : "Generate revision"}
      </Button>
    </section>
  );
}
