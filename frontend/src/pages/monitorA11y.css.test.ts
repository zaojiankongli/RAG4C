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

describe("Monitor responsive and focus contracts", () => {
  it("lets the TDesign table content own horizontal scrolling", () => {
    expect(body(".monitor-table-scroll")).toMatch(/overflow:\s*hidden/);
    expect(body(".monitor-table-scroll:focus-visible")).toMatch(/outline:/);
    expect(body(".monitor-table-scroll .t-table")).toMatch(/width:\s*100%/);
    expect(body(".monitor-table-scroll .t-table")).toMatch(/min-width:\s*0/);
    expect(body(".monitor-table-scroll .t-table__content")).toMatch(/max-width:\s*100%/);
    expect(body(".monitor-table-scroll .t-table__content")).toMatch(/overflow-x:\s*auto/);
    expect(body(".monitor-metrics-table .t-table__content > table")).toMatch(/min-width:\s*760px/);
    expect(body(".monitor-recent-table .t-table__content > table")).toMatch(/min-width:\s*920px/);
    expect(css).not.toContain(".monitor-table-scroll .t-table__content {\n  overflow: visible");
  });

  it("uses readable scoped text colors on tinted monitor surfaces", () => {
    expect(body(".monitor-attention-group .t-typography--secondary")).toMatch(
      /color:\s*var\(--color-text-secondary\)/,
    );
    expect(
      body(
        ':root:not([data-theme="dark"]) .ingest-monitor-alert.t-alert--warning .t-alert__description',
      ),
    ).toMatch(/color:\s*var\(--color-warning\)/);
  });

  it("stacks observability panels and alert actions on 375px layouts", () => {
    expect(css).toMatch(
      /@media\s*\(max-width:\s*700px\)[\s\S]*\.ingest-monitor-grid\s*\{[^}]*grid-template-columns:\s*1fr/,
    );
    expect(css).toMatch(
      /@media\s*\(max-width:\s*700px\)[\s\S]*\.monitor-stale-alert\s+\.t-alert__operation/,
    );
  });
});
