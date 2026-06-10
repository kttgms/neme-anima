import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createFlash } from "../src/lib/composables/flash.svelte";

describe("createFlash", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("turns on, then off after the duration", () => {
    const flash = createFlash(2000);
    expect(flash.active).toBe(false);
    flash.trigger();
    expect(flash.active).toBe(true);
    vi.advanceTimersByTime(1999);
    expect(flash.active).toBe(true);
    vi.advanceTimersByTime(1);
    expect(flash.active).toBe(false);
  });

  it("re-triggering restarts the countdown", () => {
    const flash = createFlash(2000);
    flash.trigger();
    vi.advanceTimersByTime(1500);
    flash.trigger();
    vi.advanceTimersByTime(1500);
    expect(flash.active).toBe(true);
    vi.advanceTimersByTime(500);
    expect(flash.active).toBe(false);
  });

  it("destroy() cancels the pending timer", () => {
    const flash = createFlash(2000);
    flash.trigger();
    flash.destroy();
    vi.advanceTimersByTime(5000);
    expect(flash.active).toBe(true);
  });
});
