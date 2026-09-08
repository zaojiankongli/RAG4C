// Vitest runs this CSS contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

function block(selector: string): string {
  const start = css.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`missing CSS block: ${selector}`);
  const bodyStart = css.indexOf("{", start) + 1;
  const bodyEnd = css.indexOf("}", bodyStart);
  return css.slice(bodyStart, bodyEnd);
}

function hexToken(source: string, name: string): string {
  const match = source.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`));
  if (!match) throw new Error(`missing hex token: ${name}`);
  return match[1];
}

function hexProperty(source: string, name: string): string {
  const match = source.match(new RegExp(`(?:^|;)\\s*${name}:\\s*(#[0-9a-fA-F]{6})`));
  if (!match) throw new Error(`missing hex property: ${name}`);
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

const REQUIRED_DARK_TOKENS = [
  "td-brand-color-1",
  "td-brand-color-2",
  "td-brand-color-3",
  "td-brand-color-4",
  "td-brand-color-5",
  "td-brand-color-6",
  "td-brand-color-7",
  "td-brand-color-8",
  "td-brand-color-9",
  "td-brand-color-10",
  "td-brand-color",
  "td-brand-color-hover",
  "td-brand-color-focus",
  "td-brand-color-active",
  "td-brand-color-disabled",
  "td-brand-color-light",
  "td-brand-color-light-hover",
  "td-bg-color-page",
  "td-bg-color-container",
  "td-bg-color-container-hover",
  "td-bg-color-container-active",
  "td-bg-color-secondarycontainer",
  "td-bg-color-secondarycontainer-hover",
  "td-bg-color-component",
  "td-bg-color-component-hover",
  "td-bg-color-component-active",
  "td-bg-color-component-disabled",
  "td-bg-color-specialcomponent",
  "td-text-color-primary",
  "td-text-color-secondary",
  "td-text-color-placeholder",
  "td-text-color-disabled",
  "td-text-color-anti",
  "td-text-color-brand",
  "td-text-color-link",
  "td-border-level-1-color",
  "td-border-level-2-color",
  "td-component-stroke",
  "td-component-border",
  "td-success-color",
  "td-success-color-light",
  "td-warning-color",
  "td-warning-color-light",
  "td-error-color",
  "td-error-color-light",
  "td-rag-success-text",
  "td-rag-warning-text",
  "td-rag-error-text",
] as const;

describe("RAG4C TDesign dark theme contrast", () => {
  it("defines a complete dark token bridge instead of inheriting light TDesign values", () => {
    const dark = block('html[data-theme="dark"]');
    for (const name of REQUIRED_DARK_TOKENS) {
      expect(dark).toContain(`--${name}:`);
    }
  });

  it("keeps filled primary buttons, outline buttons, and primary tags at WCAG AA", () => {
    const dark = block('html[data-theme="dark"]');
    const container = hexToken(dark, "td-bg-color-container");
    const anti = hexToken(dark, "td-text-color-anti");
    const fill = hexToken(dark, "td-brand-color");
    const outline = hexToken(dark, "td-text-color-brand");
    const tagBackground = hexToken(dark, "td-brand-color-light");
    const dangerText = hexToken(dark, "td-error-color");

    expect(contrast(anti, fill)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(dangerText, container)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(outline, container)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(outline, tagBackground)).toBeGreaterThanOrEqual(4.5);

    const outlineRule = block(
      'html[data-theme="dark"] .t-button--variant-outline.t-button--theme-primary',
    );
    expect(hexProperty(outlineRule, "color").toLowerCase()).toBe(outline.toLowerCase());
    const primaryTagRule = block('html[data-theme="dark"] .t-tag--primary.t-tag--light');
    expect(hexProperty(primaryTagRule, "color").toLowerCase()).toBe(outline.toLowerCase());
  });

  it("applies the accessible app tertiary color to TDesign menu group titles", () => {
    expect(block(".app-sider .t-menu-group__title")).toContain(
      "color: var(--color-text-tertiary) !important",
    );
  });

  it("keeps the active TDesign navigation item readable in dark mode", () => {
    const active = block('html[data-theme="dark"] .app-sider .t-menu__item.t-is-active');
    const foreground = hexProperty(active, "color");
    const background = hexProperty(active, "background-color");
    expect(contrast(foreground, background)).toBeGreaterThanOrEqual(4.5);
  });

  it("keeps dark success, warning, and danger light tags at WCAG AA", () => {
    const dark = block('html[data-theme="dark"]');
    const pairs = [
      ["td-rag-success-text", "td-success-color-light"],
      ["td-rag-warning-text", "td-warning-color-light"],
      ["td-rag-error-text", "td-error-color-light"],
    ] as const;

    for (const [foreground, background] of pairs) {
      expect(
        contrast(hexToken(dark, foreground), hexToken(dark, background)),
      ).toBeGreaterThanOrEqual(4.5);
    }
  });
});
