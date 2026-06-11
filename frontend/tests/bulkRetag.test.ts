import { beforeEach, describe, expect, it, vi } from "vitest";

// Mock the API module the runner depends on. The runner is store-agnostic, so
// the only collaborator to stub is $lib/api (the two bulk endpoints).
vi.mock("$lib/api", () => ({
  bulkRetagDanbooru: vi.fn(),
  bulkRetagLLM: vi.fn(),
}));

import * as api from "$lib/api";
import { runBulkRetag, type BulkRetagActions } from "../src/lib/bulkRetag";

const danbooru = vi.mocked(api.bulkRetagDanbooru);
const llm = vi.mocked(api.bulkRetagLLM);

/** A BulkRetagActions that records every call so tests can assert on them. */
function recorder() {
  const calls = {
    markProcessing: [] as string[][],
    unmarkProcessing: [] as string[],
    markDone: [] as string[],
    deselect: [] as string[][],
    error: [] as string[],
  };
  const actions: BulkRetagActions = {
    markProcessing: (f) => calls.markProcessing.push([...f]),
    unmarkProcessing: (f) => calls.unmarkProcessing.push(f),
    markDone: (f) => calls.markDone.push(f),
    deselect: (f) => calls.deselect.push([...f]),
    error: (m) => calls.error.push(m),
  };
  return { actions, calls };
}

/** Shape a successful WD14 retag response for one filename. */
function tagOk(fn: string) {
  return { retagged: 1, total: 1, skipped: [], effective_filenames: [fn] };
}
/** Shape a WD14 retag response that the server skipped, with a reason. */
function tagSkip(fn: string, reason: string) {
  return {
    retagged: 0,
    total: 1,
    skipped: [{ filename: fn, reason }],
    effective_filenames: [fn],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("runBulkRetag — tag (WD14)", () => {
  it("marks all in-flight up front, marks done + deselects each success, no error toast", async () => {
    danbooru.mockImplementation(async (_slug, files) => tagOk(files[0]));
    const { actions, calls } = recorder();

    const result = await runBulkRetag("tag", "proj", ["a", "b"], actions);

    expect(calls.markProcessing).toEqual([["a", "b"]]); // once, up front, with all
    expect(calls.markDone).toEqual(["a", "b"]);
    expect(calls.deselect).toEqual([["a"], ["b"]]);
    expect(calls.unmarkProcessing).toEqual(["a", "b"]); // finally, per item
    expect(calls.error).toEqual([]);
    expect(result).toEqual({ succeeded: 2, failed: 0, total: 2 });
  });

  it("surfaces a server skip reason and leaves the failed frame untouched", async () => {
    danbooru.mockImplementation(async (_slug, files) =>
      files[0] === "b" ? tagSkip("b", "frame not found on disk") : tagOk(files[0]),
    );
    const { actions, calls } = recorder();

    const result = await runBulkRetag("tag", "proj", ["a", "b"], actions);

    expect(calls.markDone).toEqual(["a"]); // only the success
    expect(calls.deselect).toEqual([["a"]]); // failed frame stays selected
    expect(calls.unmarkProcessing).toEqual(["a", "b"]); // both cleared regardless
    expect(calls.error).toHaveLength(1);
    expect(calls.error[0]).toContain("1 of 2 frames failed to tag");
    expect(calls.error[0]).toContain("frame not found on disk");
    expect(calls.error[0]).toContain("stay selected so you can retry");
    expect(result).toEqual({ succeeded: 1, failed: 1, total: 2 });
  });

  it("treats a thrown API error as a failure carrying the error message", async () => {
    danbooru.mockImplementation(async (_slug, files) => {
      if (files[0] === "b") throw new Error("network down");
      return tagOk(files[0]);
    });
    const { actions, calls } = recorder();

    const result = await runBulkRetag("tag", "proj", ["a", "b"], actions);

    expect(calls.markDone).toEqual(["a"]);
    expect(calls.unmarkProcessing).toEqual(["a", "b"]); // finally runs even on throw
    expect(calls.error[0]).toContain("network down");
    expect(result.failed).toBe(1);
  });

  it("uses the singular 'frame' and 'unknown error' fallback for a lone reasonless skip", async () => {
    danbooru.mockResolvedValue({
      retagged: 0,
      total: 1,
      skipped: [],
      effective_filenames: ["a"],
    });
    const { actions, calls } = recorder();

    await runBulkRetag("tag", "proj", ["a"], actions);

    expect(calls.error[0]).toContain("1 of 1 frame failed to tag");
    expect(calls.error[0]).not.toContain("frames failed"); // singular, no plural s
    expect(calls.error[0]).toContain("unknown error");
  });

  it("de-duplicates reasons and caps the toast at 3 distinct ones", async () => {
    const reasons: Record<string, string> = {
      a: "r1", b: "r1", c: "r2", d: "r3", e: "r4",
    };
    danbooru.mockImplementation(async (_slug, files) =>
      tagSkip(files[0], reasons[files[0]]),
    );
    const { actions, calls } = recorder();

    const result = await runBulkRetag("tag", "proj", ["a", "b", "c", "d", "e"], actions);

    expect(result.failed).toBe(5);
    const msg = calls.error[0];
    expect(msg).toContain("r1");
    expect(msg).toContain("r2");
    expect(msg).toContain("r3");
    expect(msg).not.toContain("r4"); // 4th distinct reason is dropped
  });
});

describe("runBulkRetag — describe (LLM)", () => {
  it("marks done on the effective (crop) filename, not the requested one", async () => {
    llm.mockResolvedValue({
      described: 1,
      total: 1,
      error: null,
      skipped: [],
      effective_filenames: ["a_crop"],
    });
    const { actions, calls } = recorder();

    const result = await runBulkRetag("describe", "proj", ["a"], actions);

    expect(calls.markDone).toEqual(["a_crop"]); // crop derivative, per the runner's contract
    expect(calls.deselect).toEqual([["a"]]); // deselect uses the requested filename
    expect(result).toEqual({ succeeded: 1, failed: 0, total: 1 });
  });

  it("falls back to res.error for the reason when skipped is empty", async () => {
    llm.mockResolvedValue({
      described: 0,
      total: 1,
      error: "LM Studio offline",
      skipped: [],
      effective_filenames: ["a"],
    });
    const { actions, calls } = recorder();

    await runBulkRetag("describe", "proj", ["a"], actions);

    expect(calls.error[0]).toContain("failed to describe");
    expect(calls.error[0]).toContain("LM Studio offline");
  });

  it("falls back to the requested filename when effective_filenames is empty", async () => {
    llm.mockResolvedValue({
      described: 1,
      total: 1,
      error: null,
      skipped: [],
      effective_filenames: [],
    });
    const { actions, calls } = recorder();

    await runBulkRetag("describe", "proj", ["a"], actions);

    expect(calls.markDone).toEqual(["a"]);
  });
});
