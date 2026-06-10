/** Standard wiring for editors that report unsaved-edit state up to the
 *  modal's discard guard. Must be called during component init (it creates
 *  an $effect). Pass both values as getters so the $effect tracks the live
 *  prop reference rather than capturing it at call time. */
export function reportDirty(
  isDirty: () => boolean,
  getOndirty: () => ((dirty: boolean) => void) | undefined,
): void {
  $effect(() => {
    getOndirty()?.(isDirty());
  });
}
