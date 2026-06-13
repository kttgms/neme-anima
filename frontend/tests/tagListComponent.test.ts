import { describe, it, expect, vi, beforeAll, afterAll } from "vitest";
import { render, fireEvent } from "@testing-library/svelte";
import { tick } from "svelte";
import TagList from "../src/lib/components/TagList.svelte";

// happy-dom bug: `instanceof Comment` returns false for comment nodes because
// the Comment class exposed in the global scope has a different prototype than
// the one actually used for nodes created by innerHTML / template.content.
// Svelte 5's first_child() relies on `instanceof Comment` to skip the leading
// empty anchor comment it prepends to every fragment template.  Without the
// fix the traversal lands on the wrong sibling and set_attribute receives a
// text node instead of an element, crashing with "getAttribute is not a function".
beforeAll(() => {
  Object.defineProperty(Comment, Symbol.hasInstance, {
    value(obj: unknown) {
      return (obj as Node | null)?.nodeType === 8; // Node.COMMENT_NODE
    },
    configurable: true,
    writable: true,
  });
});

afterAll(() => {
  delete (Comment as unknown as Record<symbol, unknown>)[Symbol.hasInstance];
});

function renderRich(tags: string[]) {
  const onchange = vi.fn();
  const onselectionchange = vi.fn();
  const result = render(TagList, {
    props: {
      tags,
      onchange,
      size: "md",
      autocomplete: false,
      reorderable: true,
      searchable: true,
      selectable: true,
      onselectionchange,
    },
  });
  const surface = result.container.querySelector('[data-testid="tag-editor-surface"]') as HTMLElement;
  const pills = () => Array.from(result.container.querySelectorAll("[data-tag-index]")) as HTMLElement[];
  return { ...result, onchange, onselectionchange, surface, pills };
}

describe("rich TagList", () => {
  it("single-click selects a pill (does not enter edit mode)", async () => {
    const { pills, onselectionchange, surface } = renderRich(["a", "b", "c"]);
    await fireEvent(pills()[0], new MouseEvent("pointerdown", { button: 0, bubbles: true }));
    await fireEvent.click(pills()[0]);
    await tick();
    expect(onselectionchange).toHaveBeenLastCalledWith(["a"]);
    // The surface itself must not contain an editing input (the search input
    // lives outside the surface; TagPill edit inputs are inside it).
    expect(surface.querySelector("input")).toBeNull();
  });

  it("Ctrl/Cmd-click toggles pills into a multi-selection", async () => {
    const { pills, onselectionchange } = renderRich(["a", "b", "c"]);
    await fireEvent(pills()[0], new MouseEvent("pointerdown", { button: 0, ctrlKey: true, bubbles: true }));
    await tick();
    await fireEvent(pills()[2], new MouseEvent("pointerdown", { button: 0, ctrlKey: true, bubbles: true }));
    await tick();
    expect(onselectionchange).toHaveBeenLastCalledWith(["a", "c"]);
  });

  it("double-click enters edit mode (renders an input)", async () => {
    const { pills, container } = renderRich(["a", "b"]);
    await fireEvent.dblClick(pills()[1]);
    await tick();
    expect(container.querySelector("input")).not.toBeNull();
  });

  it("clicking empty space clears the selection", async () => {
    const { pills, surface, onselectionchange } = renderRich(["a", "b"]);
    await fireEvent(pills()[0], new MouseEvent("pointerdown", { button: 0, bubbles: true }));
    await fireEvent.click(pills()[0]);
    await tick();
    expect(onselectionchange).toHaveBeenLastCalledWith(["a"]);
    await fireEvent.click(surface);
    await tick();
    expect(onselectionchange).toHaveBeenLastCalledWith([]);
  });

  it("Delete removes the selected tags", async () => {
    const { pills, surface, onchange } = renderRich(["a", "b", "c"]);
    await fireEvent(pills()[1], new MouseEvent("pointerdown", { button: 0, bubbles: true }));
    await fireEvent.click(pills()[1]);
    await tick();
    await fireEvent.keyDown(surface, { key: "Delete" });
    await tick();
    expect(onchange).toHaveBeenLastCalledWith(["a", "c"]);
  });

  it("Ctrl/Cmd+A selects all", async () => {
    const { surface, onselectionchange } = renderRich(["a", "b", "c"]);
    await fireEvent.keyDown(surface, { key: "a", ctrlKey: true });
    await tick();
    expect(onselectionchange).toHaveBeenLastCalledWith(["a", "b", "c"]);
  });
});
