// Vitest runs this CSS contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("./enterprise-workspace.css", import.meta.url), "utf8");

describe("enterprise workspace responsive styling", () => {
  it("keeps the eyebrow selector from leaking into TDesign button text", () => {
    expect(css).toContain(".enterprise-workspace-center__header > div:nth-child(2) > span");
    expect(css).not.toContain(".enterprise-workspace-center__header span {");
  });
  it("uses a restrained console surface with dense desktop table and mobile cards", () => {
    expect(css).toContain(".enterprise-workspace-center");
    expect(css).toContain(".enterprise-workspace-table");
    expect(css).toContain(".enterprise-workspace-cards");
    expect(css).toMatch(
      /@media \(max-width: 600px\)[\s\S]*enterprise-workspace-table[\s\S]*display:\s*none/,
    );
    expect(css).toMatch(
      /@media \(max-width: 600px\)[\s\S]*enterprise-workspace-cards[\s\S]*display:\s*grid/,
    );
  });

  it("allows the Workspace Center title to wrap instead of clipping at 280px", () => {
    expect(css).toMatch(
      /enterprise-workspace-center__header h2\s*\{[^}]*overflow-wrap:\s*anywhere/s,
    );
  });

  it("wraps long authorization evidence inside the permissions alert", () => {
    expect(css).toMatch(
      /enterprise-workspace-permissions \.t-alert__description\s*\{[^}]*overflow-wrap:\s*anywhere[^}]*word-break:\s*break-all/s,
    );
  });

  it("keeps 375 and 280 layouts single-column without horizontal overflow", () => {
    expect(css).toMatch(/@media \(max-width: 375px\)/);
    expect(css).toMatch(/@media \(max-width: 280px\)/);
    expect(css).toMatch(/max-width:\s*100%/);
    expect(css).toMatch(/overflow-wrap:\s*anywhere/);
  });

  it("keeps the authorization evidence and mode panel readable at 375px and 280px", () => {
    expect(css).toContain(".workspace-authorization-change-evidence");
    expect(css).toMatch(
      /@media \(max-width: 600px\)[\s\S]*workspace-authorization-change-evidence[\s\S]*grid-template-columns:\s*1fr/s,
    );
    expect(css).toMatch(
      /@media \(max-width: 280px\)[\s\S]*workspace-authorization-mode-dialog[\s\S]*padding:\s*10px/s,
    );
  });

  it("bounds the mobile authorization mode panel to the viewport with internal scrolling", () => {
    expect(css).toMatch(
      /@media \(max-width: 600px\)[\s\S]*workspace-authorization-mode-dialog[\s\S]*max-height:\s*calc\(100dvh - 72px\)[\s\S]*overflow-y:\s*auto/s,
    );
  });

  it("bridges dark theme tokens and reduced motion", () => {
    expect(css).toMatch(/\[data-theme=["']dark["']\]/);
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)/);
  });
});
