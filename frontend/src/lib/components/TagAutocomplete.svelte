<!-- frontend/src/lib/components/TagAutocomplete.svelte -->
<script lang="ts">
  import { categoryColor, formatCount, type Suggestion } from "$lib/tagSearch";

  type Props = {
    suggestions: Suggestion[];
    activeIndex: number;
    /** The tag input this dropdown is anchored to (for positioning). */
    anchor: HTMLElement;
    onaccept: (s: Suggestion) => void;
    onhover: (index: number) => void;
  };
  const { suggestions, activeIndex, anchor, onaccept, onhover }: Props = $props();

  // Position in a fixed layer off the anchor's rect so we escape the
  // overflow-hidden grid-hover panel. Flip above the input if there's no room
  // below. Recomputed whenever the suggestion set changes.
  let top = $state(0);
  let left = $state(0);
  let width = $state(0);
  let flipUp = $state(false);

  const ROW = 26; // px per row, for the flip-up height estimate
  $effect(() => {
    // Touch `suggestions` so this recomputes as the list grows/shrinks.
    const count = suggestions.length;
    const r = anchor.getBoundingClientRect();
    const estHeight = Math.min(count, 10) * ROW + 8;
    flipUp = r.bottom + estHeight > window.innerHeight && r.top > estHeight;
    top = flipUp ? r.top - estHeight : r.bottom + 2;
    left = r.left;
    width = Math.max(r.width, 220);
  });
</script>

<ul
  role="listbox"
  style="position:fixed; top:{top}px; left:{left}px; min-width:{width}px;"
  class="z-50 max-h-64 overflow-y-auto rounded-lg border border-ink-700 bg-ink-950/95 backdrop-blur-sm shadow-xl py-1 text-[11px]"
>
  {#each suggestions as s, i (s.entry.name)}
    <li role="option" aria-selected={i === activeIndex}>
      <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
      <button
        type="button"
        onmousedown={(e) => e.preventDefault()}
        onclick={() => onaccept(s)}
        onmouseenter={() => onhover(i)}
        class="flex w-full items-center gap-2 px-2 py-1 text-left
          {i === activeIndex ? 'bg-accent-500/30' : 'hover:bg-white/5'}"
      >
        <span class="flex-1 truncate {categoryColor(s.entry.category)}">{s.entry.name}</span>
        {#if s.viaAlias}
          <span class="text-[9px] text-slate-500">alias</span>
        {/if}
        <span class="text-[9px] tabular-nums text-slate-500">{formatCount(s.entry.count)}</span>
      </button>
    </li>
  {/each}
</ul>
