import { describe, expect, it } from "vitest";
import { createAsyncLoad } from "../src/lib/composables/asyncLoad.svelte";

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("createAsyncLoad", () => {
  it("applies the result and clears loading", async () => {
    const loader = createAsyncLoad();
    let applied: string | null = null;
    await loader.run(() => Promise.resolve("hi"), (v) => { applied = v; });
    expect(applied).toBe("hi");
    expect(loader.loading).toBe(false);
    expect(loader.error).toBeNull();
  });

  it("drops a stale response when a newer run has started", async () => {
    const loader = createAsyncLoad();
    const first = deferred<string>();
    const applied: string[] = [];
    const p1 = loader.run(() => first.promise, (v) => applied.push(v));
    const p2 = loader.run(() => Promise.resolve("new"), (v) => applied.push(v));
    await p2;
    first.resolve("old"); // resolves AFTER the newer run finished
    await p1;
    expect(applied).toEqual(["new"]);
    expect(loader.loading).toBe(false);
  });

  it("records the error and invokes onError for the current run", async () => {
    const loader = createAsyncLoad();
    let reset = false;
    await loader.run(
      () => Promise.reject(new Error("boom")),
      () => {},
      () => { reset = true; },
    );
    expect(loader.error).toBe("boom");
    expect(reset).toBe(true);
    expect(loader.loading).toBe(false);
  });

  it("a stale rejection neither sets error nor calls onError", async () => {
    const loader = createAsyncLoad();
    const first = deferred<string>();
    let onErrorCalled = false;
    const p1 = loader.run(() => first.promise, () => {}, () => { onErrorCalled = true; });
    await loader.run(() => Promise.resolve("new"), () => {});
    first.reject(new Error("stale boom"));
    await p1;
    expect(loader.error).toBeNull();
    expect(onErrorCalled).toBe(false);
  });

  it("settle() lands in a non-loading, no-error state", () => {
    const loader = createAsyncLoad();
    loader.settle();
    expect(loader.loading).toBe(false);
    expect(loader.error).toBeNull();
  });
});
