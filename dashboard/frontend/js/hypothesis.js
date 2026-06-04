import { state } from "./state.js";
import { refreshSelect } from "./custom-select.js";

const hypothesisTypeEl = document.getElementById("hypothesisType");
const hypothesisVariantEl = document.getElementById("hypothesisVariant");
const hypothesisVariantRowEl = document.getElementById("hypothesisVariantRow");
const hypothesisSummaryEl = document.getElementById("hypothesisSummary");

export function renderHypothesisUI() {
  if (!hypothesisTypeEl || !state.defaultData) return;
  const vars = state.defaultData.hypothesis?.variables || {};

  hypothesisTypeEl.innerHTML = "";
  Object.entries(vars).forEach(([key, defn]) => {
    const opt = document.createElement("option");
    opt.value = key;
    opt.textContent = defn.label || key;
    if (state.hypothesisConfig.type === key) opt.selected = true;
    hypothesisTypeEl.appendChild(opt);
  });

  updateHypothesisVariantOptions();
  refreshSelect(hypothesisTypeEl);
  updateHypothesisSummary();
}

export function updateHypothesisVariantOptions() {
  if (!hypothesisVariantEl || !state.defaultData) return;
  const type = hypothesisTypeEl.value;
  const vars = state.defaultData.hypothesis?.variables || {};
  const defn = vars[type];

  if (!defn || !defn.options || defn.options.length === 0) {
    hypothesisVariantRowEl?.classList.add("hidden");
    refreshSelect(hypothesisVariantEl);
    return;
  }

  hypothesisVariantRowEl?.classList.remove("hidden");
  hypothesisVariantEl.innerHTML = "";
  defn.options.forEach((opt) => {
    const option = document.createElement("option");
    option.value = opt.id;
    option.textContent = opt.label;
    if (state.hypothesisConfig.variant === opt.id) option.selected = true;
    hypothesisVariantEl.appendChild(option);
  });
  refreshSelect(hypothesisVariantEl);
}

export function updateHypothesisSummary() {
  if (!hypothesisSummaryEl || !state.defaultData) return;
  const type = hypothesisTypeEl.value;
  const vars = state.defaultData.hypothesis?.variables || {};
  const defn = vars[type];

  if (!defn || type === "none") {
    hypothesisSummaryEl.textContent = "No hypothesis style selected. Ads will generate normally.";
    return;
  }

  const variant = hypothesisVariantEl.value;
  const variantLabel = defn.options?.find((o) => o.id === variant)?.label || variant;
  hypothesisSummaryEl.textContent = `Style: ${defn.label} \u2014 ${variantLabel}`;
}

if (hypothesisTypeEl) {
  hypothesisTypeEl.addEventListener("change", () => {
    updateHypothesisVariantOptions();
    updateHypothesisSummary();
  });
}
if (hypothesisVariantEl) {
  hypothesisVariantEl.addEventListener("change", updateHypothesisSummary);
}
