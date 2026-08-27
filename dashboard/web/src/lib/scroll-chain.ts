const PANE =
  ".persona-board, .desk-scroll, .desk-row .file-card-grid, .tile-business .file-card-grid, .run-terminal";

function pageScroller(from: HTMLElement): HTMLElement | null {
  const stage = from.closest(".stage");
  if (stage instanceof HTMLElement && stage.scrollHeight - stage.clientHeight > 1) {
    return stage;
  }
  const root = document.scrollingElement;
  return root instanceof HTMLElement ? root : document.documentElement;
}

function atScrollEnd(el: HTMLElement, deltaY: number): boolean {
  if (el.scrollHeight - el.clientHeight <= 1) return true;
  if (deltaY < 0 && el.scrollTop <= 0) return true;
  if (deltaY > 0 && el.scrollTop + el.clientHeight >= el.scrollHeight - 1) return true;
  return false;
}

export function attachScrollChain(): () => void {
  const onWheel = (event: WheelEvent) => {
    if (event.defaultPrevented || event.ctrlKey || event.deltaY === 0) return;
    const target = event.target;
    if (!(target instanceof Element)) return;
    const pane = target.closest(PANE);
    if (!(pane instanceof HTMLElement) || !atScrollEnd(pane, event.deltaY)) return;
    const page = pageScroller(pane);
    if (!page || page === pane) return;
    page.scrollTop += event.deltaY;
    event.preventDefault();
  };
  document.addEventListener("wheel", onWheel, { passive: false, capture: true });
  return () => document.removeEventListener("wheel", onWheel, true);
}
