<script lang="ts">
  import ConfirmationDialog from "./ConfirmationDialog.svelte";

  type Props = {
    tagsDirty?: boolean;
    descDirty?: boolean;
    onconfirm: () => void;
    oncancel: () => void;
  };
  const {
    tagsDirty = false,
    descDirty = false,
    onconfirm,
    oncancel,
  }: Props = $props();

  let what = $derived(
    tagsDirty && descDirty
      ? "tag and description edits"
      : tagsDirty
        ? "tag edits"
        : descDirty
          ? "description edits"
          : "edits",
  );
</script>

<ConfirmationDialog
  title="Discard unsaved changes?"
  titleId="confirm-discard-title"
  {oncancel}
>
  <p class="text-xs text-slate-500 mb-4">
    You have unsaved {what} on this frame. Leaving now will lose them.
  </p>

  {#snippet footer()}
    <button
      type="button"
      onclick={oncancel}
      class="px-3 py-1.5 text-xs rounded bg-ink-800 hover:bg-ink-700 text-slate-300 border border-ink-700"
    >Cancel</button>
    <button
      type="button"
      onclick={onconfirm}
      class="px-3 py-1.5 text-xs rounded bg-red-600 hover:bg-red-500 text-white shadow-[0_2px_8px_rgba(220,38,38,0.3)]"
    >Discard</button>
  {/snippet}
</ConfirmationDialog>
