const cache = new Map();
const inflight = new Map();
const TTL_MS = 30_000;

function cacheKey(url, init) {
  return `${url}::${init?.method || "GET"}::${init?.body || ""}`;
}

export async function fetchJSON(url, init = {}) {
  const { noCache, ...fetchInit } = init;
  const bypassCache = noCache || fetchInit.cache === "no-store";
  const key = cacheKey(url, init);
  const method = String(fetchInit.method || "GET").toUpperCase();
  const cacheable = method === "GET" && !bypassCache;
  const cached = cache.get(key);
  if (cacheable && cached && Date.now() - cached.ts < TTL_MS) {
    return cached.data;
  }
  if (cacheable && inflight.has(key)) return inflight.get(key);
  const request = (async () => {
    const res = await fetch(url, {
      credentials: "same-origin",
      cache: "no-store",
      ...fetchInit,
    });
    const raw = await res.text();
    let data = null;
    if (raw) {
      try {
        data = JSON.parse(raw);
      } catch {
        data = raw;
      }
    }
    if (!res.ok) {
      const detail = data?.detail || data || res.statusText;
      // Name the endpoint: a bare status code makes a failing bootstrap request
      // impossible to identify from the status log alone.
      throw new Error(`${res.status} ${url}: ${detail}`);
    }
    if (cacheable) cache.set(key, { data, ts: Date.now() });
    return data;
  })();
  if (cacheable) inflight.set(key, request);
  try {
    return await request;
  } finally {
    if (cacheable) inflight.delete(key);
  }
}

export function clearCache(urlPattern) {
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
