import { describe, expect, it } from "vitest";
import {
  matchesQuery,
  reorder,
  tagsEqual,
  rangeBetween,
  moveSelection,
  movingIndices,
  dropIndexAtPoint,
} from "../src/lib/tagList";

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

describe("rangeBetween", () => {
  it("returns an inclusive range regardless of argument order", () => {
    expect(rangeBetween(3, 1)).toEqual([1, 2, 3]);
    expect(rangeBetween(2, 2)).toEqual([2]);
  });
});

describe("moveSelection", () => {
  it("moves a contiguous block forward", () => {
    const r = moveSelection(["a", "b", "c", "d"], [0, 1], 3);
    expect(r.next).toEqual(["c", "a", "b", "d"]);
    expect(r.selection).toEqual([1, 2]);
  });
  it("moves a non-contiguous selection preserving relative order", () => {
    const r = moveSelection(["a", "b", "c", "d"], [0, 2], 4);
    expect(r.next).toEqual(["b", "d", "a", "c"]);
    expect(r.selection).toEqual([2, 3]);
  });
  it("returns a copy with empty selection when nothing is selected", () => {
    const r = moveSelection(["a", "b"], [], 1);
    expect(r.next).toEqual(["a", "b"]);
    expect(r.selection).toEqual([]);
  });
});

describe("movingIndices", () => {
  it("moves the whole selection when the dragged tag is part of it", () => {
    expect(movingIndices(new Set([0, 2, 3]), 2)).toEqual([0, 2, 3]);
  });
  it("moves only the dragged tag when it is not selected", () => {
    expect(movingIndices(new Set([0, 2]), 4)).toEqual([4]);
  });
  it("moves only the dragged tag when nothing is selected", () => {
    expect(movingIndices(new Set<number>(), 1)).toEqual([1]);
  });
});

describe("dropIndexAtPoint (reading-order insertion)", () => {
  // Row 1: indices 0,1,2 (y 0-20). Row 2: index 3 (y 30-50).
  const rects = [
    { index: 0, rect: { left: 0, top: 0, right: 30, bottom: 20 } },
    { index: 1, rect: { left: 40, top: 0, right: 70, bottom: 20 } },
    { index: 2, rect: { left: 80, top: 0, right: 110, bottom: 20 } },
    { index: 3, rect: { left: 0, top: 30, right: 30, bottom: 50 } },
  ];
  it("inserts between two tags when the point is in the gap", () => {
    expect(dropIndexAtPoint(rects, 35, 10, 4)).toBe(1);
  });
  it("appends when the point is past the last tag on the last row", () => {
    expect(dropIndexAtPoint(rects, 200, 40, 4)).toBe(4);
  });
  it("inserts at the next row when the point is in an earlier row's trailing space", () => {
    expect(dropIndexAtPoint(rects, 300, 10, 4)).toBe(3);
  });
  it("inserts at the front when the point precedes everything", () => {
    expect(dropIndexAtPoint(rects, -5, -5, 4)).toBe(0);
  });
});
