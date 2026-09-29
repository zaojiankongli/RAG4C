// Vitest runs this contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("../pages/VisualizePage.tsx", import.meta.url), "utf8");

describe("Visualize navigation commit boundary", () => {
  it("keeps canonical URL replacement behind the shared adapter", () => {
    expect(source).toContain("commitNavigationIntent");
    expect(source).toContain("dispatchPopStateAfterHistory: false");
    expect(source).not.toMatch(/window\.history\.replaceState/);
  });
});
