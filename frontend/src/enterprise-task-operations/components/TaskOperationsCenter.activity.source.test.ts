// Vitest reads source contracts in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./TaskOperationsCenter.tsx", import.meta.url), "utf8");

describe("TaskOperationsCenter activity notice facade boundary", () => {
  it("uses the shared Alert facade", () => {
    expect(source).not.toContain('from "tdesign-react"');
    expect(source).toContain('from "../../ui/index"');
  });
});
