const enhanced = new WeakMap();

function optionLabel(option) {
  return option?.textContent?.trim() || option?.value || "Select";
}

function restoreMenu(root) {
  const menu = root?._menuEl;
  if (!menu) return;
  menu.classList.remove("is-ported");
  menu.style.left = "";
  menu.style.top = "";
  menu.style.width = "";
  menu.style.maxWidth = "";
  menu.style.maxHeight = "";
  if (menu.parentElement !== root) root.appendChild(menu);
}

function portMenu(root, btn) {
  const menu = root._menuEl;
  if (!menu) return;
  const rect = btn.getBoundingClientRect();
  const width = Math.max(rect.width, 220);
  const left = Math.min(Math.max(8, rect.left), window.innerWidth - width - 8);
  const spaceBelow = window.innerHeight - rect.bottom - 12;
  const spaceAbove = rect.top - 12;
  const maxHeight = Math.max(160, spaceBelow >= 180 ? spaceBelow : Math.max(spaceAbove, spaceBelow));
  menu.classList.add("is-ported");
  menu.style.left = `${left}px`;
  menu.style.top = spaceBelow >= 180 || spaceBelow >= spaceAbove
    ? `${rect.bottom + 4}px`
    : `${Math.max(8, rect.top - Math.min(maxHeight, 420) - 4)}px`;
  menu.style.width = `${width}px`;
  menu.style.maxWidth = `${Math.min(width, window.innerWidth - 16)}px`;
  menu.style.maxHeight = `${Math.min(maxHeight, 420)}px`;
  document.body.appendChild(menu);
}

function closeAllCustomSelects(except = null) {
  document.querySelectorAll(".custom-select.is-open").forEach((root) => {
    if (root === except) return;
    root.classList.remove("is-open");
    root._menuEl?.classList.add("hidden");
    restoreMenu(root);
    root.querySelector(".custom-select-btn")?.setAttribute("aria-expanded", "false");
  });
}

function sync(selectEl) {
  const root = enhanced.get(selectEl);
  if (!root) return;
  const btn = root.querySelector(".custom-select-btn");
  const label = root.querySelector(".custom-select-label");
  const menu = root._menuEl;
  const grid = menu?.querySelector(".custom-select-grid");
  if (!btn || !label || !menu || !grid) return;

  btn.disabled = selectEl.disabled;
  const selected = selectEl.selectedOptions[0];
  label.textContent = optionLabel(selected) || "Select";
  btn.title = selected?.title || selected?.value || label.textContent;
  grid.innerHTML = "";

  const num = selectEl.options.length;
  const rootWidth = Math.max(220, root.getBoundingClientRect().width || 0);
  const maxColsForWidth = Math.max(1, Math.floor(rootWidth / 150));
  const cols = Math.max(1, Math.min(num, maxColsForWidth, Math.ceil(Math.sqrt(num))));
  grid.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;

  Array.from(selectEl.options).forEach((option) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "custom-select-item";
    item.dataset.value = option.value;
    item.textContent = optionLabel(option);
    item.title = option.title || option.value || item.textContent;
    item.disabled = option.disabled;
    item.classList.toggle("is-selected", option.selected);
    item.addEventListener("click", () => {
      selectEl.value = option.value;
      selectEl.dispatchEvent(new Event("change", { bubbles: true }));
      closeAllCustomSelects();
      sync(selectEl);
    });
    grid.appendChild(item);
  });
}

export function enhanceSelect(selectEl) {
  if (!selectEl || enhanced.has(selectEl)) {
    if (selectEl) sync(selectEl);
    return;
  }

  selectEl.classList.add("custom-select-native");
  const root = document.createElement("div");
  root.className = "custom-select";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "ghost-btn custom-select-btn";
  btn.setAttribute("aria-expanded", "false");
  const label = document.createElement("span");
  label.className = "custom-select-label";
  btn.appendChild(label);
  const menu = document.createElement("div");
  menu.className = "custom-select-menu hidden";
  const grid = document.createElement("div");
  grid.className = "custom-select-grid";
  menu.appendChild(grid);
  root.append(btn, menu);
  root._menuEl = menu;
  selectEl.insertAdjacentElement("afterend", root);
  enhanced.set(selectEl, root);

  btn.addEventListener("click", (event) => {
    event.stopPropagation();
    if (btn.disabled) return;
    const willOpen = menu.classList.contains("hidden");
    closeAllCustomSelects(root);
    root.classList.toggle("is-open", willOpen);
    menu.classList.toggle("hidden", !willOpen);
    btn.setAttribute("aria-expanded", willOpen ? "true" : "false");
    if (willOpen) portMenu(root, btn);
    else restoreMenu(root);
  });

  selectEl.addEventListener("change", () => sync(selectEl));
  sync(selectEl);
}

export function refreshSelect(selectEl) {
  if (!selectEl) return;
  enhanceSelect(selectEl);
  sync(selectEl);
}

export function enhanceAllSelects(root = document) {
  root.querySelectorAll("select").forEach((selectEl) => enhanceSelect(selectEl));
}

document.addEventListener("click", () => closeAllCustomSelects());
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeAllCustomSelects();
});
window.addEventListener("resize", () => closeAllCustomSelects());
window.addEventListener("scroll", (event) => {
  if (event.target?.closest?.(".custom-select-menu")) return;
  closeAllCustomSelects();
}, true);
