<script lang="ts">
  import type { Segment } from "$lib/types";
  import { formatTime } from "$lib/segmentEditor";

  type Props = {
    segments: Segment[];
    duration: number;
    /** Commit a start-time edit on row `i` from raw input text. */
    onupdatestart: (i: number, text: string) => void;
    /** Commit an end-time edit on row `i` from raw input text. */
    onupdateend: (i: number, text: string) => void;
    /** Remove row `i`. */
    onremove: (i: number) => void;
    /** Append a segment at the playhead (the [+ Add segment] button). */
    onadd: () => void;
  };
  const { segments, duration, onupdatestart, onupdateend, onremove, onadd }: Props = $props();
</script>

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
        onchange={(e) => onupdatestart(i, (e.target as HTMLInputElement).value)}
        class="bg-ink-950 border border-ink-700 rounded px-2 py-1 text-xs font-mono w-24 text-slate-200 focus:border-indigo-500 focus:outline-none"
      />
      <span class="text-slate-600">→</span>
      <input
        type="text"
        value={formatTime(seg.end_seconds)}
        onchange={(e) => onupdateend(i, (e.target as HTMLInputElement).value)}
        class="bg-ink-950 border border-ink-700 rounded px-2 py-1 text-xs font-mono w-24 text-slate-200 focus:border-indigo-500 focus:outline-none"
      />
      <span class="text-slate-500 text-xs tabular-nums w-16">
        ({formatTime(seg.end_seconds - seg.start_seconds)})
      </span>
      <button
        type="button"
        onclick={() => onremove(i)}
        class="text-slate-600 hover:text-red-400 px-2 py-1 text-xs"
        aria-label="Remove segment"
      >✕</button>
    </div>
  {/each}
  <button
    type="button"
    onclick={onadd}
    disabled={duration <= 0}
    class="text-xs text-indigo-300 hover:text-indigo-200 px-1 btn-disabled"
  >+ Add segment at playhead</button>
</div>
