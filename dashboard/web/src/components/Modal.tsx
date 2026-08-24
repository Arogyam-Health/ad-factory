import { useRef, useState, type MouseEvent as ReactMouseEvent, type ReactNode } from "react";
import { Button } from "@/components/Button";

type Props = {
  title: string;
  children: ReactNode;
  onClose: () => void;
  footer?: ReactNode;
  size?: "default" | "wide";
};

const WIDE_DEFAULT = { width: 1280, height: 860 };
const WIDE_MIN = { width: 720, height: 560 };

function defaultWideSize() {
  if (typeof window === "undefined") return WIDE_DEFAULT;
  return {
    width: Math.min(WIDE_DEFAULT.width, Math.max(WIDE_MIN.width, window.innerWidth - 32)),
    height: Math.min(WIDE_DEFAULT.height, Math.max(WIDE_MIN.height, window.innerHeight - 32)),
  };
}

export function Modal({ title, children, onClose, footer, size = "default" }: Props) {
  const boxRef = useRef<HTMLDivElement>(null);
  const [wideSize, setWideSize] = useState(defaultWideSize);

  function startResize(event: ReactMouseEvent<HTMLButtonElement>) {
    event.preventDefault();
    event.stopPropagation();
    const box = boxRef.current;
    if (!box) return;
    const startX = event.clientX;
    const startY = event.clientY;
    const startWidth = box.getBoundingClientRect().width;
    const startHeight = box.getBoundingClientRect().height;
    const maxWidth = Math.min(window.innerWidth - 24, 1800);
    const maxHeight = window.innerHeight - 24;

    function move(next: MouseEvent) {
      setWideSize({
        width: Math.min(maxWidth, Math.max(WIDE_MIN.width, startWidth + (next.clientX - startX))),
        height: Math.min(maxHeight, Math.max(WIDE_MIN.height, startHeight + (next.clientY - startY))),
      });
    }

    function stop() {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", stop);
    }

    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", stop);
  }

  return (
    <div className="modal-overlay" onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div
        ref={boxRef}
        className={`modal-box${size === "wide" ? " modal-box-wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        style={size === "wide" ? { width: wideSize.width, height: wideSize.height } : undefined}
      >
        <div className="modal-header">
          <h2>{title}</h2>
          <Button variant="ghost" onClick={onClose} aria-label="Close">
            Close
          </Button>
        </div>
        <div className="modal-body">{children}</div>
        {footer ? <div className="modal-footer">{footer}</div> : null}
        {size === "wide" ? (
          <button
            type="button"
            className="modal-resize"
            aria-label="Resize editor"
            title="Drag to resize"
            onMouseDown={startResize}
          />
        ) : null}
      </div>
    </div>
  );
}
