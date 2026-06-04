<script lang="ts">
  import ConfirmationDialog from "./ConfirmationDialog.svelte";

  type Action = "retag" | "describe";

  type Props = {
    action: Action;
    selectedCount: number;
    affectedCount: number;
    onconfirm: () => void;
    oncancel: () => void;
  };
  const { action, selectedCount, affectedCount, onconfirm, oncancel }: Props = $props();

  let title = $derived(action === "retag"
    ? "Tag selected frames?"
    : "Describe selected frames?");
  let affectedLabel = $derived(action === "retag" ? "tag line" : "description");
  let preservedLabel = $derived(action === "retag" ? "LLM descriptions" : "WD14 tags");
  let confirmLabel = $derived(action === "retag" ? "Tag anyway" : "Describe anyway");
</script>

<ConfirmationDialog
  title={title}
  titleId="confirm-frame-overwrite-title"
  {oncancel}
>
  <p class="text-xs text-slate-500 mb-4">
    This will overwrite existing {affectedLabel}s on selected frames.
  </p>

  <ul class="text-sm text-slate-300 mb-4 space-y-1 pl-4">
    <li class="list-disc">
      <span class="text-slate-100">{affectedCount}</span>
      of <span class="text-slate-100">{selectedCount}</span>
      selected frame{selectedCount === 1 ? "" : "s"} already
      {affectedCount === 1 ? "has" : "have"} a {affectedLabel}
    </li>
    <li class="list-disc">
      Existing {preservedLabel} will be preserved
    </li>
  </ul>

  <p class="text-xs text-amber-400 mb-4">
    This cannot be undone.
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
    >{confirmLabel}</button>
  {/snippet}
</ConfirmationDialog>
