const themeToggleEl = document.getElementById("themeToggle");

export function applyTheme(theme) {
  document.body.setAttribute("data-theme", theme);
  localStorage.setItem("dashboard_theme", theme);
  if (themeToggleEl) {
    themeToggleEl.textContent = theme === "dark" ? "Light mode" : "Dark mode";
  }
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

if (themeToggleEl) {
  themeToggleEl.addEventListener("click", () => {
    const current = document.body.getAttribute("data-theme") === "dark" ? "dark" : "light";
    applyTheme(current === "dark" ? "light" : "dark");
  });
}
