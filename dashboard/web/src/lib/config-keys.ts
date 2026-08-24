import { fetchJSON, invalidateDefaults } from "@/lib/api";

export const CONFIG_KEYS = [
  "product_master_doc",
  "starting_prompt",
  "copy_prompt_templates",
  "persona_seeds",
  "concept",
  "copy_architecture",
  "ad_formats",
  "ad_hooks",
  "ad_angles",
  "ad_frameworks",
  "ad_proof",
  "ad_objections",
  "ad_value_props",
  "ad_awareness",
  "ad_emotions",
  "ad_specificity",
  "ad_feature_focus",
  "ad_guardrails",
  "background_variant",
  "prompt_assembler_templates",
  "conversion_916_prompt",
  "reference_starting_prompt",
  "reference_product_master_doc",
] as const;

export type ConfigKey = (typeof CONFIG_KEYS)[number];

export const JSON_KEYS = new Set<string>([
  "copy_prompt_templates",
  "persona_seeds",
  "concept",
  "copy_architecture",
  "ad_formats",
  "ad_hooks",
  "ad_angles",
  "ad_frameworks",
  "ad_proof",
  "ad_objections",
  "ad_value_props",
  "ad_awareness",
  "ad_emotions",
  "ad_specificity",
  "ad_feature_focus",
  "ad_guardrails",
  "background_variant",
  "prompt_assembler_templates",
]);

export const KEY_LABELS: Record<string, string> = {
  product_master_doc: "Product Master Doc",
  starting_prompt: "Starting Prompt",
  copy_prompt_templates: "Copy Prompt Templates",
  persona_seeds: "Persona Seeds",
  concept: "Concept",
  copy_architecture: "Copy Architecture",
  ad_formats: "Ad Formats",
  ad_hooks: "Hook Structures",
  ad_angles: "Concept Angles",
  ad_frameworks: "Copy Frameworks",
  ad_proof: "Proof Strategies",
  ad_objections: "Objection Strategies",
  ad_value_props: "Value Propositions",
  ad_awareness: "Awareness Stages",
  ad_emotions: "Emotional Drivers",
  ad_specificity: "Specificity Levels",
  ad_feature_focus: "Feature Focus",
  ad_guardrails: "Copy Guardrails",
  background_variant: "Background Variant",
  prompt_assembler_templates: "Prompt Assembler Templates",
  conversion_916_prompt: "9:16 Conversion Prompt",
  reference_starting_prompt: "Reference Starting Prompt",
  reference_product_master_doc: "Reference Product Doc",
};

export const KEY_HINTS: Record<string, string> = {
  product_master_doc: "Product info, claims, ingredients, use-cases",
  starting_prompt: "Prepended to all structured generation prompts",
  copy_prompt_templates: "Visual archetypes for image prompts. Live copy reads the Ad * files.",
  persona_seeds: "Personas with tags, guardrails, headline anchors",
  concept: "Creative-format catalog for Studio and Reference",
  copy_architecture: "Legacy headline file. Live copy reads the Ad * files instead.",
  ad_formats: "Format descriptions and copy skeletons sent to the copy LLM",
  ad_hooks: "Hook Structure hypothesis styles",
  ad_angles: "Concept Angle hypothesis styles",
  ad_frameworks: "Copy Framework hypothesis styles",
  ad_proof: "Proof Strategy hypothesis styles",
  ad_objections: "Objection Strategy hypothesis styles",
  ad_value_props: "Value Proposition hypothesis styles",
  ad_awareness: "Awareness Stage hypothesis styles",
  ad_emotions: "Emotional Driver hypothesis styles",
  ad_specificity: "Specificity Level hypothesis styles",
  ad_feature_focus: "Feature Focus hypothesis styles",
  ad_guardrails: "Always-on safety lines and no-hypothesis instruction",
  background_variant: "Background visual variants catalog",
  prompt_assembler_templates: "Image prompt assembly blocks",
  conversion_916_prompt: "Converts 4:5 creatives to 9:16",
  reference_starting_prompt: "Reference flow only — separate starting prompt",
  reference_product_master_doc: "Reference flow only — separate product doc",
};

export function asConfigText(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export type ConceptCatalogItem = {
  id: string;
  label: string;
  description: string;
};

export function catalogConcepts(studio: {
  concepts?: { id?: string; label?: string; description?: string }[];
  config?: Record<string, unknown>;
} | null | undefined): ConceptCatalogItem[] {
  const listed = studio?.concepts;
  if (Array.isArray(listed) && listed.length) {
    return listed
      .map((item) => ({
        id: String(item.id || "").trim(),
        label: String(item.label || item.id || "").trim(),
        description: String(item.description || ""),
      }))
      .filter((item) => item.id);
  }
  const raw = studio?.config?.concept;
  let parsed: unknown = raw;
  if (typeof raw === "string") {
    try {
      parsed = JSON.parse(raw);
    } catch {
      return [];
    }
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return [];
  return Object.entries(parsed as Record<string, unknown>).flatMap(([key, value]) => {
    const id = String(key || "").trim();
    if (!id) return [];
    const description = typeof value === "string"
      ? value
      : value && typeof value === "object" && "description" in value
        ? String((value as { description?: unknown }).description || "")
        : "";
    return [{
      id,
      label: id.split("/").pop()?.replace(/_/g, " ") || id,
      description,
    }];
  });
}

export function studioOrgKey(userId: string) {
  return `adFactoryStudioOrg:${userId}`;
}

export const LAST_STUDIO_ORG_KEY = "adFactoryStudioOrg";

export function readStudioOrg(userId?: string) {
  if (typeof localStorage === "undefined") return "personal";
  if (userId) {
    const scoped = localStorage.getItem(studioOrgKey(userId));
    if (scoped) return scoped;
  }
  return localStorage.getItem(LAST_STUDIO_ORG_KEY) || "personal";
}

export function writeStudioOrg(userId: string, orgId: string) {
  if (typeof localStorage === "undefined") return;
  localStorage.setItem(LAST_STUDIO_ORG_KEY, orgId);
  if (userId) localStorage.setItem(studioOrgKey(userId), orgId);
}

export type ConfigSaveTarget = {
  canEdit?: boolean;
  version?: number;
  ownerType?: string;
  orgId?: string;
};

export function validateConfigText(key: string, text: string) {
  if (!JSON_KEYS.has(key)) return text;
  try {
    JSON.parse(text || (key === "persona_seeds" ? "[]" : "{}"));
  } catch {
    throw new Error(`Invalid JSON in ${KEY_LABELS[key] || key}`);
  }
  return text;
}

export async function saveConfigFile(key: string, text: string, target: ConfigSaveTarget) {
  if (!target.canEdit) throw new Error("Sign in to save your own files.");
  validateConfigText(key, text);
  const path = target.ownerType === "org" && target.orgId
    ? `/api/orgs/${encodeURIComponent(target.orgId)}/config`
    : "/api/user/config";
  const result = await fetchJSON<{ status?: string; config?: Record<string, unknown> }>(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      config: { [key]: text },
      expected_version: target.version,
    }),
  });
  invalidateDefaults();
  return result;
}
