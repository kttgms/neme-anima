import { describe, expect, it } from "vitest";
import { matchesQuery, reorder, tagsEqual } from "../src/lib/tagList";

describe("reorder", () => {
  it("moves an item forward", () => {
    expect(reorder(["a", "b", "c", "d"], 0, 2)).toEqual(["b", "c", "a", "d"]);
  });
  it("moves an item backward", () => {
    expect(reorder(["a", "b", "c", "d"], 3, 1)).toEqual(["a", "d", "b", "c"]);
  });
  it("clamps an out-of-range target", () => {
    expect(reorder(["a", "b", "c"], 0, 99)).toEqual(["b", "c", "a"]);
  });
  it("returns a fresh copy for an out-of-range source", () => {
    const src = ["a", "b"];
    const out = reorder(src, 5, 0);
    expect(out).toEqual(["a", "b"]);
    expect(out).not.toBe(src);
  });
});

describe("tagsEqual", () => {
  it("is true for identical order", () => {
    expect(tagsEqual(["a", "b"], ["a", "b"])).toBe(true);
  });
  it("is false when order differs", () => {
    expect(tagsEqual(["a", "b"], ["b", "a"])).toBe(false);
  });
  it("is false when length differs", () => {
    expect(tagsEqual(["a"], ["a", "b"])).toBe(false);
  });
});

describe("matchesQuery", () => {
  it("matches case-insensitively", () => {
    expect(matchesQuery("1Girl", "girl")).toBe(true);
  });
  it("does not match on an empty/whitespace query", () => {
    expect(matchesQuery("anything", "  ")).toBe(false);
  });
  it("is false for a non-substring", () => {
    expect(matchesQuery("smile", "frown")).toBe(false);
  });
});
