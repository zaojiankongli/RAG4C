// Vitest runs this CSS contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

let css = "";
let shellCss = "";
try {
  css = readFileSync(new URL("../../theme/enterprise-components.css", import.meta.url), "utf8");
  shellCss = readFileSync(new URL("../../shell/enterprise-shell.css", import.meta.url), "utf8");
} catch {
  css = "";
  shellCss = "";
}

describe("enterprise component visual contracts", () => {
  it("defines a responsive enterprise workspace hierarchy", () => {
    expect(css).toContain(".enterprise-workspace-scope");
    expect(css).toContain(".enterprise-lifecycle-rail");
    expect(css).toContain(".enterprise-metric-strip");
    expect(css).toContain("@media (max-width: 1080px)");
    expect(css).toContain("@media (max-width: 720px)");
  });

  it("supports the application dark theme contract", () => {
    expect(css).toContain('html[data-theme="dark"]');
    expect(css).toContain("--enterprise-surface");
    expect(css).toContain("--enterprise-border");
  });

  it("keeps keyboard focus visible on every enterprise interaction", () => {
    expect(css).toContain(":focus-visible");
    expect(css).toMatch(/:focus-visible\s*\{[^}]*outline:/s);
  });

  it("removes decorative motion when the user requests reduced motion", () => {
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
    expect(css).toMatch(/prefers-reduced-motion:[\s\S]*animation-duration:\s*0\.01ms/s);
    expect(css).toMatch(/prefers-reduced-motion:[\s\S]*transition-duration:\s*0\.01ms/s);
  });

  it("keeps environment and keyboard-hint text on the readable secondary color", () => {
    expect(css).toMatch(/enterprise-workspace-scope__environment\s*\{[^}]*color:\s*var\(--enterprise-text-secondary\)/s);
    expect(css).toMatch(/enterprise-workspace-scope__search kbd\s*\{[^}]*color:\s*var\(--enterprise-text-secondary\)/s);
  });

  it("overrides the legacy 320px floor for narrower accessible viewports", () => {
    expect(shellCss).toContain("@media (max-width: 319px)");
    expect(shellCss).toMatch(/html,\s*body\s*\{[^}]*min-width:\s*0/s);
    expect(shellCss).toMatch(/\.app-layout,\s*\.app-content\s*\{[^}]*max-width:\s*100%/s);
  });

});
