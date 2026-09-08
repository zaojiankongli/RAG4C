// Vitest runs this source contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const center = readFileSync(
  new URL("./components/EnterpriseWorkspaceCenter.tsx", import.meta.url),
  "utf8",
);
const scopeBar = readFileSync(
  new URL("../ui/enterprise/WorkspaceScopeBar.tsx", import.meta.url),
  "utf8",
);

describe("Stage16 TDesign Workspace contracts", () => {
  it("uses TDesign Form controls for every business field", () => {
    expect(center).toMatch(/\bForm\b/);
    expect(center).toContain("<Form.FormItem");
    expect(center).toContain("<Input");
    expect(center).toContain("<Textarea");
    expect(center).toContain("<Select");
    expect(center).not.toMatch(/<(?:input|select|textarea)\b/);
  });

  it("uses the TDesign Select for the server Workspace selector", () => {
    expect(scopeBar).toContain("<Select");
    expect(scopeBar).not.toMatch(/<select\b/);
  });

  it("keeps the detail drawer within the viewport on narrow screens", () => {
    expect(center).toContain('size="min(720px, 100vw)"');
  });

  it("leaves the only page title to PageTopbar", () => {
    expect(center).not.toMatch(/<h[12][^>]*>\s*Enterprise Workspace Center/);
    expect(center).toContain("enterprise-workspace-table-toolbar");
  });
});
