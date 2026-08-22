import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { fetchJSON } from "@/lib/api";
import { warmupCache } from "@/lib/prefetch";
import { clearLocalPairingSessions } from "@/lib/local-data-plane.js";

export type AuthUser = {
  authenticated: boolean;
  user_id: string;
  email: string;
  display_name: string;
  is_super_admin: boolean;
};

const LAST_ACCOUNT_KEY = "ad_factory_last_account";

const emptyUser: AuthUser = {
  authenticated: false,
  user_id: "",
  email: "",
  display_name: "",
  is_super_admin: false,
};

type AuthContextValue = {
  user: AuthUser;
  ready: boolean;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

function discardOtherAccountPairings(userId: string) {
  const previous = localStorage.getItem(LAST_ACCOUNT_KEY) || "";
      if (previous && previous !== userId) clearLocalPairingSessions();
  if (userId) localStorage.setItem(LAST_ACCOUNT_KEY, userId);
  else localStorage.removeItem(LAST_ACCOUNT_KEY);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser>(emptyUser);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    warmupCache(emptyUser);
    fetchJSON<Partial<AuthUser>>("/api/auth/status")
      .then((data) => {
        if (cancelled) return;
        const next: AuthUser = {
          authenticated: Boolean(data.authenticated),
          user_id: data.user_id || "",
          email: data.email || "",
          display_name: data.display_name || "",
          is_super_admin: Boolean(data.is_super_admin),
        };
        discardOtherAccountPairings(next.user_id);
        setUser(next);
        warmupCache(next);
      })
      .catch(() => {
        if (!cancelled) setUser(emptyUser);
      })
      .finally(() => {
        if (!cancelled) setReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      ready,
      logout: async () => {
        clearLocalPairingSessions();
        localStorage.removeItem(LAST_ACCOUNT_KEY);
        try {
          await fetchJSON("/api/auth/logout", { method: "POST" });
        } catch {
          /* session already gone */
        }
        window.location.reload();
      },
    }),
    [user, ready],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
