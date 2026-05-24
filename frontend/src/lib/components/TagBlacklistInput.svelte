<script lang="ts">
  type Props = {
    tags: string[];
    ontagsChange: (next: string[]) => void;
  };
  const { tags, ontagsChange }: Props = $props();

  let draft = $state("");

  function pushTag(raw: string) {
    const t = raw.trim().toLowerCase();
    if (!t) return;
    if (tags.includes(t)) return;
    ontagsChange([...tags, t]);
  }

  function removeTag(t: string) {
    ontagsChange(tags.filter((x) => x !== t));
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      pushTag(draft);
      draft = "";
    } else if (e.key === "Backspace" && draft === "" && tags.length > 0) {
      // Friendly: backspace in an empty input pops the last chip.
      removeTag(tags[tags.length - 1]);
    }
  }

  function onPaste(e: ClipboardEvent) {
    const text = e.clipboardData?.getData("text") ?? "";
    if (!text.includes(",")) return;  // single tag: let default handler run
    e.preventDefault();
    for (const part of text.split(",")) pushTag(part);
    draft = "";
  }
</script>

<div
  class="w-full px-2 py-1.5 bg-ink-950 border border-ink-700 rounded
         focus-within:border-accent-500 flex flex-wrap gap-1.5 items-center"
>
  {#each tags as t (t)}
    <span
      class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full
             bg-ink-800 border border-ink-700 text-xs text-slate-200"
    >
      <span class="font-mono">{t}</span>
      <button
        type="button"
        onclick={() => removeTag(t)}
        aria-label={`remove ${t}`}
        class="text-slate-500 hover:text-slate-200 leading-none px-0.5"
      >&times;</button>
    </span>
  {/each}
  <input
    bind:value={draft}
    onkeydown={onKeydown}
    onpaste={onPaste}
    placeholder={tags.length === 0 ? "type a tag, press Enter or comma" : ""}
    class="flex-1 min-w-[10ch] bg-transparent text-sm font-mono
           text-slate-200 placeholder:text-slate-600 focus:outline-none"
  />
</div>
