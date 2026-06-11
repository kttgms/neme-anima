<script lang="ts">
  import type { Segment } from "$lib/types";
  import { snap, formatTime, type DragState } from "$lib/segmentEditor";

  type Props = {
    segments: Segment[];
    duration: number;
    playhead: number;
    /** Emit the full mutated segment array on every drag step / drop. The
     *  parent owns `segments` and re-renders from it (controlled). */
    onsegmentschange: (next: Segment[]) => void;
  };
  const { segments, duration, playhead, onsegmentschange }: Props = $props();

  let timelineEl: HTMLDivElement | undefined = $state(undefined);

  // Active drag descriptor. `null` when idle. Stored at module-scope (well,
  // component-scope) so the global pointerup handler can finalise without
  // having to thread state through every event.
  let drag = $state<DragState>(null);

  function pct(seconds: number): number {
    if (duration <= 0) return 0;
    return Math.max(0, Math.min(100, (seconds / duration) * 100));
  }

  function timeFromClientX(clientX: number): number {
    if (!timelineEl || duration <= 0) return 0;
    const rect = timelineEl.getBoundingClientRect();
    const ratio = (clientX - rect.left) / Math.max(1, rect.width);
    return snap(Math.max(0, Math.min(1, ratio)) * duration);
  }

  /** Pointerdown on the timeline background: start creating a new
   *  segment, but only if the click landed in empty space. Clicks on an
   *  existing segment (or its handles) are caught by per-segment handlers
   *  and never reach here. */
  function timelinePointerDown(e: PointerEvent) {
    if (duration <= 0) return;
    if (e.button !== 0) return;
    const t = timeFromClientX(e.clientX);
    // Don't start a create inside an existing segment.
    if (segments.some((s) => t >= s.start_seconds && t < s.end_seconds)) return;
    timelineEl?.setPointerCapture(e.pointerId);
    // Insert a degenerate segment at the click point; expand it as the
    // pointer moves. Re-sort once so its index is stable.
    const newSeg: Segment = { start_seconds: t, end_seconds: snap(t + 0.1) };
    const merged = [...segments, newSeg].sort(
      (a, b) => a.start_seconds - b.start_seconds,
    );
    const idx = merged.indexOf(newSeg);
    onsegmentschange(merged);
    drag = { kind: "create", startTime: t, index: idx };
  }

  function segmentPointerDown(i: number, e: PointerEvent) {
    if (e.button !== 0) return;
    if (duration <= 0) return;
    e.stopPropagation();
    timelineEl?.setPointerCapture(e.pointerId);
    const seg = segments[i];
    const t = timeFromClientX(e.clientX);
    drag = {
      kind: "move", index: i, offsetTime: t - seg.start_seconds,
      origStart: seg.start_seconds, origEnd: seg.end_seconds,
    };
  }

  function handlePointerDown(i: number, side: "left" | "right", e: PointerEvent) {
    if (e.button !== 0) return;
    e.stopPropagation();
    timelineEl?.setPointerCapture(e.pointerId);
    drag = side === "left"
      ? { kind: "resize-left", index: i }
      : { kind: "resize-right", index: i };
  }

  function timelinePointerMove(e: PointerEvent) {
    if (!drag) return;
    const t = timeFromClientX(e.clientX);
    if (drag.kind === "create") {
      const i = drag.index;
      const seg = segments[i];
      const lower = i > 0 ? segments[i - 1].end_seconds : 0;
      const upper = i < segments.length - 1
        ? segments[i + 1].start_seconds
        : duration;
      const clampedT = Math.max(lower, Math.min(t, upper));
      let nextStart = Math.min(drag.startTime, clampedT);
      let nextEnd = Math.max(drag.startTime, clampedT);
      if (nextEnd - nextStart < 0.1) nextEnd = snap(nextStart + 0.1);
      const next = segments.slice();
      next[i] = { ...seg, start_seconds: snap(nextStart), end_seconds: snap(nextEnd) };
      onsegmentschange(next);
    } else if (drag.kind === "move") {
      const i = drag.index;
      const seg = segments[i];
      const len = drag.origEnd - drag.origStart;
      const lower = i > 0 ? segments[i - 1].end_seconds : 0;
      const upper = i < segments.length - 1
        ? segments[i + 1].start_seconds
        : duration;
      const desiredStart = t - drag.offsetTime;
      const clampedStart = Math.max(lower, Math.min(desiredStart, upper - len));
      const next = segments.slice();
      next[i] = {
        ...seg,
        start_seconds: snap(clampedStart),
        end_seconds: snap(clampedStart + len),
      };
      onsegmentschange(next);
    } else if (drag.kind === "resize-left") {
      const i = drag.index;
      const seg = segments[i];
      const lower = i > 0 ? segments[i - 1].end_seconds : 0;
      const upper = seg.end_seconds - 0.1;
      const clampedT = Math.max(lower, Math.min(t, upper));
      const next = segments.slice();
      next[i] = { ...seg, start_seconds: snap(clampedT) };
      onsegmentschange(next);
    } else if (drag.kind === "resize-right") {
      const i = drag.index;
      const seg = segments[i];
      const lower = seg.start_seconds + 0.1;
      const upper = i < segments.length - 1
        ? segments[i + 1].start_seconds
        : (duration > 0 ? duration : t);
      const clampedT = Math.max(lower, Math.min(t, upper));
      const next = segments.slice();
      next[i] = { ...seg, end_seconds: snap(clampedT) };
      onsegmentschange(next);
    }
  }

  function timelinePointerUp(e: PointerEvent) {
    if (drag) {
      // If a "create" drag finished without actually moving, ensure the
      // resulting segment has a sane minimum length (1 second) so the
      // user gets something visible to interact with.
      if (drag.kind === "create") {
        const seg = segments[drag.index];
        if (seg.end_seconds - seg.start_seconds < 0.2) {
          const lower = drag.index > 0 ? segments[drag.index - 1].end_seconds : 0;
          const upper = drag.index < segments.length - 1
            ? segments[drag.index + 1].start_seconds
            : duration;
          const desiredEnd = Math.min(seg.start_seconds + 1, upper);
          if (desiredEnd > seg.start_seconds + 0.1) {
            const next = segments.slice();
            next[drag.index] = { ...seg, end_seconds: snap(desiredEnd) };
            onsegmentschange(next);
          } else if (seg.start_seconds - 0.5 >= lower) {
            // No room on the right — try expanding left.
            const next = segments.slice();
            next[drag.index] = {
              ...seg,
              start_seconds: snap(seg.start_seconds - 0.5),
              end_seconds: snap(seg.start_seconds + 0.5),
            };
            onsegmentschange(next);
          }
        }
      }
      drag = null;
      try { timelineEl?.releasePointerCapture(e.pointerId); } catch { /* */ }
    }
  }
</script>

<div
  bind:this={timelineEl}
  class="relative h-10 bg-ink-800 rounded select-none touch-none cursor-crosshair border border-ink-700"
  onpointerdown={timelinePointerDown}
  onpointermove={timelinePointerMove}
  onpointerup={timelinePointerUp}
  onpointercancel={timelinePointerUp}
  role="slider"
  aria-valuemin="0"
  aria-valuemax={duration}
  aria-valuenow={playhead}
  aria-label="Video timeline — drag to create segments"
  tabindex="-1"
>
  {#each segments as seg, i (i)}
    <div
      class="absolute top-1 bottom-1 bg-indigo-500/70 hover:bg-indigo-500 border border-indigo-300 rounded cursor-move"
      style="left: {pct(seg.start_seconds)}%; width: {pct(seg.end_seconds) - pct(seg.start_seconds)}%;"
      onpointerdown={(e) => segmentPointerDown(i, e)}
      role="presentation"
    >
      <div
        class="absolute left-0 top-0 bottom-0 w-1.5 bg-indigo-200 cursor-ew-resize"
        onpointerdown={(e) => handlePointerDown(i, "left", e)}
        role="presentation"
      ></div>
      <div
        class="absolute right-0 top-0 bottom-0 w-1.5 bg-indigo-200 cursor-ew-resize"
        onpointerdown={(e) => handlePointerDown(i, "right", e)}
        role="presentation"
      ></div>
      <span class="absolute inset-0 flex items-center justify-center text-[10px] text-white/90 pointer-events-none font-medium tabular-nums">
        {i + 1}
      </span>
    </div>
  {/each}
  <!-- Playhead -->
  <div
    class="absolute top-0 bottom-0 w-px bg-amber-400 pointer-events-none"
    style="left: {pct(playhead)}%;"
  ></div>
</div>
<div class="flex justify-between text-[10px] text-slate-500 mt-1 tabular-nums">
  <span>00:00</span>
  <span>{duration > 0 ? formatTime(duration) : "—"}</span>
</div>
