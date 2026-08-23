import { fetchJSON } from "@/lib/api";
import type { AuthUser } from "@/lib/auth";

const GUEST_URLS = ["/api/public/studio", "/api/auth/status"];

const AUTH_URLS = [
  "/api/defaults",
  "/api/config/effective",
  "/api/config/sources",
  "/api/config/persona-summary",
  "/api/runs?flow=structured",
  "/api/runs?flow=reference",
  "/api/orgs/me",
  "/api/llm-traces",
  "/api/user/provider-config",
];

const ADMIN_URLS = [
  "/api/admin/overview",
  "/api/admin/stats",
  "/api/admin/health",
  "/api/admin/readiness",
  "/api/admin/users?page=1&per_page=50",
  "/api/admin/orgs?page=1&per_page=50",
  "/api/admin/configs?page=1&per_page=50",
  "/api/admin/audit-logs?page=1&per_page=50",
];

const ROUTE_PREFETCH: Record<string, { guest: string[]; auth: string[]; admin?: string[] }> = {
  "/": {
    guest: ["/api/public/studio"],
    auth: ["/api/public/studio", "/api/defaults", "/api/runs?flow=structured", "/api/runs?flow=reference", "/api/config/persona-summary"],
  },
  "/config": {
    guest: ["/api/public/studio"],
    auth: ["/api/public/studio", "/api/config/effective", "/api/config/sources"],
  },
  "/organizations": {
    guest: [],
    auth: ["/api/orgs/me"],
  },
  "/traces": {
    guest: [],
    auth: ["/api/llm-traces"],
  },
  "/profile": {
    guest: [],
    auth: ["/api/orgs/me", "/api/user/provider-config"],
  },
  "/admin": {
    guest: [],
    auth: [],
    admin: ADMIN_URLS,
  },
};

function orgScopedUrls(user: AuthUser, path: string): string[] {
  if (!user.authenticated || !user.user_id) return [];
  if (path !== "/" && path !== "/config") return [];
  const orgId = localStorage.getItem(`adFactoryStudioOrg:${user.user_id}`) || "";
  if (!orgId || orgId === "personal") return [];
  return [
    `/api/config/effective?org_id=${encodeURIComponent(orgId)}`,
    `/api/config/persona-summary?org_id=${encodeURIComponent(orgId)}`,
  ];
}

function collectUrls(user: AuthUser, path?: string): string[] {
  if (path) {
    const spec = ROUTE_PREFETCH[path];
    if (!spec) return [];
    const urls = [...spec.guest];
    if (user.authenticated) urls.push(...spec.auth);
    if (user.is_super_admin && spec.admin) urls.push(...spec.admin);
    urls.push(...orgScopedUrls(user, path));
    return [...new Set(urls)];
  }
  const urls = [
    ...GUEST_URLS,
    ...(user.authenticated ? AUTH_URLS : []),
    ...(user.is_super_admin ? ADMIN_URLS : []),
    ...orgScopedUrls(user, "/"),
  ];
  return [...new Set(urls)];
}

function warm(urls: string[]) {
  if (!urls.length) return;
  void Promise.allSettled(urls.map((url) => fetchJSON(url).catch(() => null)));
}

export function warmupCache(user: AuthUser) {
  warm(collectUrls(user));
}

export function prefetchRoute(path: string, user: AuthUser) {
  warm(collectUrls(user, path));
}
