const SUN = "&#9728;";
const MOON = "&#9790;";

export function applyTheme(theme) {
  document.body.setAttribute("data-theme", theme);
  localStorage.setItem("dashboard_theme", theme);
  const btn = document.getElementById("themeToggle");
  if (btn) btn.innerHTML = theme === "dark" ? MOON : SUN;
}

export function initTheme() {
  const saved = localStorage.getItem("dashboard_theme");
  if (saved === "dark" || saved === "light") {
    applyTheme(saved);
    return;
  }
  const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  applyTheme(prefersDark ? "dark" : "light");
}

export function toggleTheme() {
  const current = document.body.getAttribute("data-theme") === "dark" ? "dark" : "light";
  applyTheme(current === "dark" ? "light" : "dark");
}

// Wire up the toggle button if present (single handler, no duplicates)
const themeToggleEl = document.getElementById("themeToggle");
if (themeToggleEl) {
  themeToggleEl.addEventListener("click", toggleTheme);
}
