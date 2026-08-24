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
  rows = 16,
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

  if (!isJson) {
    return (
      <label className="form-cell">
        <span className="form-caption">Value</span>
        {textarea}
      </label>
    );
  }

  return (
    <div className="cfg-editor">
      <div className="form-mode-switch">
        <button
          type="button"
          className={mode === "fields" ? "active" : ""}
          disabled={!parsed.ok}
          onClick={() => {
            if (parsed.ok) setMode("fields");
          }}
        >
          Form
        </button>
        <button
          type="button"
          className={mode === "json" ? "active" : ""}
          onClick={() => setMode("json")}
        >
          JSON
        </button>
      </div>
      {!parsed.ok ? (
        <p className="hint">This file is not valid JSON yet. Fix it here, then switch back to Form. {parsed.error}</p>
      ) : (
        <p className="hint">
          {mode === "fields"
            ? "Each row is a field name and its value. Use JSON only if you need to paste the whole file."
            : "Raw JSON. Switch to Form to edit field names and values."}
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
