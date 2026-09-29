// Vitest runs this contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const pageSources = [
  [
    "ChunkWorkbenchPage",
    readFileSync(new URL("../pages/ChunkWorkbenchPage.tsx", import.meta.url), "utf8"),
  ],
  [
    "KnowledgeOverviewPage",
    readFileSync(new URL("../pages/KnowledgeOverviewPage.tsx", import.meta.url), "utf8"),
  ],
  [
    "KnowledgeTaxonomyPage",
    readFileSync(new URL("../pages/KnowledgeTaxonomyPage.tsx", import.meta.url), "utf8"),
  ],
] as const;

describe("page navigation commit boundary", () => {
  it("keeps page route commits behind the shared adapter", () => {
    for (const [, source] of pageSources) {
      expect(source).toContain("commitNavigationIntent");
      expect(source).not.toMatch(/window\.history\.pushState/);
      expect(source).not.toMatch(/window\.location\.hash\s*=/);
      expect(source).not.toMatch(/window\.dispatchEvent\(new PopStateEvent\("popstate"\)/);
    }
  });

  it("pins legacy hash-event compatibility to the pages that historically emitted it", () => {
    const overview = pageSources.find(([name]) => name === "KnowledgeOverviewPage")?.[1] ?? "";
    const taxonomy = pageSources.find(([name]) => name === "KnowledgeTaxonomyPage")?.[1] ?? "";

    expect(overview).toContain("dispatchPopStateAfterHash: true");
    expect(taxonomy).toContain("dispatchPopStateAfterHash: true");
  });
});
