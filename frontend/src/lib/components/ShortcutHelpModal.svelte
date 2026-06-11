<script lang="ts">
  import { focusTrap } from "$lib/actions/focusTrap";
  import { defaultShortcuts } from "$lib/shortcuts.svelte";

  type Props = { onclose: () => void };
  const { onclose }: Props = $props();

  // Collapse the two clear-selection rows (d, Esc) into one display row with
  // both keys, so the list reads cleanly. Everything else is 1:1.
  type Row = { keys: string[]; description: string };
  const rows: Row[] = (() => {
    const out: Row[] = [];
    const seen = new Map<string, Row>();
    for (const s of defaultShortcuts) {
      const existing = seen.get(s.description);
      if (existing) {
        existing.keys.push(s.label);
        continue;
      }
      const row: Row = { keys: [s.label], description: s.description };
      seen.set(s.description, row);
      out.push(row);
    }
    return out;
  })();
</script>

<div
  class="fixed inset-0 z-50 bg-ink-950/70 backdrop-blur-sm flex items-center justify-center p-4"
  role="presentation"
  onclick={onclose}
  onkeydown={(e) => { if (e.key === "Escape") onclose(); }}
>
  <div
    role="dialog"
    aria-modal="true"
    aria-labelledby="shortcut-help-title"
    tabindex="-1"
    use:focusTrap={{ onEscape: onclose }}
    class="bg-ink-900 border border-ink-700 rounded-xl shadow-2xl p-5 max-w-sm w-full"
    onclick={(e) => e.stopPropagation()}
    onkeydown={(e) => e.stopPropagation()}
  >
    <div class="flex items-center justify-between mb-3">
      <h2 id="shortcut-help-title" class="text-base font-semibold text-slate-100">
        Keyboard shortcuts
      </h2>
      <button
        type="button"
        onclick={onclose}
        aria-label="Close"
        title="Close"
        class="w-6 h-6 rounded text-slate-400 hover:text-slate-100 hover:bg-ink-800 flex items-center justify-center text-lg leading-none transition-colors"
      >×</button>
    </div>

    <p class="text-[11px] text-slate-500 mb-3 leading-snug">
      Active on the Frames tab (except <kbd class="px-1 rounded bg-ink-800 border border-ink-700 text-slate-300">?</kbd>,
      which works anywhere). Ignored while typing in a field or with a modal open.
      The selection actions (Tag / Describe / Regex) need at least one selected frame.
    </p>

    <ul class="space-y-1.5">
      {#each rows as row}
        <li class="flex items-center justify-between gap-3 text-sm">
          <span class="text-slate-300">{row.description}</span>
          <span class="flex gap-1 flex-shrink-0">
            {#each row.keys as k}
              <kbd class="px-2 py-0.5 rounded bg-ink-800 border border-ink-700 text-slate-200 text-xs font-mono">{k}</kbd>
            {/each}
          </span>
        </li>
      {/each}
    </ul>
  </div>
</div>
