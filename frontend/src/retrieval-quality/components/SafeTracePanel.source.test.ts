// Vitest runs this source contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./SafeTracePanel.tsx", import.meta.url), "utf8");

describe("SafeTracePanel facade adoption contracts", () => {
  it("uses the shared Collapse facade instead of importing TDesign directly", () => {
    expect(source).toContain('import { Collapse } from "../../ui"');
    expect(source).not.toContain('from "tdesign-react"');
    expect(source).not.toContain("Collapse.Panel");
  });

  it("preserves the closed-by-default trace items contract", () => {
    expect(source).toContain("defaultValue={[]}");
    expect(source).toContain("borderless");
    expect(source).toContain("items={[");
    expect(source).toContain('key: "trace"');
    expect(source).toContain("rq-traces");
    expect(source).toContain("安全执行 Trace");
  });
});
