// Vitest runs this CSS contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../content-recovery.css", import.meta.url), "utf8");

describe("Stage 23 content recovery visual contracts", () => {
  it("defines an official enterprise hierarchy and the lifecycle signature", () => {
    expect(css).toContain(".content-recovery__header");
    expect(css).toContain(".content-recovery__rail");
    expect(css).toContain(".content-recovery__desktop-table");
    expect(css).toContain(".content-recovery__mobile-cards");
    expect(css).toContain("@media (max-width: 720px)");
    expect(css).toContain("@media (max-width: 320px)");
  });

  it("keeps dark theme, visible focus, and reduced-motion contracts", () => {
    expect(css).toContain('html[data-theme="dark"] .content-recovery');
    expect(css).toMatch(/:focus-visible[\s\S]*outline:/);
    expect(css).toMatch(/prefers-reduced-motion:[\s\S]*animation-duration:\s*0\.01ms/);
    expect(css).toMatch(/prefers-reduced-motion:[\s\S]*transition-duration:\s*0\.01ms/);
  });

  it("supports narrow enterprise surfaces without forcing a desktop minimum width", () => {
    expect(css).toContain("overflow-wrap: anywhere");
    expect(css).toContain("min-width: 0");
    expect(css).toContain("grid-template-columns: 1fr");
  });
});
