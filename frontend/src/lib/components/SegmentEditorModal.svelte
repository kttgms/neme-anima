<script lang="ts">
  import * as api from "$lib/api";
  import { projectsStore } from "$lib/stores/projects.svelte";
  import type { Segment, Source } from "$lib/types";
  import { focusTrap } from "$lib/actions/focusTrap";
  import { formatTime, parseTime, snap } from "$lib/segmentEditor";
  import SegmentList from "$lib/components/SegmentList.svelte";
  import TimelineEditor from "$lib/components/TimelineEditor.svelte";
  import VideoPreview from "./VideoPreview.svelte";

  type Props = {
    source: Source;
    sourceIdx: number;
    onClose: () => void;
  };
  const { source, sourceIdx, onClose }: Props = $props();

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

  let videoEl: HTMLVideoElement | undefined = $state(undefined);

  let saving = $state(false);
  let saveError = $state<string>("");

  // ---------------- frame capture ----------------

  // Characters the captured frame can be routed to. The segment editor is a
  // per-source dialog with no "active character" of its own, so when a project
  // has more than one we let the user pick the destination; with a single
  // character it routes there silently.
  let captureChars = $derived(projectsStore.active?.characters ?? []);
  // svelte-ignore state_referenced_locally
  let captureCharSlug = $state<string>(
    projectsStore.active?.characters?.[0]?.slug ?? "default",
  );
  $effect(() => {
    // Keep the selection valid as the active project / character set changes;
    // default to the first character when the current pick disappears.
    const slugs = (projectsStore.active?.characters ?? []).map((c) => c.slug);
    if (slugs.length && !slugs.includes(captureCharSlug)) {
      captureCharSlug = slugs[0];
    }
  });

  // Frames grabbed during this modal session. They're persisted server-side
  // the instant they're captured (independent of the segment Save button), so
  // this list is purely an in-modal confirmation strip — newest first.
  let captures = $state<
    { filename: string; characterSlug: string; time: number; llmError: string | null }[]
  >([]);
  let capturing = $state(false);
  let captureError = $state<string>("");

  // Whether an LLM caption is expected on each capture — drives the
  // "WD14 + caption" vs "WD14" hint next to the capture button.
  let llmEnabled = $derived(projectsStore.active?.llm?.enabled ?? false);

  // ---------------- url + duration boot ----------------

  // Probe duration/fps/codec once per source; VideoPreview consumes vcodec to
  // decide whether the original is browser-decodable.
  let vcodec = $state<string>("");
  $effect(() => {
    const s = projectsStore.active?.slug;
    if (!s) return;
    const idx = sourceIdx;
    api.getSourceDuration(s, idx)
      .then((r) => {
        if (duration <= 0 && r.duration_seconds > 0) duration = r.duration_seconds;
        if (fps <= 0 && r.fps > 0) fps = r.fps;
        vcodec = r.vcodec ?? "";
      })
      .catch(() => { /* <video> metadata backstops the duration */ });
  });

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

  // ---------------- transport ----------------

  /** Effective frame rate for frame-stepping. Falls back to 24 fps when the
   *  source hasn't been probed (or reports nothing) so the step buttons still
   *  move by a sensible increment instead of doing nothing. */
  function fpsEffective(): number {
    return fps > 0 ? fps : 24;
  }

  function togglePlay() {
    if (!videoEl) return;
    if (videoEl.paused) videoEl.play();
    else videoEl.pause();
  }

  /** Seek by a relative number of seconds, clamped to the clip. */
  function seekBy(deltaSeconds: number) {
    if (!videoEl || duration <= 0) return;
    const next = Math.max(0, Math.min(duration, videoEl.currentTime + deltaSeconds));
    videoEl.currentTime = next;
    playhead = next;
  }

  /** Step exactly one frame in ``dir`` (−1 / +1) and pause — frame-accurate
   *  stepping only makes sense on a still image. We seek to the *middle* of the
   *  target frame's display interval so the browser reliably decodes that frame
   *  (landing exactly on a boundary is prone to off-by-one rounding), and use
   *  ``floor()`` to recover the current frame index from that mid-frame time so
   *  repeated steps don't drift. */
  function stepFrame(dir: number) {
    if (!videoEl || duration <= 0) return;
    videoEl.pause();
    const f = 1 / fpsEffective();
    const cur = videoEl.currentTime;
    const frame = Math.floor(cur / f + 1e-4);
    const lastFrame = Math.max(0, Math.floor(duration / f - 1e-4));
    const target = Math.max(0, Math.min(lastFrame, frame + dir));
    const next = Math.min(duration, (target + 0.5) * f);
    videoEl.currentTime = next;
    playhead = next;
  }

  function frameIndexForTime(seconds: number): number | undefined {
    if (fps <= 0 || duration <= 0) return undefined;
    const f = 1 / fps;
    const lastFrame = Math.max(0, Math.floor(duration / f - 1e-4));
    return Math.max(0, Math.min(lastFrame, Math.floor(seconds / f + 1e-4)));
  }

  // ---------------- capture ----------------

  let slug = $derived(projectsStore.active?.slug ?? "");

  function captureThumbUrl(filename: string): string {
    return slug ? api.frameImageUrl(slug, filename) : "";
  }

  /** Grab the frame at the current playhead, send it to the server (which
   *  reads it full-resolution from the original file, WD14-tags it, and adds an
   *  LLM caption when LLM tagging is enabled), and prepend it to the session
   *  strip. Pauses first so the captured moment is locked and verifiable. */
  async function capture() {
    if (!slug || !videoEl || capturing || duration <= 0) return;
    videoEl.pause();
    capturing = true;
    captureError = "";
    try {
      await waitForPendingSeek(videoEl);
      const t = videoEl.currentTime;
      const frameIdx = frameIndexForTime(t);
      const { frame, llm_error } = await api.captureSourceFrame(slug, sourceIdx, {
        time_seconds: t,
        ...(frameIdx === undefined ? {} : { frame_idx: frameIdx }),
        character_slug: captureCharSlug || undefined,
      });
      captures = [
        {
          filename: frame.filename,
          characterSlug: frame.character_slug,
          time: t,
          llmError: llm_error,
        },
        ...captures,
      ];
      if (llm_error) {
        captureError =
          `Frame captured & WD14-tagged, but the LLM caption failed: ${llm_error}`;
      }
    } catch (e: any) {
      captureError = e?.message || "Capture failed";
    } finally {
      capturing = false;
    }
  }

  async function waitForPendingSeek(video: HTMLVideoElement): Promise<void> {
    if (!video.seeking) return;
    await new Promise<void>((resolve, reject) => {
      let done = false;
      const cleanup = () => {
        window.clearTimeout(timeout);
        video.removeEventListener("seeked", handleSeeked);
        video.removeEventListener("error", handleError);
      };
      const finish = (error?: Error) => {
        if (done) return;
        done = true;
        cleanup();
        if (error) reject(error);
        else resolve();
      };
      const handleSeeked = () => finish();
      const handleError = () => finish(new Error("Could not load the preview frame."));
      const timeout = window.setTimeout(
        () => finish(new Error("Timed out waiting for the preview frame.")),
        5000,
      );
      video.addEventListener("seeked", handleSeeked, { once: true });
      video.addEventListener("error", handleError, { once: true });
    });
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  }

  /** Remove a just-captured frame from the strip and delete it server-side. */
  async function removeCapture(i: number) {
    const cap = captures[i];
    if (!cap || !slug) return;
    captures = captures.filter((_, idx) => idx !== i);
    try {
      await api.deleteFrame(slug, cap.filename);
    } catch {
      /* Best-effort: the strip already dropped it; a stray file is harmless
         and the Frames tab's delete can mop it up later. */
    }
  }

  // ---------------- keyboard ----------------

  function handleKey(e: KeyboardEvent) {
    if (e.key === "Escape") {
      onClose();
      return;
    }
    // Don't hijack typing in the segment time inputs (or any future field).
    const tgt = e.target as HTMLElement | null;
    if (
      tgt &&
      (tgt.tagName === "INPUT" ||
        tgt.tagName === "TEXTAREA" ||
        tgt.isContentEditable)
    ) {
      return;
    }
    if (!videoEl || duration <= 0) return;
    // preventDefault on every handled key also suppresses the <video>
    // element's built-in key handling when it happens to be focused, so a key
    // never fires twice (e.g. Space toggling play here *and* natively).
    switch (e.key) {
      case " ":
      case "k":
      case "K":
        e.preventDefault();
        togglePlay();
        break;
      case "ArrowLeft":
        e.preventDefault();
        seekBy(e.shiftKey ? -1 : -5);
        break;
      case "ArrowRight":
        e.preventDefault();
        seekBy(e.shiftKey ? 1 : 5);
        break;
      case ",":
        e.preventDefault();
        stepFrame(-1);
        break;
      case ".":
        e.preventDefault();
        stepFrame(1);
        break;
      case "[":
      case "]":
        e.preventDefault();
        markAtPlayhead(e.key === "[" ? "start" : "end");
        break;
      case "c":
      case "C":
        e.preventDefault();
        capture();
        break;
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
  <!-- onclick only stops the backdrop's close-on-click from firing for clicks
       inside the dialog; it isn't an interactive control. Keyboard close is
       handled by handleKey (Escape) at the window level, so no keydown handler
       is needed here — and adding one (stopPropagation) would block the
       window-level shortcut handler. -->
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <div
    role="dialog"
    aria-modal="true"
    aria-labelledby="segment-editor-title"
    tabindex="-1"
    use:focusTrap={{}}
    class="bg-ink-900 border border-ink-700 rounded-xl shadow-2xl p-5 max-w-4xl w-full mx-4 max-h-[92vh] overflow-y-auto"
    onclick={(e) => e.stopPropagation()}
  >
    <div class="flex items-start justify-between mb-4">
      <div>
        <h2 id="segment-editor-title" class="text-base font-semibold text-slate-100">
          Segments &amp; frame capture
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

    <VideoPreview
      {sourceIdx}
      {duration}
      {playhead}
      {vcodec}
      bind:videoEl
      {captureChars}
      {captureCharSlug}
      {captures}
      {capturing}
      {captureError}
      {llmEnabled}
      {captureThumbUrl}
      onplayhead={(t) => (playhead = t)}
      onduration={(d) => (duration = d)}
      oncharchange={(s) => (captureCharSlug = s)}
      onseekby={seekBy}
      onstepframe={stepFrame}
      ontoggleplay={togglePlay}
      oncapture={capture}
      onremovecapture={removeCapture}
    />

    <!-- Timeline. Clicking + dragging on empty space creates a new
         segment; existing segments can be moved/resized with the handles. -->
    <div class="mb-2">
      <TimelineEditor
        {segments}
        {duration}
        {playhead}
        onsegmentschange={(next) => (segments = next)}
      />
    </div>

    <SegmentList
      {segments}
      {duration}
      onupdatestart={updateSegmentStart}
      onupdateend={updateSegmentEnd}
      onremove={removeSegment}
      onadd={addSegmentAtPlayhead}
    />

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
        ←/→ ±5s · Shift+←/→ ±1s · ,/. frame · Space/K play · C capture · [ ] mark in/out · Esc close
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
