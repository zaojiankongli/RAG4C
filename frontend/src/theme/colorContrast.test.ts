// Vitest runs this CSS contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { BRAND } from "./tokens";

const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

function block(selector: string): string {
  const start = css.indexOf(selector + " {");
  if (start < 0) throw new Error("missing token block: " + selector);
  const bodyStart = css.indexOf("{", start) + 1;
  const bodyEnd = css.indexOf("}", bodyStart);
  return css.slice(bodyStart, bodyEnd);
}

function token(source: string, name: string): string {
  const match = source.match(new RegExp("--" + name + ":\\s*(#[0-9a-fA-F]{6})"));
  if (!match) throw new Error("missing hex token: " + name);
  return match[1];
}

function property(source: string, name: string): string {
  const match = source.match(new RegExp("(?:^|;)\\s*" + name + ":\\s*(#[0-9a-fA-F]{6})"));
  if (!match) throw new Error("missing hex property: " + name);
  return match[1];
}

function luminance(hex: string): number {
  const channels = [1, 3, 5].map((index) => Number.parseInt(hex.slice(index, index + 2), 16) / 255);
  const [red, green, blue] = channels.map((value) =>
    value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4,
  );
  return red * 0.2126 + green * 0.7152 + blue * 0.0722;
}

function contrast(foreground: string, background: string): number {
  const first = luminance(foreground);
  const second = luminance(background);
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
}

describe("accessible semantic color tokens", () => {
  it("meets WCAG AA for light tertiary and semantic status text", () => {
    const light = block(":root");
    const tertiary = token(light, "color-text-tertiary");
    const success = token(light, "color-success");
    const warning = token(light, "color-warning");
    const danger = token(light, "color-danger");
    const elevated = token(light, "color-bg-elevated");
    const warningBackground = token(light, "color-warning-bg");
    const dangerBackground = token(light, "color-danger-bg");

    expect(contrast(tertiary, elevated)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(success, elevated)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(warning, warningBackground)).toBeGreaterThanOrEqual(4.5);
    const warningTag = block(':root:not([data-theme="dark"]) .t-tag--warning');
    const tagColor = property(warningTag, "color");
    const tagBackground = property(warningTag, "background");
    expect(tagColor.toLowerCase()).toBe("#6b2d0e");
    expect(tagBackground.toLowerCase()).toBe("#fffaeb");
    expect(property(warningTag, "border-color").toLowerCase()).toBe("#fde68a");
    expect(contrast(tagColor, tagBackground)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(danger, dangerBackground)).toBeGreaterThanOrEqual(4.5);
    expect(BRAND.success.toLowerCase()).toBe(success.toLowerCase());
    expect(BRAND.warning.toLowerCase()).toBe(warning.toLowerCase());
    expect(BRAND.danger.toLowerCase()).toBe(danger.toLowerCase());
  });

  it("keeps dark tertiary and semantic status text above AA", () => {
    const dark = block('html[data-theme="dark"]');
    const elevated = token(dark, "color-bg-elevated");
    expect(contrast(token(dark, "color-text-tertiary"), elevated)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(token(dark, "color-success"), elevated)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(token(dark, "color-warning"), elevated)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(token(dark, "color-danger"), elevated)).toBeGreaterThanOrEqual(4.5);
  });

  it("keeps eyebrow label tokens above AA in every theme", () => {
    const light = block(":root");
    const dark = block('html[data-theme="dark"]');
    const anime = block('html[data-theme="anime"]');
    const lightSurface = token(light, "color-bg-elevated");

    expect(contrast(token(light, "color-eyebrow"), lightSurface)).toBeGreaterThanOrEqual(4.5);
    expect(
      contrast(token(light, "color-eyebrow-warning"), token(light, "color-warning-bg")),
    ).toBeGreaterThanOrEqual(4.5);
    expect(
      contrast(token(dark, "color-eyebrow"), token(dark, "color-bg-elevated")),
    ).toBeGreaterThanOrEqual(4.5);
    expect(
      contrast(token(dark, "color-eyebrow-warning"), token(dark, "color-bg-elevated")),
    ).toBeGreaterThanOrEqual(4.5);
    expect(contrast(token(anime, "color-eyebrow"), lightSurface)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(token(anime, "color-eyebrow-warning"), lightSurface)).toBeGreaterThanOrEqual(4.5);

    // 品牌主色直接当 10px eyebrow 文字用不达 AA —— 这正是 eyebrow 必须独立成 token 的原因
    expect(contrast(token(light, "color-primary"), token(light, "color-primary-bg"))).toBeLessThan(4.5);
  });
});
