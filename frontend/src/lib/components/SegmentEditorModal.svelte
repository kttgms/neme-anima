<script lang="ts">
  import * as api from "$lib/api";
  import { projectsStore } from "$lib/stores/projects.svelte";
  import type { Segment, Source } from "$lib/types";

  type Props = {
    source: Source;
    sourceIdx: number;
    onClose: () => void;
  };
  const { source, sourceIdx, onClose }: Props = $props();

  // ---------------- formatting helpers ----------------

  /** Format a number of seconds as ``mm:ss.s`` (or ``hh:mm:ss.s`` past
   *  one hour). The tenth-of-a-second precision matches the snap grid the
   *  rest of the modal works on, so what the user sees is what they save. */
  function formatTime(seconds: number): string {
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
  function parseTime(text: string): number | null {
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
  function snap(seconds: number): number {
    return Math.round(seconds * 10) / 10;
  }

  // ---------------- local state ----------------

  // Segments edited in-memory until the user clicks Save. Cloned from the
  // source so cancelling preserves the persisted shape; sorted ascending
  // because the UI assumes that order when computing neighbour bounds.
  // svelte-ignore state_referenced_locally
  let segments = $state<Segment[]>(
    [...source.segments].sort((a, b) => a.start_seconds - b.start_seconds),
  );

  // Total video duration. Initially seeded from the cached value on the
  // source; refreshed via the duration endpoint when the modal opens (and
  // again if the <video> element reports a different number once it has
  // loaded metadata — duration from decoding the file is the most reliable
  // source of truth for clamping).
  // svelte-ignore state_referenced_locally
  let duration = $state<number>(source.duration_seconds ?? 0);
  // svelte-ignore state_referenced_locally
  let fps = $state<number>(source.fps ?? 0);

  // Current playhead time, mirrored from the <video> element so the
  // timeline indicator stays in sync without redundant requestAnimationFrame
  // loops.
  let playhead = $state<number>(0);

  // Two URLs: try the original stream first. If the browser can't decode
  // the container/codec, the error handler swaps to the lazy-transcoded
  // 480p preview. The preview URL stays bound to the polling status so
  // we can show "preparing" feedback if the backend is still encoding.
  let videoSrc = $state<string>("");
  let usingPreview = $state(false);
  let previewStatus = $state<"idle" | "preparing" | "ready" | "failed">("idle");
  let previewError = $state<string>("");

  let videoEl: HTMLVideoElement | undefined = $state(undefined);
  let timelineEl: HTMLDivElement | undefined = $state(undefined);

  let saving = $state(false);
  let saveError = $state<string>("");

  // ---------------- url + duration boot ----------------

  $effect(() => {
    const slug = projectsStore.active?.slug;
    if (!slug) return;
    videoSrc = api.sourceStreamUrl(slug, sourceIdx);
    usingPreview = false;
    previewStatus = "idle";
    previewError = "";

    // Always refresh duration from the server — the first time this is
    // hit for a given source it triggers an ffprobe; subsequent times it
    // returns the cached value. Cheap either way and guarantees the
    // timeline math has something to work with even if the source
    // record hadn't been probed yet.
    api.getSourceDuration(slug, sourceIdx)
      .then((r) => {
        if (duration <= 0 && r.duration_seconds > 0) duration = r.duration_seconds;
        if (fps <= 0 && r.fps > 0) fps = r.fps;
      })
      .catch(() => {
        // Non-fatal — the <video> element will tell us the duration once
        // it loads metadata. We just won't have an upper bound until then.
      });
  });

  function handleLoadedMetadata() {
    if (!videoEl) return;
    if (Number.isFinite(videoEl.duration) && videoEl.duration > 0) {
      // <video>.duration is the authoritative number once decoding starts.
      duration = videoEl.duration;
    }
  }

  function handleTimeUpdate() {
    if (!videoEl) return;
    playhead = videoEl.currentTime;
  }

  /** Browser refused to decode the original stream (HEVC, exotic codec,
   *  unsupported container). Switch to the transcoded preview. The /preview
   *  endpoint either returns the cached MP4 (200) or kicks off ffmpeg and
   *  returns 202 — we probe with a HEAD-style fetch first so we can show
   *  "preparing" feedback before the <video> element starts spamming
   *  errors against an endpoint that isn't ready yet. */
  async function handleVideoError() {
    if (usingPreview) {
      // Both stream and preview failed — surface the error.
      if (previewStatus !== "failed") {
        previewStatus = "failed";
        previewError = "Could not load video preview";
      }
      return;
    }
    const slug = projectsStore.active?.slug;
    if (!slug) return;
    usingPreview = true;
    previewStatus = "preparing";
    await pollPreviewReady(slug);
  }

  async function pollPreviewReady(slug: string) {
    const url = api.sourcePreviewUrl(slug, sourceIdx);
    // Up to two minutes of polling — ffmpeg on a long source can take
    // tens of seconds. After that we give up and tell the user.
    for (let i = 0; i < 60; i++) {
      try {
        const resp = await fetch(url, { method: "GET", headers: { Range: "bytes=0-0" } });
        if (resp.status === 200 || resp.status === 206) {
          previewStatus = "ready";
          // Cache-bust so the <video> element doesn't show a stale 202
          // response cached by the browser when it first tried to load.
          videoSrc = `${url}?t=${Date.now()}`;
          return;
        }
        if (resp.status !== 202) {
          previewStatus = "failed";
          previewError = `Preview endpoint returned ${resp.status}`;
          return;
        }
      } catch (e) {
        // Transient network error — try again.
      }
      await new Promise((r) => setTimeout(r, 2000));
    }
    previewStatus = "failed";
    previewError = "Preview did not become ready in time";
  }

  // ---------------- segment math ----------------

  /** True iff every segment is well-formed, in-range, and non-overlapping
   *  with its neighbours. Save is gated on this so server-side validation
   *  is the secondary check, not the primary one. */
  let valid = $derived.by(() => {
    if (duration <= 0) return segments.length === 0;
    for (let i = 0; i < segments.length; i++) {
      const s = segments[i];
      if (!(s.end_seconds > s.start_seconds)) return false;
      if (s.start_seconds < 0) return false;
      if (s.end_seconds > duration + 0.05) return false;
      if (i > 0 && s.start_seconds < segments[i - 1].end_seconds) return false;
    }
    return true;
  });

  let totalSelectedSeconds = $derived(
    segments.reduce((a, s) => a + (s.end_seconds - s.start_seconds), 0),
  );

  function pct(seconds: number): number {
    if (duration <= 0) return 0;
    return Math.max(0, Math.min(100, (seconds / duration) * 100));
  }

  // ---------------- segment mutations ----------------

  /** Append a new segment at the playhead position, sized to whatever
   *  remains until the next segment (capped at 5s by default). Falls back
   *  to a fixed 5-second range at time zero if the playhead is over an
   *  existing segment. Re-sorts so list order matches start_time. */
  function addSegmentAtPlayhead() {
    if (duration <= 0) return;
    let at = snap(playhead);
    // Don't drop a new range inside an existing one.
    if (segments.some((s) => at >= s.start_seconds && at < s.end_seconds)) {
      // Try the first gap.
      const gap = firstGap();
      if (gap === null) return;
      at = gap.start;
    }
    const upperBound = nextStartAfter(at);
    const desiredLen = Math.min(5, upperBound - at);
    if (desiredLen <= 0) return;
    segments = [
      ...segments,
      { start_seconds: at, end_seconds: snap(at + desiredLen) },
    ].sort((a, b) => a.start_seconds - b.start_seconds);
  }

  function firstGap(): { start: number; end: number } | null {
    if (duration <= 0) return null;
    let cursor = 0;
    for (const s of segments) {
      if (s.start_seconds > cursor) return { start: cursor, end: s.start_seconds };
      cursor = Math.max(cursor, s.end_seconds);
    }
    if (cursor < duration) return { start: cursor, end: duration };
    return null;
  }

  function nextStartAfter(t: number): number {
    for (const s of segments) {
      if (s.start_seconds > t) return s.start_seconds;
    }
    return duration > 0 ? duration : t + 5;
  }

  function prevEndBefore(t: number): number {
    let best = 0;
    for (const s of segments) {
      if (s.end_seconds <= t) best = Math.max(best, s.end_seconds);
    }
    return best;
  }

  function removeSegment(i: number) {
    segments = segments.filter((_, idx) => idx !== i);
  }

  function updateSegmentStart(i: number, text: string) {
    const parsed = parseTime(text);
    if (parsed === null) return;
    const seg = segments[i];
    const lower = i > 0 ? segments[i - 1].end_seconds : 0;
    const upper = seg.end_seconds - 0.1;
    const clamped = snap(Math.max(lower, Math.min(parsed, upper)));
    segments[i] = { ...seg, start_seconds: clamped };
  }

  function updateSegmentEnd(i: number, text: string) {
    const parsed = parseTime(text);
    if (parsed === null) return;
    const seg = segments[i];
    const lower = seg.start_seconds + 0.1;
    const upper = i < segments.length - 1
      ? segments[i + 1].start_seconds
      : (duration > 0 ? duration : parsed);
    const clamped = snap(Math.max(lower, Math.min(parsed, upper)));
    segments[i] = { ...seg, end_seconds: clamped };
  }

  // ---------------- pointer drag on timeline ----------------

  // Active drag descriptor. `null` when idle. Stored at module-scope (well,
  // component-scope) so the global pointerup handler can finalise without
  // having to thread state through every event.
  let drag = $state<
    | { kind: "create"; startTime: number; index: number }
    | { kind: "move"; index: number; offsetTime: number; origStart: number; origEnd: number }
    | { kind: "resize-left"; index: number }
    | { kind: "resize-right"; index: number }
    | null
  >(null);

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
    segments = merged;
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
      segments[i] = { ...seg, start_seconds: snap(nextStart), end_seconds: snap(nextEnd) };
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
      segments[i] = {
        ...seg,
        start_seconds: snap(clampedStart),
        end_seconds: snap(clampedStart + len),
      };
    } else if (drag.kind === "resize-left") {
      const i = drag.index;
      const seg = segments[i];
      const lower = i > 0 ? segments[i - 1].end_seconds : 0;
      const upper = seg.end_seconds - 0.1;
      const clampedT = Math.max(lower, Math.min(t, upper));
      segments[i] = { ...seg, start_seconds: snap(clampedT) };
    } else if (drag.kind === "resize-right") {
      const i = drag.index;
      const seg = segments[i];
      const lower = seg.start_seconds + 0.1;
      const upper = i < segments.length - 1
        ? segments[i + 1].start_seconds
        : (duration > 0 ? duration : t);
      const clampedT = Math.max(lower, Math.min(t, upper));
      segments[i] = { ...seg, end_seconds: snap(clampedT) };
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
            segments[drag.index] = { ...seg, end_seconds: snap(desiredEnd) };
          } else if (seg.start_seconds - 0.5 >= lower) {
            // No room on the right — try expanding left.
            segments[drag.index] = {
              ...seg,
              start_seconds: snap(seg.start_seconds - 0.5),
              end_seconds: snap(seg.start_seconds + 0.5),
            };
          }
        }
      }
      drag = null;
      try { timelineEl?.releasePointerCapture(e.pointerId); } catch { /* */ }
    }
  }

  // ---------------- keyboard ----------------

  function handleKey(e: KeyboardEvent) {
    if (e.key === "Escape") {
      onClose();
      return;
    }
    if (!videoEl || duration <= 0) return;
    const step = e.shiftKey ? 1 : 0.1;
    if (e.key === "ArrowLeft") {
      e.preventDefault();
      videoEl.currentTime = Math.max(0, snap(videoEl.currentTime - step));
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      videoEl.currentTime = Math.min(duration, snap(videoEl.currentTime + step));
    } else if (e.key === " ") {
      e.preventDefault();
      if (videoEl.paused) videoEl.play(); else videoEl.pause();
    } else if (e.key === "[" || e.key === "]") {
      e.preventDefault();
      markAtPlayhead(e.key === "[" ? "start" : "end");
    }
  }

  /** ``[`` and ``]`` shortcut: bind the playhead to the open segment's
   *  start or end. If no segment is currently "open" (last-added with an
   *  un-set end, or the segment containing the playhead) we instead create
   *  a new one rooted at the playhead. */
  function markAtPlayhead(side: "start" | "end") {
    const t = snap(playhead);
    // Find a segment we're inside, or the most recent one to extend.
    const idx = segments.findIndex(
      (s) => t >= s.start_seconds && t <= s.end_seconds,
    );
    if (idx !== -1) {
      if (side === "start") updateSegmentStart(idx, formatTime(t));
      else updateSegmentEnd(idx, formatTime(t));
      return;
    }
    addSegmentAtPlayhead();
  }

  // ---------------- save ----------------

  async function save() {
    const slug = projectsStore.active?.slug;
    if (!slug || !valid || saving) return;
    saving = true;
    saveError = "";
    try {
      await api.saveSourceSegments(slug, sourceIdx, segments);
      await projectsStore.load(slug); // refresh row state (incl. cache flag)
      onClose();
    } catch (e: any) {
      saveError = e?.message || "Save failed";
    } finally {
      saving = false;
    }
  }

  let basename = $derived(source.path.split("/").pop() ?? source.path);
</script>

<svelte:window onkeydown={handleKey} />

<div
  class="fixed inset-0 z-50 flex items-center justify-center bg-ink-950/70 backdrop-blur-sm"
  role="presentation"
  onclick={onClose}
>
  <div
    role="dialog"
    aria-modal="true"
    aria-labelledby="segment-editor-title"
    tabindex="-1"
    class="bg-ink-900 border border-ink-700 rounded-xl shadow-2xl p-5 max-w-4xl w-full mx-4 max-h-[92vh] overflow-y-auto"
    onclick={(e) => e.stopPropagation()}
    onkeydown={(e) => e.stopPropagation()}
  >
    <div class="flex items-start justify-between mb-4">
      <div>
        <h2 id="segment-editor-title" class="text-base font-semibold text-slate-100">
          Edit segments
        </h2>
        <p class="text-xs text-slate-500 truncate max-w-xl" title={source.path}>
          {basename}
          {#if duration > 0}
            <span class="text-slate-600">·</span>
            <span class="tabular-nums">{formatTime(duration)}</span>
          {/if}
          {#if fps > 0}
            <span class="text-slate-600">·</span>
            <span class="tabular-nums">{fps.toFixed(2)} fps</span>
          {/if}
        </p>
      </div>
      <button
        type="button"
        onclick={onClose}
        class="text-slate-500 hover:text-slate-300 text-xl leading-none"
        aria-label="Close"
      >✕</button>
    </div>

    <!-- Player. Aspect-ratio kept fluid so portrait videos don't blow up
         the dialog; max-height bounded so super-wide content doesn't
         crowd out the timeline + list. -->
    <div class="relative bg-ink-950 rounded overflow-hidden mb-2 flex items-center justify-center" style="max-height: 50vh;">
      {#if videoSrc}
        <video
          bind:this={videoEl}
          src={videoSrc}
          controls
          preload="metadata"
          onerror={handleVideoError}
          onloadedmetadata={handleLoadedMetadata}
          ontimeupdate={handleTimeUpdate}
          class="w-full h-auto max-h-[50vh]"
        >
          <track kind="captions" />
        </video>
      {/if}
      {#if usingPreview && previewStatus === "preparing"}
        <div class="absolute inset-0 flex items-center justify-center bg-ink-950/80 text-slate-300 text-sm">
          Preparing video preview… (transcoding for browser playback)
        </div>
      {/if}
      {#if previewStatus === "failed"}
        <div class="absolute inset-0 flex items-center justify-center bg-ink-950/80 text-amber-300 text-sm px-4 text-center">
          Could not load video.<br />{previewError}<br />
          <span class="text-slate-400 text-xs mt-1">You can still edit segments by typing times below.</span>
        </div>
      {/if}
    </div>

    <!-- Timeline. Clicking + dragging on empty space creates a new
         segment; existing segments can be moved/resized with the handles. -->
    <div class="mb-2">
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
    </div>

    <!-- Segments list. Editable mm:ss inputs let users type-correct
         what the drag produced, and the [+ Add segment] button gives a
         keyboard path that doesn't require pointer drag. -->
    <div class="space-y-1.5 mb-3">
      {#if segments.length === 0}
        <p class="text-xs text-slate-500 italic px-1">
          No segments — the entire video will be processed.
          Click on the timeline (or [+ Add segment]) to restrict to specific time ranges.
        </p>
      {/if}
      {#each segments as seg, i (i)}
        <div class="flex items-center gap-2 text-sm">
          <span class="text-slate-500 w-6 text-right tabular-nums">#{i + 1}</span>
          <input
            type="text"
            value={formatTime(seg.start_seconds)}
            onchange={(e) => updateSegmentStart(i, (e.target as HTMLInputElement).value)}
            class="bg-ink-950 border border-ink-700 rounded px-2 py-1 text-xs font-mono w-24 text-slate-200 focus:border-indigo-500 focus:outline-none"
          />
          <span class="text-slate-600">→</span>
          <input
            type="text"
            value={formatTime(seg.end_seconds)}
            onchange={(e) => updateSegmentEnd(i, (e.target as HTMLInputElement).value)}
            class="bg-ink-950 border border-ink-700 rounded px-2 py-1 text-xs font-mono w-24 text-slate-200 focus:border-indigo-500 focus:outline-none"
          />
          <span class="text-slate-500 text-xs tabular-nums w-16">
            ({formatTime(seg.end_seconds - seg.start_seconds)})
          </span>
          <button
            type="button"
            onclick={() => removeSegment(i)}
            class="text-slate-600 hover:text-red-400 px-2 py-1 text-xs"
            aria-label="Remove segment"
          >✕</button>
        </div>
      {/each}
      <button
        type="button"
        onclick={addSegmentAtPlayhead}
        disabled={duration <= 0}
        class="text-xs text-indigo-300 hover:text-indigo-200 px-1 disabled:opacity-40 disabled:cursor-not-allowed"
      >+ Add segment at playhead</button>
    </div>

    <!-- Status row: total selected time + keyboard cheatsheet. -->
    <div class="flex items-center justify-between text-[11px] text-slate-500 mb-4 px-1">
      <span>
        {#if segments.length === 0}
          Whole video will be processed
        {:else}
          {segments.length} segment{segments.length === 1 ? "" : "s"} ·
          <span class="text-slate-300 tabular-nums">{formatTime(totalSelectedSeconds)}</span> selected
        {/if}
      </span>
      <span class="hidden md:inline">
        ←/→ seek 0.1s · Shift+←/→ 1s · Space play/pause · [ ] mark in/out · Esc cancel
      </span>
    </div>

    {#if saveError}
      <p class="text-xs text-red-400 mb-2 px-1">{saveError}</p>
    {/if}

    <div class="flex justify-end gap-2">
      <button
        type="button"
        onclick={onClose}
        class="px-3 py-1.5 text-xs rounded bg-ink-800 hover:bg-ink-700 text-slate-300 border border-ink-700"
      >Cancel</button>
      <button
        type="button"
        onclick={save}
        disabled={saving || !valid}
        class="px-3 py-1.5 text-xs rounded gradient-accent text-white shadow-[0_2px_8px_rgba(99,102,241,0.3)] disabled:opacity-40 disabled:cursor-not-allowed"
      >{saving ? "Saving…" : "Save"}</button>
    </div>
  </div>
</div>
