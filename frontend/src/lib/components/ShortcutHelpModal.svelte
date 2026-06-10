<script lang="ts">
  type Props = { onclose: () => void };
  const { onclose }: Props = $props();
</script>

<!-- Filled out in Task 7. Minimal valid modal so App compiles + stays green. -->
<div
  class="fixed inset-0 z-50 bg-black/60 flex items-center justify-center"
  role="dialog"
  aria-modal="true"
  tabindex="-1"
  onkeydown={(e) => { if (e.key === "Escape") onclose(); }}
>
  <div class="bg-ink-900 border border-ink-700 rounded-xl p-5" role="document">
    <button type="button" onclick={onclose}>Close</button>
  </div>
</div>
