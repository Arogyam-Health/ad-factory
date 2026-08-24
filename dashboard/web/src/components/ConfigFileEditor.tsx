import { useEffect, useMemo, useState } from "react";
import { parseJsonDraft, stringifyJsonDraft } from "@/lib/json-fields";
import { JsonFieldEditor } from "@/components/JsonFieldEditor";

type Props = {
  fileKey: string;
  text: string;
  isJson: boolean;
  canEdit: boolean;
  onChange: (text: string) => void;
  rows?: number;
};

export function ConfigFileEditor({
  fileKey,
  text,
  isJson,
  canEdit,
  onChange,
  rows = 20,
}: Props) {
  const [mode, setMode] = useState<"fields" | "json">("fields");
  const parsed = useMemo(
    () => parseJsonDraft(text, fileKey === "persona_seeds" ? "array" : "object"),
    [fileKey, text],
  );

  useEffect(() => {
    setMode("fields");
  }, [fileKey]);

  const textarea = (
    <textarea
      className="cfg-textarea"
      value={text}
      readOnly={!canEdit}
      rows={rows}
      spellCheck={false}
      onChange={(event) => onChange(event.target.value)}
    />
  );

  if (!isJson) return textarea;

  return (
    <div className="cfg-editor">
      <div className="chips json-mode-switch">
        <button
          type="button"
          className={`chip${mode === "fields" ? " active" : ""}`}
          disabled={!parsed.ok}
          onClick={() => {
            if (parsed.ok) setMode("fields");
          }}
        >
          Fields
        </button>
        <button
          type="button"
          className={`chip${mode === "json" ? " active" : ""}`}
          onClick={() => setMode("json")}
        >
          JSON
        </button>
      </div>
      {!parsed.ok ? (
        <p className="hint">Fix the JSON to use Fields view. {parsed.error}</p>
      ) : (
        <p className="hint">
          {mode === "fields"
            ? "Edit fields and values here. Switch to JSON to replace the whole file."
            : "Paste or replace the complete JSON file."}
        </p>
      )}
      {mode === "json" || !parsed.ok ? textarea : (
        <JsonFieldEditor
          value={parsed.value}
          readOnly={!canEdit}
          onChange={(next) => onChange(stringifyJsonDraft(next))}
        />
      )}
    </div>
  );
}
