import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { fetchJSON } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/Button";
import { SkeletonLines } from "@/components/Skeleton";

type Invite = {
  org_name?: string;
  role?: string;
  email?: string;
  expires_at?: number;
  org_domain?: string;
};

function alreadyAccepted(err: unknown) {
  const text = String(err || "");
  return text.includes("already been accepted") || text.includes("already a member");
}

export function InvitePage() {
  const { token = "" } = useParams();
  const navigate = useNavigate();
  const { user, ready } = useAuth();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [invite, setInvite] = useState<Invite | null>(null);
  const acceptOnce = useRef(false);

  const loginUrl = (email: string) =>
    `/api/auth/google/login?login_hint=${encodeURIComponent(email)}&return_to=${encodeURIComponent(`/invite/${token}`)}`;

  function goHome() {
    navigate("/", { replace: true });
  }

  async function accept() {
    if (acceptOnce.current) return;
    acceptOnce.current = true;
    try {
      await fetchJSON(`/api/invites/${token}/accept`, { method: "POST", noCache: true });
      goHome();
    } catch (err) {
      if (alreadyAccepted(err)) {
        goHome();
        return;
      }
      acceptOnce.current = false;
      setError(String(err));
    }
  }

  useEffect(() => {
    if (!token) {
      setError("Invalid invite link.");
      setLoading(false);
      return;
    }
    fetchJSON<{ valid?: boolean; status?: string; message?: string; invite?: Invite }>(
      `/api/invites/${token}`,
      { noCache: true },
    )
      .then((data) => {
        if (data.status === "accepted") {
          goHome();
          return;
        }
        if (!data.valid) {
          setError(data.message || "Invite is no longer valid.");
          return;
        }
        setInvite(data.invite || null);
      })
      .catch((err) => {
        if (alreadyAccepted(err)) {
          goHome();
          return;
        }
        const msg = String(err);
        setError(msg.includes("404") ? "Invite not found or invalid." : msg);
      })
      .finally(() => setLoading(false));
  }, [token]);

  useEffect(() => {
    if (ready && user.authenticated && invite && user.email === invite.email) {
      void accept();
    }
  }, [ready, user.authenticated, user.email, invite]);

  const mismatch = Boolean(user.authenticated && invite?.email && user.email && user.email !== invite.email);

  return (
    <div className="page-gate" style={{ maxWidth: 480 }}>
      <p className="eyebrow">Invitation</p>
      <h1 style={{ margin: "8px 0 16px" }}>Organization invite</h1>
      {loading ? <SkeletonLines lines={5} /> : error ? (
        <p style={{ color: "var(--danger)" }}>{error}</p>
      ) : invite ? (
        <>
          <p className="hint">You've been invited to join</p>
          <h2 style={{ margin: "8px 0" }}>{invite.org_name}</h2>
          <p className="hint">{invite.role} · {invite.email}</p>
          <p className="hint">
            Expires {invite.expires_at ? new Date(invite.expires_at * 1000).toLocaleDateString() : "?"}
          </p>
          {mismatch ? (
            <div style={{ marginTop: 16 }}>
              <p className="hint">You're logged in as {user.email}. This invite is for {invite.email}.</p>
              <a className="btn btn-primary" href={loginUrl(invite.email || "")}>Switch account</a>
            </div>
          ) : user.authenticated ? (
            <Button variant="primary" onClick={() => void accept()}>Accept invite</Button>
          ) : (
            <a className="btn btn-primary" href={loginUrl(invite.email || "")}>
              Login as {invite.email}
            </a>
          )}
        </>
      ) : null}
    </div>
  );
}
