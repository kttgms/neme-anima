// frontend/src/lib/tagVocabulary.svelte.ts
/** Session-singleton danbooru tag vocabulary. Lazily fetches the CSV served at
 *  /api/tags/vocabulary once, builds a ranked index, and exposes search().
 *  All ranking/parsing lives in tagSearch.ts (pure + tested). */
import {
  MAX_TAGS,
  parseVocabulary,
  searchTags,
  type Suggestion,
  type TagEntry,
} from "./tagSearch";

class TagVocabulary {
  entries = $state<TagEntry[]>([]);
  /** False after a 404 (not downloaded) or a fetch error — drives the Settings
   *  hint and makes search() a no-op. */
  available = $state(true);
  loaded = $state(false);
  #loadPromise: Promise<void> | null = null;

  /** Fetch + build the index exactly once. Safe to call repeatedly. */
  ensureLoaded(): Promise<void> {
    if (this.#loadPromise) return this.#loadPromise;
    this.#loadPromise = (async () => {
      try {
        const resp = await fetch("/api/tags/vocabulary");
        if (!resp.ok) {
          this.available = false;
          return;
        }
        const all = parseVocabulary(await resp.text());
        if (all.length > MAX_TAGS) {
          // Explicit, not a silent truncation (spec §6.2): say what was dropped.
          console.info(
            `[tag-vocabulary] loaded top ${MAX_TAGS} of ${all.length} tags ` +
              `(dropped ${all.length - MAX_TAGS} low-post-count tags)`,
          );
        }
        this.entries = all.length > MAX_TAGS ? all.slice(0, MAX_TAGS) : all;
        this.available = this.entries.length > 0;
      } catch {
        this.available = false;
      } finally {
        this.loaded = true;
      }
    })();
    return this.#loadPromise;
  }

  search(query: string, opts: { exclude?: Set<string> } = {}): Suggestion[] {
    if (this.entries.length === 0) return [];
    return searchTags(query, this.entries, { exclude: opts.exclude, limit: 10 });
  }
}

export const tagVocabulary = new TagVocabulary();
