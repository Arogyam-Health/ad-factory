import { useEffect, useState } from "react";
import {
  emptyArrayItem,
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

export function JsonFieldEditor({ value, onChange, readOnly = false }: Props) {
  return (
    <div className="form-fields">
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
  return (
    <label className="form-cell">
      <span className="form-caption">Value</span>
      <PrimitiveEditor value={value} onChange={onChange} readOnly={readOnly} />
    </label>
  );
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
    <div className="form-stack">
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
        <div className="form-add">
          <Button
            variant="ghost"
            onClick={() => onChange({ ...obj, [uniqueObjectKey(Object.keys(obj))]: "" })}
          >
            + Add field
          </Button>
          <Button
            variant="ghost"
            onClick={() => onChange({ ...obj, [uniqueObjectKey(Object.keys(obj), "new_list")]: [] })}
          >
            + Add list
          </Button>
          <Button
            variant="ghost"
            onClick={() => onChange({ ...obj, [uniqueObjectKey(Object.keys(obj), "new_group")]: {} })}
          >
            + Add group
          </Button>
        </div>
      )}
    </div>
  );
}

function ArrayEditor({
  items,
  onChange,
  readOnly,
  depth,
  compact = false,
}: {
  items: JsonValue[];
  onChange: (next: JsonValue[]) => void;
  readOnly: boolean;
  depth: number;
  compact?: boolean;
}) {
  if (compact && isShortStringList(items)) {
    return (
      <div className="form-chips">
        {items.map((item, index) => (
          <span key={`${index}-${String(item)}`} className="form-chip">
            <input
              className="field form-chip-input"
              value={String(item)}
              readOnly={readOnly}
              spellCheck={false}
              aria-label={`Value ${index + 1}`}
              onChange={(event) => onChange(items.map((current, itemIndex) => (
                itemIndex === index ? event.target.value : current
              )))}
            />
            {readOnly ? null : (
              <button type="button" className="form-chip-remove" onClick={() => onChange(items.filter((_, itemIndex) => itemIndex !== index))} aria-label="Delete">
                ×
              </button>
            )}
          </span>
        ))}
        {readOnly ? null : (
          <Button variant="ghost" onClick={() => onChange([...items, ""])}>+ Add value</Button>
        )}
      </div>
    );
  }

  return (
    <div className="form-stack">
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
        <p className="hint">No values yet.</p>
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

  const nameField = indexLabel ? (
    <div className="form-cell">
      <span className="form-caption">Value</span>
      <span className="form-index">{label}</span>
    </div>
  ) : (
    <label className="form-cell">
      <span className="form-caption">Field</span>
      <input
        className="field"
        value={keyDraft}
        readOnly={readOnly || !onRename}
        spellCheck={false}
        aria-label="Field name"
        onChange={(event) => setKeyDraft(event.target.value)}
        onBlur={() => onRename?.(keyDraft)}
      />
    </label>
  );

  if (!nested) {
    return (
      <div className="form-row">
        {nameField}
        <label className="form-cell form-cell-value">
          <span className="form-caption">{indexLabel ? "Text" : "Value"}</span>
          <PrimitiveEditor value={value} onChange={onChange} readOnly={readOnly} />
        </label>
        {readOnly ? <span /> : (
          <Button variant="danger" onClick={onRemove}>Delete</Button>
        )}
      </div>
    );
  }

  const count = kind === "list"
    ? `${(value as JsonValue[]).length} values`
    : `${Object.keys(value as object).length} fields`;

  return (
    <div className="form-group">
      <div className="form-row form-row-group">
        {nameField}
        <div className="form-cell">
          <span className="form-caption">{kind === "list" ? "List" : "Group"}</span>
          <p className="hint" style={{ margin: 0 }}>{count}</p>
        </div>
        <div className="form-row-actions">
          <Button variant="ghost" onClick={() => setOpen((current) => !current)}>
            {open ? "Hide" : "Show"}
          </Button>
          {readOnly ? null : (
            <Button variant="danger" onClick={onRemove}>Delete</Button>
          )}
        </div>
      </div>
      {open ? (
        <div className="form-group-body">
          {kind === "list" ? (
            <ArrayEditor
              items={value as JsonValue[]}
              onChange={onChange}
              readOnly={readOnly}
              depth={depth + 1}
              compact
            />
          ) : (
            <NodeEditor value={value} onChange={onChange} readOnly={readOnly} depth={depth + 1} />
          )}
        </div>
      ) : null}
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
      <label className="toggle-row" style={{ margin: 0 }}>
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
  const tall = text.includes("\n") || text.length > 90;
  return (
    <textarea
      className={`field form-value${tall ? " form-value-tall" : ""}`}
      rows={tall ? 5 : 2}
      value={text}
      readOnly={readOnly}
      spellCheck={false}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

function isShortStringList(items: JsonValue[]): items is string[] {
  return items.length === 0 || items.every((item) => (
    typeof item === "string" && !item.includes("\n") && item.length < 48
  ));
}
