// Vitest runs this source contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./index.tsx", import.meta.url), "utf8");

describe("Collapse renderer parity contracts", () => {
  it("maps the compatibility contract to TDesign's expandMutex and normalized values", () => {
    const start = source.indexOf("const CompatTDesignCollapse");
    const section = source.slice(start, source.indexOf("export const Segmented", start));

    expect(section).toContain("expandMutex={accordion}");
    expect(source).toContain("value={normalizedValue}");
    expect(source).toContain("defaultValue={normalizedDefaultValue}");
    expect(source).toContain("normalizeCollapseItems(items)");
    expect(section).toContain("normalizeCollapseValue(nextValue, validKeys, accordion)");
  });

  it("keeps native trigger and panel ARIA links stable across collapse state", () => {
    const start = source.indexOf("const CompatCollapse");
    const end = source.indexOf("export const Collapse", start);
    const section = source.slice(start, end);

    expect(section).toContain("aria-controls={panelId}");
    expect(section).toContain("hidden={!expanded}");
    expect(section).toContain("disabled || item.disabled");
  });

  it("keeps the TDesign branch keyboard and ARIA addressable", () => {
    const start = source.indexOf("const CompatTDesignCollapse");
    const end = source.indexOf("export const Collapse", start);
    const section = source.slice(start, end);

    expect(section).toContain('role="button"');
    expect(section).toContain("tabIndex={itemDisabled ? -1 : 0}");
    expect(section).toContain("aria-expanded={expanded}");
    expect(section).toContain("aria-controls={panelId}");
    expect(section).toContain('role="region"');
    expect(section).toContain("aria-labelledby={triggerId}");
    expect(section).toContain("event.currentTarget.click()");
  });
});
