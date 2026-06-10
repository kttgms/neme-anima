/** A transient "Saved ✓"-style boolean: trigger() flips it on for
 *  `durationMs`, re-triggering restarts the countdown. Call destroy() from
 *  onDestroy so a pending timer can't fire after unmount. */
export function createFlash(durationMs = 2000) {
  let active = $state(false);
  let timer: ReturnType<typeof setTimeout> | null = null;
  return {
    get active() {
      return active;
    },
    trigger(): void {
      active = true;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        active = false;
      }, durationMs);
    },
    destroy(): void {
      if (timer) clearTimeout(timer);
      timer = null;
    },
  };
}
