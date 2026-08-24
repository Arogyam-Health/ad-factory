export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

export type JsonParseResult =
  | { ok: true; value: JsonValue }
  | { ok: false; error: string };

export function parseJsonDraft(text: string, empty: "object" | "array" = "object"): JsonParseResult {
  const raw = text.trim();
  if (!raw) return { ok: true, value: empty === "array" ? [] : {} };
  try {
    return { ok: true, value: JSON.parse(raw) as JsonValue };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : "Invalid JSON" };
  }
}

export function stringifyJsonDraft(value: JsonValue): string {
  return `${JSON.stringify(value, null, 2)}\n`;
}

export function jsonTypeOf(value: JsonValue): "text" | "number" | "yesno" | "list" | "group" {
  if (Array.isArray(value)) return "list";
  if (value && typeof value === "object") return "group";
  if (typeof value === "number") return "number";
  if (typeof value === "boolean") return "yesno";
  return "text";
}

export function emptyJsonOfType(type: ReturnType<typeof jsonTypeOf>): JsonValue {
  if (type === "list") return [];
  if (type === "group") return {};
  if (type === "number") return 0;
  if (type === "yesno") return false;
  return "";
}

export function uniqueObjectKey(keys: string[], base = "new_field"): string {
  if (!keys.includes(base)) return base;
  let index = 2;
  while (keys.includes(`${base}_${index}`)) index += 1;
  return `${base}_${index}`;
}

export function emptyArrayItem(items: JsonValue[]): JsonValue {
  const last = items[items.length - 1];
  if (last == null) return "";
  if (Array.isArray(last)) return [];
  if (typeof last === "object") {
    return Object.fromEntries(Object.keys(last).map((key) => [key, ""]));
  }
  if (typeof last === "number") return 0;
  if (typeof last === "boolean") return false;
  return "";
}

export function renameObjectKey(
  obj: Record<string, JsonValue>,
  oldKey: string,
  nextKey: string,
): Record<string, JsonValue> {
  const trimmed = nextKey.trim();
  if (!trimmed || trimmed === oldKey) return obj;
  const used = Object.keys(obj).filter((key) => key !== oldKey);
  const finalKey = used.includes(trimmed) ? uniqueObjectKey(used, trimmed) : trimmed;
  const next: Record<string, JsonValue> = {};
  for (const [key, value] of Object.entries(obj)) {
    next[key === oldKey ? finalKey : key] = value;
  }
  return next;
}
