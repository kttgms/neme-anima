<script lang="ts">
  import * as api from "$lib/api";
  import { untrack } from "svelte";
  import { projectsStore } from "$lib/stores/projects.svelte";
  import { formatTime } from "$lib/segmentEditor";

  type Capture = { filename: string; characterSlug: string; time: number; llmError: string | null };

  type Props = {
    sourceIdx: number;
    /** Probed duration from the shell (shared). VideoPreview reads it to
     *  gate disabled states + show the time ruler; it also reports a better
     *  duration up via `onduration` once <video> metadata loads. */
    duration: number;
    /** Current playhead (for the time display); the shell owns it and updates it
     *  from `onplayhead` (fired by the <video> timeupdate/seeked handlers). */
    playhead: number;
    /** Probed video codec, used to decide up-front whether the original is
     *  browser-decodable (HEVC → show Convert). Empty string = unknown. */
    vcodec: string;
    /** The shared <video> element ref. The shell owns it (transport/capture/
     *  keyboard operate on it); VideoPreview binds it. */
    videoEl: HTMLVideoElement | undefined;
    captureChars: { slug: string; name: string }[];
    captureCharSlug: string;
    captures: Capture[];
    capturing: boolean;
    captureError: string;
    llmEnabled: boolean;
    /** Thumbnail URL for a captured frame (shell owns the slug). */
    captureThumbUrl: (filename: string) => string;
    // ---- callbacks up to the shell ----
    onplayhead: (t: number) => void;
    onduration: (d: number) => void;
    oncharchange: (slug: string) => void;
    onseekby: (delta: number) => void;
    onstepframe: (dir: number) => void;
    ontoggleplay: () => void;
    oncapture: () => void;
    onremovecapture: (i: number) => void;
  };
  let {
    sourceIdx, duration, playhead, vcodec, videoEl = $bindable(),
    captureChars, captureCharSlug, captures, capturing, captureError, llmEnabled,
    captureThumbUrl, onplayhead, onduration, oncharchange,
    onseekby, onstepframe, ontoggleplay, oncapture, onremovecapture,
  }: Props = $props();

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

  // Play/pause mirrored from the element so the transport button can show the
  // right glyph without polling.
  let paused = $state<boolean>(true);

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
    const idx = sourceIdx; // deps: slug, sourceIdx, vcodec
    const vc = vcodec;
    untrack(() => {
      videoSrc = api.sourceStreamUrl(slug, idx);
      usingConverted = false;
      needsConvert = false;
      convertState = "idle";
      convertPct = 0;
      convertError = "";
      triedH264Fallback = false;
      convertMode = detectConvertMode();
      if (!browserCanPlayCodec(vc)) {
        loadCachedOrPromptConvert(slug);
      }
    });
  });

  function handleLoadedMetadata() {
    if (!videoEl) return;
    if (Number.isFinite(videoEl.duration) && videoEl.duration > 0) {
      onduration(videoEl.duration);
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
    onplayhead(videoEl.currentTime);
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

  /** On open of an undecodable source: if a conversion is already cached, play
   *  it directly; otherwise show the Convert button. Avoids re-converting (or
   *  even re-clicking) every time the editor is reopened. */
  async function loadCachedOrPromptConvert(slug: string) {
    try {
      const st = await api.getConvertStatus(slug, sourceIdx, convertMode);
      if (st.state === "ready") {
        needsConvert = false;
        convertState = "ready";
        usingConverted = true;
        videoSrc = `${api.sourcePreviewUrl(slug, sourceIdx, convertMode)}&t=${Date.now()}`;
        return;
      }
    } catch {
      // fall through to prompting a conversion
    }
    if (!usingConverted) needsConvert = true;
  }

  /** Delete the cached converted copy so it can be reclaimed / re-made, then
   *  reset to the Convert prompt (the original still can't play). */
  async function deletePreview() {
    const slug = projectsStore.active?.slug;
    if (!slug) return;
    try {
      await api.deleteSourcePreview(slug, sourceIdx);
    } catch {
      // Even if the delete failed server-side, reset the UI so the user can retry.
    }
    usingConverted = false;
    convertState = "idle";
    convertPct = 0;
    triedH264Fallback = false;
    needsConvert = true;
    videoSrc = api.sourceStreamUrl(slug, sourceIdx);
  }
</script>

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
  {#if usingConverted}
    <button
      type="button"
      onclick={deletePreview}
      title="Delete the converted copy (frees disk; you can re-convert anytime)"
      class="absolute top-2 right-2 z-10 px-2 py-1 rounded bg-ink-900/80 hover:bg-ink-800 text-slate-300 hover:text-white text-xs border border-ink-700"
    >Delete preview</button>
  {/if}
</div>

<!-- Transport + capture bar. Mirrors the keyboard shortcuts as visible,
     clickable controls so they're discoverable, and hosts the one-click
     frame grab that feeds straight into the tagger. -->
<div class="flex flex-wrap items-center gap-2 mb-1">
  <div class="flex items-center gap-1">
    <button
      type="button"
      onclick={() => onseekby(-5)}
      disabled={duration <= 0}
      title="Back 5 seconds (←)"
      class="px-2 py-1.5 text-xs rounded bg-ink-800 hover:bg-ink-700 text-slate-300 border border-ink-700 btn-disabled tabular-nums"
    >« 5s</button>
    <button
      type="button"
      onclick={() => onstepframe(-1)}
      disabled={duration <= 0}
      title="Previous frame — pauses (,)"
      class="px-2 py-1.5 text-xs rounded bg-ink-800 hover:bg-ink-700 text-slate-300 border border-ink-700 btn-disabled"
    >‹ frame</button>
    <button
      type="button"
      onclick={ontoggleplay}
      disabled={duration <= 0}
      title="Play / pause (Space or K)"
      aria-label={paused ? "Play" : "Pause"}
      class="px-3 py-1.5 text-xs rounded bg-ink-800 hover:bg-ink-700 text-slate-200 border border-ink-700 btn-disabled w-9"
    >{paused ? "▶" : "⏸"}</button>
    <button
      type="button"
      onclick={() => onstepframe(1)}
      disabled={duration <= 0}
      title="Next frame — pauses (.)"
      class="px-2 py-1.5 text-xs rounded bg-ink-800 hover:bg-ink-700 text-slate-300 border border-ink-700 btn-disabled"
    >frame ›</button>
    <button
      type="button"
      onclick={() => onseekby(5)}
      disabled={duration <= 0}
      title="Forward 5 seconds (→)"
      class="px-2 py-1.5 text-xs rounded bg-ink-800 hover:bg-ink-700 text-slate-300 border border-ink-700 btn-disabled tabular-nums"
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
        value={captureCharSlug}
        onchange={(e) => oncharchange((e.target as HTMLSelectElement).value)}
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
      onclick={oncapture}
      disabled={duration <= 0 || capturing}
      title="Capture the current frame at full resolution and tag it (C)"
      class="px-3 py-1.5 text-xs rounded inline-flex items-center gap-1.5 gradient-accent text-white shadow-[0_2px_8px_rgba(99,102,241,0.3)] btn-disabled"
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
            onclick={() => onremovecapture(i)}
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
