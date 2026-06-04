/** Pure tag-vocabulary logic: parsing the danbooru CSV into a ranked index and
 *  matching a query against it. No Svelte runes here so it's trivially testable
 *  and importable from the reactive store. */

/** Max entries kept in the in-memory index, by descending post count. The
 *  danbooru list has ~200k rows; the dropped tail is near-zero-count noise.
 *  This also bounds the per-keystroke worst case (a query with fewer than
 *  `limit` prefix matches scans the whole index): 30k keeps that scan ~1-2ms
 *  while still covering far more tags than WD14 (~9k) or a human ever types. */
export const MAX_TAGS = 30_000;

/** danbooru category int -> { label, Tailwind text colour } for dropdown rows. */
export const CATEGORY: Record<number, { label: string; color: string }> = {
  0: { label: "general", color: "text-slate-200" },
  1: { label: "artist", color: "text-rose-300" },
  3: { label: "copyright", color: "text-fuchsia-300" },
  4: { label: "character", color: "text-emerald-300" },
  5: { label: "meta", color: "text-amber-300" },
};

export function categoryColor(category: number): string {
  return CATEGORY[category]?.color ?? "text-slate-200";
}

/** Mirror of imgutils.tagging.format._KAOMOJIS — these tags keep their
 *  underscores when WD14 emits them, so we must not space them out either. */
const KAOMOJIS = new Set([
  "0_0", "(o)_(o)", "+_+", "+_-", "._.", "<o>_<o>", "<|>_<|>", "=_=", ">_<",
  "3_3", "6_9", ">_o", "@_@", "^_^", "o_o", "u_u", "x_x", "|_|", "||_||",
]);

/** Convert a danbooru name (underscores) to the WD14/on-disk space form,
 *  preserving kaomoji underscores exactly as imgutils' remove_underline does. */
export function toDisplayTag(name: string): string {
  return KAOMOJIS.has(name) ? name : name.replace(/_/g, " ");
}

/** Normalize for case/underscore/space-insensitive matching. */
export function normalizeTagKey(s: string): string {
  return s.trim().toLowerCase().replace(/_/g, " ").replace(/\s+/g, " ");
}

export interface TagEntry {
  /** Display + insert value, in the WD14/on-disk space form. */
  name: string;
  /** Pre-normalized key for matching. */
  nameKey: string;
  /** danbooru category int (0/1/3/4/5). */
  category: number;
  /** danbooru post count (popularity, ranking key). */
  count: number;
  /** Alias forms, pre-normalized for matching. */
  aliases: string[];
}

export interface Suggestion {
  entry: TagEntry;
  /** True when the match came via an alias rather than the canonical name. */
  viaAlias: boolean;
}

/** Parse one CSV row: name,category,count,"a,b,c". The first three fields never
 *  contain commas; everything after the third comma is the (optionally quoted)
 *  alias field, which itself may contain commas. */
function parseRow(line: string): TagEntry | null {
  const i1 = line.indexOf(",");
  if (i1 < 0) return null;
  const i2 = line.indexOf(",", i1 + 1);
  if (i2 < 0) return null;
  const i3 = line.indexOf(",", i2 + 1);

  const rawName = line.slice(0, i1).trim();
  if (!rawName) return null;
  const category = Number(line.slice(i1 + 1, i2));
  const countStr = (i3 < 0 ? line.slice(i2 + 1) : line.slice(i2 + 1, i3)).trim();
  const count = Number(countStr);
  if (!Number.isFinite(category) || !Number.isFinite(count)) return null;

  let aliasRaw = i3 < 0 ? "" : line.slice(i3 + 1).trim();
  if (aliasRaw.startsWith('"') && aliasRaw.endsWith('"')) {
    aliasRaw = aliasRaw.slice(1, -1);
  }
  const aliases = aliasRaw
    ? aliasRaw.split(",").map((a) => normalizeTagKey(a)).filter(Boolean)
    : [];

  const name = toDisplayTag(rawName);
  return { name, nameKey: normalizeTagKey(name), category, count, aliases };
}

/** Parse the full CSV text into a ranked index, uncapped. Sorted by post
 *  count descending. Callers that want the cap applied use buildVocabulary;
 *  this is exposed so the caller can report how many tags fall beyond the cap. */
export function parseVocabulary(csvText: string): TagEntry[] {
  const out: TagEntry[] = [];
  for (const line of csvText.split("\n")) {
    if (!line.trim()) continue;
    const entry = parseRow(line);
    if (entry) out.push(entry);
  }
  out.sort((a, b) => b.count - a.count);
  return out;
}

/** Parse the full CSV text into a ranked, capped index. */
export function buildVocabulary(csvText: string, cap: number = MAX_TAGS): TagEntry[] {
  const all = parseVocabulary(csvText);
  return all.length > cap ? all.slice(0, cap) : all;
}

/** Rank vocabulary entries for a query. Tiers (best first): name-prefix (incl.
 *  exact), name word-prefix, name substring, alias hit. Within a tier, higher
 *  post count wins. Excludes any tag whose normalized key is in `exclude`.
 *
 *  `entries` MUST be pre-sorted by post count descending (buildVocabulary does
 *  this). That invariant is what lets the scan stop early: the first matches we
 *  see in each tier are already the most popular, and once `limit` prefix
 *  matches are found they fill the whole result, so the long, less-popular tail
 *  can't change the answer. A common keystroke therefore scans only the first
 *  few hundred entries instead of all ~100k. */
export function searchTags(
  query: string,
  entries: TagEntry[],
  opts: { exclude?: Set<string>; limit?: number },
): Suggestion[] {
  const q = normalizeTagKey(query);
  if (!q) return [];
  const exclude = opts.exclude ?? new Set<string>();
  const limit = opts.limit ?? 10;
  const spaceQ = " " + q; // " <query>" — a non-first word starting with q

  // Per-tier buckets, each capped at `limit`. Filled in count-descending order
  // because `entries` is pre-sorted, so each bucket already holds its most
  // popular members.
  const prefix: TagEntry[] = [];
  const word: TagEntry[] = [];
  const sub: TagEntry[] = [];
  const alias: TagEntry[] = [];

  for (let i = 0; i < entries.length; i++) {
    const e = entries[i];
    const k = e.nameKey;
    if (exclude.has(k)) continue;
    if (k.startsWith(q)) {
      prefix.push(e);
      if (prefix.length >= limit) break; // result fully determined — stop
      continue;
    }
    // Classify by tier first, then cap — a word match never leaks into `sub`.
    if (k.includes(spaceQ)) {
      if (word.length < limit) word.push(e);
    } else if (k.includes(q)) {
      if (sub.length < limit) sub.push(e);
    } else if (alias.length < limit) {
      const aliases = e.aliases;
      for (let j = 0; j < aliases.length; j++) {
        if (aliases[j].includes(q)) {
          alias.push(e);
          break;
        }
      }
    }
  }

  const out: Suggestion[] = [];
  for (let i = 0; i < prefix.length && out.length < limit; i++) {
    out.push({ entry: prefix[i], viaAlias: false });
  }
  for (let i = 0; i < word.length && out.length < limit; i++) {
    out.push({ entry: word[i], viaAlias: false });
  }
  for (let i = 0; i < sub.length && out.length < limit; i++) {
    out.push({ entry: sub[i], viaAlias: false });
  }
  for (let i = 0; i < alias.length && out.length < limit; i++) {
    out.push({ entry: alias[i], viaAlias: true });
  }
  return out;
}

/** Compact a post count for display: 7641780 -> "7.6M", 12345 -> "12k". */
export function formatCount(n: number): string {
  // 999_500+ rounds into the M tier so we never render "1000k".
  if (n >= 999_500) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`;
  return String(n);
}
