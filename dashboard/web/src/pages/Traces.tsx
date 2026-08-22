import { useEffect, useMemo, useState } from "react";
import { fetchJSON, peekCache, clearCache } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Trace } from "@/lib/types";
import { Bento, Tile } from "@/components/Tile";
import { Button } from "@/components/Button";
import { SkeletonLines } from "@/components/Skeleton";

const PAGE_SIZE = 20;

export function TracesPage() {
  const { user, ready } = useAuth();
  const [loading, setLoading] = useState(true);
  const [traces, setTraces] = useState<Trace[]>([]);
  const [open, setOpen] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [status, setStatus] = useState("");

  useEffect(() => {
    if (!ready) return;
    if (!user.authenticated) {
      setLoading(false);
      return;
    }
    const cached = peekCache<{ traces?: Trace[] }>("/api/llm-traces");
    if (cached?.traces) {
      setTraces(cached.traces);
      setLoading(false);
    }
    fetchJSON<{ traces?: Trace[] }>("/api/llm-traces")
      .then((data) => setTraces(data.traces || []))
      .catch((err) => setStatus(String(err)))
      .finally(() => setLoading(false));
  }, [ready, user.authenticated]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return traces;
    return traces.filter((trace) =>
      [trace.label, trace.run_id, trace.model, trace.provider, trace.trace_id]
        .join(" ")
        .toLowerCase()
        .includes(q),
    );
  }, [traces, query]);

  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const slice = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  if (ready && !user.authenticated) {
    return (
      <Bento>
        <Tile span="wide" kicker="Proof sheet" title="Copy LLM calls">
          <p className="hint">
            Traces are account-scoped. Guests can still browse generic studio files and rules.
            Sign in to inspect request and response bodies from your runs.
          </p>
        </Tile>
      </Bento>
    );
  }

  return (
    <Bento>
      <Tile span="wide" kicker="Proof sheet" title="Copy LLM calls">
        <div className="action-row" style={{ marginBottom: 14 }}>
          <input className="field" value={query} onChange={(e) => { setQuery(e.target.value); setPage(1); }} placeholder="Filter by run, model, label" />
          <Button
            variant="danger"
            disabled={!selected.size}
            onClick={async () => {
              if (!window.confirm(`Delete ${selected.size} trace(s)?`)) return;
              try {
                await fetchJSON("/api/llm-traces/delete-batch", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ trace_ids: [...selected].slice(0, 10) }),
                });
                clearCache("/api/llm-traces");
                setTraces((prev) => prev.filter((trace) => !selected.has(trace.trace_id || "")));
                setSelected(new Set());
                setStatus("Deleted.");
              } catch (err) {
                setStatus(String(err));
              }
            }}
          >
            Delete selected
          </Button>
          <span className="hint">{filtered.length} traces · {status}</span>
        </div>
        {loading ? <SkeletonLines lines={8} /> : slice.length ? (
          <div className="run-list">
            {slice.map((trace) => {
              const id = trace.trace_id || `${trace.run_id}-${trace.created_at}`;
              return (
                <article key={id} className="run-row trace-row">
                  <label className="hint">
                    <input
                      type="checkbox"
                      checked={selected.has(id)}
                      onChange={() => {
                        setSelected((prev) => {
                          const next = new Set(prev);
                          if (next.has(id)) next.delete(id);
                          else next.add(id);
                          return next;
                        });
                      }}
                    />
                  </label>
                  <div>
                    <strong>{trace.label || "trace"}</strong>
                    <p className="hint">{trace.run_id}</p>
                  </div>
                  <span>{trace.model || "?"}</span>
                  <span>{trace.http_status || (trace.status === "completed" ? 200 : 500)}</span>
                  <span>{((trace.duration_ms || 0) / 1000).toFixed(2)}s</span>
                  <Button variant="ghost" onClick={() => setOpen(open === id ? "" : id)}>
                    {open === id ? "Hide" : "Open"}
                  </Button>
                  {open === id ? (
                    <pre className="trace-pre">
                      {JSON.stringify({ request: trace.request, response: trace.response, error: trace.error_detail }, null, 2)}
                    </pre>
                  ) : null}
                </article>
              );
            })}
          </div>
        ) : (
          <p className="hint">No copy LLM calls yet. Image and reference runs do not write traces.</p>
        )}
        {pages > 1 ? (
          <div className="action-row" style={{ marginTop: 14 }}>
            <Button disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Prev</Button>
            <span className="hint">{page} / {pages}</span>
            <Button disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>Next</Button>
          </div>
        ) : null}
      </Tile>
    </Bento>
  );
}
