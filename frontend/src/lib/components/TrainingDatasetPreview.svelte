<script lang="ts">
  import { trainingStore } from "$lib/stores/training.svelte";
  let preview = $derived(trainingStore.preview);
</script>

      <!-- ====================================================== -->
      <!-- ================= DATASET SUB-TAB ==================== -->
      <!-- ====================================================== -->

      {#if preview}
        <div class="bg-ink-900 border border-ink-700 rounded-xl p-4 mb-3">
          <h3 class="text-sm font-medium text-slate-200 mb-3">Dataset summary</h3>
          <div class="grid grid-cols-3 gap-3 mb-3 text-xs">
            <div class="bg-ink-950 rounded p-2">
              <div class="text-[10px] text-slate-500 uppercase tracking-wide">total images</div>
              <div class="font-mono text-slate-200">{preview.total_images}</div>
            </div>
            <div class="bg-ink-950 rounded p-2">
              <div class="text-[10px] text-slate-500 uppercase tracking-wide">with tags</div>
              <div class="font-mono text-slate-200">{preview.with_tags}</div>
            </div>
            <div class="bg-ink-950 rounded p-2">
              <div class="text-[10px] text-slate-500 uppercase tracking-wide">with NL desc.</div>
              <div class="font-mono text-slate-200">{preview.with_descriptions}</div>
            </div>
          </div>
          {#if preview.samples.length > 0}
            <details open>
              <summary class="text-xs text-slate-400 cursor-pointer hover:text-slate-200 mb-2">
                Caption preview (first {preview.samples.length})
              </summary>
              <ul class="space-y-2 text-[11px] font-mono text-slate-400">
                {#each preview.samples as s}
                  <li class="bg-ink-950 rounded p-2">
                    <div class="text-slate-300 truncate">{s.filename}</div>
                    <div class="text-slate-500 mt-1 break-words">{s.rendered}</div>
                  </li>
                {/each}
              </ul>
            </details>
          {:else}
            <p class="text-[11px] text-slate-500">
              No frames yet — extract some from the Frames tab first.
            </p>
          {/if}
        </div>
      {:else}
        <div class="bg-ink-900 border border-ink-700 rounded-xl p-4 text-sm text-slate-400">
          Loading dataset preview…
        </div>
      {/if}
