// @vitest-environment node
// @ts-expect-error -- test-only Node builtin without adding @types/node.

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("./enterprise-access.css", import.meta.url), "utf8");

describe("enterprise access graph responsive CSS", () => {
  it("keeps dense tables horizontally scrollable and stacks the group relation view on mobile", () => {
    expect(css).toMatch(/\.enterprise-access-table-viewport[^{]*\{[^}]*overflow-x:\s*auto/s);
    expect(css).toMatch(
      /@media\s*\(max-width:\s*375px\)[\s\S]*\.enterprise-access-group-layout[^{]*\{[^}]*grid-template-columns:\s*1fr/s,
    );
    expect(css).toMatch(/@media\s*\(max-width:\s*280px\)/);
  });

  it("uses TDesign tokens and includes visible keyboard focus", () => {
    expect(css).toContain("var(--td-brand-color");
    expect(css).toContain("var(--td-bg-color-container");
    expect(css).toMatch(/:focus-visible/);
  });

  it("switches invitation tables to cards and an action drawer at 375px and 280px", () => {
    expect(css).toMatch(/\.enterprise-invitation-desktop[^{]*\{/);
    expect(css).toMatch(/\.enterprise-invitation-mobile[^{]*\{/);
    expect(css).toMatch(/\.enterprise-invitation-card[^{]*\{/);
    expect(css).toMatch(/\.enterprise-invitation-action-drawer[^{]*\{/);
    expect(css).toMatch(
      /@media\s*\(max-width:\s*600px\)[\s\S]*\.enterprise-invitation-desktop[^{]*\{[^}]*display:\s*none/s,
    );
    expect(css).toMatch(
      /@media\s*\(max-width:\s*600px\)[\s\S]*\.enterprise-invitation-mobile[^{]*\{[^}]*display:\s*grid/s,
    );
    expect(css).toMatch(/@media\s*\(max-width:\s*280px\)[\s\S]*\.enterprise-invitation-card/s);
  });

  it("provides a dense ACL evidence surface that stacks at 375px and 280px", () => {
    expect(css).toMatch(/\.enterprise-access-acl-evidence[^{]*{/);
    expect(css).toMatch(/\.enterprise-access-acl-evidence__facts[^{]*{/);
    expect(css).toMatch(
      /@media\s*\(max-width:\s*375px\)[\s\S]*\.enterprise-access-acl-evidence__facts[^{]*{[^}]*grid-template-columns:\s*1fr/s,
    );
    expect(css).toMatch(
      /@media\s*\(max-width:\s*280px\)[\s\S]*\.enterprise-access-acl-evidence[^{]*{/s,
    );
  });
});
