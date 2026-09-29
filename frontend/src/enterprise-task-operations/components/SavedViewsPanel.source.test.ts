// Vitest reads source contracts in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./SavedViewsPanel.tsx", import.meta.url), "utf8");
const styleSource = readFileSync(new URL("../task-operations.css", import.meta.url), "utf8");

describe("SavedViewsPanel facade boundary", () => {
  it("uses the shared UI facade instead of importing TDesign controls", () => {
    expect(source).not.toContain('from "tdesign-react"');
    expect(source).toContain('from "../../ui"');
  });

  it("maps the historical text action to the shared button vocabulary", () => {
    expect(source).not.toContain('variant="text"');
    expect(source).toContain('type="text"');
  });

  it("keeps native and TDesign selectors for long saved-view actions", () => {
    expect(styleSource).toContain(".task-operations__saved-view .t-button");
    expect(styleSource).toContain(".task-operations__saved-view .rag-button");
  });

  it("declares stable active-state and warning-tag semantics", () => {
    expect(source).toContain('aria-pressed={view.id === controller.activeId}');
    expect(source).toContain('theme="warning" variant="light-outline"');
  });
});
