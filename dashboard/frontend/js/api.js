const cache = new Map();
const TTL_MS = 30_000;

function cacheKey(url, init) {
  return `${url}::${init?.method || "GET"}::${init?.body || ""}`;
}

export async function fetchJSON(url, init = {}) {
  const { noCache, ...fetchInit } = init;
  const bypassCache = noCache || fetchInit.cache === "no-store";
  const key = cacheKey(url, init);
  const cached = cache.get(key);
  if (!bypassCache && cached && Date.now() - cached.ts < TTL_MS && fetchInit.method !== "POST") {
    return cached.data;
  }
  const res = await fetch(url, fetchInit);
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
    throw new Error(`${res.status}: ${detail}`);
  }
  if (!bypassCache) cache.set(key, { data, ts: Date.now() });
  return data;
}

export function clearCache(urlPattern) {
  for (const key of cache.keys()) {
    if (!urlPattern || key.includes(urlPattern)) cache.delete(key);
  }
}

export function invalidateRuns() {
  clearCache("/api/runs");
}

export function invalidateDefaults() {
  clearCache("/api/defaults");
}
