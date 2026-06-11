// Concrete `BulkRetagActions` wired to this app's stores. Kept OUT of
// bulkRetag.ts so that module stays store-agnostic (no framesStore/toasts
// import — the runner only knows the interface). ActionBar's Tag/Describe
// buttons and App's t/s shortcuts share these singletons instead of each
// declaring an identical copy.
import type { BulkRetagActions } from "$lib/bulkRetag";
import { framesStore } from "$lib/stores/frames.svelte";
import { toasts } from "$lib/stores/toasts.svelte";

// Everything except the success cache-bust is identical across the two kinds.
const shared = {
  markProcessing: (f: string[]) => framesStore.markProcessing(f),
  unmarkProcessing: (f: string) => framesStore.unmarkProcessing(f),
  deselect: (f: string[]) => framesStore.deselect(f),
  error: (m: string) => toasts.error(m),
};

/** Bulk WD14 retag: a success bumps the per-frame retag counter. */
export const tagActions: BulkRetagActions = {
  ...shared,
  markDone: (f) => framesStore.markRetagged(f),
};

/** Bulk LLM describe: a success bumps the per-frame describe counter. */
export const describeActions: BulkRetagActions = {
  ...shared,
  markDone: (f) => framesStore.markDescribed(f),
};
