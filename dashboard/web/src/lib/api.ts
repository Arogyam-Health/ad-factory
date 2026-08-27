const FRESH_MS = 8_000;
const STALE_MS = 15 * 60_000;
const STORAGE_KEY = "adFactoryUiCache";

type Entry = { data: unknown; ts: number };
type FetchInit = RequestInit & { noCache?: boolean; retry?: number };

const cache = new Map<string, Entry>();
const inflight = new Map<string, Promise<unknown>>();

function cacheKey(url: string, init: FetchInit) {
  return `${url}::${init?.method || "GET"}::${typeof init?.body === "string" ? init.body : ""}`;
}

function loadPersistent() {
  if (typeof sessionStorage === "undefined") return;
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw) as Record<string, Entry>;
    for (const [key, value] of Object.entries(parsed || {})) {
      if (value && Date.now() - value.ts < STALE_MS) cache.set(key, value);
    }
  } catch {
    /* ignore corrupt cache */
  }
}

function persist() {
  if (typeof sessionStorage === "undefined") return;
  try {
    const out: Record<string, Entry> = {};
    for (const [key, value] of cache.entries()) {
      if (key.includes("::GET::") && Date.now() - value.ts < STALE_MS) out[key] = value;
    }
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(out));
  } catch {
    /* quota */
  }
}

loadPersistent();

function errorDetail(res: Response, data: unknown): string {
  if (data && typeof data === "object" && "detail" in data) {
    return String((data as { detail: unknown }).detail);
  }
  if (typeof data === "string") {
    const trimmed = data.trim();
    if (trimmed.startsWith("<") || trimmed.includes("<!DOCTYPE")) {
      return res.statusText || "upstream error";
    }
    return trimmed.length > 180 ? `${trimmed.slice(0, 180)}…` : trimmed;
  }
  return res.statusText || "request failed";
}

async function networkFetch<T>(
  url: string,
  fetchInit: RequestInit,
  key: string,
  cacheable: boolean,
  retries = 0,
): Promise<T> {
  const res = await fetch(url, {
    credentials: "same-origin",
    cache: "no-store",
    ...fetchInit,
  });
  const raw = await res.text();
  let data: unknown = null;
  if (raw) {
    try {
      data = JSON.parse(raw);
    } catch {
      data = raw;
    }
  }
  if (!res.ok) {
    if (retries > 0 && [502, 503, 504].includes(res.status)) {
      await new Promise((resolve) => window.setTimeout(resolve, 800));
      return networkFetch<T>(url, fetchInit, key, cacheable, retries - 1);
    }
    throw new Error(`${res.status} ${url}: ${errorDetail(res, data)}`);
  }
  if (cacheable) {
    cache.set(key, { data, ts: Date.now() });
    persist();
  }
  return data as T;
}

export async function fetchJSON<T = unknown>(url: string, init: FetchInit = {}): Promise<T> {
  const { noCache, retry = 0, ...fetchInit } = init;
  const bypassCache = noCache || fetchInit.cache === "no-store" || url.includes("/api/auth/") || url.includes("/api/invites/");
  const key = cacheKey(url, init);
  const method = String(fetchInit.method || "GET").toUpperCase();
  const cacheable = method === "GET" && !bypassCache;
  const cached = cache.get(key);

  if (cacheable && cached) {
    const age = Date.now() - cached.ts;
    if (age < FRESH_MS) return cached.data as T;
    if (age < STALE_MS) {
      if (!inflight.has(key)) {
        const refresh = networkFetch<T>(url, fetchInit, key, true).finally(() => {
          inflight.delete(key);
        });
        inflight.set(key, refresh);
      }
      return cached.data as T;
    }
  }

  if (cacheable && inflight.has(key)) return inflight.get(key) as Promise<T>;

  const request = networkFetch<T>(url, fetchInit, key, cacheable, retry);
  if (cacheable) inflight.set(key, request);
  try {
    return await request;
  } finally {
    if (cacheable) inflight.delete(key);
  }
}

export function peekCache<T = unknown>(url: string): T | undefined {
  const key = cacheKey(url, {});
  return cache.get(key)?.data as T | undefined;
}

export function primeCache(url: string, data: unknown) {
  cache.set(cacheKey(url, {}), { data, ts: Date.now() });
  persist();
}

export function clearCache(urlPattern?: string) {
  for (const key of cache.keys()) {
    if (!urlPattern || key.includes(urlPattern)) cache.delete(key);
  }
  for (const key of inflight.keys()) {
    if (!urlPattern || key.includes(urlPattern)) inflight.delete(key);
  }
  persist();
}

export function invalidateRuns() {
  clearCache("/api/runs");
}

export function invalidateDefaults() {
  clearCache("/api/defaults");
  clearCache("/api/public/studio");
  clearCache("/api/config/");
}
