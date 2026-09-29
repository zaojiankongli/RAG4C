// Vitest runs this source contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./ComparisonResults.tsx", import.meta.url), "utf8");

describe("ComparisonResults facade adoption contracts", () => {
  it("uses the shared facade for migrated result controls", () => {
    expect(source).toContain('from "../../ui"');
    expect(source).not.toContain('from "tdesign-react"');
    expect(source).toContain("<Alert");
    expect(source).toContain("<Card");
    expect(source).toContain("<Tag");
  });

  it("keeps the result-specific semantic consumers in this boundary", () => {
    expect(source).toContain("<EvidenceComparisonTable");
    expect(source).toContain("<LineageStrip");
    expect(source).toContain("<SafeTracePanel");
    expect(source).toContain("rq-neutral-note");
  });
});
