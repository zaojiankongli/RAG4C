// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("./governance.css", import.meta.url), "utf8");

function block(selector: string): string {
  const start = css.indexOf(selector);
  if (start < 0) return "";
  const open = css.indexOf("{", start);
  const close = css.indexOf("}", open);
  return css.slice(open + 1, close);
}

describe("knowledge governance responsive CSS", () => {
  it("gives the 375px QA table one keyboard-focusable horizontal scroll owner", () => {
    const owner = block(".governance-table-scroll");
    expect(owner).toContain("overflow-x: auto");
    expect(owner).toContain("max-width: 100%");
    expect(block(".governance-table-scroll:focus-visible")).toContain("outline:");
    expect(block(".governance-table-scroll .t-table__content > table")).toContain("min-width: 1180px");
  });

  it("uses existing color tokens and stacks governance forms and facts on narrow screens", () => {
    expect(css).toContain("var(--color-bg-elevated)");
    expect(css).toContain("var(--color-text)");
    expect(css).toContain("@media (max-width: 600px)");
    expect(css).toMatch(/\.governance-form-grid[\s\S]*grid-template-columns:\s*1fr/);
    expect(css).toContain(".governance-dialog");
    expect(css).toContain(".governance-drawer");
  });

  it("respects reduced motion for governance interactions", () => {
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
    expect(css).toContain("transition: none");
  });
});
