/** Format a number of seconds as ``mm:ss.s`` (or ``hh:mm:ss.s`` past
 *  one hour). The tenth-of-a-second precision matches the snap grid the
 *  rest of the modal works on, so what the user sees is what they save. */
export function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) seconds = 0;
  const total = Math.round(seconds * 10) / 10;
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = (total % 60);
  const padded = s < 10 ? `0${s.toFixed(1)}` : s.toFixed(1);
  if (h > 0) {
    return `${h}:${m.toString().padStart(2, "0")}:${padded}`;
  }
  return `${m.toString().padStart(2, "0")}:${padded}`;
}

/** Parse ``mm:ss``, ``mm:ss.s`` or ``hh:mm:ss[.s]`` into seconds; returns
 *  null when the string doesn't fit either shape. Plain numbers ("90") are
 *  accepted as seconds so quick edits don't force the user to add a colon. */
export function parseTime(text: string): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const parts = trimmed.split(":").map((p) => p.trim());
  if (parts.length === 1) {
    const n = Number(parts[0]);
    return Number.isFinite(n) ? n : null;
  }
  if (parts.length === 2 || parts.length === 3) {
    const nums = parts.map((p) => Number(p));
    if (nums.some((n) => !Number.isFinite(n))) return null;
    if (parts.length === 2) return nums[0] * 60 + nums[1];
    return nums[0] * 3600 + nums[1] * 60 + nums[2];
  }
  return null;
}

/** Round to 1/10s — the snap grid the timeline and inputs share. */
export function snap(seconds: number): number {
  return Math.round(seconds * 10) / 10;
}

export type DragState =
  | { kind: "create"; startTime: number; index: number }
  | { kind: "move"; index: number; offsetTime: number; origStart: number; origEnd: number }
  | { kind: "resize-left"; index: number }
  | { kind: "resize-right"; index: number }
  | null;

