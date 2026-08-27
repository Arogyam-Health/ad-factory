import { useEffect, useRef } from "react";

export type TerminalLine = {
  id: number;
  at: number;
  level: "info" | "warning" | "error";
  text: string;
};

export function RunTerminal({ lines }: { lines: TerminalLine[] }) {
  const scroller = useRef<HTMLPreElement>(null);
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines]);
  return (
    <pre className="run-terminal" aria-live="polite" ref={scroller}>
      {lines.length ? lines.map((line) => (
        <div key={line.id} className={`run-terminal-line ${line.level}`}>
          <span className="run-terminal-time">{new Date(line.at).toLocaleTimeString()}</span>
          {line.text}
        </div>
      )) : (
        <div className="run-terminal-line info">Plate is idle.</div>
      )}
    </pre>
  );
}
