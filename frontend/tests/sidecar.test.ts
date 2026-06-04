import { describe, expect, it } from "vitest";
import { parseTags, splitSidecar } from "../src/lib/sidecar";

describe("splitSidecar", () => {
  it("returns empty parts for empty input", () => {
    expect(splitSidecar("")).toEqual({ danbooru: "", description: "" });
  });
  it("splits the tags line from the description", () => {
    expect(splitSidecar("1girl, smile\nA happy girl.")).toEqual({
      danbooru: "1girl, smile",
      description: "A happy girl.",
    });
  });
  it("treats a single line as tags only", () => {
    expect(splitSidecar("1girl, smile")).toEqual({
      danbooru: "1girl, smile",
      description: "",
    });
  });
  it("skips blank lines between tags and a multi-line description", () => {
    expect(splitSidecar("1girl\n\n  \nmulti\nline")).toEqual({
      danbooru: "1girl",
      description: "multi\nline",
    });
  });
});

describe("parseTags", () => {
  it("splits, trims and drops empty entries", () => {
    expect(parseTags(" a ,b,  , c ")).toEqual(["a", "b", "c"]);
  });
  it("dedupes preserving first-seen order", () => {
    expect(parseTags("a, b, a, c, b")).toEqual(["a", "b", "c"]);
  });
  it("returns [] for an empty line", () => {
    expect(parseTags("")).toEqual([]);
  });
});
