import { getContext, setContext } from "svelte";

/** The overwrite-confirm action shared between the bulk image-selection flow
 *  (ActionBar) and the single-frame Tag/Describe buttons (the crop-modal
 *  panels). Returns true when the user confirms the overwrite. */
export type ConfirmFrameOverwrite = (
  action: "retag" | "describe",
  selectedCount: number,
  affectedCount: number,
) => Promise<boolean>;

// A symbol key keeps this context private to these helpers — no string-key
// collisions, and consumers can't grab it by guessing a name.
const KEY = Symbol("confirmFrameOverwrite");

/** Provide the confirm fn at the app root. Call once, during App init. */
export function setFrameOverwriteConfirm(fn: ConfirmFrameOverwrite): void {
  setContext(KEY, fn);
}

/** Read the confirm fn anywhere below the provider. Throws if the provider
 *  is missing so a wiring mistake surfaces immediately instead of silently
 *  skipping the confirmation. */
export function getFrameOverwriteConfirm(): ConfirmFrameOverwrite {
  const fn = getContext<ConfirmFrameOverwrite | undefined>(KEY);
  if (!fn) {
    throw new Error(
      "getFrameOverwriteConfirm() called without a provider — " +
        "setFrameOverwriteConfirm() must run at the App root first.",
    );
  }
  return fn;
}
