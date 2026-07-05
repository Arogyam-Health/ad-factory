import { fetchJSON } from "./api.js";

let authState = { authenticated: false, user_id: "", email: "", display_name: "" };
let authCheckDone = false;

export function isAuthenticated() {
  return authState.authenticated;
}

export function getAuthUser() {
  return authState;
}

export async function checkAuth() {
  if (authCheckDone) return authState;
  try {
    const data = await fetchJSON("/api/auth/status");
    authState = {
      authenticated: data.authenticated || false,
      user_id: data.user_id || "",
      email: data.email || "",
      display_name: data.display_name || "",
      is_super_admin: data.is_super_admin || false,
    };
  } catch {
    authState = { authenticated: false, user_id: "", email: "", display_name: "" };
  }
  authCheckDone = true;
  return authState;
}

export async function initAuth() {
  const bar = document.getElementById("authBar");
  if (!bar) return;

  await checkAuth();

  if (authState.authenticated) {
    const name = authState.display_name || authState.email || authState.user_id;
    bar.innerHTML = `
      <span class="auth-user-info">
        <span class="auth-user-avatar">${name.charAt(0).toUpperCase()}</span>
        <span class="auth-user-name">${escapeHtml(name)}</span>
      </span>
      <button id="logoutBtn" class="ghost-btn auth-btn" type="button">Logout</button>
    `;
    document.getElementById("logoutBtn")?.addEventListener("click", async () => {
      try {
        await fetchJSON("/api/auth/logout", { method: "POST" });
      } catch {}
      window.location.reload();
    });
  } else {
    bar.innerHTML = `
      <span class="auth-user-info">
        <span class="auth-user-name muted">Not logged in</span>
      </span>
      <a href="/api/auth/google/login" class="ghost-btn auth-btn login-btn" type="button">Login with Google</a>
    `;
  }
  bar.classList.remove("auth-hidden");
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}
