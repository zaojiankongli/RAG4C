// Vitest runs this source contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./JudgmentEditor.tsx", import.meta.url), "utf8");

describe("JudgmentEditor facade adoption contracts", () => {
  it("uses the shared facade for all migrated controls", () => {
    expect(source).toContain('from "../../ui"');
    expect(source).not.toContain('from "tdesign-react"');
  });

  it("keeps score and note normalization explicit", () => {
    expect(source).toContain('score: value === undefined || value === "" ? null : Number(value)');
    expect(source).toContain("maxLength={20000}");
    expect(source).toContain("target?.value");
  });
});
