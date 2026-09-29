// Vitest runs this contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const workspaceSource = readFileSync(
  new URL("../enterprise-workspace/components/EnterpriseWorkspaceCenter.tsx", import.meta.url),
  "utf8",
);
const knowledgeBaseSource = readFileSync(
  new URL(
    "../enterprise-knowledge-base/components/EnterpriseKnowledgeBaseCenter.tsx",
    import.meta.url,
  ),
  "utf8",
);

describe("enterprise resource navigation commit boundary", () => {
  it("keeps Workspace and Knowledge Base route writes behind the shared adapter", () => {
    for (const source of [workspaceSource, knowledgeBaseSource]) {
      expect(source).toContain("commitNavigationIntent");
      expect(source).not.toMatch(/window\.history\.(pushState|replaceState)/);
      expect(source).not.toMatch(/window\.location\.hash\s*=/);
      expect(source).not.toMatch(/window\.dispatchEvent\(new PopStateEvent\("popstate"\)/);
    }
  });

  it("keeps replace semantics explicit at both resource route writers", () => {
    expect(workspaceSource).toContain('historyAction: replace ? "replace" : "push"');
    expect(knowledgeBaseSource).toContain('historyAction: replace ? "replace" : "push"');
  });
});
