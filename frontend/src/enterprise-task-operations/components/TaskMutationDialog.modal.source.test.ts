// Vitest reads source contracts in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./TaskMutationDialog.tsx", import.meta.url), "utf8");

describe("TaskMutationDialog modal semantics", () => {
  it("declares an explicit modal boundary for the nested confirmation dialog", () => {
    expect(source).toContain('role: "dialog"');
    expect(source).toContain('"aria-modal": "true"');
  });
});
