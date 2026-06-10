/** Stale-guarded async loader for panels that reload when the displayed
 *  frame changes (arrow-key nav). Only the most recent run() may touch
 *  state: a slow response for a frame the user already navigated away from
 *  is dropped instead of clobbering the new frame's data.
 *
 *  `error` is writable so save paths can share the same error line the
 *  template already renders. */
export function createAsyncLoad() {
  let loading = $state(true);
  let error = $state<string | null>(null);
  let current = 0;

  async function run<T>(
    fetcher: () => Promise<T>,
    apply: (value: T) => void,
    onError?: () => void,
  ): Promise<void> {
    const token = ++current;
    loading = true;
    error = null;
    try {
      const value = await fetcher();
      if (token !== current) return;
      apply(value);
    } catch (e) {
      if (token !== current) return;
      error = e instanceof Error ? e.message : String(e);
      onError?.();
    } finally {
      if (token === current) loading = false;
    }
  }

  /** Early-out (e.g. no active project yet): land in the idle state. */
  function settle(): void {
    current++;
    loading = false;
    error = null;
  }

  return {
    get loading() {
      return loading;
    },
    get error() {
      return error;
    },
    set error(v: string | null) {
      error = v;
    },
    run,
    settle,
  };
}
