// Vitest runs this route contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const routeSource = readFileSync(new URL("../AppRoutes.tsx", import.meta.url), "utf8");
// 主导航清单单独成一份模块（`run/appNav.tsx`），可达性守卫直接读它，不必把整个 App 拖进来。
const navSource = readFileSync(new URL("./appNav.tsx", import.meta.url), "utf8");

describe("Consistency Console route registration", () => {
  it("lazy-loads the page and places consistency under Quality & Ops", () => {
    expect(routeSource).toMatch(
      /const ConsistencyPage = lazy\(\(\) => import\("\.\/pages\/ConsistencyPage"\)\)/,
    );
    const operationsGroup = navSource.match(
      /label: "质量与运维",[\s\S]*?children: \[([\s\S]*?)\n\s*\],/,
    );
    expect(operationsGroup?.[1]).toContain('key: "consistency"');
    expect(operationsGroup?.[1]).toContain('label: "一致性控制台"');
    expect(routeSource).toMatch(/consistency:\s*<ConsistencyPage\s*\/>/);
  });
});
