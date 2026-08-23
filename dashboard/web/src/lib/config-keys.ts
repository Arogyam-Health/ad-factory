import { fetchJSON, invalidateDefaults } from "@/lib/api";

export const CONFIG_KEYS = [
  "product_master_doc",
  "starting_prompt",
  "copy_prompt_templates",
  "persona_seeds",
  "copy_architecture",
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
  "copy_architecture",
  "background_variant",
  "prompt_assembler_templates",
]);

export const KEY_LABELS: Record<string, string> = {
  product_master_doc: "Product Master Doc",
  starting_prompt: "Starting Prompt",
  copy_prompt_templates: "Copy Prompt Templates",
  persona_seeds: "Persona Seeds",
  copy_architecture: "Copy Architecture",
  background_variant: "Background Variant",
  prompt_assembler_templates: "Prompt Assembler Templates",
  conversion_916_prompt: "9:16 Conversion Prompt",
  reference_starting_prompt: "Reference Starting Prompt",
  reference_product_master_doc: "Reference Product Doc",
};

export const KEY_HINTS: Record<string, string> = {
  product_master_doc: "Product info, claims, ingredients, use-cases",
  starting_prompt: "Prepended to all structured generation prompts",
  copy_prompt_templates: "System prompts, format rules, schema, angles",
  persona_seeds: "Personas with tags, guardrails, headline anchors",
  copy_architecture: "Headline and support-line architecture",
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

export function studioOrgKey(userId: string) {
  return `adFactoryStudioOrg:${userId}`;
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
