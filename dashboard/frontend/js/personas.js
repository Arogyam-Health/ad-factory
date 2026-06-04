import { state, FORMATS, getSelectedFormatsForSelectedPersonas } from "./state.js";
import { chip, skeletonPersonaCard } from "./ui.js";
import { enhanceSelect } from "./custom-select.js";

const personaListEl = document.getElementById("personaList");
const globalFormatsEl = document.getElementById("globalFormats");
const formatPatternsEl = document.getElementById("formatPatterns");

export function renderPersonas() {
  if (!personaListEl || !state.defaultData?.personas) return;
  personaListEl.innerHTML = "";

  const frag = document.createDocumentFragment();
  state.defaultData.personas.forEach((persona, index) => {
    frag.appendChild(buildPersonaCard(persona, index));
  });
  personaListEl.appendChild(frag);
}

export function showPersonaSkeletons(count = 8) {
  if (!personaListEl) return;
  personaListEl.innerHTML = "";
  const frag = document.createDocumentFragment();
  for (let i = 0; i < count; i++) frag.appendChild(skeletonPersonaCard());
  personaListEl.appendChild(frag);
}

export function renderGlobalFormats() {
  if (!globalFormatsEl) return;
  globalFormatsEl.innerHTML = "";
  FORMATS.forEach((fmt) => {
    globalFormatsEl.appendChild(chip(fmt, state.selectedGlobalFormats.has(fmt), () => {
      if (state.selectedGlobalFormats.has(fmt)) state.selectedGlobalFormats.delete(fmt);
      else state.selectedGlobalFormats.add(fmt);
      renderGlobalFormats();
      renderFormatPatterns();
    }));
  });

  const applyBtn = document.createElement("button");
  applyBtn.type = "button";
  applyBtn.className = "ghost-btn";
  applyBtn.textContent = "Apply to selected personas";
    applyBtn.onclick = () => {
    const selectedPersonas = state.selectedPersonas;
    if (!selectedPersonas.size) {
      import("./ui.js").then((m) => m.appendLog("Select at least one persona to apply global formats."));
      return;
    }
    for (const persona of state.defaultData.personas) {
      if (!selectedPersonas.has(persona.number)) continue;
      const set = state.personaFormatsByNumber.get(persona.number) || new Set();
      for (const fmt of FORMATS) {
        if (state.selectedGlobalFormats.has(fmt)) set.add(fmt);
        else set.delete(fmt);
      }
      state.personaFormatsByNumber.set(persona.number, set);
    }
    renderPersonas();
    renderFormatPatterns();
  };
  globalFormatsEl.appendChild(applyBtn);
}

export function renderFormatPatterns() {
  if (!formatPatternsEl) return;
  formatPatternsEl.innerHTML = "";
  const selectedFormats = getSelectedFormatsForSelectedPersonas();
  if (!selectedFormats.length) {
    const hint = document.createElement("p");
    hint.className = "hint";
    hint.textContent = "Select personas and formats to choose visual patterns.";
    formatPatternsEl.appendChild(hint);
    return;
  }

  const title = document.createElement("label");
  title.textContent = "Visual pattern per selected format";
  formatPatternsEl.appendChild(title);

  const grid = document.createElement("div");
  grid.className = "format-pattern-grid";
  const patterns = state.defaultData?.format_patterns || {};
  selectedFormats.forEach((fmt) => {
    const block = document.createElement("div");
    block.className = `format-pattern-block fmt-${fmt.toLowerCase()}`;

    const name = document.createElement("div");
    name.className = "format-pattern-title";
    name.textContent = fmt;

    const select = document.createElement("select");
    const options = patterns[fmt] || [];
    const auto = document.createElement("option");
    auto.value = "";
    auto.textContent = "Auto rotate pattern";
    select.appendChild(auto);
    options.forEach((item) => {
      const opt = document.createElement("option");
      opt.value = item.id;
      opt.textContent = `${item.label} (${item.id})`;
      select.appendChild(opt);
    });
    select.value = state.selectedVisualArchetypesByFormat[fmt] || "";
    select.addEventListener("change", () => {
      if (select.value) state.selectedVisualArchetypesByFormat[fmt] = select.value;
      else delete state.selectedVisualArchetypesByFormat[fmt];
    });

    block.append(name, select);
    enhanceSelect(select);
    grid.appendChild(block);
  });
  formatPatternsEl.appendChild(grid);
}

export function renderLanguageModes() {
  const languageModesEl = document.getElementById("languageModes");
  if (!languageModesEl) return;
  languageModesEl.innerHTML = "";
  const modes = ["ALL", "EN", "HI", "HINGLISH"];
  modes.forEach((mode) => {
    languageModesEl.appendChild(chip(mode, state.selectedLanguageMode === mode, () => {
      state.selectedLanguageMode = mode;
      renderLanguageModes();
    }));
  });
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function buildPersonaCard(persona, index) {
  const card = document.createElement("div");
  card.className = "persona";
  card.style.animationDelay = `${Math.min(index * 0.015, 0.22)}s`;

  const personaChecked = state.selectedPersonas.has(persona.number);
  const formatSet = state.personaFormatsByNumber.get(persona.number) || new Set();
  const main = document.createElement("label");
  main.innerHTML = `<input class="persona-check" type="checkbox" value="${persona.number}" ${personaChecked ? "checked" : ""}/> <span class="persona-number">${persona.number}.</span> <span class="persona-name">${escapeHtml(persona.name)}</span>`;
  const personaInput = main.querySelector(".persona-check");
  if (personaInput) {
    personaInput.addEventListener("change", () => {
      if (personaInput.checked) state.selectedPersonas.add(persona.number);
      else state.selectedPersonas.delete(persona.number);
      renderFormatPatterns();
    });
  }
  card.appendChild(main);

  const chips = document.createElement("div");
  chips.className = "chips format-chip-group";
  FORMATS.forEach((fmt) => {
    const lbl = document.createElement("label");
    lbl.className = `chip chip-format fmt-${fmt.toLowerCase()}`;

    const input = document.createElement("input");
    input.type = "checkbox";
    input.className = "format-check";
    input.checked = formatSet.has(fmt);

    const text = document.createElement("span");
    text.textContent = fmt;

    const syncState = () => lbl.classList.toggle("checked", input.checked);
    input.addEventListener("change", () => {
      const set = state.personaFormatsByNumber.get(persona.number) || new Set();
      if (input.checked) set.add(fmt);
      else set.delete(fmt);
      state.personaFormatsByNumber.set(persona.number, set);
      syncState();
      renderFormatPatterns();
    });
    syncState();

    lbl.append(input, text);
    chips.appendChild(lbl);
  });
  card.appendChild(chips);

  card.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    if (target.closest("input") || target.closest("button") || target.closest("label")) return;
    const checkbox = card.querySelector(".persona-check");
    if (checkbox instanceof HTMLInputElement) {
      checkbox.checked = !checkbox.checked;
      checkbox.dispatchEvent(new Event("change"));
    }
  });

  return card;
}
