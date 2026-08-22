import { NavLink, useLocation } from "react-router-dom";
import type { ReactNode } from "react";
import { useAuth } from "@/lib/auth";
import { useTheme } from "@/lib/theme";
import { Button } from "@/components/Button";
import { AgentStatus } from "@/components/AgentStatus";

const LINKS = [
  { to: "/", label: "Studio", index: "01" },
  { to: "/config", label: "Config", index: "02" },
  { to: "/organizations", label: "Teams", index: "03" },
  { to: "/traces", label: "Traces", index: "04" },
  { to: "/profile", label: "Profile", index: "05" },
];

const TITLES: Record<string, { eyebrow: string; title: string }> = {
  "/": { eyebrow: "Plate", title: "Generation studio" },
  "/config": { eyebrow: "Copy desk", title: "Config files" },
  "/organizations": { eyebrow: "Floor", title: "Teams" },
  "/traces": { eyebrow: "Proof", title: "LLM traces" },
  "/profile": { eyebrow: "Press pass", title: "Profile" },
  "/admin": { eyebrow: "Make ready", title: "Admin" },
};

export function Shell({ children }: { children: ReactNode }) {
  const { user, ready, logout } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const { pathname } = useLocation();
  const page = TITLES[pathname] || TITLES["/"];
  const name = user.display_name || user.email || "Guest";

  return (
    <div className="app">
      <aside className="rail">
        <div className="brand">
          <span className="brand-mark">AF</span>
          <span className="brand-name">Factory</span>
        </div>
        <nav className="nav" aria-label="Main">
          {LINKS.map((link) => (
            <NavLink key={link.to} to={link.to} end={link.to === "/"} className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
              <span>{link.label}</span>
              <span className="nav-index">{link.index}</span>
            </NavLink>
          ))}
          {user.is_super_admin ? (
            <NavLink to="/admin" className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
              <span>Admin</span>
              <span className="nav-index">06</span>
            </NavLink>
          ) : null}
        </nav>
        <p className="rail-foot">PRESS ROOM · BLACK PLATE</p>
      </aside>

      <header className="topbar">
        <div className="topbar-title">
          <p className="eyebrow">{page.eyebrow}</p>
          <h1>{page.title}</h1>
        </div>
        <div className="topbar-actions">
          <AgentStatus />
          <Button variant="ghost" onClick={toggleTheme} aria-label="Toggle theme">
            {theme === "dark" ? "Moon" : "Sun"}
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

      <main className="stage">{children}</main>
    </div>
  );
}
