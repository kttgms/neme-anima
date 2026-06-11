<script lang="ts">
  import { matchesQuery, reorder } from "$lib/tagList";
  import TagPill from "./TagPill.svelte";

  type Props = {
    tags: string[];
    onchange: (next: string[]) => void;
    size?: "sm" | "md";
    autocomplete?: boolean;
    reorderable?: boolean;
    searchable?: boolean;
    selectable?: boolean;
    onselectionchange?: (selectedTags: string[]) => void;
    clearSelection?: () => void;
  };
  let {
    tags,
    onchange,
    size = "sm",
    autocomplete = false,
    reorderable = false,
    searchable = false,
    selectable = false,
    onselectionchange,
    clearSelection = $bindable(),
  }: Props = $props();

  // Sentinel rendered as the empty editable "+" placeholder.
  const PLACEHOLDER = " __new__";

  let addingTag = $state(false);
  let search = $state("");
  let dragFrom = $state<number | null>(null);
  // Insertion index (0..tags.length) the drop would land at — drives the "|"
  // marker. Computed from which side of the hovered pill's midpoint the
  // pointer is on.
  let dropIndex = $state<number | null>(null);

  // ---- middle-click selection (keyed by tag text) ----
  let selected = $state<Set<string>>(new Set());
  // Only count tags still present — a selected tag that's since been
  // deleted/renamed shouldn't linger in the surfaced selection.
  let selectedTags = $derived(tags.filter((t) => selected.has(t)));

  // Surface the selection to the parent whenever it (or the tag list) changes.
  // `selectedTags` is a fresh array on every recompute, so the parent's
  // `onselectionchange` handler MUST treat an equal selection as a no-op
  // (no state write) — otherwise this effect → parent write → effect re-run
  // forms an infinite update loop (effect_update_depth_exceeded). See the
  // guard in TagEditorPanel.onSelectionChange.
  $effect(() => {
    if (selectable) onselectionchange?.(selectedTags);
  });

  // Expose an imperative clear to the parent (bound via bind:clearSelection).
  clearSelection = () => {
    selected = new Set();
  };

  function toggleSelect(tag: string) {
    const next = new Set(selected);
    if (next.has(tag)) next.delete(tag);
    else next.add(tag);
    selected = next;
  }

  // Rendered list = working tags + the placeholder while adding.
  let pills = $derived(addingTag ? [...tags, PLACEHOLDER] : tags);

  function dedupe(list: string[]): string[] {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const t of list) {
      if (seen.has(t)) continue;
      seen.add(t);
      out.push(t);
    }
    return out;
  }

  function replaceTag(oldTag: string, next: string) {
    onchange(dedupe(tags.map((t) => (t === oldTag ? next : t))));
  }

  function deleteTag(oldTag: string) {
    onchange(tags.filter((t) => t !== oldTag));
  }

  function addTag(next: string) {
    addingTag = false;
    const trimmed = next.trim();
    if (!trimmed) return;
    onchange(dedupe([...tags, trimmed]));
  }

  // ---- drag-to-reorder ----
  function onDragStart(i: number, ev: DragEvent) {
    // Don't hijack text selection when a pill is in its edit input.
    const t = ev.target as HTMLElement | null;
    if (t && (t.tagName === "INPUT" || t.isContentEditable)) {
      ev.preventDefault();
      return;
    }
    dragFrom = i;
    if (ev.dataTransfer) {
      ev.dataTransfer.setData("text/plain", String(i));
      ev.dataTransfer.effectAllowed = "move";
    }
  }
  // Left half of the hovered pill → insert before it; right half → after it.
  function insertionIndex(i: number, ev: DragEvent): number {
    const rect = (ev.currentTarget as HTMLElement).getBoundingClientRect();
    return ev.clientX > rect.left + rect.width / 2 ? i + 1 : i;
  }
  function onDragOver(i: number, ev: DragEvent) {
    if (dragFrom === null) return;
    ev.preventDefault();
    if (ev.dataTransfer) ev.dataTransfer.dropEffect = "move";
    dropIndex = insertionIndex(i, ev);
  }
  function onContainerDragOver(ev: DragEvent) {
    if (dragFrom === null) return;
    ev.preventDefault();
    if (ev.dataTransfer) ev.dataTransfer.dropEffect = "move";
  }
  function onContainerDrop(ev: DragEvent) {
    ev.preventDefault();
    if (dragFrom === null || dropIndex === null) return;
    // reorder() removes `from` before inserting, so an insertion point past
    // the removed item shifts left by one.
    const to = dropIndex > dragFrom ? dropIndex - 1 : dropIndex;
    onchange(reorder(tags, dragFrom, to));
    dragFrom = null;
    dropIndex = null;
  }
  function onContainerDragLeave(ev: DragEvent) {
    if (dragFrom === null) return;
    const related = ev.relatedTarget as Node | null;
    if (related && (ev.currentTarget as HTMLElement).contains(related)) return;
    dropIndex = null;
  }
  function onDragEnd() {
    dragFrom = null;
    dropIndex = null;
  }

  // Size-dependent classes for the marker height (matches the modal's old
  // min-h on the "md" surface; the grid never shows a marker).
  let markerCls = $derived(
    size === "md"
      ? "w-0.5 self-stretch min-h-[1.5rem] bg-amber-400 rounded-full"
      : "w-0.5 self-stretch min-h-[1rem] bg-amber-400 rounded-full",
  );
  // The "+" button matches each surface's pill size family.
  let addBtnCls = $derived(
    size === "md"
      ? "px-2 py-0.5 text-[11.25px] leading-none rounded-full bg-white/15 hover:bg-white/25 text-white border border-white/10 transition-colors self-start"
      : "px-2 py-0.5 text-[9.5px] leading-none rounded-full bg-white/15 hover:bg-white/25 text-white border border-white/10 transition-colors",
  );
</script>

{#if searchable}
  <!-- Search: highlights matching tags in place (no filtering). -->
  <div class="relative mt-1.5">
    <input
      bind:value={search}
      placeholder="Search tags to highlight…"
      class="w-full pl-2 pr-7 py-1 text-xs bg-ink-950 border border-ink-700 rounded focus:outline-none focus:border-accent-500"
    />
    {#if search}
      <button
        type="button"
        onclick={() => (search = "")}
        aria-label="Clear search"
        class="absolute right-1 top-1/2 -translate-y-1/2 w-5 h-5 rounded text-slate-400 hover:text-slate-100 flex items-center justify-center"
      >×</button>
    {/if}
  </div>
{/if}

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
  ondragover={reorderable ? onContainerDragOver : undefined}
  ondrop={reorderable ? onContainerDrop : undefined}
  ondragleave={reorderable ? onContainerDragLeave : undefined}
  class={size === "md"
    ? "flex flex-wrap gap-1.5 content-start overflow-y-auto flex-1 min-h-0 pr-1"
    : "contents"}
>
  {#each pills as p, i (p === PLACEHOLDER ? `__new__${i}` : p)}
    {#if p === PLACEHOLDER}
      <TagPill
        text=""
        startEditing
        {size}
        {autocomplete}
        existingTags={tags}
        onreplace={(next) => addTag(next)}
        oncancel={() => { addingTag = false; }}
      />
    {:else}
      {#if reorderable && dragFrom !== null && dropIndex === i}
        <span class={markerCls} aria-hidden="true"></span>
      {/if}
      {#if reorderable || selectable}
        <!-- svelte-ignore a11y_no_static_element_interactions -->
        <span
          draggable={reorderable}
          ondragstart={reorderable ? (e) => onDragStart(i, e) : undefined}
          ondragover={reorderable ? (e) => onDragOver(i, e) : undefined}
          ondragend={reorderable ? onDragEnd : undefined}
          onmousedown={selectable ? (e) => { if (e.button === 1) e.preventDefault(); } : undefined}
          onauxclick={selectable ? (e) => { if (e.button === 1) { e.preventDefault(); toggleSelect(p); } } : undefined}
          title={selectable ? "Middle-click to select" : undefined}
          class="inline-flex rounded-full transition-shadow
            {reorderable ? 'cursor-grab active:cursor-grabbing' : ''}
            {selectable && selected.has(p) ? 'ring-2 ring-sky-400' : searchable && matchesQuery(p, search) ? 'ring-2 ring-amber-400' : ''}
            {reorderable && dragFrom === i ? 'opacity-40' : ''}"
        >
          <TagPill
            text={p}
            {size}
            {autocomplete}
            existingTags={tags}
            onreplace={(next) => replaceTag(p, next)}
            ondelete={() => deleteTag(p)}
          />
        </span>
      {:else}
        <TagPill
          text={p}
          {size}
          {autocomplete}
          existingTags={tags}
          onreplace={(next) => replaceTag(p, next)}
          ondelete={() => deleteTag(p)}
        />
      {/if}
    {/if}
  {/each}

  {#if reorderable && dragFrom !== null && dropIndex === tags.length}
    <span class={markerCls} aria-hidden="true"></span>
  {/if}

  <button
    type="button"
    onclick={() => (addingTag = true)}
    title="Add tag"
    aria-label="Add tag"
    class={addBtnCls}
  >+</button>
</div>
