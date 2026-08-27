import type { TerminalLine } from "@/components/RunTerminal";
import type { Run } from "@/lib/types";

const STUDIO_SESSION_KEY = "adFactoryStudioSession";

export type StudioSession = {
  structuredRuns: Run[];
  referenceRuns: Run[];
  copyJobId: string;
  activeRunId: string;
  structuredLogLines: TerminalLine[];
  referenceLogLines: TerminalLine[];
  logId: number;
  status: string;
};

const emptySession: StudioSession = {
  structuredRuns: [],
  referenceRuns: [],
  copyJobId: "",
  activeRunId: "",
  structuredLogLines: [],
  referenceLogLines: [],
  logId: 0,
  status: "Plate is idle.",
};

function readLogLines(value: unknown): TerminalLine[] {
  return Array.isArray(value) ? value.slice(-80) as TerminalLine[] : [];
}

export function readStudioSession(): StudioSession {
  if (typeof sessionStorage === "undefined") return emptySession;
  try {
    const raw = sessionStorage.getItem(STUDIO_SESSION_KEY);
    if (!raw) return emptySession;
    const parsed = JSON.parse(raw) as Partial<StudioSession> & { logLines?: unknown };
    return {
      structuredRuns: Array.isArray(parsed.structuredRuns) ? parsed.structuredRuns : [],
      referenceRuns: Array.isArray(parsed.referenceRuns) ? parsed.referenceRuns : [],
      copyJobId: String(parsed.copyJobId || ""),
      activeRunId: String(parsed.activeRunId || ""),
      structuredLogLines: readLogLines(parsed.structuredLogLines ?? parsed.logLines),
      referenceLogLines: readLogLines(parsed.referenceLogLines),
      logId: Number(parsed.logId || 0),
      status: String(parsed.status || emptySession.status),
    };
  } catch {
    return emptySession;
  }
}

export function writeStudioSession(next: StudioSession) {
  if (typeof sessionStorage === "undefined") return;
  try {
    sessionStorage.setItem(STUDIO_SESSION_KEY, JSON.stringify(next));
  } catch {
    /* quota */
  }
}
