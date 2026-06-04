<script lang="ts">
  import { onDestroy } from "svelte";
  import * as api from "$lib/api";
  import { framesStore } from "$lib/stores/frames.svelte";
  import { projectsStore } from "$lib/stores/projects.svelte";
  import { parseTags, splitSidecar } from "$lib/sidecar";
  import { matchesQuery, reorder, tagsEqual } from "$lib/tagList";
  import TagPill from "./TagPill.svelte";

  type Props = {
    filename: string;
    /** Report unsaved-edit state up to the modal's discard guard. */
    ondirty?: (dirty: boolean) => void;
    /** Close the whole modal (routed through the modal's discard guard). */
    onclose?: () => void;
  };
  const { filename, ondirty, onclose }: Props = $props();

  // Sentinel that renders as the empty editable "+" placeholder, matching the
  // pattern used in FrameThumb's hover panel.
  const PLACEHOLDER = " __new__";

  let savedTags = $state<string[]>([]); // last-persisted baseline
  let tags = $state<string[]>([]); // local working copy
  let loading = $state(true);
  let saving = $state(false);
  let error = $state<string | null>(null);
  let savedFlash = $state(false);
  let flashTimer: ReturnType<typeof setTimeout> | null = null;

  let addingTag = $state(false);
  let search = $state("");
  let dragFrom = $state<number | null>(null);
  // Insertion index (0..tags.length) the drop would land at — drives the "|"
  // marker. Computed from which side of the hovered pill's midpoint the
  // pointer is on.
  let dropIndex = $state<number | null>(null);

  let dirty = $derived(!tagsEqual(tags, savedTags));
  $effect(() => { ondirty?.(dirty); });

  // Reload whenever the displayed frame changes (arrow-key nav in the modal).
  $effect(() => {
    const fn = filename;
    loading = true;
    error = null;
    addingTag = false;
    search = "";
    dragFrom = null;
    dropIndex = null;
    void load(fn);
  });

  async function load(fn: string) {
    const slug = projectsStore.active?.slug;
    if (!slug || !fn) { loading = false; return; }
    try {
      const r = await api.getTags(slug, fn);
      if (fn !== filename) return; // stale — user navigated away
      const parsed = parseTags(splitSidecar(r.text).danbooru);
      savedTags = parsed;
      tags = [...parsed];
    } catch (e) {
      if (fn !== filename) return;
      error = e instanceof Error ? e.message : String(e);
      savedTags = [];
      tags = [];
    } finally {
      if (fn === filename) loading = false;
    }
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
    tags = dedupe(tags.map((t) => (t === oldTag ? next : t)));
  }

  function deleteTag(oldTag: string) {
    tags = tags.filter((t) => t !== oldTag);
  }

  function addTag(next: string) {
    addingTag = false;
    const trimmed = next.trim();
    if (!trimmed) return;
    tags = dedupe([...tags, trimmed]);
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
  // Uses the pill's own rect so each wrapped row computes independently.
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
  function onDrop(i: number, ev: DragEvent) {
    ev.preventDefault();
    if (dragFrom === null) return;
    const insertion = insertionIndex(i, ev);
    // reorder() removes `from` before inserting, so an insertion point past
    // the removed item shifts left by one.
    const to = insertion > dragFrom ? insertion - 1 : insertion;
    tags = reorder(tags, dragFrom, to);
    dragFrom = null;
    dropIndex = null;
  }
  function onDragEnd() {
    dragFrom = null;
    dropIndex = null;
  }

  async function save() {
    const slug = projectsStore.active?.slug;
    if (!slug || saving || !dirty) return;
    saving = true;
    error = null;
    try {
      // Single-line body → the server preserves the on-disk description and
      // applies its own dedupe/normalization.
      const r = await api.putTags(slug, filename, tags.join(", "));
      const parsed = parseTags(splitSidecar(r.text).danbooru);
      savedTags = parsed;
      tags = [...parsed];
      framesStore.markRetagged(filename);
      framesStore.setSidecarFlags(filename, { has_tags: parsed.length > 0 });
      savedFlash = true;
      if (flashTimer) clearTimeout(flashTimer);
      flashTimer = setTimeout(() => { savedFlash = false; }, 2000);
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      saving = false;
    }
  }

  onDestroy(() => { if (flashTimer) clearTimeout(flashTimer); });
</script>

<div class="flex flex-col gap-2 flex-1 min-h-0">
  <div class="flex items-center justify-between">
    <h3 class="text-xs font-semibold uppercase tracking-wide text-slate-400">
      Tags {#if tags.length}<span class="text-slate-600">({tags.length})</span>{/if}
    </h3>
    <div class="flex items-center gap-2">
      {#if savedFlash}
        <span class="text-[10px] text-emerald-400">Saved ✓</span>
      {/if}
      {#if onclose}
        <button
          type="button"
          onclick={onclose}
          aria-label="Close"
          title="Close"
          class="w-6 h-6 rounded text-slate-400 hover:text-slate-100 hover:bg-ink-800 flex items-center justify-center text-lg leading-none transition-colors"
        >×</button>
      {/if}
    </div>
  </div>

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

  {#if loading}
    <p class="text-slate-500 text-xs py-4 text-center">Loading…</p>
  {:else}
    <!-- Scrollable, grows to fill the column. Drag a pill to reorder; click to
         edit; empty-commit deletes; "+" adds. -->
    <div class="flex flex-wrap gap-1.5 content-start overflow-y-auto flex-1 min-h-0 pr-1">
      {#each pills as p, i (p === PLACEHOLDER ? `__new__${i}` : p)}
        {#if p === PLACEHOLDER}
          <TagPill
            text=""
            startEditing
            size="md"
            onreplace={(next) => addTag(next)}
            oncancel={() => { addingTag = false; }}
          />
        {:else}
          <!-- Insertion marker: a vertical bar showing where the dragged tag
               will drop (immediately before this pill). -->
          {#if dragFrom !== null && dropIndex === i}
            <span
              class="w-0.5 self-stretch min-h-[1.5rem] bg-amber-400 rounded-full"
              aria-hidden="true"
            ></span>
          {/if}
          <!-- svelte-ignore a11y_no_static_element_interactions -->
          <span
            draggable="true"
            ondragstart={(e) => onDragStart(i, e)}
            ondragover={(e) => onDragOver(i, e)}
            ondrop={(e) => onDrop(i, e)}
            ondragend={onDragEnd}
            class="inline-flex rounded-full cursor-grab active:cursor-grabbing transition-shadow
              {matchesQuery(p, search) ? 'ring-2 ring-amber-400' : ''}
              {dragFrom === i ? 'opacity-40' : ''}"
          >
            <TagPill
              text={p}
              size="md"
              onreplace={(next) => replaceTag(p, next)}
              ondelete={() => deleteTag(p)}
            />
          </span>
        {/if}
      {/each}

      <!-- End-of-list insertion marker (dropping past the last tag's midpoint). -->
      {#if dragFrom !== null && dropIndex === tags.length}
        <span
          class="w-0.5 self-stretch min-h-[1.5rem] bg-amber-400 rounded-full"
          aria-hidden="true"
        ></span>
      {/if}

      <button
        type="button"
        onclick={() => (addingTag = true)}
        title="Add tag"
        aria-label="Add tag"
        class="px-2 py-0.5 text-[11.25px] leading-none rounded-full bg-white/15 hover:bg-white/25 text-white border border-white/10 transition-colors self-start"
      >+</button>
    </div>
  {/if}

  {#if error}
    <p class="text-xs text-red-400 break-all">{error}</p>
  {/if}

  <div class="flex justify-end">
    <button
      type="button"
      onclick={save}
      disabled={loading || saving || !dirty}
      class="px-4 py-1.5 text-xs rounded gradient-accent text-white disabled:opacity-40 disabled:cursor-not-allowed"
    >{saving ? "Saving…" : "Save tags"}</button>
  </div>
</div>
