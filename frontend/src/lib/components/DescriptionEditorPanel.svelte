<script lang="ts">
  import { onDestroy } from "svelte";
  import * as api from "$lib/api";
  import { createAsyncLoad } from "$lib/composables/asyncLoad.svelte";
  import { createFlash } from "$lib/composables/flash.svelte";
  import { framesStore } from "$lib/stores/frames.svelte";
  import { projectsStore } from "$lib/stores/projects.svelte";

  type Props = {
    filename: string;
    /** Report unsaved-edit state up to the modal's discard guard. */
    ondirty?: (dirty: boolean) => void;
    /** Overwrite confirmation, shared with the bulk image-selection flow. */
    onconfirmFrameOverwrite?: (
      action: "retag" | "describe",
      selectedCount: number,
      affectedCount: number,
    ) => Promise<boolean>;
  };
  const { filename, ondirty, onconfirmFrameOverwrite }: Props = $props();

  // Matches ActionBar: the Describe button only renders when the project has
  // an LLM model picked, so a user can't fire a doomed call.
  let llmModelSelected = $derived(!!projectsStore.active?.llm?.model);

  let saved = $state("");
  let text = $state("");
  const loader = createAsyncLoad();
  let saving = $state(false);
  let describing = $state(false);
  const savedFlash = createFlash();

  let dirty = $derived(text !== saved);
  $effect(() => { ondirty?.(dirty); });

  // Reload whenever the displayed frame changes (arrow-key nav in the modal).
  $effect(() => {
    const fn = filename;
    void load(fn);
  });

  async function load(fn: string) {
    const slug = projectsStore.active?.slug;
    if (!slug || !fn) { loader.settle(); return; }
    await loader.run(
      () => api.getDescription(slug, fn),
      (r) => {
        saved = r.text;
        text = r.text;
      },
    );
  }

  async function save() {
    const slug = projectsStore.active?.slug;
    if (!slug || saving || !dirty) return;
    saving = true;
    loader.error = null;
    try {
      const r = await api.putDescription(slug, filename, text);
      saved = r.text;
      text = r.text;
      framesStore.markDescribed(filename);
      framesStore.setSidecarFlags(filename, {
        has_description: r.text.trim().length > 0,
      });
      savedFlash.trigger();
    } catch (e) {
      loader.error = e instanceof Error ? e.message : String(e);
    } finally {
      saving = false;
    }
  }

  // Re-run the LLM describer on this one frame. Mirrors the bulk image-
  // selection flow (api.bulkRetagLLM + the shared overwrite popup) scoped to a
  // single filename, then reloads the freshly-written description.
  async function describeNow() {
    const slug = projectsStore.active?.slug;
    if (!slug || describing || saving || loader.loading) return;
    const affected = saved.trim().length > 0 ? 1 : 0;
    if (
      affected > 0 &&
      onconfirmFrameOverwrite &&
      !(await onconfirmFrameOverwrite("describe", 1, affected))
    ) {
      return;
    }
    describing = true;
    loader.error = null;
    try {
      const res = await api.bulkRetagLLM(slug, [filename]);
      if (res.described > 0) {
        const eff = res.effective_filenames?.[0] ?? filename;
        await load(filename);
        framesStore.markDescribed(eff);
        framesStore.setSidecarFlags(filename, {
          has_description: saved.trim().length > 0,
        });
      } else if (res.error) {
        loader.error = res.error;
      }
    } catch (e) {
      loader.error = e instanceof Error ? e.message : String(e);
    } finally {
      describing = false;
    }
  }

  onDestroy(() => savedFlash.destroy());
</script>

<div class="flex flex-col gap-2">
  <div class="flex items-center justify-between">
    <h3 class="text-xs font-semibold uppercase tracking-wide text-slate-400">
      Description
    </h3>
    {#if savedFlash.active}
      <span class="text-[10px] text-emerald-400">Saved ✓</span>
    {/if}
  </div>

  {#if loader.loading}
    <p class="text-slate-500 text-xs py-4 text-center">Loading…</p>
  {:else}
    <textarea
      bind:value={text}
      rows="10"
      placeholder="Describe the image — this becomes the second line of the .txt sidecar. Leave blank to remove the description."
      class="w-full px-3 py-2 bg-ink-950 border border-ink-700 rounded text-sm focus:outline-none focus:border-accent-500 resize-y"
    ></textarea>
    <p class="text-[11px] text-slate-600 leading-snug">
      The danbooru tag line stays untouched. Saving an empty value reverts the
      file to a single-line sidecar.
    </p>
  {/if}

  {#if loader.error}
    <p class="text-xs text-red-400 break-all">{loader.error}</p>
  {/if}

  <div class="flex justify-end gap-2">
    {#if llmModelSelected}
      <button
        type="button"
        onclick={describeNow}
        disabled={loader.loading || saving || describing}
        title="Run the LLM describer on this frame (preserves the tags)"
        class="px-4 py-1.5 text-xs rounded bg-teal-600 hover:bg-teal-500 text-white disabled:opacity-40 disabled:cursor-not-allowed"
      >{describing ? "Describing…" : "Describe"}</button>
    {/if}
    <button
      type="button"
      onclick={save}
      disabled={loader.loading || saving || !dirty}
      class="px-4 py-1.5 text-xs rounded gradient-accent text-white disabled:opacity-40 disabled:cursor-not-allowed"
    >{saving ? "Saving…" : "Save description"}</button>
  </div>
</div>
