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
