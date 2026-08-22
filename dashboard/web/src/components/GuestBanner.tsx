import { useAuth } from "@/lib/auth";

export function GuestBanner() {
  const { user, ready } = useAuth();
  if (!ready || user.authenticated) return null;
  return (
    <div className="guest-banner">
      <p>
        You are browsing the generic plate — every file and rule is visible.
        Sign in to run jobs, save config, or manage teams.
      </p>
      <a className="btn btn-primary" href="/api/auth/google/login">
        Sign in
      </a>
    </div>
  );
}
