<script lang="ts">
  import { onDestroy } from "svelte";
  import * as api from "$lib/api";
  import { createAsyncLoad } from "$lib/composables/asyncLoad.svelte";
  import { createFlash } from "$lib/composables/flash.svelte";
  import { framesStore } from "$lib/stores/frames.svelte";
  import { projectsStore } from "$lib/stores/projects.svelte";
  import { tagClipboard } from "$lib/stores/tagClipboard.svelte";
  import { parseTags, splitSidecar } from "$lib/sidecar";
  import { matchesQuery, reorder, tagsEqual } from "$lib/tagList";
  import TagPill from "./TagPill.svelte";

  type Props = {
    filename: string;
    /** Report unsaved-edit state up to the modal's discard guard. */
    ondirty?: (dirty: boolean) => void;
    /** Close the whole modal (routed through the modal's discard guard). */
    onclose?: () => void;
    /** Overwrite confirmation, shared with the bulk image-selection flow. */
    onconfirmFrameOverwrite?: (
      action: "retag" | "describe",
      selectedCount: number,
      affectedCount: number,
    ) => Promise<boolean>;
  };
  const { filename, ondirty, onclose, onconfirmFrameOverwrite }: Props = $props();

  let autocompleteOn = $derived(projectsStore.active?.tag_autocomplete ?? true);
  // Same gate the Describe button uses: the Review button only appears once a
  // model has been picked in Settings.
  let llmModelSelected = $derived(!!projectsStore.active?.llm?.model);

  // Sentinel that renders as the empty editable "+" placeholder, matching the
  // pattern used in FrameThumb's hover panel.
  const PLACEHOLDER = " __new__";

  let savedTags = $state<string[]>([]); // last-persisted baseline
  let tags = $state<string[]>([]); // local working copy
  const loader = createAsyncLoad();
  let saving = $state(false);
  const savedFlash = createFlash();

  let addingTag = $state(false);
  let tagging = $state(false);
  let search = $state("");

  // ---- LLM tag review (staged: applies into `tags`, not the server) ----
  let reviewing = $state(false);
  let reviewResult = $state<api.TagReview | null>(null);
  // Which suggested removals/additions are currently checked. Keyed by tag
  // text; default all-checked when a review arrives.
  let acceptRemove = $state<Set<string>>(new Set());
  let acceptAdd = $state<Set<string>>(new Set());
  let dragFrom = $state<number | null>(null);
  // Insertion index (0..tags.length) the drop would land at — drives the "|"
  // marker. Computed from which side of the hovered pill's midpoint the
  // pointer is on.
  let dropIndex = $state<number | null>(null);

  // ---- middle-click tag selection (per-frame, staging-only) ----
  // Middle-clicking a pill toggles it in/out of this set. The set is keyed by
  // tag text and reset whenever the frame changes (see the reload effect).
  let selected = $state<Set<string>>(new Set());
  // Only count tags still present in the working list — a selected tag that's
  // since been deleted/renamed shouldn't keep the Copy/Unselect buttons alive.
  let selectedTags = $derived(tags.filter((t) => selected.has(t)));
  // True once the current selection has been copied; Copy stays disabled until
  // the selection changes again (a tag is added to or removed from it).
  let copied = $state(false);
  // The clipboard tags that would actually be added to this frame — i.e. those
  // not already present (paste dedupes the rest away). Drives both the Paste
  // count and the hover preview so they match what the paste really does.
  let pasteTags = $derived(tagClipboard.tags.filter((t) => !tags.includes(t)));

  function toggleSelect(tag: string) {
    const next = new Set(selected);
    if (next.has(tag)) next.delete(tag);
    else next.add(tag);
    selected = next;
    copied = false; // selection changed → the clipboard is now stale
  }
  function copySelection() {
    if (selectedTags.length === 0 || copied) return;
    tagClipboard.set(selectedTags); // order-preserving; keeps selection intact
    copied = true;
  }
  function clearSelection() {
    selected = new Set();
  }
  function pasteClipboard() {
    if (tagClipboard.size === 0) return;
    tags = dedupe([...tags, ...tagClipboard.tags]); // append + dedupe → marks dirty
  }

  let dirty = $derived(!tagsEqual(tags, savedTags));
  $effect(() => { ondirty?.(dirty); });

  // Reload whenever the displayed frame changes (arrow-key nav in the modal).
  $effect(() => {
    const fn = filename;
    addingTag = false;
    search = "";
    dragFrom = null;
    dropIndex = null;
    selected = new Set(); // selection is per-frame
    copied = false;
    reviewResult = null; // discard stale suggestions for the previous frame
    void load(fn);
  });

  async function load(fn: string) {
    const slug = projectsStore.active?.slug;
    if (!slug || !fn) { loader.settle(); return; }
    await loader.run(
      () => api.getTags(slug, fn),
      (r) => {
        const parsed = parseTags(splitSidecar(r.text).danbooru);
        savedTags = parsed;
        tags = [...parsed];
      },
      () => {
        savedTags = [];
        tags = [];
      },
    );
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
  // The whole container is a drop zone so a tag can be dropped in the gaps
  // between pills (or anywhere we're showing the marker), not only when the
  // pointer is over a pill. The per-pill onDragOver keeps `dropIndex` precise
  // while over a pill; in the gaps it simply retains the last position, so the
  // drop lands wherever the ghost bar is shown.
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
    tags = reorder(tags, dragFrom, to);
    dragFrom = null;
    dropIndex = null;
  }
  function onContainerDragLeave(ev: DragEvent) {
    if (dragFrom === null) return;
    // dragleave also fires when crossing between child pills; only drop the
    // marker (and the drop target) when the pointer truly leaves the container.
    const related = ev.relatedTarget as Node | null;
    if (related && (ev.currentTarget as HTMLElement).contains(related)) return;
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
    loader.error = null;
    try {
      // Single-line body → the server preserves the on-disk description and
      // applies its own dedupe/normalization.
      const r = await api.putTags(slug, filename, tags.join(", "));
      const parsed = parseTags(splitSidecar(r.text).danbooru);
      savedTags = parsed;
      tags = [...parsed];
      framesStore.markRetagged(filename);
      framesStore.setSidecarFlags(filename, { has_tags: parsed.length > 0 });
      savedFlash.trigger();
    } catch (e) {
      loader.error = e instanceof Error ? e.message : String(e);
    } finally {
      saving = false;
    }
  }

  // Re-run the WD14 tagger on this one frame. Mirrors the bulk image-selection
  // flow (api.bulkRetagDanbooru + the shared overwrite popup) but scoped to a
  // single filename, then reloads the freshly-written tag line into the panel.
  async function retagNow() {
    const slug = projectsStore.active?.slug;
    if (!slug || tagging || saving || loader.loading) return;
    const affected = savedTags.length > 0 ? 1 : 0;
    if (
      affected > 0 &&
      onconfirmFrameOverwrite &&
      !(await onconfirmFrameOverwrite("retag", 1, affected))
    ) {
      return;
    }
    tagging = true;
    loader.error = null;
    try {
      const res = await api.bulkRetagDanbooru(slug, [filename]);
      if (res.retagged > 0) {
        await load(filename);
        framesStore.markRetagged(filename);
        framesStore.setSidecarFlags(filename, { has_tags: savedTags.length > 0 });
      }
    } catch (e) {
      loader.error = e instanceof Error ? e.message : String(e);
    } finally {
      tagging = false;
    }
  }

  // Ask the LLM to review the current tags against the (cropped) image. The
  // result is a proposed diff the user accepts/rejects; applying it only stages
  // into `tags` — nothing is written until the user hits "Save tags".
  async function reviewNow() {
    const slug = projectsStore.active?.slug;
    if (!slug || reviewing || saving || loader.loading || tagging) return;
    const fn = filename; // review is slow; guard against arrow-key nav meanwhile
    reviewing = true;
    loader.error = null;
    reviewResult = null;
    try {
      const r = await api.reviewTags(slug, fn);
      if (fn !== filename) return; // user navigated away — drop stale result
      reviewResult = r;
      acceptRemove = new Set(r.remove.map((x) => x.tag));
      acceptAdd = new Set(r.add.map((x) => x.tag));
    } catch (e) {
      if (fn !== filename) return;
      loader.error = e instanceof Error ? e.message : String(e);
    } finally {
      if (fn === filename) reviewing = false;
    }
  }

  function toggleAccept(set: Set<string>, tag: string): Set<string> {
    const next = new Set(set);
    if (next.has(tag)) next.delete(tag);
    else next.add(tag);
    return next;
  }

  let acceptedCount = $derived(acceptRemove.size + acceptAdd.size);

  function applyReview() {
    if (!reviewResult) return;
    let next = tags.filter((t) => !acceptRemove.has(t));
    for (const a of reviewResult.add) {
      if (acceptAdd.has(a.tag)) next.push(a.tag);
    }
    tags = dedupe(next);
    reviewResult = null;
  }

  onDestroy(() => savedFlash.destroy());
</script>

<div class="flex flex-col gap-2 flex-1 min-h-0">
  <div class="flex items-center justify-between">
    <h3 class="text-xs font-semibold uppercase tracking-wide text-slate-400">
      Tags {#if tags.length}<span class="text-slate-600">({tags.length})</span>{/if}
    </h3>
    <div class="flex items-center gap-2">
      {#if savedFlash.active}
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

  {#if loader.loading}
    <p class="text-slate-500 text-xs py-4 text-center">Loading…</p>
  {:else}
    <!-- Scrollable, grows to fill the column. Drag a pill to reorder; click to
         edit; empty-commit deletes; "+" adds. The container is the drop zone so
         drops land in the gaps between pills too (ondragover/ondrop here);
         leaving the container clears the insertion marker. -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div
      ondragover={onContainerDragOver}
      ondrop={onContainerDrop}
      ondragleave={onContainerDragLeave}
      class="flex flex-wrap gap-1.5 content-start overflow-y-auto flex-1 min-h-0 pr-1"
    >
      {#each pills as p, i (p === PLACEHOLDER ? `__new__${i}` : p)}
        {#if p === PLACEHOLDER}
          <TagPill
            text=""
            startEditing
            size="md"
            autocomplete={autocompleteOn}
            existingTags={tags}
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
            ondragend={onDragEnd}
            onmousedown={(e) => { if (e.button === 1) e.preventDefault(); }}
            onauxclick={(e) => { if (e.button === 1) { e.preventDefault(); toggleSelect(p); } }}
            title="Middle-click to select"
            class="inline-flex rounded-full cursor-grab active:cursor-grabbing transition-shadow
              {selected.has(p) ? 'ring-2 ring-sky-400' : matchesQuery(p, search) ? 'ring-2 ring-amber-400' : ''}
              {dragFrom === i ? 'opacity-40' : ''}"
          >
            <TagPill
              text={p}
              size="md"
              autocomplete={autocompleteOn}
              existingTags={tags}
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

  {#if reviewResult}
    <!-- LLM review diff: accept/reject removals + additions, then "Apply" to
         stage them into the tag list (still requires Save tags to persist). -->
    <div class="border border-violet-700/50 rounded bg-violet-950/20 p-2 flex flex-col gap-2 text-xs max-h-[45%] overflow-y-auto">
      <div class="flex items-center justify-between">
        <span class="text-[10px] font-semibold uppercase tracking-wide text-violet-300">LLM review</span>
        <button
          type="button"
          onclick={() => (reviewResult = null)}
          aria-label="Dismiss review"
          class="w-5 h-5 rounded text-slate-400 hover:text-slate-100 flex items-center justify-center leading-none"
        >×</button>
      </div>

      {#if reviewResult.remove.length === 0 && reviewResult.add.length === 0}
        <p class="text-slate-500">No changes suggested — the tags look accurate.</p>
      {/if}

      {#if reviewResult.remove.length}
        <div class="flex flex-col gap-0.5">
          <p class="text-[10px] uppercase tracking-wide text-rose-400/80">Remove</p>
          {#each reviewResult.remove as r (r.tag)}
            <label class="flex items-start gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={acceptRemove.has(r.tag)}
                onchange={() => (acceptRemove = toggleAccept(acceptRemove, r.tag))}
                class="mt-0.5 accent-rose-500"
              />
              <span>
                <span class="line-through text-rose-300">{r.tag}</span>
                <span class="text-slate-500"> — {r.reason}</span>
              </span>
            </label>
          {/each}
        </div>
      {/if}

      {#if reviewResult.add.length}
        <div class="flex flex-col gap-0.5">
          <p class="text-[10px] uppercase tracking-wide text-emerald-400/80">Add</p>
          {#each reviewResult.add as a (a.tag)}
            <label class="flex items-start gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={acceptAdd.has(a.tag)}
                onchange={() => (acceptAdd = toggleAccept(acceptAdd, a.tag))}
                class="mt-0.5 accent-emerald-500"
              />
              <span>
                <span class="text-emerald-300">{a.tag}</span>
                <span class="text-slate-500"> — {a.reason}</span>
              </span>
            </label>
          {/each}
        </div>
      {/if}

      {#if reviewResult.notes.length}
        <details class="text-slate-500">
          <summary class="cursor-pointer text-[10px]">{reviewResult.notes.length} note{reviewResult.notes.length === 1 ? "" : "s"}</summary>
          <ul class="list-disc pl-4 mt-1 space-y-0.5">
            {#each reviewResult.notes as n}<li>{n}</li>{/each}
          </ul>
        </details>
      {/if}

      {#if reviewResult.remove.length || reviewResult.add.length}
        <div class="flex justify-end gap-2 pt-1">
          <button
            type="button"
            onclick={() => (reviewResult = null)}
            class="px-3 py-1 rounded bg-ink-800 hover:bg-ink-700 text-slate-300"
          >Dismiss</button>
          <button
            type="button"
            onclick={applyReview}
            disabled={acceptedCount === 0}
            class="px-3 py-1 rounded bg-violet-600 hover:bg-violet-500 text-white disabled:opacity-40 disabled:cursor-not-allowed"
          >Apply {acceptedCount} change{acceptedCount === 1 ? "" : "s"}</button>
        </div>
      {/if}
    </div>
  {/if}

  {#if loader.error}
    <p class="text-xs text-red-400 break-all">{loader.error}</p>
  {/if}

  <div class="flex justify-end gap-2">
    {#if selectedTags.length > 0}
      <!-- A tag selection exists (middle-click): copy it to the clipboard, or
           clear the selection. -->
      <button
        type="button"
        onclick={copySelection}
        disabled={copied}
        title={copied
          ? "Copied — select or deselect a tag to copy again"
          : `Copy the ${selectedTags.length} selected tag${selectedTags.length === 1 ? "" : "s"} to the clipboard`}
        class="px-4 py-1.5 text-xs rounded bg-sky-600 hover:bg-sky-500 text-white disabled:opacity-40 disabled:cursor-not-allowed"
      >{copied ? "Copied ✓" : `Copy ${selectedTags.length}`}</button>
      <button
        type="button"
        onclick={clearSelection}
        title="Clear the tag selection"
        class="px-4 py-1.5 text-xs rounded bg-ink-800 hover:bg-ink-700 text-slate-300"
      >Unselect</button>
    {:else if tagClipboard.size > 0}
      <!-- Nothing selected here but the clipboard holds tags from another frame:
           offer to append them (deduped) to this frame's tags. Hovering the
           button previews the exact tags that will be pasted. -->
      <div class="relative group">
        <button
          type="button"
          onclick={pasteClipboard}
          disabled={loader.loading || pasteTags.length === 0}
          class="px-4 py-1.5 text-xs rounded bg-sky-600 hover:bg-sky-500 text-white disabled:opacity-40 disabled:cursor-not-allowed"
        >Paste {pasteTags.length}</button>
        <div
          class="pointer-events-none absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 z-20 hidden group-hover:block"
        >
          <div class="w-56 max-h-48 overflow-hidden rounded border border-ink-700 bg-ink-900 shadow-lg p-2">
            {#if pasteTags.length}
              <p class="text-[10px] uppercase tracking-wide text-slate-400 mb-1">
                Will paste ({pasteTags.length})
              </p>
              <div class="flex flex-wrap gap-1">
                {#each pasteTags as t}
                  <span class="px-1.5 py-0.5 text-[10px] leading-none rounded-full bg-white/10 text-slate-200">{t}</span>
                {/each}
              </div>
            {:else}
              <p class="text-[10px] text-slate-400">All {tagClipboard.size} copied tag{tagClipboard.size === 1 ? "" : "s"} are already on this frame.</p>
            {/if}
          </div>
        </div>
      </div>
    {/if}
    {#if llmModelSelected}
      <button
        type="button"
        onclick={reviewNow}
        disabled={loader.loading || saving || tagging || reviewing}
        title="Ask the LLM to review these tags against the image (proposes removals and additions)"
        class="px-4 py-1.5 text-xs rounded bg-violet-600 hover:bg-violet-500 text-white disabled:opacity-40 disabled:cursor-not-allowed"
      >{reviewing ? "Reviewing…" : "Review"}</button>
    {/if}
    <button
      type="button"
      onclick={retagNow}
      disabled={loader.loading || saving || tagging}
      title="Re-run the WD14 tagger on this frame (preserves the description)"
      class="px-4 py-1.5 text-xs rounded bg-emerald-600 hover:bg-emerald-500 text-white disabled:opacity-40 disabled:cursor-not-allowed"
    >{tagging ? "Tagging…" : "Tag"}</button>
    <button
      type="button"
      onclick={save}
      disabled={loader.loading || saving || !dirty}
      class="px-4 py-1.5 text-xs rounded gradient-accent text-white disabled:opacity-40 disabled:cursor-not-allowed"
    >{saving ? "Saving…" : "Save tags"}</button>
  </div>
</div>
