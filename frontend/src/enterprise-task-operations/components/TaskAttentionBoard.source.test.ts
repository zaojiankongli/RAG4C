// Vitest reads source contracts in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./TaskAttentionBoard.tsx", import.meta.url), "utf8");

describe("TaskAttentionBoard facade boundary", () => {
  it("uses the shared Tag facade instead of direct TDesign", () => {
    expect(source).not.toContain('from "tdesign-react"');
    expect(source).toContain('from "../../ui"');
  });
});
