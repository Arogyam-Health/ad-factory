import { fetchJSON } from "./api.js";

export const FORMATS = ["HERO", "BA", "TEST", "FEAT", "UGC"];
export const LANGUAGE_MODES = ["ALL", "EN", "HI", "HINGLISH"];

export const state = {
  defaultData: null,
  selectedGlobalFormats: new Set(["HERO"]),
  selectedLanguageMode: "EN",
  selectedPersonas: new Set(),
  personaFormatsByNumber: new Map(),
  selectedVisualArchetypesByFormat: {},
  modelsByProvider: {},
  runsData: [],
  currentRunIndex: 0,
  hypothesisConfig: { type: "none", variant: "" },
  currentServerType: "opencode",
  headlessModeEnabled: false,
  chromeProcessActive: false,
  isLoading: false,
  isPersonasLoading: false,
  isRunsLoading: false,
  runPollInterval: null,
};

export function getPersonaSelection() {
  return [...state.selectedPersonas];
}

export function getFormatsByPersona() {
  const map = {};
  if (!state.defaultData?.personas) return map;
  for (const persona of state.defaultData.personas) {
    const selected = state.personaFormatsByNumber.get(persona.number) || new Set();
    map[String(persona.number)] = FORMATS.filter((fmt) => selected.has(fmt));
  }
  return map;
}

export function getSelectedFormatsForSelectedPersonas() {
  const selected = new Set();
  if (!state.defaultData?.personas) return [];
  for (const persona of state.defaultData.personas) {
    if (!state.selectedPersonas.has(persona.number)) continue;
    const formatSet = state.personaFormatsByNumber.get(persona.number) || new Set();
    for (const fmt of FORMATS) {
      if (formatSet.has(fmt)) selected.add(fmt);
    }
  }
  return FORMATS.filter((fmt) => selected.has(fmt));
}

export function initPersonaState(personas = []) {
  state.selectedPersonas = new Set();
  state.personaFormatsByNumber = new Map();
  state.selectedVisualArchetypesByFormat = {};
  personas.forEach((persona) => {
    state.personaFormatsByNumber.set(persona.number, new Set());
  });
}

export function getHypothesisConfig() {
  const type = document.getElementById("hypothesisType")?.value || "none";
  if (type === "none") return { type: "none", variant: "" };
  return { type, variant: document.getElementById("hypothesisVariant")?.value || "" };
}

export async function loadDefaults() {
  state.isPersonasLoading = true;
  try {
    const [defaultsResult, configResult] = await Promise.allSettled([
      fetchJSON("/api/defaults"),
      fetchJSON("/api/config/effective"),
    ]);
    if (defaultsResult.status !== "fulfilled") throw defaultsResult.reason;
    state.defaultData = defaultsResult.value;

    // Overlay user's custom persona seeds from MongoDB config
    try {
      const cfg = configResult.status === "fulfilled" ? configResult.value : null;
      const rawSeeds = cfg?.config?.persona_seeds;
      if (rawSeeds) {
        const seeds = typeof rawSeeds === "string" ? JSON.parse(rawSeeds) : rawSeeds;
        if (Array.isArray(seeds) && seeds.length) {
          const userPersonas = seeds.map(e => ({
            number: parseInt(e.persona_number || e.number, 10),
            name: String(e.persona_name || e.name || `Persona ${e.persona_number || e.number}`),
          })).filter(p => p.number);
          if (userPersonas.length) {
            state.defaultData.personas = userPersonas;
          }
        }
      }
    } catch {}

    initPersonaState(state.defaultData?.personas || []);
    return state.defaultData;
  } finally {
    state.isPersonasLoading = false;
  }
}

export async function loadRuns() {
  state.isRunsLoading = true;
  try {
    const data = await fetchJSON("/api/runs");
    state.runsData = data.runs || [];
    state.currentRunIndex = 0;
    return state.runsData;
  } finally {
    state.isRunsLoading = false;
  }
}
