import { useEffect, useState } from "react";
import { fetchJSON } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Bento, Tile } from "@/components/Tile";
import { Button } from "@/components/Button";
import { SkeletonLines } from "@/components/Skeleton";

type Trace = {
  trace_id?: string;
  run_id?: string;
  label?: string;
  model?: string;
  provider?: string;
  status?: string;
  http_status?: number;
  duration_ms?: number;
  created_at?: number;
  error_detail?: string;
  request?: { prompt?: unknown };
  response?: { content?: unknown };
};

export function TracesPage() {
  const { user, ready } = useAuth();
  const [loading, setLoading] = useState(true);
  const [traces, setTraces] = useState<Trace[]>([]);
  const [open, setOpen] = useState<string>("");

  useEffect(() => {
    if (!ready || !user.authenticated) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    fetchJSON<{ traces?: Trace[] }>("/api/llm-traces", { cache: "no-store" })
      .then((data) => {
        if (!cancelled) setTraces(data.traces || []);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [ready, user.authenticated]);

  if (ready && !user.authenticated) {
    return (
      <div className="page-gate">
        <p className="eyebrow">Proofs</p>
        <h1 style={{ margin: "8px 0 12px" }}>Sign in to inspect traces</h1>
        <a className="btn btn-primary" href="/api/auth/google/login">Sign in</a>
      </div>
    );
  }

  return (
    <Bento>
      <Tile span="wide" kicker="Proof sheet" title="Copy LLM calls">
        {loading ? <SkeletonLines lines={8} /> : traces.length ? (
          <div className="run-list">
            {traces.map((trace) => {
              const id = trace.trace_id || `${trace.run_id}-${trace.created_at}`;
              return (
                <article key={id} className="run-row" style={{ alignItems: "start" }}>
                  <div>
                    <strong>{trace.label || "trace"}</strong>
                    <p className="hint">{trace.run_id}</p>
                  </div>
                  <span>{trace.model || "?"}</span>
                  <span>{trace.http_status || (trace.status === "completed" ? 200 : 500)}</span>
                  <span>{((trace.duration_ms || 0) / 1000).toFixed(2)}s</span>
                  <Button
                    variant="ghost"
                    onClick={() => setOpen(open === id ? "" : id)}
                  >
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
      </Tile>
    </Bento>
  );
}
