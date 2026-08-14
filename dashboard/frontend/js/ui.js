import { refreshSelect } from "./custom-select.js";

const statusEl = document.getElementById("status");
const logStatusEl = document.getElementById("logStatus");

export function setStatus(text) {
  if (!statusEl) return;
  statusEl.textContent = text;
  statusEl.scrollTop = 0;
}

export function appendLog(text) {
  if (!logStatusEl) return;
  const ts = new Date().toLocaleTimeString();
  const prefix = logStatusEl.textContent ? "\n" : "";
  logStatusEl.textContent += `${prefix}[${ts}] ${text}`;
  logStatusEl.scrollTop = logStatusEl.scrollHeight;
}

export function setLogStatus(text) {
  if (!logStatusEl) return;
  logStatusEl.textContent = text;
  logStatusEl.scrollTop = 0;
}

export function chip(label, active, onClick) {
  const el = document.createElement("button");
  el.type = "button";
  el.className = `chip ${active ? "active" : ""}`;
  el.textContent = label;
  el.onclick = onClick;
  return el;
}

export function setSelectOptions(selectEl, values, selectedValue) {
  selectEl.innerHTML = "";
  values.forEach((value) => {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = value;
    if (selectedValue && selectedValue === value) opt.selected = true;
    selectEl.appendChild(opt);
  });
  refreshSelect(selectEl);
}

export function skeletonPersonaCard() {
  const card = document.createElement("div");
  card.className = "persona skeleton";
  card.innerHTML = `
    <div class="skeleton-line" style="width:70%"></div>
    <div class="skeleton-chips">
      <span class="skeleton-chip"></span>
      <span class="skeleton-chip"></span>
      <span class="skeleton-chip"></span>
      <span class="skeleton-chip"></span>
      <span class="skeleton-chip"></span>
    </div>
  `;
  return card;
}

export function skeletonRunCard() {
  const div = document.createElement("div");
  div.className = "run skeleton-run";
  div.innerHTML = `
    <div class="skeleton-line" style="width:50%"></div>
    <div class="skeleton-line" style="width:30%;margin-top:8px"></div>
    <div class="skeleton-line" style="width:80%;margin-top:12px"></div>
  `;
  return div;
}

export function skeletonBlock(lines = 3) {
  const wrap = document.createElement("div");
  wrap.className = "page-skeleton";
  wrap.setAttribute("aria-hidden", "true");
  const widths = ["70%", "90%", "55%", "80%", "40%"];
  for (let i = 0; i < lines; i++) {
    const line = document.createElement("div");
    line.className = "skeleton-line";
    line.style.width = widths[i % widths.length];
    line.style.marginTop = i ? "8px" : "0";
    wrap.appendChild(line);
  }
  return wrap;
}

export function showElementSkeleton(el, lines = 4) {
  if (!el) return;
  el.innerHTML = "";
  el.appendChild(skeletonBlock(lines));
}

export function skeletonLocalSection(title, tiles = 4) {
  const section = document.createElement("div");
  section.className = "local-skeleton";
  const heading = document.createElement("div");
  heading.className = "local-skeleton-title";
  heading.textContent = title;
  section.appendChild(heading);
  const grid = document.createElement("div");
  grid.className = "local-skeleton-grid";
  for (let i = 0; i < tiles; i++) {
    const tile = document.createElement("div");
    tile.className = "local-skeleton-tile";
    grid.appendChild(tile);
  }
  section.appendChild(grid);
  return section;
}

export function showGlobalLoading(msg = "Loading...") {
  let overlay = document.getElementById("globalLoadingOverlay");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.id = "globalLoadingOverlay";
    overlay.innerHTML = `<div class="global-loader"><span class="spinner"></span><p>${msg}</p></div>`;
    document.body.appendChild(overlay);
  } else {
    overlay.querySelector("p").textContent = msg;
    overlay.style.display = "";
  }
}

export function hideGlobalLoading() {
  const overlay = document.getElementById("globalLoadingOverlay");
  if (overlay) overlay.style.display = "none";
}

export function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}
