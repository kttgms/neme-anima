import { describe, expect, it } from "vitest";
import { buildVocabulary, formatCount, normalizeTagKey, searchTags } from "../src/lib/tagSearch";

describe("formatCount", () => {
  it("compacts millions and thousands", () => {
    expect(formatCount(7_641_780)).toBe("7.6M");
    expect(formatCount(12_345)).toBe("12k");
    expect(formatCount(842)).toBe("842");
  });
  it("rounds 999999 up into the M tier instead of '1000k'", () => {
    expect(formatCount(999_999)).toBe("1.0M");
  });
});

const CSV = [
  '1girl,0,7641780,"sole_female,1girls"',
  'long_hair,0,5624146,"/lh,longhair"',
  'longcoat,0,50000,""',
  'very_long_hair,0,40000,',
  '^_^,0,12345,',
].join("\n");

describe("normalizeTagKey", () => {
  it("lowercases, converts underscores, collapses whitespace", () => {
    expect(normalizeTagKey("Long_Hair")).toBe("long hair");
    expect(normalizeTagKey("  a__b  ")).toBe("a b");
  });
});

describe("buildVocabulary", () => {
  it("parses rows into the space form and preserves kaomoji underscores", () => {
    const v = buildVocabulary(CSV, 100);
    const names = v.map((e) => e.name);
    expect(names).toContain("long hair");
    expect(names).toContain("^_^"); // kaomoji underscores preserved
    const lh = v.find((e) => e.name === "long hair")!;
    expect(lh.count).toBe(5624146);
    expect(lh.aliases).toContain("longhair");
  });
  it("sorts by post count descending and applies the cap", () => {
    const v = buildVocabulary(CSV, 2);
    expect(v.map((e) => e.name)).toEqual(["1girl", "long hair"]);
  });
});

describe("searchTags", () => {
  const vocab = buildVocabulary(CSV, 100);

  it("ranks prefix above substring", () => {
    const r = searchTags("long", vocab, {});
    // "long hair" / "longcoat" are prefix; "very long hair" is word-prefix.
    expect(r[0].entry.name).toBe("long hair"); // highest count among prefixes
    expect(r.some((s) => s.entry.name === "very long hair")).toBe(true);
  });

  it("orders by post count within a tier", () => {
    const r = searchTags("long", vocab, {}).filter((s) => !s.viaAlias);
    const longHairIdx = r.findIndex((s) => s.entry.name === "long hair");
    const longcoatIdx = r.findIndex((s) => s.entry.name === "longcoat");
    expect(longHairIdx).toBeLessThan(longcoatIdx);
  });

  it("matches underscore/space/case-insensitively", () => {
    expect(searchTags("long_h", vocab, {})[0].entry.name).toBe("long hair");
    expect(searchTags("LONG H", vocab, {})[0].entry.name).toBe("long hair");
  });

  it("surfaces the canonical tag via an alias and flags it", () => {
    const r = searchTags("longhair", vocab, {});
    const hit = r.find((s) => s.entry.name === "long hair");
    expect(hit?.viaAlias).toBe(true);
  });

  it("excludes already-present tags", () => {
    const r = searchTags("long", vocab, { exclude: new Set(["long hair"]) });
    expect(r.some((s) => s.entry.name === "long hair")).toBe(false);
  });

  it("respects the limit and returns nothing for an empty query", () => {
    expect(searchTags("long", vocab, { limit: 1 }).length).toBe(1);
    expect(searchTags("   ", vocab, {})).toEqual([]);
  });

  it("returns the most-popular prefix matches and stops early (no higher-count substring leaks in)", () => {
    // 15 prefix matches plus a substring match ('scatter') with a far higher
    // post count, which buildVocabulary sorts to the front. The early-break must
    // still return the top-10 *prefix* matches and exclude the substring hit.
    const rows: string[] = ["scatter,0,99999999,"];
    for (let i = 0; i < 15; i++) rows.push(`cat_${i},0,${100000 - i},`);
    const big = buildVocabulary(rows.join("\n"));
    const r = searchTags("cat", big, { limit: 10 });
    expect(r.length).toBe(10);
    expect(r.every((s) => s.entry.name.startsWith("cat "))).toBe(true);
    expect(r.some((s) => s.entry.name === "scatter")).toBe(false);
    // Most popular prefix match ('cat 0', count 100000) ranks first.
    expect(r[0].entry.name).toBe("cat 0");
  });
});
