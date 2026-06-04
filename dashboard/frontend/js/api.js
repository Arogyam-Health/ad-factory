const cache = new Map();
const TTL_MS = 30_000;

function cacheKey(url, init) {
  return `${url}::${init?.method || "GET"}::${init?.body || ""}`;
}

export async function fetchJSON(url, init = {}) {
  const key = cacheKey(url, init);
  const cached = cache.get(key);
  if (cached && Date.now() - cached.ts < TTL_MS && init.method !== "POST") {
    return cached.data;
  }
  const res = await fetch(url, init);
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
  cache.set(key, { data, ts: Date.now() });
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
