import { useEffect, useMemo, useRef, useState } from "react";
import type { Run } from "@/lib/types";

export function BatchSelect({
  runs,
  picked,
  onChange,
}: {
  runs: Run[];
  picked: Set<string>;
  onChange: (next: Set<string>) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const cols = Math.max(1, Math.ceil(Math.sqrt(runs.length || 1)));

  useEffect(() => {
    function onDocClick(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("click", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, []);

  const label = useMemo(
    () => (picked.size ? `${picked.size} batch(es) selected` : "Select batches"),
    [picked.size],
  );

  function toggle(id: string) {
    const next = new Set(picked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onChange(next);
  }

  return (
    <div className="batch-dropdown" ref={rootRef}>
      <button
        type="button"
        className="btn batch-dropdown-btn"
        aria-expanded={open}
        onClick={(event) => {
          event.stopPropagation();
          setOpen((value) => !value);
        }}
      >
        {label}
      </button>
      <div className={`dropdown-menu${open ? "" : " hidden"}`}>
        {runs.length ? (
          <div className="batch-grid" style={{ gridTemplateColumns: `repeat(${cols}, minmax(140px, 1fr))` }}>
            {runs.map((run) => {
              const id = run.run_id || "";
              const name = run.display_batch || id;
              return (
                <div
                  key={id}
                  className="batch-grid-item"
                  onClick={() => toggle(id)}
                >
                  <input
                    type="checkbox"
                    className="batch-check"
                    checked={picked.has(id)}
                    onChange={() => toggle(id)}
                    onClick={(event) => event.stopPropagation()}
                  />
                  <span className="batch-label">{name}</span>
                </div>
              );
            })}
          </div>
        ) : (
          <p className="hint">No batches on this plate.</p>
        )}
      </div>
    </div>
  );
}
