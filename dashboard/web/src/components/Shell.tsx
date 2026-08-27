import { NavLink, useLocation } from "react-router-dom";
import { useEffect, type ReactNode } from "react";
import { useAuth } from "@/lib/auth";
import { attachScrollChain } from "@/lib/scroll-chain";
import { useTheme } from "@/lib/theme";
import { prefetchRoute } from "@/lib/prefetch";
import { Button } from "@/components/Button";
import { AgentStatus } from "@/components/AgentStatus";
import { GuestBanner } from "@/components/GuestBanner";

const LINKS = [
  { to: "/", label: "Studio", index: "01" },
  { to: "/config", label: "Config", index: "02" },
  { to: "/guide", label: "Guide", index: "03" },
  { to: "/organizations", label: "Teams", index: "04" },
  { to: "/traces", label: "Traces", index: "05" },
  { to: "/profile", label: "Profile", index: "06" },
];

const TITLES: Record<string, { eyebrow: string; title: string }> = {
  "/": { eyebrow: "Plate", title: "Generation studio" },
  "/config": { eyebrow: "Copy desk", title: "Config files" },
  "/guide": { eyebrow: "Copy desk", title: "Operator guide" },
  "/organizations": { eyebrow: "Floor", title: "Teams" },
  "/traces": { eyebrow: "Proof", title: "LLM traces" },
  "/profile": { eyebrow: "Press pass", title: "Profile" },
  "/admin": { eyebrow: "Make ready", title: "Admin" },
};

export function Shell({ children }: { children: ReactNode }) {
  const { user, ready, logout } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const { pathname } = useLocation();
  const page = TITLES[pathname]
    || ((pathname.startsWith("/docs/") || pathname.endsWith(".md"))
      ? { eyebrow: "Copy desk", title: "Docs" }
      : TITLES["/"]);
  const name = user.display_name || user.email || "Guest";

  useEffect(() => attachScrollChain(), []);

  return (
    <div className="app">
      <header className="masthead">
        <div className="brand">
          <span className="brand-mark">AF</span>
          <span className="brand-name">Factory</span>
        </div>
        <nav className="nav" aria-label="Main">
          {LINKS.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.to === "/"}
              className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
              onMouseEnter={() => prefetchRoute(link.to, user)}
              onFocus={() => prefetchRoute(link.to, user)}
              onTouchStart={() => prefetchRoute(link.to, user)}
            >
              <span>{link.label}</span>
              <span className="nav-index">{link.index}</span>
            </NavLink>
          ))}
          {user.is_super_admin ? (
            <NavLink
              to="/admin"
              className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
              onMouseEnter={() => prefetchRoute("/admin", user)}
              onFocus={() => prefetchRoute("/admin", user)}
              onTouchStart={() => prefetchRoute("/admin", user)}
            >
              <span>Admin</span>
              <span className="nav-index">07</span>
            </NavLink>
          ) : null}
        </nav>
        <div className="topbar-title">
          <p className="eyebrow">{page.eyebrow}</p>
          <h1>{page.title}</h1>
        </div>
        <div className="topbar-actions">
          <AgentStatus />
          <Button
            variant="ghost"
            onClick={toggleTheme}
            aria-label={theme === "dark" ? "Switch to light" : "Switch to dark"}
          >
            {theme === "dark" ? "light" : "dark"}
          </Button>
          {ready && user.authenticated ? (
            <>
              <span className="user-chip">
                <span className="avatar" aria-hidden="true">
                  {name.charAt(0).toUpperCase()}
                </span>
                {name.split(" ")[0]}
              </span>
              <Button variant="ghost" onClick={() => void logout()}>
                Sign out
              </Button>
            </>
          ) : (
            <a className="btn btn-primary" href="/api/auth/google/login">
              Sign in
            </a>
          )}
        </div>
      </header>

      <main className="stage">
        <GuestBanner />
        {children}
      </main>
    </div>
  );
}
