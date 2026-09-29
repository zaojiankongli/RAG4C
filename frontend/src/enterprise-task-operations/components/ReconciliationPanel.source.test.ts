// Vitest reads source contracts in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./ReconciliationPanel.tsx", import.meta.url), "utf8");
const styleSource = readFileSync(new URL("../task-operations.css", import.meta.url), "utf8");

describe("ReconciliationPanel facade boundary", () => {
  it("uses the shared UI facade instead of importing TDesign controls", () => {
    expect(source).not.toContain('from "tdesign-react"');
    expect(source).toContain('from "../../ui"');
  });

  it("maps the historical text action to the shared button vocabulary", () => {
    expect(source).not.toContain('variant="text"');
    expect(source).toContain('type="text"');
  });

  it("keeps native and TDesign selectors for the resolve action", () => {
    expect(styleSource).toContain(".task-operations__reconciliation-copy .t-button");
    expect(styleSource).toContain(".task-operations__reconciliation-copy .rag-button");
  });
});
