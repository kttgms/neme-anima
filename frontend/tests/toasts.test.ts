import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { toasts } from "../src/lib/stores/toasts.svelte";

describe("toasts", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    // The store is a module singleton — drain it so tests stay independent.
    for (const t of [...toasts.list]) toasts.dismiss(t.id);
    vi.useRealTimers();
  });

  it("success and info auto-dismiss after 4s; error sticks", () => {
    toasts.success("ok");
    toasts.info("fyi");
    toasts.error("bad");
    expect(toasts.list.map((t) => t.kind)).toEqual(["success", "info", "error"]);
    vi.advanceTimersByTime(4000);
    expect(toasts.list.map((t) => t.kind)).toEqual(["error"]);
  });

  it("dismiss removes a toast by id", () => {
    const id = toasts.error("bad");
    toasts.dismiss(id);
    expect(toasts.list).toEqual([]);
  });

  it("pause freezes auto-dismiss; resume restarts the full countdown", () => {
    const id = toasts.success("ok");
    vi.advanceTimersByTime(3000);
    toasts.pause(id);
    vi.advanceTimersByTime(10_000);
    expect(toasts.list).toHaveLength(1);
    toasts.resume(id);
    vi.advanceTimersByTime(3999);
    expect(toasts.list).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(toasts.list).toHaveLength(0);
  });

  it("pause/resume on a sticky error is a no-op (it never auto-dismisses)", () => {
    const id = toasts.error("bad");
    toasts.pause(id);
    toasts.resume(id);
    vi.advanceTimersByTime(60_000);
    expect(toasts.list).toHaveLength(1);
  });
});
