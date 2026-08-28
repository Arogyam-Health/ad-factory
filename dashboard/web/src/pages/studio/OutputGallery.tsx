import { useMemo, useState } from "react";
import { localDataPlane } from "@/lib/local-data-plane.js";
import { Button } from "@/components/Button";
import { DownloadKindDialog } from "@/components/DownloadKindDialog";
import { ListPager, usePageWindow } from "@/components/ListPager";

const IMAGES_PER_PAGE = 8;

export type OutputRow = {
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

function outputKey(item: OutputRow) {
  return item.output_id || item.resource_id || item.artifact_id || "";
}

function downloadName(item: OutputRow) {
  const raw = String(item.display_name || item.filename || "").trim();
  if (raw) return /\.[A-Za-z0-9]+$/.test(raw) ? raw : `${raw}.png`;
  return `${outputKey(item) || "image"}.png`;
}

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function aspectKey(item: OutputRow) {
  const label = String(item.aspect_ratio || "");
  if (label.includes("9") && label.includes("16")) return "9_16";
  if (label.includes("4") && label.includes("5")) return "4_5";
  return "";
}

async function waitForRevision(
  revisionId: string,
  deviceId: string,
  onTick: (status: string) => void,
) {
  let fetchFails = 0;
  for (let attempt = 0; attempt < 450; attempt += 1) {
    try {
      const data = await localDataPlane.revisionStatus(revisionId, deviceId);
      fetchFails = 0;
      const status = String(data.status || "");
      onTick(status);
      if (status === "completed" || status === "error") return data;
    } catch (err) {
      fetchFails += 1;
      onTick("waiting");
      if (fetchFails >= 8) {
        return { status: "error", error: String(err) };
      }
    }
    await new Promise((resolve) => window.setTimeout(resolve, 2000));
  }
  return { status: "error", error: "Timed out waiting for revision" };
}

export function OutputGallery({
  outputs,
  deviceId,
  defaultEngine,
  onStatus,
  onChange,
}: {
  outputs: OutputRow[];
  deviceId: string;
  defaultEngine: string;
  onStatus: (value: string) => void;
  onChange: () => void;
}) {
  const [filter, setFilter] = useState("");
  const [comments, setComments] = useState<Record<string, string>>({});
  const [engines, setEngines] = useState<Record<string, string>>({});
  const [busyId, setBusyId] = useState("");
  const [lightbox, setLightbox] = useState<OutputRow | null>(null);
  const [batchBusy, setBatchBusy] = useState(false);
  const [downloadItem, setDownloadItem] = useState<OutputRow | null>(null);

  const counts = useMemo(() => {
    let ar45 = 0;
    let ar916 = 0;
    for (const item of outputs) {
      const key = aspectKey(item);
      if (key === "4_5") ar45 += 1;
      if (key === "9_16") ar916 += 1;
    }
    return { all: outputs.length, ar45, ar916 };
  }, [outputs]);

  const visible = outputs.filter((item) => !filter || aspectKey(item) === filter);
  const imageWindow = usePageWindow(visible, IMAGES_PER_PAGE, filter);

  async function openImage(item: OutputRow) {
    if (item.url) {
      setLightbox(item);
      return;
    }
    const id = outputKey(item);
    if (!id || !deviceId) return;
    const url = await localDataPlane.outputObjectUrl(
      id,
      deviceId,
      item.current_version || item.version || item.resource_version,
    ).catch(() => "");
    if (url) setLightbox({ ...item, url });
  }

  async function downloadImage(item: OutputRow, includeRaw: boolean) {
    const id = outputKey(item);
    if (!id || !deviceId) {
      onStatus("Pair the local agent before downloading an image.");
      return;
    }
    const name = downloadName(item);
    try {
      const blob = item.url
        ? await fetch(item.url).then((response) => response.blob())
        : await fetch(await localDataPlane.outputObjectUrl(id, deviceId, item.current_version || item.version)).then((response) => response.blob());
      triggerDownload(blob, name);
      if (includeRaw) {
        const raw = await localDataPlane.outputRawBlob(id, deviceId);
        const stem = name.replace(/\.[^.]+$/, "");
        const ext = name.includes(".") ? name.slice(name.lastIndexOf(".")) : ".png";
        triggerDownload(raw, `${stem}.raw${ext}`);
      }
    } catch (err) {
      onStatus(String(err));
    }
  }

  async function deleteImage(item: OutputRow) {
    const id = outputKey(item);
    if (!id || !deviceId) {
      onStatus("Pair the local agent before deleting an image.");
      return;
    }
    if (!window.confirm(`Delete ${downloadName(item)}?`)) return;
    setBusyId(id);
    try {
      await localDataPlane.deleteOutput(id, deviceId);
      if (lightbox && outputKey(lightbox) === id) setLightbox(null);
      onStatus(`Deleted ${downloadName(item)}.`);
      onChange();
    } catch (err) {
      onStatus(String(err));
    } finally {
      setBusyId("");
    }
  }

  async function reviseImage(item: OutputRow) {
    const id = outputKey(item);
    const comment = (comments[id] || "").trim();
    if (!id || !comment) {
      onStatus("Write a revision comment on this image.");
      return;
    }
    if (!deviceId) {
      onStatus("Pair this dashboard with the local agent before revising.");
      return;
    }
    setBusyId(id);
    try {
      const queued = await localDataPlane.outputAction(id, "revisions", deviceId, {
        comment,
        engine: engines[id] || defaultEngine,
      }) as { revision_id?: string };
      onStatus(`Revision queued for ${downloadName(item)}.`);
      if (queued.revision_id) {
        const result = await waitForRevision(queued.revision_id, deviceId, (status) => {
          onStatus(`Revision ${status} for ${downloadName(item)}.`);
        });
        if (result.status === "completed") {
          setComments((prev) => ({ ...prev, [id]: "" }));
          onChange();
        } else {
          onStatus(String(result.error || "Revision failed"));
        }
      }
    } catch (err) {
      onStatus(String(err));
    } finally {
      setBusyId("");
    }
  }

  async function reviseAllCommented() {
    const targets = outputs.filter((item) => (comments[outputKey(item)] || "").trim());
    if (!targets.length) {
      onStatus("No commented images found.");
      return;
    }
    setBatchBusy(true);
    try {
      for (const item of targets) {
        await reviseImage(item);
      }
    } finally {
      setBatchBusy(false);
    }
  }

  if (!outputs.length) return null;

  return (
    <div className="image-gallery">
      <div className="gallery-header">
        <strong>Generated Images ({outputs.length})</strong>
        <Button variant="ghost" disabled={batchBusy} onClick={() => void reviseAllCommented()}>
          {batchBusy ? "Revising…" : "Revise all commented"}
        </Button>
      </div>
      {counts.ar45 > 0 && counts.ar916 > 0 ? (
        <div className="gallery-filters">
          {[
            { label: `All (${counts.all})`, value: "" },
            { label: `4:5 (${counts.ar45})`, value: "4_5" },
            { label: `9:16 (${counts.ar916})`, value: "9_16" },
          ].map((item) => (
            <button
              key={item.value || "all"}
              type="button"
              className={`gallery-filter${filter === item.value ? " active" : ""}`}
              onClick={() => setFilter(item.value)}
            >
              {item.label}
            </button>
          ))}
        </div>
      ) : null}
      <div className="output-grid">
        {imageWindow.items.map((item, index) => {
          const id = outputKey(item) || String(imageWindow.page * IMAGES_PER_PAGE + index);
          const busy = busyId === id;
          return (
            <article key={id} className="image-card">
              <div className="image-wrap">
                {item.url ? (
                  <img
                    src={item.url}
                    alt={item.display_name || item.filename || id}
                    onClick={() => void openImage(item)}
                  />
                ) : (
                  <button type="button" className="image-missing" onClick={() => void openImage(item)}>
                    {item.aspect_ratio || item.display_name || "image"}
                  </button>
                )}
                <div className="image-card-actions">
                  <button type="button" title="Open" onClick={() => void openImage(item)}>Open</button>
                  <button type="button" title="Download" onClick={() => setDownloadItem(item)}>↓</button>
                  <button type="button" title="Delete this image" disabled={busy} onClick={() => void deleteImage(item)}>✕</button>
                </div>
              </div>
              <div className="image-filename">{item.display_name || item.filename || id}</div>
              <div className="image-output-meta">{item.aspect_ratio || "4:5"}</div>
              <label className="image-comment-box">
                <span>Comment & revise</span>
                <textarea
                  rows={3}
                  maxLength={8000}
                  placeholder="Tell the model exactly what to change on this image."
                  value={comments[id] || ""}
                  onChange={(e) => setComments((prev) => ({ ...prev, [id]: e.target.value }))}
                />
                <div className="image-comment-controls">
                  <select
                    aria-label="Revision engine"
                    value={engines[id] || defaultEngine}
                    onChange={(e) => setEngines((prev) => ({ ...prev, [id]: e.target.value }))}
                  >
                    <option value="chatgpt">ChatGPT</option>
                    <option value="gemini">Gemini</option>
                  </select>
                  <Button disabled={busy || !(comments[id] || "").trim()} onClick={() => void reviseImage(item)}>
                    {busy ? "Revising…" : "Generate revision"}
                  </Button>
                </div>
              </label>
            </article>
          );
        })}
      </div>
      <ListPager
        page={imageWindow.page}
        pageCount={imageWindow.pageCount}
        onPage={imageWindow.setPage}
        summary={`${visible.length} images`}
      />
      {downloadItem ? (
        <DownloadKindDialog
          title={`Download ${downloadName(downloadItem)}`}
          onClose={() => setDownloadItem(null)}
          onChoose={(includeRaw) => {
            const item = downloadItem;
            setDownloadItem(null);
            void downloadImage(item, includeRaw);
          }}
        />
      ) : null}
      {lightbox?.url ? (
        <div className="image-lightbox" onClick={() => setLightbox(null)} role="presentation">
          <div className="image-lightbox-card" onClick={(event) => event.stopPropagation()} role="dialog" aria-modal="true">
            <div className="action-row">
              <strong>{lightbox.display_name || lightbox.filename || "Image"}</strong>
              <Button variant="ghost" onClick={() => setDownloadItem(lightbox)}>Download</Button>
              <Button variant="ghost" onClick={() => setLightbox(null)}>Close</Button>
            </div>
            <img src={lightbox.url} alt={lightbox.display_name || lightbox.filename || "Generated image"} />
          </div>
        </div>
      ) : null}
    </div>
  );
}
