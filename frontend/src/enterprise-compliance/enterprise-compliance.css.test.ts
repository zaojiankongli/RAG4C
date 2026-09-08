// @vitest-environment node
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
const css = readFileSync(new URL("./enterprise-compliance.css", import.meta.url), "utf8");
describe("Stage 11 compliance responsive CSS", () => {
  it("provides enterprise surfaces, mobile cards/drawer, dark tokens and reduced motion", () => {
    expect(css).toMatch(/\.enterprise-compliance-center[^{]*\{/);
    expect(css).toMatch(/\.compliance-policy-card[^{]*\{/);
    expect(css).toMatch(/\.compliance-mobile-list[^{]*\{/);
    expect(css).toMatch(/\.compliance-action-drawer[^{]*\{/);
    expect(css).toMatch(
      /@media\s*\(max-width:\s*600px\)[\s\S]*\.compliance-desktop[^{]*\{[^}]*display:\s*none/s,
    );
    expect(css).toMatch(
      /@media\s*\(max-width:\s*600px\)[\s\S]*\.compliance-mobile-list[^{]*\{[^}]*display:\s*grid/s,
    );
    expect(css).toMatch(/@media\s*\(max-width:\s*280px\)[\s\S]*\.compliance-lifecycle-card/s);
    expect(css).toContain("var(--td-bg-color-container");
    expect(css).toMatch(/prefers-reduced-motion:\s*reduce/);
  });
});
