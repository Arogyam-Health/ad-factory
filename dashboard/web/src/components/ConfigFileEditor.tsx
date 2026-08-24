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
}: Props) {
  const [mode, setMode] = useState<"fields" | "json">(isJson ? "fields" : "json");
  const parsed = useMemo(
    () => parseJsonDraft(text, fileKey === "persona_seeds" ? "array" : "object"),
    [fileKey, text],
  );

  useEffect(() => {
    setMode(isJson ? "fields" : "json");
  }, [fileKey, isJson]);

  const textarea = (
    <textarea
      className="cfg-textarea"
      value={text}
      readOnly={!canEdit}
      spellCheck={false}
      onChange={(event) => onChange(event.target.value)}
    />
  );

  const showForm = mode === "fields" && parsed.ok;

  return (
    <div className="cfg-editor">
      <div className="form-mode-switch">
        <button
          type="button"
          className={showForm ? "active" : ""}
          disabled={!parsed.ok}
          onClick={() => {
            if (parsed.ok) setMode("fields");
          }}
        >
          Form
        </button>
        <button
          type="button"
          className={!showForm ? "active" : ""}
          onClick={() => setMode("json")}
        >
          JSON
        </button>
      </div>
      {!parsed.ok ? (
        <p className="hint">
          {isJson
            ? `This file is not valid JSON yet. Fix it here, then switch back to Form. ${parsed.error}`
            : "This is a text file. Form is available when the contents are JSON."}
        </p>
      ) : (
        <p className="hint">
          {showForm
            ? "Each row is a field name and its value. Use JSON only if you need to paste the whole file."
            : "Raw file. Switch to Form to edit field names and values."}
        </p>
      )}
      <div className={`cfg-editor-scroll${showForm ? "" : " cfg-editor-scroll-text"}`}>
        {showForm ? (
          <JsonFieldEditor
            value={parsed.value}
            readOnly={!canEdit}
            onChange={(next) => onChange(stringifyJsonDraft(next))}
          />
        ) : textarea}
      </div>
    </div>
  );
}
