import { fetchJSON } from "./api.js";
import { setStatus } from "./ui.js";

const els = {
  status: document.getElementById("configStatus"),
  productMaster: document.getElementById("cfgProductMaster"),
  startingPrompt: document.getElementById("cfgStartingPrompt"),
  personaSeeds: document.getElementById("cfgPersonaSeeds"),
  copyTemplates: document.getElementById("cfgCopyTemplates"),
  saveBtn: document.getElementById("saveUserConfig"),
  resetBtn: document.getElementById("resetUserConfig"),
  saveStatus: document.getElementById("configSaveStatus"),
};

let currentConfig = {};

export async function loadUserConfig() {
  if (!els.status) return;
  els.status.textContent = "Loading your config...";
  try {
    const data = await fetchJSON("/api/user/config");
    currentConfig = data.config || {};
    fillFields(currentConfig);
    const hasCustom = data.has_custom;
    els.status.textContent = hasCustom
      ? "Using your saved config from MongoDB."
      : "Using generic defaults. Save to create your custom config.";
  } catch (err) {
    els.status.textContent = `Failed to load config: ${String(err)}`;
  }
}

function fillFields(cfg) {
  if (els.productMaster) els.productMaster.value = cfg.product_master_doc || "";
  if (els.startingPrompt) els.startingPrompt.value = cfg.starting_prompt || "";
  if (els.personaSeeds) els.personaSeeds.value = cfg.persona_seeds || "";
  if (els.copyTemplates) els.copyTemplates.value = cfg.copy_prompt_templates || "";
}

function gatherFields() {
  return {
    product_master_doc: els.productMaster?.value || "",
    starting_prompt: els.startingPrompt?.value || "",
    persona_seeds: els.personaSeeds?.value || "",
    copy_prompt_templates: els.copyTemplates?.value || "",
  };
}

if (els.saveBtn) {
  els.saveBtn.addEventListener("click", async () => {
    if (els.saveStatus) els.saveStatus.textContent = "Saving...";
    try {
      const config = gatherFields();
      await fetchJSON("/api/user/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config }),
      });
      currentConfig = config;
      if (els.saveStatus) els.saveStatus.textContent = "Saved.";
      setStatus("User config saved to MongoDB.");
    } catch (err) {
      if (els.saveStatus) els.saveStatus.textContent = `Failed: ${String(err)}`;
    }
  });
}

if (els.resetBtn) {
  els.resetBtn.addEventListener("click", async () => {
    if (!confirm("Reset to generic defaults? Your current config will be deleted.")) return;
    try {
      await fetchJSON("/api/user/config", { method: "DELETE" });
      await loadUserConfig();
      setStatus("Config reset to generic defaults.");
    } catch (err) {
      setStatus(`Reset failed: ${String(err)}`);
    }
  });
}
