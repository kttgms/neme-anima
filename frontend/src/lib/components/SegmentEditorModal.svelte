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

  // Player source: the original stream first. If the browser can't decode it,
  // we show a manual "Convert for playback" button rather than auto-transcoding
  // — conversion is explicit and shows progress. `remux` (lossless HEVC rewrap)
  // is used when the browser can decode HEVC; `h264` (480p transcode) otherwise.
  // Captured/extracted frames always read the original at full resolution, so
  // the on-screen quality here never affects the dataset.
  let videoSrc = $state<string>("");
  let usingConverted = $state(false);
  let convertMode = $state<"remux" | "h264">("h264");
  let needsConvert = $state(false);
  let convertState = $state<"idle" | "running" | "ready" | "failed">("idle");
  let convertPct = $state(0);
  let convertError = $state<string>("");
  let triedH264Fallback = $state(false);

  let videoEl: HTMLVideoElement | undefined = $state(undefined);
  let timelineEl: HTMLDivElement | undefined = $state(undefined);

  // Play/pause mirrored from the element so the transport button can show the
  // right glyph without polling.
  let paused = $state<boolean>(true);

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

  /** Pick the conversion mode from what the browser can decode. If it can play
   *  HEVC in MP4 we only need a lossless rewrap (remux); otherwise we must
   *  re-encode to H.264. canPlayType can over-report HEVC, so handleVideoError
   *  falls back to h264 if a remux still won't play. */
  function detectConvertMode(): "remux" | "h264" {
    try {
      const v = document.createElement("video");
      const canHevc = v.canPlayType('video/mp4; codecs="hvc1.1.6.L93.B0"') !== "";
      return canHevc ? "remux" : "h264";
    } catch {
      return "h264";
    }
  }

  /** Whether this browser can decode the source's video codec well enough to
   *  render the original. HEVC/AV1 are probed via canPlayType; common codecs
   *  (h264, vp8/9) are assumed playable. This is what lets us surface the
   *  Convert button up-front: an undecodable codec plays black-with-audio and
   *  never fires a <video> error event, so onerror alone isn't enough. */
  function browserCanPlayCodec(vcodec: string | undefined): boolean {
    if (!vcodec) return true; // unknown — let the element try; onerror/videoWidth backstop
    const v = document.createElement("video");
    const can = (t: string) => v.canPlayType(t) !== "";
    switch (vcodec.toLowerCase()) {
      case "hevc":
      case "h265":
        return can('video/mp4; codecs="hvc1.1.6.L93.B0"')
          || can('video/mp4; codecs="hev1.1.6.L93.B0"');
      case "av1":
        return can('video/mp4; codecs="av01.0.05M.08"');
      case "vp9":
        return can('video/webm; codecs="vp9"') || can('video/mp4; codecs="vp09.00.10.08"');
      case "vp8":
        return can('video/webm; codecs="vp8"');
      default:
        return true; // h264/avc1 and friends — universally playable
    }
  }

  $effect(() => {
    const slug = projectsStore.active?.slug;
    if (!slug) return;
    videoSrc = api.sourceStreamUrl(slug, sourceIdx);
    usingConverted = false;
    needsConvert = false;
    convertState = "idle";
    convertPct = 0;
    convertError = "";
    triedH264Fallback = false;
    convertMode = detectConvertMode();

    // Always refresh duration from the server — the first time this is
    // hit for a given source it triggers an ffprobe; subsequent times it
    // returns the cached value. Cheap either way and guarantees the
    // timeline math has something to work with even if the source
    // record hadn't been probed yet.
    api.getSourceDuration(slug, sourceIdx)
      .then((r) => {
        if (duration <= 0 && r.duration_seconds > 0) duration = r.duration_seconds;
        if (fps <= 0 && r.fps > 0) fps = r.fps;
        // Proactively surface the Convert button when the browser can't decode
        // the source codec. Such sources (HEVC in MKV) play black-with-audio and
        // never fire a <video> error, so we can't wait for handleVideoError.
        if (!usingConverted && !needsConvert && !browserCanPlayCodec(r.vcodec)) {
          needsConvert = true;
        }
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
    // Backstop for the "audio plays, video is black, no error event" case: the
    // container loaded but the video track has no decodable dimensions. Catches
    // codecs the proactive check in the boot effect missed.
    if (!usingConverted && videoEl.videoWidth === 0 && videoEl.videoHeight === 0) {
      needsConvert = true;
    }
  }

  function handleTimeUpdate() {
    if (!videoEl) return;
    playhead = videoEl.currentTime;
  }

  /** The original stream failed to decode (HEVC/exotic codec/unsupported
   *  container) — or a converted file did. Show the Convert button, or fall
   *  back from remux to h264 once if the browser lied about HEVC support. */
  function handleVideoError() {
    if (usingConverted) {
      if (convertMode === "remux" && !triedH264Fallback) {
        triedH264Fallback = true;
        convertMode = "h264";
        usingConverted = false;
        needsConvert = true;
        convertState = "idle";
        return;
      }
      if (convertState !== "failed") {
        convertState = "failed";
        convertError = "Could not play the converted video.";
      }
      return;
    }
    needsConvert = true;
    convertState = "idle";
  }

  async function startConvert() {
    const slug = projectsStore.active?.slug;
    if (!slug) return;
    needsConvert = false;
    convertState = "running";
    convertPct = 0;
    convertError = "";
    try {
      await api.convertSource(slug, sourceIdx, convertMode);
    } catch (e) {
      convertState = "failed";
      convertError = e instanceof Error ? e.message : String(e);
      return;
    }
    await pollConvert(slug);
  }

  /** Poll convert status ~1s up to ~10min — an H.264 transcode of a
   *  feature-length source is slow; the remux is fast but I/O-bound off
   *  /mnt/c under WSL. */
  async function pollConvert(slug: string) {
    for (let i = 0; i < 600; i++) {
      let s;
      try {
        s = await api.getConvertStatus(slug, sourceIdx, convertMode);
      } catch {
        await new Promise((r) => setTimeout(r, 1000));
        continue;
      }
      convertPct = s.pct ?? 0;
      if (s.state === "ready") {
        convertState = "ready";
        usingConverted = true;
        videoSrc = `${api.sourcePreviewUrl(slug, sourceIdx, convertMode)}&t=${Date.now()}`;
        return;
      }
      if (s.state === "failed") {
        convertState = "failed";
        convertError = s.error || "Conversion failed";
        return;
      }
      await new Promise((r) => setTimeout(r, 1000));
    }
    convertState = "failed";
    convertError = "Conversion timed out";
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
    const t = videoEl.currentTime;
    capturing = true;
    captureError = "";
    try {
      const { frame, llm_error } = await api.captureSourceFrame(slug, sourceIdx, {
        time_seconds: t,
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
          onseeked={handleTimeUpdate}
          onplay={() => (paused = false)}
          onpause={() => (paused = true)}
          class="w-full h-auto max-h-[50vh]"
        >
          <track kind="captions" />
        </video>
      {/if}
      {#if needsConvert}
        <div class="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-ink-950/85 text-slate-300 text-sm px-4 text-center">
          <span>This video's format can't play directly in your browser.</span>
          <span class="text-slate-400 text-xs">
            Captured frames are always saved at the original full resolution.
          </span>
          <button
            type="button"
            onclick={startConvert}
            class="mt-1 px-3 py-1.5 rounded bg-sky-600 hover:bg-sky-500 text-white text-sm"
          >
            {triedH264Fallback ? "Re-encode to H.264" : "Convert for playback"}
          </button>
        </div>
      {/if}
      {#if convertState === "running"}
        <div class="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-ink-950/85 text-slate-300 text-sm px-6 text-center">
          <span>{convertMode === "remux" ? "Preparing video…" : "Transcoding for playback…"}</span>
          <div class="w-2/3 h-1.5 bg-ink-800 rounded overflow-hidden">
            <div class="h-full bg-sky-500 transition-[width]" style="width: {Math.max(2, convertPct)}%"></div>
          </div>
          <span class="text-slate-400 text-xs">{Math.round(convertPct)}%</span>
        </div>
      {/if}
      {#if convertState === "failed"}
        <div class="absolute inset-0 flex flex-col items-center justify-center bg-ink-950/85 text-amber-300 text-sm px-4 text-center">
          Could not load video.<br />{convertError}<br />
          <span class="text-slate-400 text-xs mt-1">You can still edit segments by typing times below.</span>
        </div>
      {/if}
    </div>

    <!-- Transport + capture bar. Mirrors the keyboard shortcuts as visible,
         clickable controls so they're discoverable, and hosts the one-click
         frame grab that feeds straight into the tagger. -->
    <div class="flex flex-wrap items-center gap-2 mb-1">
      <div class="flex items-center gap-1">
        <button
          type="button"
          onclick={() => seekBy(-5)}
          disabled={duration <= 0}
          title="Back 5 seconds (←)"
          class="px-2 py-1.5 text-xs rounded bg-ink-800 hover:bg-ink-700 text-slate-300 border border-ink-700 disabled:opacity-40 disabled:cursor-not-allowed tabular-nums"
        >« 5s</button>
        <button
          type="button"
          onclick={() => stepFrame(-1)}
          disabled={duration <= 0}
          title="Previous frame — pauses (,)"
          class="px-2 py-1.5 text-xs rounded bg-ink-800 hover:bg-ink-700 text-slate-300 border border-ink-700 disabled:opacity-40 disabled:cursor-not-allowed"
        >‹ frame</button>
        <button
          type="button"
          onclick={togglePlay}
          disabled={duration <= 0}
          title="Play / pause (Space or K)"
          aria-label={paused ? "Play" : "Pause"}
          class="px-3 py-1.5 text-xs rounded bg-ink-800 hover:bg-ink-700 text-slate-200 border border-ink-700 disabled:opacity-40 disabled:cursor-not-allowed w-9"
        >{paused ? "▶" : "⏸"}</button>
        <button
          type="button"
          onclick={() => stepFrame(1)}
          disabled={duration <= 0}
          title="Next frame — pauses (.)"
          class="px-2 py-1.5 text-xs rounded bg-ink-800 hover:bg-ink-700 text-slate-300 border border-ink-700 disabled:opacity-40 disabled:cursor-not-allowed"
        >frame ›</button>
        <button
          type="button"
          onclick={() => seekBy(5)}
          disabled={duration <= 0}
          title="Forward 5 seconds (→)"
          class="px-2 py-1.5 text-xs rounded bg-ink-800 hover:bg-ink-700 text-slate-300 border border-ink-700 disabled:opacity-40 disabled:cursor-not-allowed tabular-nums"
        >5s »</button>
      </div>

      <span class="text-[11px] text-slate-500 tabular-nums px-1">
        {formatTime(playhead)} / {duration > 0 ? formatTime(duration) : "—"}
      </span>

      <!-- Capture group, pushed to the right. -->
      <div class="flex items-center gap-2 ml-auto">
        {#if captureChars.length > 1}
          <label class="sr-only" for="capture-character">Capture target character</label>
          <select
            id="capture-character"
            bind:value={captureCharSlug}
            title="Character the captured frame is routed to"
            class="px-2 py-1.5 text-xs rounded bg-ink-950 border border-ink-700 text-slate-200 focus:border-indigo-500 focus:outline-none max-w-[10rem]"
          >
            {#each captureChars as c (c.slug)}
              <option value={c.slug}>{c.name}</option>
            {/each}
          </select>
        {/if}
        <button
          type="button"
          onclick={capture}
          disabled={duration <= 0 || capturing}
          title="Capture the current frame at full resolution and tag it (C)"
          class="px-3 py-1.5 text-xs rounded inline-flex items-center gap-1.5 gradient-accent text-white shadow-[0_2px_8px_rgba(99,102,241,0.3)] disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <span aria-hidden="true">📷</span>
          {capturing ? "Capturing…" : "Capture frame"}
        </button>
      </div>
    </div>
    <p class="text-[10px] text-slate-600 mb-3 px-0.5">
      Grabs the exact frame at the playhead, full-resolution from the source —
      {llmEnabled ? "WD14-tagged + LLM caption" : "WD14-tagged"}, saved straight to
      the Frames tab{captureChars.length <= 1 && captureChars[0]
        ? ` (${captureChars[0].name})`
        : ""}.
    </p>

    {#if captureError}
      <p class="text-xs text-amber-300 mb-3 px-1">{captureError}</p>
    {/if}

    <!-- Frames captured in this session. They're persisted the instant they're
         grabbed; this strip is just an in-modal confirmation + quick-undo. -->
    {#if captures.length > 0}
      <div class="mb-3">
        <div class="flex items-center gap-2 mb-1.5 flex-wrap">
          <span class="text-[11px] text-slate-400">
            Captured this session
            <span class="text-slate-200 tabular-nums">({captures.length})</span>
          </span>
          <span class="text-[10px] text-slate-600">
            · already saved to the Frames tab — the Save button below only persists segments
          </span>
        </div>
        <div class="flex gap-2 overflow-x-auto pb-1">
          {#each captures as cap, i (cap.filename)}
            <div class="relative flex-shrink-0 w-20 group">
              <img
                src={captureThumbUrl(cap.filename)}
                alt="Captured frame at {formatTime(cap.time)}"
                title="{cap.characterSlug} · {formatTime(cap.time)}{cap.llmError ? ' · caption failed' : ''}"
                draggable="false"
                class="w-20 h-12 object-cover rounded border border-ink-700 bg-ink-950 select-none"
              />
              <button
                type="button"
                onclick={() => removeCapture(i)}
                title="Delete this captured frame"
                aria-label="Delete captured frame"
                class="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-ink-900 border border-ink-600 text-slate-400 hover:text-red-400 hover:border-red-500 text-[11px] leading-none flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
              >✕</button>
              <div class="text-[9px] text-slate-500 tabular-nums text-center mt-0.5 truncate">
                {formatTime(cap.time)}
              </div>
            </div>
          {/each}
        </div>
      </div>
    {/if}

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
