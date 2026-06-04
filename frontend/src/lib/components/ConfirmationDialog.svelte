<script lang="ts">
  import type { Snippet } from "svelte";

  type Props = {
    title: string;
    titleId: string;
    oncancel: () => void;
    children: Snippet;
    footer: Snippet;
  };
  const { title, titleId, oncancel, children, footer }: Props = $props();
</script>

<div
  class="fixed inset-0 z-50 overflow-y-auto bg-ink-950/70 backdrop-blur-sm"
  role="presentation"
  onclick={oncancel}
  onkeydown={(e) => { if (e.key === "Escape") oncancel(); }}
>
  <div class="flex min-h-full items-center justify-center p-4">
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      tabindex="-1"
      class="bg-ink-900 border border-ink-700 rounded-xl shadow-2xl p-5 max-w-md w-full"
      onclick={(e) => e.stopPropagation()}
      onkeydown={(e) => e.stopPropagation()}
    >
      <h2
        id={titleId}
        class="text-base font-semibold text-slate-100 mb-1"
      >{title}</h2>

      {@render children()}

      <div class="flex justify-end gap-2">
        {@render footer()}
      </div>
    </div>
  </div>
</div>
