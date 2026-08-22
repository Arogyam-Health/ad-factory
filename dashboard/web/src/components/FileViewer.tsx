import { asConfigText, KEY_LABELS } from "@/lib/config-keys";
import { Modal } from "@/components/Modal";

type Props = {
  configKey: string;
  value: unknown;
  onClose: () => void;
};

export function FileViewer({ configKey, value, onClose }: Props) {
  return (
    <Modal title={KEY_LABELS[configKey] || configKey} onClose={onClose}>
      <textarea
        readOnly
        className="cfg-textarea"
        rows={18}
        value={asConfigText(value)}
        spellCheck={false}
      />
    </Modal>
  );
}
