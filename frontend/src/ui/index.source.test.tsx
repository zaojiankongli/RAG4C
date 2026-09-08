// Vitest runs this source contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./index.tsx", import.meta.url), "utf8");

describe("native TDesign compatibility branches", () => {
  it("forwards loading and className through the native button branch", () => {
    expect(source).toMatch(/<TButton[^>]*className=\{className\}[^>]*loading=\{loading\}/s);
  });

  it("keeps the native tooltip controlled by both hover and focus", () => {
    expect(source).toMatch(/<TTooltip[^>]*visible=\{visible\}[^>]*onVisibleChange=\{setVisible\}/s);
    expect(source).not.toMatch(/if \(canRenderTDesign\(\)\) return <TTooltip[^;]+>\{children\}<\/TTooltip>;/s);
  });
});
