<script lang="ts">
  import { toasts, type ToastKind } from "$lib/stores/toasts.svelte";

  const KIND_CLASSES: Record<ToastKind, string> = {
    success: "border-emerald-500/60 text-emerald-200",
    info: "border-sky-500/60 text-sky-200",
    error: "border-red-500/60 text-red-200",
  };
</script>

<!-- z-[60]: modals sit at z-50; toasts must stay visible above them. -->
<div
  class="fixed bottom-4 right-4 z-[60] flex flex-col items-end gap-2 pointer-events-none"
  aria-live="polite"
>
  {#each toasts.list as t (t.id)}
    <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
    <div
      role="status"
      onmouseenter={() => toasts.pause(t.id)}
      onmouseleave={() => toasts.resume(t.id)}
      class="pointer-events-auto max-w-sm flex items-start gap-2 px-3 py-2 rounded-lg border bg-ink-900 shadow-2xl text-xs {KIND_CLASSES[t.kind]}"
    >
      <span class="break-words whitespace-pre-wrap">{t.message}</span>
      <button
        type="button"
        onclick={() => toasts.dismiss(t.id)}
        aria-label="Dismiss notification"
        class="shrink-0 w-4 h-4 inline-flex items-center justify-center rounded text-slate-400 hover:text-slate-100"
      >×</button>
    </div>
  {/each}
</div>
