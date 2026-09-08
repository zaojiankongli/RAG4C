// @vitest-environment node
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("./enterprise-identity.css", import.meta.url), "utf8");

describe("Stage 10 SCIM data-plane responsive CSS", () => {
  it("styles the endpoint evidence and token usage cards across dark/mobile widths", () => {
    expect(css).toMatch(/\.identity-scim-data-plane[^{]*\{/);
    expect(css).toMatch(/\.identity-scim-endpoints[^{]*\{/);
    expect(css).toMatch(/\.identity-scim-token-evidence[^{]*\{/);
    expect(css).toMatch(
      /@media\s*\(max-width:\s*600px\)[\s\S]*\.identity-scim-endpoints[^{]*\{[^}]*grid-template-columns:\s*1fr/s,
    );
    expect(css).toMatch(/@media\s*\(max-width:\s*280px\)[\s\S]*\.identity-scim-data-plane/s);
    expect(css).toContain("var(--td-bg-color-container");
  });
});
