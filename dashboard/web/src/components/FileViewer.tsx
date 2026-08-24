import { useEffect, useState } from "react";
import { asConfigText, JSON_KEYS, KEY_LABELS, saveConfigFile } from "@/lib/config-keys";
import { Button } from "@/components/Button";
import { ConfigFileEditor } from "@/components/ConfigFileEditor";
import { Modal } from "@/components/Modal";

type Props = {
  configKey: string;
  value: unknown;
  onClose: () => void;
  canEdit?: boolean;
  version?: number;
  ownerType?: string;
  orgId?: string;
  onSaved?: (key: string, text: string, result?: { notice?: string; config?: Record<string, unknown> }) => void;
};

export function FileViewer({
  configKey,
  value,
  onClose,
  canEdit = false,
  version,
  ownerType,
  orgId,
  onSaved,
}: Props) {
  const [draft, setDraft] = useState(() => asConfigText(value));
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setDraft(asConfigText(value));
    setStatus("");
  }, [configKey, value]);

  async function save() {
    if (!canEdit) {
      setStatus("Sign in to edit this plate.");
      return;
    }
    setSaving(true);
    setStatus("Saving…");
    try {
      const result = await saveConfigFile(configKey, draft, {
        canEdit,
        version,
        ownerType,
        orgId,
      });
      setStatus(result.notice || "Saved to this plate.");
      onSaved?.(configKey, draft, result);
    } catch (err) {
      const message = String(err);
      setStatus(message.includes("409") ? "Someone else saved this plate. Reload and try again." : message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      title={KEY_LABELS[configKey] || configKey}
      onClose={onClose}
      footer={(
        <>
          <div className="action-row">
            {canEdit ? (
              <Button variant="primary" disabled={saving} onClick={() => void save()}>
                {saving ? "Saving…" : "Save file"}
              </Button>
            ) : null}
            <Button variant="ghost" onClick={onClose}>Close</Button>
          </div>
          <span className="hint">
            {status || (canEdit ? "Edits write to the selected Mongo config." : "This file is read only.")}
          </span>
        </>
      )}
    >
      <ConfigFileEditor
        fileKey={configKey}
        text={draft}
        isJson={JSON_KEYS.has(configKey)}
        canEdit={canEdit}
        rows={18}
        onChange={setDraft}
      />
    </Modal>
  );
}
