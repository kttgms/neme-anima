/** Move the element at `from` to `to`, returning a new array. An out-of-range
 *  `from` returns a shallow copy unchanged; `to` is clamped into range. */
export function reorder<T>(arr: T[], from: number, to: number): T[] {
  const next = [...arr];
  if (from < 0 || from >= next.length) return next;
  const clampedTo = Math.max(0, Math.min(next.length - 1, to));
  const [moved] = next.splice(from, 1);
  next.splice(clampedTo, 0, moved);
  return next;
}

/** Order-sensitive equality for tag lists — a pure reorder counts as a change
 *  so the Save button lights up. */
export function tagsEqual(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

/** Case-insensitive substring match used to highlight searched tags. An empty
 *  (or whitespace-only) query matches nothing — no highlight. */
export function matchesQuery(tag: string, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return false;
  return tag.toLowerCase().includes(q);
}
