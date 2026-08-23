import { useId, useState, type ChangeEvent } from "react";

type Props = {
  id?: string;
  label?: string;
  accept?: string;
  multiple?: boolean;
  disabled?: boolean;
  emptyHint?: string;
  onFiles: (files: FileList | null, input: HTMLInputElement) => void | Promise<void>;
};

export function FileField({
  id,
  label = "Choose files",
  accept,
  multiple,
  disabled,
  emptyHint = "No file chosen",
  onFiles,
}: Props) {
  const autoId = useId();
  const inputId = id || autoId;
  const [hint, setHint] = useState(emptyHint);

  return (
    <div className={`file-field${disabled ? " is-disabled" : ""}`}>
      <input
        id={inputId}
        className="file-field-input"
        type="file"
        accept={accept}
        multiple={multiple}
        disabled={disabled}
        onChange={(event: ChangeEvent<HTMLInputElement>) => {
          const files = event.target.files;
          if (files?.length) {
            const names = Array.from(files).map((file) => file.name);
            setHint(names.length > 2 ? `${names.length} files selected` : names.join(", "));
          } else {
            setHint(emptyHint);
          }
          void onFiles(files, event.target);
        }}
      />
      <label htmlFor={inputId} className="btn file-field-trigger">
        {label}
      </label>
      <span className="file-field-hint">{hint}</span>
    </div>
  );
}
