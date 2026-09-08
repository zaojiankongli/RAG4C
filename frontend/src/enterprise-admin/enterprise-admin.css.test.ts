// Vitest runs this CSS contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("./enterprise-admin.css", import.meta.url), "utf8");

describe("enterprise admin responsive style contract", () => {
  it("contains dark-theme and compact 375px/280px layouts without a marketing card wall", () => {
    expect(css).toContain('[data-theme="dark"]');
    expect(css).toContain("@media (max-width: 375px)");
    expect(css).toContain("@media (max-width: 280px)");
    expect(css).toContain("grid-template-columns: 1fr");
    expect(css).toContain("prefers-reduced-motion");
    expect(css).toContain(".enterprise-admin-member__identity");
    expect(css).toContain(".enterprise-admin-status-cell");
    expect(css).toContain(".enterprise-admin-row-actions");
    expect(css).toContain(".enterprise-admin-member-table .t-table");
    expect(css).toContain(".enterprise-admin-form-grid > div");
    expect(css).toContain(".enterprise-admin-audit-detail__snapshot");
    expect(css).not.toContain("linear-gradient");
  });
});
