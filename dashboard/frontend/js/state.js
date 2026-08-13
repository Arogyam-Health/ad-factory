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
  missingLocalRuns: [],
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

function mapPersonaSummary(personas) {
  if (!Array.isArray(personas)) return [];
  return personas.map((persona) => ({
    number: Number(persona.number),
    name: String(persona.name || `Persona ${persona.number}`),
  })).filter((persona) => persona.number);
}

function studioDefaultsFromPersonas(personas) {
  return {
    personas,
    formats: ["HERO", "BA", "TEST", "FEAT", "UGC"],
    format_patterns: {},
    image_sources: [],
    input_images: [],
    product_doc: {},
    default_files: {
      product_info: "MongoDB: product_master_doc",
      playbook: "MongoDB dashboard config",
    },
    opencode: {
      api_url: "",
      providers: [],
      models_by_provider: {},
      default_model: "",
    },
    provider: {
      current: "opencode",
      google_api_key: false,
      opencode_api_url: "",
      google_model: "",
      google_models: [],
    },
    hypothesis: {
      variables: {
        none: {
          label: "No hypothesis test",
          description: "Generate ads normally without controlled A/B testing.",
          options: [],
        },
      },
      default: { type: "none", variant: "" },
    },
    batch_size: 10,
  };
}

export async function loadDefaults() {
  state.isPersonasLoading = true;
  try {
    const [defaultsResult, personaResult] = await Promise.allSettled([
      fetchJSON("/api/defaults", { cache: "no-store" }),
      fetchJSON("/api/config/persona-summary", { cache: "no-store" }),
    ]);
    const summaryPersonas = mapPersonaSummary(
      personaResult.status === "fulfilled" ? personaResult.value?.personas : null,
    );
    if (defaultsResult.status === "fulfilled") {
      state.defaultData = defaultsResult.value;
    } else if (summaryPersonas.length) {
      state.defaultData = studioDefaultsFromPersonas(summaryPersonas);
    } else {
      throw defaultsResult.reason;
    }

    if (summaryPersonas.length) {
      state.defaultData.personas = summaryPersonas;
    }

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
