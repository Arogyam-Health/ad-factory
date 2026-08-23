import { Modal } from "@/components/Modal";
import { Button } from "@/components/Button";

export function DownloadKindDialog({
  title = "Download images",
  onClose,
  onChoose,
}: {
  title?: string;
  onClose: () => void;
  onChoose: (includeRaw: boolean) => void;
}) {
  return (
    <Modal title={title} onClose={onClose}>
      <p className="hint">
        Cropped is the finished ad. Raw is the original file from ChatGPT or Gemini before crop.
      </p>
      <div className="action-row" style={{ marginTop: 12 }}>
        <Button variant="primary" onClick={() => onChoose(false)}>Cropped only</Button>
        <Button onClick={() => onChoose(true)}>Cropped + raw</Button>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
      </div>
    </Modal>
  );
}
