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

export function warmupCache(user: AuthUser) {
  const urls = [
    ...GUEST_URLS,
    ...(user.authenticated ? AUTH_URLS : []),
    ...(user.is_super_admin ? ADMIN_URLS : []),
  ];
  if (user.authenticated) {
    const orgId = localStorage.getItem(`adFactoryStudioOrg:${user.user_id}`) || "";
    if (orgId && orgId !== "personal") {
      urls.push(`/api/config/effective?org_id=${encodeURIComponent(orgId)}`);
      urls.push(`/api/config/persona-summary?org_id=${encodeURIComponent(orgId)}`);
    }
  }
  void Promise.allSettled(urls.map((url) => fetchJSON(url)));
}
