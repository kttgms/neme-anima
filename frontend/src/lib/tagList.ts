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

/** Inclusive index range between two indices, regardless of order. */
export function rangeBetween(a: number, b: number): number[] {
  const lo = Math.min(a, b);
  const hi = Math.max(a, b);
  const out: number[] = [];
  for (let i = lo; i <= hi; i++) out.push(i);
  return out;
}

/** Move the items at `selected` indices to `dropIndex` (an index into the
 *  ORIGINAL array, 0..arr.length), preserving their relative order. Returns the
 *  new array and the new indices of the moved items. */
export function moveSelection<T>(
  arr: T[],
  selected: number[],
  dropIndex: number,
): { next: T[]; selection: number[] } {
  const sel = [...new Set(selected)]
    .filter((i) => i >= 0 && i < arr.length)
    .sort((a, b) => a - b);
  if (sel.length === 0) return { next: [...arr], selection: [] };
  const moving = sel.map((i) => arr[i]);
  const selSet = new Set(sel);
  const remaining = arr.filter((_, i) => !selSet.has(i));
  const before = sel.filter((i) => i < dropIndex).length;
  const insertAt = Math.max(0, Math.min(remaining.length, dropIndex - before));
  const next = [...remaining.slice(0, insertAt), ...moving, ...remaining.slice(insertAt)];
  const selection = moving.map((_, k) => insertAt + k);
  return { next, selection };
}

/** The indices a drag should move: the whole current selection when the dragged
 *  pill is part of it, otherwise just the dragged pill on its own. */
export function movingIndices(selected: Set<number>, dragged: number): number[] {
  if (selected.has(dragged)) return [...selected].sort((a, b) => a - b);
  return [dragged];
}

export interface SimpleRect {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

/** Reading-order INSERTION index (0..n) for a point: the count of pills that
 *  precede the point in flow order — a pill precedes the point when it's on an
 *  earlier row (point above this pill's row) or on the same row left of the
 *  pill's horizontal center. Returns `fallback` (typically tags.length → append)
 *  when the point follows every pill. */
export function dropIndexAtPoint(
  rects: Array<{ index: number; rect: SimpleRect }>,
  x: number,
  y: number,
  fallback: number,
): number {
  const sorted = [...rects].sort((a, b) => a.index - b.index);
  for (const { index, rect } of sorted) {
    const midX = (rect.left + rect.right) / 2;
    const aboveRow = y < rect.top;
    const sameRow = y >= rect.top && y <= rect.bottom;
    if (aboveRow || (sameRow && x < midX)) return index;
  }
  return fallback;
}
