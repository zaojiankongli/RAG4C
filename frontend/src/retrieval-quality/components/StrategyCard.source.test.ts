// Vitest runs this source contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./StrategyCard.tsx", import.meta.url), "utf8");

describe("StrategyCard facade adoption contracts", () => {
  it("uses the shared UI facade instead of direct TDesign imports", () => {
    expect(source).toContain('from "../../ui"');
    expect(source).not.toContain('from "tdesign-react"');
  });

  it("keeps native-compatible value and action contracts", () => {
    expect(source).toContain("maxLength={64}");
    expect(source).toContain("checked={Boolean(value[key])}");
    expect(source).toContain('next == null || next === "" ? 0 : Number(next)');
    expect(source).toContain('type="text"');
    expect(source).toContain("danger");
    expect(source).toContain('variant="light-outline"');
  });
});
