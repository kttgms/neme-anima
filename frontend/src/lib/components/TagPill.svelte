<script lang="ts">
  import { tagVocabulary } from "$lib/tagVocabulary.svelte";
  import { normalizeTagKey, type Suggestion } from "$lib/tagSearch";
  import TagAutocomplete from "./TagAutocomplete.svelte";

  type Props = {
    text: string;
    onreplace: (next: string) => void;
    /** Called when an existing pill is committed empty — parent should drop
     *  the tag entirely. If omitted, empty commits revert to the old text. */
    ondelete?: () => void;
    /** Called when a *placeholder* pill (created by the "+" button) is
     *  dismissed without typing — parent should remove the temporary slot. */
    oncancel?: () => void;
    /** Mount the pill directly in edit mode (used for the "+" placeholder). */
    startEditing?: boolean;
    /** Visual size. "sm" (default) matches the dense thumbnail hover panel;
     *  "md" is ~30% larger for the roomier crop-modal tag editor. */
    size?: "sm" | "md";
    /** Enable the danbooru autocomplete dropdown while editing. */
    autocomplete?: boolean;
    /** Other tags already on the frame — excluded from suggestions. */
    existingTags?: string[];
  };
  const {
    text,
    onreplace,
    ondelete,
    oncancel,
    startEditing = false,
    size = "sm",
    autocomplete = false,
    existingTags = [],
  }: Props = $props();

  let editing = $state<boolean>(startEditing);
  let value = $state(text);
  let inputEl = $state<HTMLInputElement | null>(null);

  // Autocomplete: dismissed closes the dropdown without leaving edit mode
  // (Escape / after accepting); typing re-opens it.
  let acDismissed = $state(false);
  let acIndex = $state(0);

  // Kick off the one-time vocabulary load the first time an autocomplete-
  // enabled pill enters edit mode.
  $effect(() => {
    if (autocomplete && editing) tagVocabulary.ensureLoaded();
  });

  // Exclude every other tag on the frame (but allow re-typing this pill's own
  // current text) from the suggestion list.
  let excludeKeys = $derived(
    new Set(existingTags.filter((t) => t !== text).map(normalizeTagKey)),
  );

  let suggestions = $derived<Suggestion[]>(
    autocomplete && editing && !acDismissed && value.trim()
      ? tagVocabulary.search(value, { exclude: excludeKeys })
      : [],
  );
  let acOpen = $derived(suggestions.length > 0);

  $effect(() => {
    // Sync the buffer to the canonical text whenever it changes from the
    // outside (e.g. after a save). Only do this when not actively editing
    // so we don't yank the user's in-progress edit out from under them.
    if (!editing) value = text;
  });

  // When we transition into edit mode, auto-select the existing text so the
  // user can immediately retype to replace, or hit Backspace + Enter to
  // delete the tag. For the placeholder pill (text === ""), there's nothing
  // to select — the cursor just sits at the empty input.
  $effect(() => {
    if (!editing) return;
    const el = inputEl;
    if (!el) return;
    queueMicrotask(() => {
      el.focus();
      if (el.value.length > 0) el.select();
    });
  });

  function finalize() {
    // Shared commit path for Enter, blur, and accepting a suggestion.
    const trimmed = value.trim();
    editing = false;
    if (trimmed === "") {
      // Empty existing pill = delete. Empty placeholder = discard.
      if (text === "") oncancel?.();
      else ondelete?.();
      return;
    }
    if (trimmed !== text) onreplace(trimmed);
  }

  function commit(ev: KeyboardEvent | FocusEvent) {
    // Idempotency guard (see original comment): Enter flips editing=false and
    // the subsequent blur would otherwise double-fire onreplace.
    if (!editing) return;
    if (ev instanceof KeyboardEvent && ev.key === "Escape") {
      ev.preventDefault();
      if (text === "" && oncancel) {
        editing = false;
        oncancel();
        return;
      }
      value = text;
      editing = false;
      return;
    }
    if (ev instanceof KeyboardEvent && ev.key !== "Enter") return;
    if (ev instanceof KeyboardEvent) ev.preventDefault();
    finalize();
  }

  function acceptSuggestion(s: Suggestion) {
    value = s.entry.name; // canonical space form
    acDismissed = true;
    finalize();
  }

  function onKeydown(ev: KeyboardEvent) {
    if (acOpen) {
      if (ev.key === "ArrowDown") {
        ev.preventDefault();
        acIndex = (acIndex + 1) % suggestions.length;
        return;
      }
      if (ev.key === "ArrowUp") {
        ev.preventDefault();
        acIndex = (acIndex - 1 + suggestions.length) % suggestions.length;
        return;
      }
      if (ev.key === "Enter" || ev.key === "Tab") {
        ev.preventDefault();
        acceptSuggestion(suggestions[acIndex]);
        return;
      }
      if (ev.key === "Escape") {
        // Close the dropdown only; keep editing. A second Escape reverts (the
        // dropdown is closed by then, so commit() handles it).
        ev.preventDefault();
        ev.stopPropagation();
        acDismissed = true;
        return;
      }
    }
    commit(ev);
  }

  function onInput() {
    // Re-open the dropdown and reset the highlight as the user types.
    acDismissed = false;
    acIndex = 0;
  }

  // Heuristic: a tag containing "character" — server doesn't currently expose
  // a flag for character vs general tags in the listFrames payload, so we
  // treat anything that LOOKS like a character name as "important". This is
  // intentionally simple for v1; server-driven labels are a Phase 2C polish.
  let isCharacter = $derived(text.toLowerCase().includes("character"));

  // "md" bumps the dense 9.5px pill up ~30% for the crop-modal tag editor,
  // with proportionally larger padding and a wider edit input.
  let sz = $derived(
    size === "md"
      ? { text: "text-[11.25px]", btn: "px-2 py-0.5", input: "px-2.5 py-0.5 w-28" }
      : { text: "text-[9.5px]", btn: "px-1.5 py-0.5", input: "px-2 py-0.5 w-24" },
  );
</script>

{#if editing}
  <input
    bind:this={inputEl}
    bind:value
    oninput={onInput}
    onkeydown={onKeydown}
    onblur={commit}
    onclick={(e) => e.stopPropagation()}
    placeholder={text === "" ? "new tag…" : ""}
    class="{sz.input} {sz.text} rounded-full bg-accent-500 text-white shadow-[0_0_0_1.5px_rgba(199,210,254,1),0_0_12px_rgba(99,102,241,0.6)] outline-none placeholder-white/60"
  />
  {#if acOpen && inputEl}
    <TagAutocomplete
      {suggestions}
      activeIndex={acIndex}
      anchor={inputEl}
      onaccept={acceptSuggestion}
      onhover={(i) => (acIndex = i)}
    />
  {/if}
{:else}
  <button
    type="button"
    onclick={(e) => { e.stopPropagation(); editing = true; }}
    class="{sz.btn} {sz.text} rounded-full backdrop-blur-sm border border-white/5 transition-colors
      {isCharacter ? 'bg-amber2-500/85 text-amber-950 font-medium' : 'bg-white/15 text-white hover:bg-white/25'}"
  >
    {text}
  </button>
{/if}
