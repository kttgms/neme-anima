import { describe, expect, it } from "vitest";
import { buildVocabulary, normalizeTagKey, searchTags } from "../src/lib/tagSearch";

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
});
