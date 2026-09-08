// @vitest-environment node
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
const css = readFileSync(new URL("./enterprise-identity.css", import.meta.url), "utf8");
describe("Stage 12 OIDC runtime responsive CSS", () => {
  it("styles runtime evidence, callback result and narrow provider actions", () => {
    expect(css).toMatch(/\.identity-oidc-runtime[^{]*\{/);
    expect(css).toMatch(/\.oidc-callback-surface[^{]*\{/);
    expect(css).toMatch(/\.identity-oidc-start[^{]*\{/);
    expect(css).toMatch(/\.identity-oidc-runtime\s*>\s*div\s*>\s*span[^{]*\{/);
    expect(css).not.toMatch(/\.identity-oidc-runtime\s+span[^{]*\{/);
    expect(css).toMatch(/@media\s*\(max-width:\s*600px\)[\s\S]*\.identity-oidc-runtime/s);
    expect(css).toMatch(/@media\s*\(max-width:\s*280px\)[\s\S]*\.oidc-callback-surface/s);
    expect(css).toMatch(/prefers-reduced-motion:\s*reduce/);
  });
});
