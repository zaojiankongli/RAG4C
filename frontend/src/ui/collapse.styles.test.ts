// Vitest runs this CSS contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

describe("native Collapse visual contracts", () => {
  it("defines keyboard-visible trigger, panel, and modifier surfaces", () => {
    expect(css).toContain(".rag-collapse");
    expect(css).toContain(".rag-collapse-trigger:focus-visible");
    expect(css).toContain(".rag-collapse-panel");
    expect(css).toContain(".rag-collapse.is-ghost");
    expect(css).toContain(".rag-collapse.is-small");
  });
});
