import { useEffect, useMemo, useState } from "react";
import { fetchJSON, peekCache, clearCache } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Trace, TraceList } from "@/lib/types";
import { Bento, Tile } from "@/components/Tile";
import { Button } from "@/components/Button";
import { SkeletonLines } from "@/components/Skeleton";

function matchesQuery(trace: Trace, query: string) {
  if (!query) return true;
  return [
    trace.label,
    trace.run_id,
    trace.batch,
    trace.model,
    trace.provider,
    trace.trace_id,
    trace.scope,
    trace.display_name,
    trace.actor_email,
  ]
    .join(" ")
    .toLowerCase()
    .includes(query);
}

function TraceRows({
  traces,
  open,
  setOpen,
  selected,
  setSelected,
}: {
  traces: Trace[];
  open: string;
  setOpen: (id: string) => void;
  selected: Set<string>;
  setSelected: (next: Set<string> | ((prev: Set<string>) => Set<string>)) => void;
}) {
  return (
    <div className="run-list">
      {traces.map((trace) => {
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
              {trace.display_name || trace.actor_email ? (
                <p className="hint">{trace.display_name || trace.actor_email}</p>
              ) : null}
            </div>
            <span>{trace.model || "?"}</span>
            <span>{trace.http_status || (trace.status === "completed" ? 200 : 500)}</span>
            <span>{((trace.duration_ms || 0) / 1000).toFixed(2)}s</span>
            <Button variant="ghost" onClick={() => setOpen(open === id ? "" : id)}>
              {open === id ? "Hide" : "Open"}
            </Button>
            {open === id ? (
              <pre className="trace-pre">
                {JSON.stringify(
                  {
                    request: trace.request,
                    response: trace.response,
                    error: trace.error_detail,
                  },
                  null,
                  2,
                )}
              </pre>
            ) : null}
          </article>
        );
      })}
    </div>
  );
}

export function TracesPage() {
  const { user, ready } = useAuth();
  const [loading, setLoading] = useState(true);
  const [personal, setPersonal] = useState<Trace[]>([]);
  const [orgTraces, setOrgTraces] = useState<Trace[]>([]);
  const [orgName, setOrgName] = useState("");
  const [open, setOpen] = useState("");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [status, setStatus] = useState("");

  useEffect(() => {
    if (!ready) return;
    if (!user.authenticated) {
      setLoading(false);
      return;
    }
    const cached = peekCache<TraceList>("/api/llm-traces");
    if (cached) {
      setPersonal(cached.personal || cached.traces || []);
      setOrgTraces(cached.org || []);
      setOrgName(cached.org_name || "");
      setLoading(false);
    }
    fetchJSON<TraceList>("/api/llm-traces")
      .then((data) => {
        setPersonal(data.personal || data.traces || []);
        setOrgTraces(data.org || []);
        setOrgName(data.org_name || "");
      })
      .catch((err) => setStatus(String(err)))
      .finally(() => setLoading(false));
  }, [ready, user.authenticated]);

  const q = query.trim().toLowerCase();
  const filteredPersonal = useMemo(
    () => personal.filter((trace) => matchesQuery(trace, q)),
    [personal, q],
  );
  const filteredOrg = useMemo(
    () => orgTraces.filter((trace) => matchesQuery(trace, q)),
    [orgTraces, q],
  );
  const total = filteredPersonal.length + filteredOrg.length;

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
          <input className="field" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter by run, model, label" />
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
                setPersonal((prev) => prev.filter((trace) => !selected.has(trace.trace_id || "")));
                setOrgTraces((prev) => prev.filter((trace) => !selected.has(trace.trace_id || "")));
                setSelected(new Set());
                setStatus("Deleted.");
              } catch (err) {
                setStatus(String(err));
              }
            }}
          >
            Delete selected
          </Button>
          <span className="hint">{total} traces · {status}</span>
        </div>
        {loading ? <SkeletonLines lines={8} /> : null}
      </Tile>
      {loading ? null : (
        <>
          <Tile span="wide" kicker="Personal" title="Personal runs">
            {filteredPersonal.length ? (
              <TraceRows
                traces={filteredPersonal}
                open={open}
                setOpen={setOpen}
                selected={selected}
                setSelected={setSelected}
              />
            ) : (
              <p className="hint">No personal copy LLM calls yet. Image and reference runs do not write traces.</p>
            )}
          </Tile>
          <Tile span="wide" kicker="Organization" title={orgName ? `Org runs — ${orgName}` : "Org runs"}>
            {filteredOrg.length ? (
              <TraceRows
                traces={filteredOrg}
                open={open}
                setOpen={setOpen}
                selected={selected}
                setSelected={setSelected}
              />
            ) : (
              <p className="hint">No org copy LLM calls yet. Image and reference runs do not write traces.</p>
            )}
          </Tile>
        </>
      )}
    </Bento>
  );
}
