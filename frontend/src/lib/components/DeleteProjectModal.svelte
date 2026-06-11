<script lang="ts">
  import type { ProjectView } from "$lib/types";
  import ConfirmationDialog from "./ConfirmationDialog.svelte";

  type Props = {
    project: ProjectView;
    onconfirm: () => void | Promise<void>;
    oncancel: () => void;
  };
  const { project, onconfirm, oncancel }: Props = $props();

  let typed = $state("");
  let busy = $state(false);
  let confirmable = $derived(typed === project.name && !busy);

  // Lightweight summary — derived from what the project view already
  // carries. We deliberately don't fetch kept-frame counts here: the
  // intent is to remind the user what the project is, not to enumerate
  // every artifact (which would mean an extra API call to a folder
  // we're about to delete anyway).
  let videoCount = $derived(project.sources.length);
  let refCount = $derived(
    project.characters.reduce((a, c) => a + c.refs.length, 0),
  );

  async function handleConfirm() {
    if (!confirmable) return;
    busy = true;
    try {
      await onconfirm();
    } finally {
      busy = false;
    }
  }
</script>

<ConfirmationDialog
  title={`Delete project "${project.name}"?`}
  titleId="delete-project-title"
  {oncancel}
>
  <p class="text-xs text-slate-500 mb-4 break-all">
    Folder: <code class="text-slate-400">{project.folder}</code>
  </p>

  <ul class="text-sm text-slate-300 mb-4 space-y-1 pl-4">
    <li class="list-disc">
      <span class="text-slate-100">{videoCount}</span>
      source video{videoCount === 1 ? "" : "s"}
    </li>
    <li class="list-disc">
      <span class="text-slate-100">{refCount}</span>
      reference image{refCount === 1 ? "" : "s"}
    </li>
    <li class="list-disc">
      Every extracted frame, tag sidecar, and training artifact
      under that folder
    </li>
  </ul>

  <p class="text-xs text-amber-400 mb-2">
    This cannot be undone. Type the project name to confirm.
  </p>
  <input
    bind:value={typed}
    placeholder={project.name}
    autocomplete="off"
    spellcheck="false"
    class="w-full mb-4 px-3 py-1.5 bg-ink-950 border border-ink-700
           rounded text-sm font-mono focus:outline-none focus:border-accent-500"
  />

  {#snippet footer()}
    <button
      type="button"
      onclick={oncancel}
      class="px-3 py-1.5 text-xs rounded bg-ink-800 hover:bg-ink-700
             text-slate-300 border border-ink-700"
    >Cancel</button>
    <button
      type="button"
      disabled={!confirmable}
      onclick={handleConfirm}
      class="px-3 py-1.5 text-xs rounded bg-red-600 hover:bg-red-500
             text-white btn-disabled
             shadow-[0_2px_8px_rgba(220,38,38,0.3)]"
    >{busy ? "Deleting…" : "Delete project + files"}</button>
  {/snippet}
</ConfirmationDialog>
