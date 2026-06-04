/** Pure tag-vocabulary logic: parsing the danbooru CSV into a ranked index and
 *  matching a query against it. No Svelte runes here so it's trivially testable
 *  and importable from the reactive store. */

/** Max entries kept in the in-memory index, by descending post count. The
 *  danbooru list has ~400k rows; the dropped tail is near-zero-count noise. */
export const MAX_TAGS = 100_000;

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

function wordPrefix(nameKey: string, q: string): boolean {
  return nameKey.split(" ").some((w) => w.startsWith(q));
}

/** Rank vocabulary entries for a query. Tiers (best first): exact, name-prefix,
 *  name word-prefix, name substring, alias hit. Within a tier, higher post
 *  count wins. Excludes any tag whose normalized key is in `exclude`. */
export function searchTags(
  query: string,
  entries: TagEntry[],
  opts: { exclude?: Set<string>; limit?: number },
): Suggestion[] {
  const q = normalizeTagKey(query);
  if (!q) return [];
  const exclude = opts.exclude ?? new Set<string>();
  const limit = opts.limit ?? 10;

  const scored: { s: Suggestion; tier: number }[] = [];
  for (const e of entries) {
    if (exclude.has(e.nameKey)) continue;
    let tier = -1;
    let viaAlias = false;
    if (e.nameKey === q) tier = 0;
    else if (e.nameKey.startsWith(q)) tier = 1;
    else if (wordPrefix(e.nameKey, q)) tier = 2;
    else if (e.nameKey.includes(q)) tier = 3;
    else if (e.aliases.some((a) => a.includes(q))) {
      tier = 4;
      viaAlias = true;
    }
    if (tier < 0) continue;
    scored.push({ s: { entry: e, viaAlias }, tier });
  }
  scored.sort((a, b) => a.tier - b.tier || b.s.entry.count - a.s.entry.count);
  return scored.slice(0, limit).map((x) => x.s);
}

/** Compact a post count for display: 7641780 -> "7.6M", 12345 -> "12k". */
export function formatCount(n: number): string {
  // 999_500+ rounds into the M tier so we never render "1000k".
  if (n >= 999_500) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`;
  return String(n);
}
