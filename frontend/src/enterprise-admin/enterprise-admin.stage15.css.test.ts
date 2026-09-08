import { describe, expect, it } from "vitest";

// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("./enterprise-admin.css", import.meta.url), "utf8");

describe("Stage15 member role approval responsive styles", () => {
  it("defines a scoped policy evidence surface, request result surface, and mobile breakpoints", () => {
    expect(css).toContain(".enterprise-admin-role-approval-policy");
    expect(css).toContain(".enterprise-admin-role-approval-request");
    expect(css).toContain("@media (max-width: 375px)");
    expect(css).toContain("@media (max-width: 280px)");
  });
});
