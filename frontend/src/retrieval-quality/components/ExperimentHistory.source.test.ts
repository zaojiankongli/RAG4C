// Vitest runs this source contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./ExperimentHistory.tsx", import.meta.url), "utf8");

describe("ExperimentHistory facade adoption contracts", () => {
  it("uses the shared facade instead of direct TDesign controls", () => {
    expect(source).toContain('from "../../ui"');
    expect(source).not.toContain('from "tdesign-react"');
  });

  it("uses facade button and input contracts", () => {
    expect(source).toContain('type="primary"');
    expect(source).toContain('type="text"');
    expect(source).not.toContain('variant="outline"');
    expect(source).not.toContain('variant="text"');
    expect(source).toContain("target?.value");
  });
});
