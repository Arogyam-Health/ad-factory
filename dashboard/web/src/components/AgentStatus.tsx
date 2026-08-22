import { useEffect, useState } from "react";
import { fetchJSON } from "@/lib/api";
import { useAuth } from "@/lib/auth";

type Agent = {
  is_active?: boolean;
  last_heartbeat_at?: number;
  name?: string;
  agent_id?: string;
};

function statusOf(agent: Agent) {
  const elapsed = Date.now() / 1000 - (agent.last_heartbeat_at || 0);
  if (!agent.is_active) return "offline";
  if (elapsed > 180) return "offline";
  if (elapsed > 90) return "stale";
  return "online";
}

export function AgentStatus() {
  const { user } = useAuth();
  const [label, setLabel] = useState("Checking plate");
  const [tone, setTone] = useState("offline");

  useEffect(() => {
    if (!user.authenticated) {
      setLabel("Login to view agents");
      setTone("offline");
      return;
    }

    let alive = true;
    const tick = async () => {
      try {
        const agents = await fetchJSON<Agent[]>("/api/agents");
        if (!alive) return;
        const active = Array.isArray(agents) ? agents.filter((a) => a.is_active) : [];
        if (!active.length) {
          setLabel("No agents registered");
          setTone("offline");
          return;
        }
        const online = active.filter((a) => statusOf(a) === "online").length;
        const latest = active[0];
        const status = statusOf(latest);
        setTone(status);
        setLabel(`${online}/${active.length} agents ${status}`);
      } catch {
        if (alive) {
          setLabel("Agent status unavailable");
          setTone("offline");
        }
      }
    };

    void tick();
    const id = window.setInterval(() => void tick(), 15000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [user.authenticated]);

  return (
    <span className="agent-chip" aria-live="polite">
      <span className={`agent-dot ${tone}`} aria-hidden="true" />
      {label}
    </span>
  );
}
