// Vitest reads source contracts in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./taskOperationsUi.tsx", import.meta.url), "utf8");

describe("Task Operations state notice facade boundary", () => {
  it("uses shared state-notice facades instead of direct TDesign controls", () => {
    expect(source).not.toContain('from "tdesign-react"');
    expect(source).toContain('from "../../ui"');
    expect(source).toContain("<Spin");
  });
});
