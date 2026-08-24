import { useEffect, useState } from "react";
import {
  emptyArrayItem,
  emptyJsonOfType,
  jsonTypeOf,
  renameObjectKey,
  uniqueObjectKey,
  type JsonValue,
} from "@/lib/json-fields";
import { Button } from "@/components/Button";

type Props = {
  value: JsonValue;
  onChange: (next: JsonValue) => void;
  readOnly?: boolean;
};

const TYPE_OPTIONS = [
  { id: "text", label: "Text" },
  { id: "number", label: "Number" },
  { id: "yesno", label: "Yes / no" },
  { id: "list", label: "List" },
  { id: "group", label: "Group" },
] as const;

export function JsonFieldEditor({ value, onChange, readOnly = false }: Props) {
  return (
    <div className="json-fields">
      <NodeEditor value={value} onChange={onChange} readOnly={readOnly} depth={0} />
    </div>
  );
}

function NodeEditor({
  value,
  onChange,
  readOnly,
  depth,
}: {
  value: JsonValue;
  onChange: (next: JsonValue) => void;
  readOnly: boolean;
  depth: number;
}) {
  if (Array.isArray(value)) {
    return <ArrayEditor items={value} onChange={onChange} readOnly={readOnly} depth={depth} />;
  }
  if (value && typeof value === "object") {
    return <ObjectEditor obj={value} onChange={onChange} readOnly={readOnly} depth={depth} />;
  }
  return <PrimitiveEditor value={value} onChange={onChange} readOnly={readOnly} />;
}

function ObjectEditor({
  obj,
  onChange,
  readOnly,
  depth,
}: {
  obj: Record<string, JsonValue>;
  onChange: (next: Record<string, JsonValue>) => void;
  readOnly: boolean;
  depth: number;
}) {
  const entries = Object.entries(obj);
  return (
    <div className="json-stack">
      {entries.length ? entries.map(([key, child]) => (
        <FieldRow
          key={key}
          label={key}
          value={child}
          readOnly={readOnly}
          depth={depth}
          onRename={(nextKey) => onChange(renameObjectKey(obj, key, nextKey))}
          onChange={(next) => onChange({ ...obj, [key]: next })}
          onRemove={() => {
            const next = { ...obj };
            delete next[key];
            onChange(next);
          }}
        />
      )) : (
        <p className="hint">No fields yet.</p>
      )}
      {readOnly ? null : (
        <Button
          variant="ghost"
          onClick={() => {
            const key = uniqueObjectKey(Object.keys(obj));
            onChange({ ...obj, [key]: "" });
          }}
        >
          + Add field
        </Button>
      )}
    </div>
  );
}

function ArrayEditor({
  items,
  onChange,
  readOnly,
  depth,
}: {
  items: JsonValue[];
  onChange: (next: JsonValue[]) => void;
  readOnly: boolean;
  depth: number;
}) {
  return (
    <div className="json-stack">
      {items.length ? items.map((child, index) => (
        <FieldRow
          key={`${index}-${jsonTypeOf(child)}`}
          label={String(index + 1)}
          value={child}
          readOnly={readOnly}
          depth={depth}
          indexLabel
          onChange={(next) => onChange(items.map((item, itemIndex) => (itemIndex === index ? next : item)))}
          onRemove={() => onChange(items.filter((_, itemIndex) => itemIndex !== index))}
        />
      )) : (
        <p className="hint">No items yet.</p>
      )}
      {readOnly ? null : (
        <Button variant="ghost" onClick={() => onChange([...items, emptyArrayItem(items)])}>
          + Add item
        </Button>
      )}
    </div>
  );
}

function FieldRow({
  label,
  value,
  readOnly,
  depth,
  indexLabel = false,
  onRename,
  onChange,
  onRemove,
}: {
  label: string;
  value: JsonValue;
  readOnly: boolean;
  depth: number;
  indexLabel?: boolean;
  onRename?: (next: string) => void;
  onChange: (next: JsonValue) => void;
  onRemove: () => void;
}) {
  const kind = jsonTypeOf(value);
  const nested = kind === "list" || kind === "group";
  const [open, setOpen] = useState(depth < 1 || !nested);
  const [keyDraft, setKeyDraft] = useState(label);

  useEffect(() => {
    setKeyDraft(label);
  }, [label]);

  return (
    <div className="json-field">
      <div className="json-field-head">
        {indexLabel ? (
          <span className="json-index">{label}</span>
        ) : (
          <input
            className="field json-key"
            value={keyDraft}
            readOnly={readOnly || !onRename}
            spellCheck={false}
            aria-label="Field name"
            onChange={(event) => setKeyDraft(event.target.value)}
            onBlur={() => onRename?.(keyDraft)}
          />
        )}
        <select
          className="field json-type"
          value={kind}
          disabled={readOnly}
          aria-label="Field type"
          onChange={(event) => onChange(emptyJsonOfType(event.target.value as typeof kind))}
        >
          {TYPE_OPTIONS.map((option) => (
            <option key={option.id} value={option.id}>{option.label}</option>
          ))}
        </select>
        {nested ? (
          <Button variant="ghost" onClick={() => setOpen((current) => !current)}>
            {open ? "Hide" : "Show"}
          </Button>
        ) : null}
        {readOnly ? null : (
          <Button variant="danger" onClick={onRemove}>Delete</Button>
        )}
      </div>
      {nested ? (
        open ? (
          <div className="json-nested">
            <NodeEditor value={value} onChange={onChange} readOnly={readOnly} depth={depth + 1} />
          </div>
        ) : (
          <p className="hint">{kind === "list" ? `${(value as JsonValue[]).length} items` : `${Object.keys(value as object).length} fields`}</p>
        )
      ) : (
        <PrimitiveEditor value={value} onChange={onChange} readOnly={readOnly} />
      )}
    </div>
  );
}

function PrimitiveEditor({
  value,
  onChange,
  readOnly,
}: {
  value: JsonValue;
  onChange: (next: JsonValue) => void;
  readOnly: boolean;
}) {
  if (typeof value === "boolean") {
    return (
      <label className="toggle-row">
        <input
          type="checkbox"
          checked={value}
          disabled={readOnly}
          onChange={(event) => onChange(event.target.checked)}
        />
        {value ? "Yes" : "No"}
      </label>
    );
  }
  if (typeof value === "number") {
    return (
      <input
        className="field"
        type="number"
        value={Number.isFinite(value) ? value : 0}
        readOnly={readOnly}
        onChange={(event) => onChange(event.target.value === "" ? 0 : Number(event.target.value))}
      />
    );
  }
  const text = value == null ? "" : String(value);
  const tall = text.includes("\n") || text.length > 80;
  return (
    <textarea
      className={`field${tall ? " json-value-area" : ""}`}
      rows={tall ? 5 : 2}
      value={text}
      readOnly={readOnly}
      spellCheck={false}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}
