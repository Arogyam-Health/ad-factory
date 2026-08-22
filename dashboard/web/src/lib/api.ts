const cache = new Map<string, { data: unknown; ts: number }>();
const inflight = new Map<string, Promise<unknown>>();
const TTL_MS = 30_000;

type FetchInit = RequestInit & { noCache?: boolean };

function cacheKey(url: string, init: FetchInit) {
  return `${url}::${init?.method || "GET"}::${typeof init?.body === "string" ? init.body : ""}`;
}

export async function fetchJSON<T = unknown>(url: string, init: FetchInit = {}): Promise<T> {
  const { noCache, ...fetchInit } = init;
  const bypassCache = noCache || fetchInit.cache === "no-store";
  const key = cacheKey(url, init);
  const method = String(fetchInit.method || "GET").toUpperCase();
  const cacheable = method === "GET" && !bypassCache;
  const cached = cache.get(key);
  if (cacheable && cached && Date.now() - cached.ts < TTL_MS) {
    return cached.data as T;
  }
  if (cacheable && inflight.has(key)) return inflight.get(key) as Promise<T>;

  const request = (async () => {
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
      const detail =
        data && typeof data === "object" && "detail" in data
          ? String((data as { detail: unknown }).detail)
          : data || res.statusText;
      throw new Error(`${res.status} ${url}: ${detail}`);
    }
    if (cacheable) cache.set(key, { data, ts: Date.now() });
    return data as T;
  })();

  if (cacheable) inflight.set(key, request);
  try {
    return (await request) as T;
  } finally {
    if (cacheable) inflight.delete(key);
  }
}

export function clearCache(urlPattern?: string) {
  for (const key of cache.keys()) {
    if (!urlPattern || key.includes(urlPattern)) cache.delete(key);
  }
  for (const key of inflight.keys()) {
    if (!urlPattern || key.includes(urlPattern)) inflight.delete(key);
  }
}

export function invalidateRuns() {
  clearCache("/api/runs");
}

export function invalidateDefaults() {
  clearCache("/api/defaults");
}
