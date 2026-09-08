// Vitest runs this CSS contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

function body(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = css.match(new RegExp(escaped + "\\s*\\{([^}]*)\\}"));
  if (!match) throw new Error("missing CSS rule: " + selector);
  return match[1].replace(/\s+/g, " ");
}

describe("Consistency Console responsive contracts", () => {
  it("uses a keyboard-focusable horizontal scroll region with a 375px-safe table", () => {
    expect(body(".consistency-table-scroll")).toMatch(/overflow-x:\s*auto/);
    expect(body(".consistency-table-scroll")).toMatch(/overflow-y:\s*hidden/);
    expect(body(".consistency-table-scroll:focus-visible")).toMatch(/outline:/);
    expect(body(".consistency-table-scroll .t-table")).toMatch(/overflow:\s*visible/);
    expect(body(".consistency-table-scroll .t-table__content")).toMatch(/overflow:\s*visible/);
    expect(body(".consistency-table-scroll .t-table__content > table")).toMatch(
      /min-width:\s*980px/,
    );
  });

  it("contains the wide native table inside the 375px grid item", () => {
    expect(css).toMatch(/\.consistency-shell\s*\{[^}]*min-width:\s*0/);
    expect(css).toMatch(
      /\.consistency-overview,\s*\.consistency-dead-letter-section\s*\{[^}]*min-width:\s*0/,
    );
  });

  it("truncates refs visually while retaining the full DOM value", () => {
    expect(body(".consistency-ref-text")).toMatch(/text-overflow:\s*ellipsis/);
    expect(body(".consistency-ref-text")).toMatch(/white-space:\s*nowrap/);
  });

  it("stacks lifeline metrics and actions at 375px", () => {
    expect(css).toMatch(
      /@media\s*\(max-width:\s*600px\)[\s\S]*\.consistency-lifeline\s*\{[^}]*grid-template-columns:\s*1fr/,
    );
  });
});
