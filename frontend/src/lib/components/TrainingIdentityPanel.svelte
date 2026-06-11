<script lang="ts">
  import * as api from "$lib/api";
  import { colorForIndex } from "$lib/characterColors";
  import { projectsStore } from "$lib/stores/projects.svelte";
  let project = $derived(projectsStore.active);
  let identitySlug = $state<string | null>(null);

  $effect(() => {
    // Initialize/repair the picker selection when the project or its
    // character list changes. Falls back to characters[0] when the current
    // selection points at a character that's been deleted/renamed.
    const chars = project?.characters ?? [];
    if (chars.length === 0) {
      identitySlug = null;
      return;
    }
    if (!identitySlug || !chars.some(c => c.slug === identitySlug)) {
      identitySlug = chars[0].slug;
    }
  });

  let identityChar = $derived(
    (project?.characters ?? []).find(c => c.slug === identitySlug) ?? null,
  );

  let identitySaveTimer: ReturnType<typeof setTimeout> | null = null;

  async function saveIdentityField(
    slug: string,
    characterSlug: string,
    patch: Parameters<typeof api.updateCharacter>[2],
  ) {
    if (identitySaveTimer) clearTimeout(identitySaveTimer);
    identitySaveTimer = setTimeout(async () => {
      try {
        await api.updateCharacter(slug, characterSlug, patch);
        await projectsStore.load(slug);  // refresh in-memory view
      } catch (e) {
        console.error("identity save failed", e);
      }
    }, 350);
  }

  let coreTagsReport = $state<api.CoreTagsReport | null>(null);
  let coreTagsLoading = $state(false);

  async function runCoreTagsCompute() {
    if (!project || !identityChar) return;
    coreTagsLoading = true;
    try {
      coreTagsReport = await api.computeCharacterCoreTags(
        project.slug, identityChar.slug,
      );
    } finally {
      coreTagsLoading = false;
    }
  }

  function toggleCoreTag(tag: string) {
    if (!project || !identityChar) return;
    const cur = new Set(identityChar.core_tags);
    if (cur.has(tag)) cur.delete(tag);
    else cur.add(tag);
    saveIdentityField(project.slug, identityChar.slug, {
      core_tags: [...cur],
    });
  }

  // Reset the report when switching characters so we don't show stale tags.
  $effect(() => {
    identitySlug;  // dep
    coreTagsReport = null;
  });
</script>

      <div class="bg-ink-900 border border-ink-700 rounded-xl p-4 mb-3">
        <h3 class="text-sm font-medium text-slate-200 mb-3">Character</h3>
        <div class="flex flex-wrap gap-2">
          {#each project?.characters ?? [] as c, i (c.slug)}
            {@const active = identitySlug === c.slug}
            {@const color = colorForIndex(i)}
            <button
              type="button"
              onclick={() => (identitySlug = c.slug)}
              class="h-7 px-3 rounded-full text-xs inline-flex items-center gap-1.5 transition-colors
                {active
                  ? `${color.bgActive} ${color.borderActive} border text-white`
                  : 'bg-ink-900 border border-ink-700 text-slate-300 hover:bg-ink-800 hover:text-slate-100'}"
              style={active ? `box-shadow: 0 2px 8px ${color.glow}` : undefined}
            >
              <span
                class="w-2 h-2 rounded-full flex-shrink-0
                  {active ? 'bg-white/70' : color.dot}"
                aria-hidden="true"
              ></span>
              <span>{c.name}</span>
              <span class="opacity-70 tabular-nums">({c.ref_count})</span>
            </button>
          {/each}
        </div>
      </div>
      {#if identityChar}
        <div class="bg-ink-900 border border-ink-700 rounded-xl p-4 mb-3">
          <h3 class="text-sm font-medium text-slate-200 mb-3">Trigger token</h3>
          <label class="block text-xs">
            <span class="text-[10px] uppercase tracking-wide text-slate-500">
              trigger_token (optional)
            </span>
            <input
              value={identityChar.trigger_token}
              oninput={(e) => {
                const v = (e.target as HTMLInputElement).value;
                if (project) saveIdentityField(project.slug, identityChar!.slug, {
                  trigger_token: v,
                });
              }}
              placeholder="e.g. mychar"
              class="w-full mt-1 px-3 py-1.5 bg-ink-950 border border-ink-700 rounded font-mono"
            />
            <span class="block text-[10px] text-slate-500 mt-1">
              {identityChar.trigger_token
                ? `Will be prepended to every caption: "${identityChar.trigger_token}, ..."`
                : "Empty — captions go to the trainer unchanged."}
            </span>
          </label>
        </div>

        <div
          class="bg-ink-900 border border-ink-700 rounded-xl p-4 mb-3"
          title="Tags that show up on most of this character's frames (hair color, eye color, default outfit, …). When 'Prune core tags at staging' is on, the Selected tags are stripped from every caption right before training — the LoRA learns them visually, so leaving them in only adds noise. Click 'Compute suggestions' to surface candidates ranked by frequency, then click each tag to add or remove it from the Selected list."
        >
          <h3 class="text-sm font-medium text-slate-200 mb-3 flex items-center gap-1">
            <span>Core tags</span>
            <span aria-hidden="true" class="text-slate-600 cursor-help text-xs">ⓘ</span>
          </h3>

          <div class="flex flex-wrap items-center gap-3 mb-3">
            <label class="inline-flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={identityChar.core_tags_enabled}
                onchange={(e) => {
                  const v = (e.target as HTMLInputElement).checked;
                  if (project) saveIdentityField(project.slug, identityChar!.slug, {
                    core_tags_enabled: v,
                  });
                }}
                class="w-4 h-4 rounded bg-ink-950 border-ink-700 accent-accent-500"
              />
              <span class="text-slate-300">Prune core tags at staging</span>
            </label>

            <label class="inline-flex items-center gap-2 text-xs">
              <span class="text-[10px] uppercase tracking-wide text-slate-500">
                threshold
              </span>
              <input
                type="number" min="0.01" max="1.0" step="0.01"
                value={identityChar.core_tags_freq_threshold}
                onchange={(e) => {
                  const v = Number((e.target as HTMLInputElement).value);
                  if (project) saveIdentityField(project.slug, identityChar!.slug, {
                    core_tags_freq_threshold: v,
                  });
                }}
                class="w-20 px-2 py-1 bg-ink-950 border border-ink-700 rounded font-mono"
              />
            </label>

            <button
              type="button"
              onclick={runCoreTagsCompute}
              disabled={coreTagsLoading}
              class="px-3 py-1 text-xs rounded bg-ink-800 border border-ink-700 text-slate-300 hover:bg-ink-700 disabled:opacity-40"
            >{coreTagsLoading ? "Computing…" : "Compute suggestions"}</button>
          </div>

          {#if identityChar.core_tags.length > 0}
            <div class="mb-3">
              <span class="text-[10px] uppercase tracking-wide text-slate-500">
                Selected ({identityChar.core_tags.length}) — stripped from
                this character's training captions when pruning is on
              </span>
              <div class="flex flex-wrap gap-1 mt-1">
                {#each identityChar.core_tags as t (t)}
                  <button
                    type="button"
                    onclick={() => toggleCoreTag(t)}
                    class="px-2 py-0.5 text-[11px] rounded bg-emerald-500/15 text-emerald-300 hover:bg-emerald-500/30"
                    title="Click to remove from the selected list"
                  >{t} ✕</button>
                {/each}
              </div>
            </div>
          {/if}

          {#if coreTagsReport}
            <div>
              <span class="text-[10px] uppercase tracking-wide text-slate-500">
                Suggestions (corpus={coreTagsReport.corpus_size},
                threshold={(coreTagsReport.threshold * 100).toFixed(0)}%)
              </span>
              {#if coreTagsReport.tags.length > 0}
                <div class="flex flex-wrap gap-1 mt-1">
                  {#each coreTagsReport.tags as row (row.tag)}
                    {@const persisted = identityChar.core_tags.includes(row.tag)}
                    <button
                      type="button"
                      onclick={() => toggleCoreTag(row.tag)}
                      class="px-2 py-0.5 text-[11px] rounded
                        {persisted
                          ? 'bg-emerald-500/15 text-emerald-300'
                          : 'bg-ink-800 text-slate-400 hover:bg-ink-700'}"
                      title="Click to {persisted ? 'remove from' : 'add to'} the selected list"
                    >{row.tag} <span class="text-slate-500">{(row.freq * 100).toFixed(0)}%</span></button>
                  {/each}
                </div>
              {:else}
                <div class="text-[11px] text-slate-400 mt-1">
                  No tag appeared in at least
                  {(coreTagsReport.threshold * 100).toFixed(0)}% of this
                  character's {coreTagsReport.corpus_size} frame{coreTagsReport.corpus_size === 1 ? "" : "s"}.
                  Lower the threshold to surface less-frequent tags.
                </div>
              {/if}
              {#if coreTagsReport.blacklisted.length > 0}
                <div
                  class="text-[10px] text-slate-500 mt-2"
                  title="These tags crossed the threshold but are pose/composition meta-tags shared across characters. They're kept in the training captions on purpose — pruning them would weaken the model's general vocabulary. The blacklist lives in core_tags.py."
                >
                  Excluded from suggestions ({coreTagsReport.blacklisted.length}):
                  <span class="text-slate-400 font-mono">{coreTagsReport.blacklisted.join(", ")}</span>
                  — kept in training captions on purpose.
                </div>
              {/if}
            </div>
          {/if}
        </div>

        <div
          class="bg-ink-900 border border-ink-700 rounded-xl p-4 mb-3"
          title="Per-character training-set repeat multiplier — controls this character's exposure to the model in a multi-character run. 0.0 (auto) lets the balancing pass equalise relative frame counts so a 50-frame character isn't drowned out by a 500-frame one. A positive number pins the value manually (e.g. 2.0 means each of this character's frames is seen twice per epoch)."
        >
          <h3 class="text-sm font-medium text-slate-200 mb-3 flex items-center gap-1">
            <span>Repeat multiplier</span>
            <span aria-hidden="true" class="text-slate-600 cursor-help text-xs">ⓘ</span>
          </h3>
          <label class="block text-xs">
            <span class="text-[10px] uppercase tracking-wide text-slate-500">
              multiply (0.0 = auto-balance from frame counts)
            </span>
            <input
              type="number" min="0" step="0.1"
              value={identityChar.multiply}
              onchange={(e) => {
                const v = Number((e.target as HTMLInputElement).value);
                if (project) saveIdentityField(project.slug, identityChar!.slug, {
                  multiply: v,
                });
              }}
              class="w-32 mt-1 px-3 py-1.5 bg-ink-950 border border-ink-700 rounded font-mono"
            />
          </label>
        </div>
      {/if}
