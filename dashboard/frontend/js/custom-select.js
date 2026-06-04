const enhanced = new WeakMap();

function optionLabel(option) {
  return option?.textContent?.trim() || option?.value || "Select";
}

function closeAllCustomSelects(except = null) {
  document.querySelectorAll(".custom-select.is-open").forEach((root) => {
    if (root === except) return;
    root.classList.remove("is-open");
    root.querySelector(".custom-select-menu")?.classList.add("hidden");
    root.querySelector(".custom-select-btn")?.setAttribute("aria-expanded", "false");
  });
}

function sync(selectEl) {
  const root = enhanced.get(selectEl);
  if (!root) return;
  const btn = root.querySelector(".custom-select-btn");
  const menu = root.querySelector(".custom-select-menu");
  const grid = root.querySelector(".custom-select-grid");
  if (!btn || !menu || !grid) return;

  btn.disabled = selectEl.disabled;
  btn.textContent = optionLabel(selectEl.selectedOptions[0]) || "Select";
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
  const menu = document.createElement("div");
  menu.className = "custom-select-menu hidden";
  const grid = document.createElement("div");
  grid.className = "custom-select-grid";
  menu.appendChild(grid);
  root.append(btn, menu);
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
