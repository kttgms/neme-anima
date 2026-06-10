// Minimal modal focus trap (~no library needed for this app): keeps Tab
// cycling inside the node, optionally handles Escape, restores focus on
// destroy. Stacked modals compose naturally — each trap only listens on its
// own node, and destroy() hands focus back to whatever had it before.
const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(", ");

export type FocusTrapOptions = {
  /** Called when Escape is pressed inside the trap (modals: request close). */
  onEscape?: () => void;
};

export function focusTrap(node: HTMLElement, options: FocusTrapOptions = {}) {
  let opts = options;
  const previous = document.activeElement as HTMLElement | null;

  function focusables(): HTMLElement[] {
    return [...node.querySelectorAll<HTMLElement>(FOCUSABLE)];
  }

  // After-render so an autofocus/initial focus inside the dialog wins.
  queueMicrotask(() => {
    if (node.contains(document.activeElement)) return;
    (focusables()[0] ?? node).focus();
  });

  function onKeydown(ev: KeyboardEvent) {
    if (ev.key === "Escape" && opts.onEscape) {
      ev.stopPropagation();
      opts.onEscape();
      return;
    }
    if (ev.key !== "Tab") return;
    const els = focusables();
    if (els.length === 0) {
      ev.preventDefault();
      return;
    }
    const first = els[0];
    const last = els[els.length - 1];
    const active = document.activeElement;
    if (ev.shiftKey && (active === first || !node.contains(active))) {
      ev.preventDefault();
      last.focus();
    } else if (!ev.shiftKey && (active === last || !node.contains(active))) {
      ev.preventDefault();
      first.focus();
    }
  }

  node.addEventListener("keydown", onKeydown);

  return {
    update(next: FocusTrapOptions) {
      opts = next;
    },
    destroy() {
      node.removeEventListener("keydown", onKeydown);
      previous?.focus?.();
    },
  };
}
