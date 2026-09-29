// Vitest runs this contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const appSource = readFileSync(new URL("../App.tsx", import.meta.url), "utf8");

describe("App navigation commit boundary", () => {
  it("keeps browser URL side effects inside the navigation adapter", () => {
    expect(appSource).not.toMatch(/window\.history\.pushState/);
    expect(appSource).not.toMatch(/window\.location\.hash\s*=/);
    expect(appSource).toContain("commitNavigationIntent");
  });
});
