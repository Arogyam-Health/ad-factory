import { useEffect, useMemo, useState } from "react";
import { fetchJSON, invalidateRuns } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Bento, Tile } from "@/components/Tile";
import { Button } from "@/components/Button";
import { Skeleton, SkeletonLines } from "@/components/Skeleton";

const FORMATS = ["HERO", "BA", "TEST", "FEAT", "UGC"] as const;
const LANGUAGES = ["ALL", "EN", "HI", "HINGLISH"] as const;

type Persona = { number: number; name: string };
type Run = {
  run_id?: string;
  status?: string;
  prompt_count?: number;
  image_count?: number;
  display_batch?: string;
  created_at?: number;
};

function studioOrgKey(userId: string) {
  return `adFactoryStudioOrg:${userId}`;
}

export function StudioPage() {
  const { user } = useAuth();
  const [loading, setLoading] = useState(true);
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [formats, setFormats] = useState<Set<string>>(new Set(["HERO"]));
  const [language, setLanguage] = useState("EN");
  const [flow, setFlow] = useState<"structured" | "reference">(
    () => (localStorage.getItem("adFactoryFlowMode") === "reference" ? "reference" : "structured"),
  );
  const [runs, setRuns] = useState<Run[]>([]);
  const [status, setStatus] = useState("Plate is idle.");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    localStorage.setItem("adFactoryFlowMode", flow);
  }, [flow]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const orgId = user.user_id ? localStorage.getItem(studioOrgKey(user.user_id)) || "" : "";
    const personaUrl = orgId && orgId !== "personal"
      ? `/api/config/persona-summary?org_id=${encodeURIComponent(orgId)}`
      : "/api/config/persona-summary";

    Promise.allSettled([
      fetchJSON<{ personas?: Persona[] }>(personaUrl, { cache: "no-store" }),
      fetchJSON<{ runs?: Run[] }>(`/api/runs?flow=${flow}`, { cache: "no-store" }),
    ]).then(([personaResult, runResult]) => {
      if (cancelled) return;
      if (personaResult.status === "fulfilled") {
        setPersonas(
          (personaResult.value.personas || [])
            .map((p) => ({ number: Number(p.number), name: String(p.name || `Persona ${p.number}`) }))
            .filter((p) => p.number),
        );
      }
      if (runResult.status === "fulfilled") setRuns(runResult.value.runs || []);
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, [flow, user.user_id]);

  const selectedCount = selected.size;
  const formatList = useMemo(() => FORMATS.filter((fmt) => formats.has(fmt)), [formats]);

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
      const envelope = await fetchJSON<{ run_id: string }>("/api/runs/allocate-copy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          flow: "structured",
          language_mode: language,
          persona_numbers: [...selected],
          formats: formatList,
          formats_by_persona: formatsByPersona,
        }),
      });
      await fetchJSON(`/api/runs/${encodeURIComponent(envelope.run_id)}/structured-copy`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          operation_id: `${envelope.run_id}-structured-copy`,
          language_mode: language,
        }),
      });
      localStorage.setItem("adFactoryCopyPipeline", envelope.run_id);
      invalidateRuns();
      setStatus(`Plate ${envelope.run_id} is on press.`);
      const data = await fetchJSON<{ runs?: Run[] }>(`/api/runs?flow=${flow}`, { cache: "no-store" });
      setRuns(data.runs || []);
    } catch (err) {
      setStatus(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
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
        {loading ? (
          <SkeletonGridLite />
        ) : (
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
      </Tile>

      <Tile span="side" kicker="02 · Make ready" title="Send to press">
        {loading ? <SkeletonLines lines={5} /> : (
          <>
            <p className="hint">
              {selectedCount} persona{selectedCount === 1 ? "" : "s"} · {formatList.join(" / ") || "no formats"} · {language}
            </p>
            <p className="hint" style={{ margin: "14px 0 18px" }}>{status}</p>
            <Button variant="primary" disabled={busy} onClick={() => void startStructured()}>
              {busy ? "On press…" : flow === "structured" ? "Run structured plate" : "Reference flow stays on this plate"}
            </Button>
          </>
        )}
      </Tile>

      <Tile span="wide" kicker="03 · Dry proofs" title="Recent runs">
        {loading ? (
          <div style={{ display: "grid", gap: 10 }}>
            <Skeleton className="skel-block" />
            <Skeleton className="skel-block" />
          </div>
        ) : runs.length ? (
          <div className="run-list">
            {runs.slice(0, 8).map((run) => (
              <article key={run.run_id} className="run-row">
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
    </Bento>
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
