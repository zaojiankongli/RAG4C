// Vitest runs this source contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./index.tsx", import.meta.url), "utf8");

describe("Statistic/Skeleton TDesign Adapter contracts", () => {
  it("maps the compatibility Statistic props to TDesign's vocabulary", () => {
    const start = source.indexOf("const CompatStatistic");
    const end = source.indexOf("export const Statistic", start);
    const section = source.slice(start, end);

    expect(section).toContain("decimalPlaces={precision}");
    expect(section).toContain("format={formatter}");
    expect(section).toContain("color={tdesignColor}");
    expect(section).toContain("style={{ ...style, ...tdesignValueStyle }}");
    expect(section).not.toContain("valueStyle={valueStyle}");
  });

  it("keeps Skeleton's historical TDesign animation contract while native active=false is static", () => {
    const start = source.indexOf("export const Skeleton");
    const end = source.indexOf("const COLOR_THEME", start);
    const section = source.slice(start, end);

    expect(section).toContain('animation="gradient"');
    expect(section).not.toMatch(/<TSkeleton[\s\S]*\bactive=\{active\}/);
    expect(source).toContain("paragraph === false");
  });
});
