// @vitest-environment node
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
const css = readFileSync(new URL("./enterprise-identity.css", import.meta.url), "utf8");
describe("Stage 9 enterprise identity responsive CSS", () => {
  it("provides desktop tables, mobile cards/drawer, dark tokens and 280px stacking", () => {
    expect(css).toMatch(/\.enterprise-identity-center[^{]*\{/);
    expect(css).toMatch(/\.identity-domain-card[^{]*\{/);
    expect(css).toMatch(/\.identity-mobile-list[^{]*\{/);
    expect(css).toMatch(/\.identity-action-drawer[^{]*\{/);
    expect(css).toMatch(
      /@media\s*\(max-width:\s*600px\)[\s\S]*\.identity-desktop[^{]*\{[^}]*display:\s*none/s,
    );
    expect(css).toMatch(
      /@media\s*\(max-width:\s*600px\)[\s\S]*\.identity-mobile-list[^{]*\{[^}]*display:\s*grid/s,
    );
    expect(css).toMatch(/@media\s*\(max-width:\s*280px\)[\s\S]*\.identity-lifecycle-card/s);
    expect(css).toContain("var(--td-bg-color-container");
    expect(css).toMatch(/prefers-reduced-motion:\s*reduce/);
  });
});
