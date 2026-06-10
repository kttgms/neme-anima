import { beforeEach, describe, expect, it } from "vitest";
import { focusTrap } from "../src/lib/actions/focusTrap";

function setup() {
  document.body.innerHTML = `
    <button id="outside">outside</button>
    <div id="dialog" tabindex="-1">
      <input id="first" />
      <button id="last">ok</button>
    </div>`;
  return document.getElementById("dialog") as HTMLElement;
}

function pressKey(target: HTMLElement, key: string, shiftKey = false) {
  target.dispatchEvent(
    new KeyboardEvent("keydown", { key, shiftKey, bubbles: true, cancelable: true }),
  );
}

describe("focusTrap", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("focuses the first focusable element on mount", async () => {
    const dialog = setup();
    const trap = focusTrap(dialog, {});
    await Promise.resolve(); // queueMicrotask
    expect(document.activeElement?.id).toBe("first");
    trap.destroy();
  });

  it("does not steal focus that is already inside (autofocus)", async () => {
    const dialog = setup();
    (document.getElementById("last") as HTMLElement).focus();
    const trap = focusTrap(dialog, {});
    await Promise.resolve();
    expect(document.activeElement?.id).toBe("last");
    trap.destroy();
  });

  it("wraps Tab from the last element to the first", async () => {
    const dialog = setup();
    const trap = focusTrap(dialog, {});
    await Promise.resolve();
    (document.getElementById("last") as HTMLElement).focus();
    pressKey(dialog, "Tab");
    expect(document.activeElement?.id).toBe("first");
    trap.destroy();
  });

  it("wraps Shift+Tab from the first element to the last", async () => {
    const dialog = setup();
    const trap = focusTrap(dialog, {});
    await Promise.resolve();
    (document.getElementById("first") as HTMLElement).focus();
    pressKey(dialog, "Tab", true);
    expect(document.activeElement?.id).toBe("last");
    trap.destroy();
  });

  it("calls onEscape on Escape", async () => {
    const dialog = setup();
    let escaped = false;
    const trap = focusTrap(dialog, { onEscape: () => { escaped = true; } });
    await Promise.resolve();
    pressKey(dialog, "Escape");
    expect(escaped).toBe(true);
    trap.destroy();
  });

  it("restores focus to the previously focused element on destroy", async () => {
    const dialog = setup();
    (document.getElementById("outside") as HTMLElement).focus();
    const trap = focusTrap(dialog, {});
    await Promise.resolve();
    trap.destroy();
    expect(document.activeElement?.id).toBe("outside");
  });
});
