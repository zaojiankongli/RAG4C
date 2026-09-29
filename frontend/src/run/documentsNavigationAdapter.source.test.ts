// Vitest runs this contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("../pages/DocumentsPage.tsx", import.meta.url), "utf8");

describe("Documents navigation commit boundary", () => {
  it("keeps Documents URL writes behind the shared adapter", () => {
    expect(source).toContain("commitNavigationIntent");
    expect(source).not.toMatch(/window\.history\.(pushState|replaceState)/);
    expect(source).not.toMatch(/window\.location\.hash\s*=/);
    expect(source).not.toMatch(/window\.dispatchEvent\(new PopStateEvent\("popstate"\)/);
  });

  it("keeps silent parse/filter commits and explicit operator event compatibility", () => {
    expect(source).toContain("dispatchPopStateAfterHistory: false");
    expect(source).toContain("dispatchPopStateAfterHash: true");
    expect(source).toContain("PARSE_ORIGIN_STATE");
  });
});
