const STUDIO_BATCH_KEY = "adFactoryStudioBatch";

export type StudioBatchSettings = {
  multiplier: number;
  batchSize: number;
  shareBg: boolean;
  reuseBg: string;
  reusePattern: string;
};

export const DEFAULT_STUDIO_BATCH: StudioBatchSettings = {
  multiplier: 1,
  batchSize: 10,
  shareBg: false,
  reuseBg: "",
  reusePattern: "",
};

export function clampInt(value: number, min: number, max: number, fallback: number): number {
  if (!Number.isFinite(value)) return fallback;
  return Math.max(min, Math.min(max, Math.round(value)));
}

function boundedId(value: unknown): string {
  return String(value || "").trim().slice(0, 80);
}

export function readStudioBatch(): StudioBatchSettings {
  if (typeof localStorage === "undefined") return { ...DEFAULT_STUDIO_BATCH };
  try {
    const raw = localStorage.getItem(STUDIO_BATCH_KEY);
    if (!raw) return { ...DEFAULT_STUDIO_BATCH };
    const parsed = JSON.parse(raw) as Partial<StudioBatchSettings>;
    return {
      multiplier: clampInt(Number(parsed.multiplier), 1, 20, 1),
      batchSize: clampInt(Number(parsed.batchSize), 1, 500, 10),
      shareBg: parsed.shareBg === true,
      reuseBg: boundedId(parsed.reuseBg),
      reusePattern: boundedId(parsed.reusePattern),
    };
  } catch {
    return { ...DEFAULT_STUDIO_BATCH };
  }
}

export function writeStudioBatch(next: StudioBatchSettings) {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(STUDIO_BATCH_KEY, JSON.stringify({
      multiplier: clampInt(next.multiplier, 1, 20, 1),
      batchSize: clampInt(next.batchSize, 1, 500, 10),
      shareBg: next.shareBg === true,
      reuseBg: boundedId(next.reuseBg),
      reusePattern: boundedId(next.reusePattern),
    }));
  } catch {
    /* quota */
  }
}
