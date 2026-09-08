// @vitest-environment node
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

describe("KnowledgeDocumentTable scroll contract", () => {
  it("lets TDesign own horizontal scrolling so the fixed action column remains sticky", () => {
    expect(css).toMatch(/\.knowledge-document-table \{[\s\S]*?overflow: hidden;/);
    expect(css).toMatch(
      /\.knowledge-document-table \.t-table__content \{[\s\S]*?max-width: 100%;[\s\S]*?overflow-x: auto;/,
    );
    expect(css).toMatch(
      /\.knowledge-document-table \.t-table__content > table \{[\s\S]*?min-width: 1030px;/,
    );
    expect(css).not.toContain(
      ".knowledge-document-table .t-table__content {\n  overflow: visible;",
    );
    expect(css.match(/\.knowledge-document-table \.t-table \{/g)).toHaveLength(1);
  });
});
