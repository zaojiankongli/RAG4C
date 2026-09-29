// Vitest runs this source contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./AgreementPanel.tsx", import.meta.url), "utf8");

describe("AgreementPanel Card facade adoption contracts", () => {
  it("uses the shared Card facade instead of importing TDesign directly", () => {
    expect(source).toContain('import { Card } from "../../ui"');
    expect(source).not.toContain('from "tdesign-react"');
  });

  it("keeps the existing Card header and agreement surface vocabulary", () => {
    expect(source).toContain('header="判断一致性"');
    expect(source).toContain('className="rq-agreement"');
    expect(source).toContain("相关");
    expect(source).toContain("部分相关");
    expect(source).toContain("不相关");
  });
});
