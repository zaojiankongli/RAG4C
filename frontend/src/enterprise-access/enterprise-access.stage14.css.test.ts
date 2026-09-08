// @vitest-environment node
// @ts-expect-error -- test-only Node builtin without adding @types/node.

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("./enterprise-access.css", import.meta.url), "utf8");

describe("Stage 14 Dataset ACL approval dialog responsive CSS", () => {
  it("styles policy facts, request evidence and approval navigation with TDesign tokens", () => {
    expect(css).toMatch(/\.enterprise-access-acl-approval-policy[^{]*\{[^}]*border/s);
    expect(css).toMatch(/\.enterprise-access-acl-approval-request[^{]*\{[^}]*background/s);
    expect(css).toMatch(/\.enterprise-access-acl-approval-request__link/);
    expect(css).toMatch(/var\(--td-(brand|text|component|bg)-/);
  });

  it("stacks policy facts and actions for 375px and 280px viewports", () => {
    expect(css).toMatch(
      /@media\s*\(max-width:\s*375px\)[\s\S]*\.enterprise-access-acl-approval-policy__facts[^{]*\{[^}]*grid-template-columns:\s*1fr/s,
    );
    expect(css).toMatch(
      /@media\s*\(max-width:\s*280px\)[\s\S]*\.enterprise-access-acl-approval-request[^{]*\{[^}]*padding/s,
    );
  });
});
