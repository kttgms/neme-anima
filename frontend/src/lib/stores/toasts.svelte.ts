// Module-scoped toast queue (same pattern as tagClipboard.svelte.ts): any
// component or store can push; ToastHost renders the queue at the App root.
// success/info auto-dismiss; errors stick until dismissed by hand.
export type ToastKind = "success" | "error" | "info";

export type Toast = {
  id: number;
  kind: ToastKind;
  message: string;
  /** Sticky toasts (errors) stay until the user dismisses them. */
  sticky: boolean;
};

const AUTO_DISMISS_MS = 4000;

class ToastStore {
  list = $state<Toast[]>([]);
  #nextId = 1;
  #timers = new Map<number, ReturnType<typeof setTimeout>>();

  success(message: string): number {
    return this.#push("success", message, false);
  }

  info(message: string): number {
    return this.#push("info", message, false);
  }

  error(message: string): number {
    return this.#push("error", message, true);
  }

  dismiss(id: number): void {
    this.#clearTimer(id);
    this.list = this.list.filter((t) => t.id !== id);
  }

  /** Hover-pause: stop the auto-dismiss countdown while the pointer is over. */
  pause(id: number): void {
    this.#clearTimer(id);
  }

  /** Restart the (full) countdown after a hover-pause. */
  resume(id: number): void {
    const toast = this.list.find((t) => t.id === id);
    if (!toast || toast.sticky || this.#timers.has(id)) return;
    this.#startTimer(id);
  }

  #push(kind: ToastKind, message: string, sticky: boolean): number {
    const id = this.#nextId++;
    this.list = [...this.list, { id, kind, message, sticky }];
    if (!sticky) this.#startTimer(id);
    return id;
  }

  #startTimer(id: number): void {
    this.#timers.set(id, setTimeout(() => this.dismiss(id), AUTO_DISMISS_MS));
  }

  #clearTimer(id: number): void {
    const t = this.#timers.get(id);
    if (t) clearTimeout(t);
    this.#timers.delete(id);
  }
}

export const toasts = new ToastStore();
