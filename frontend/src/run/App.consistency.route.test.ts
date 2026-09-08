// Vitest runs this route contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("../App.tsx", import.meta.url), "utf8");

describe("Consistency Console route registration", () => {
  it("lazy-loads the page and places consistency under Knowledge Operations", () => {
    expect(source).toMatch(
      /const ConsistencyPage = lazy\(\(\) => import\("\.\/pages\/ConsistencyPage"\)\)/,
    );
    const operationsGroup = source.match(
      /label: "知识运营",[\s\S]*?children: \[([\s\S]*?)\n\s*\],/,
    );
    expect(operationsGroup?.[1]).toContain('key: "consistency"');
    expect(operationsGroup?.[1]).toContain('label: "一致性控制台"');
    expect(source).toMatch(/consistency:\s*<ConsistencyPage\s*\/>/);
  });
});
