// @vitest-environment node
// @ts-expect-error -- test-only Node builtin without adding @types/node.

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("./enterprise-approval.css", import.meta.url), "utf8");

describe("Stage 13 enterprise approval responsive CSS", () => {
  it("uses TDesign tokens, table-first density and visible keyboard focus", () => {
    // 这条守卫的本意是"品牌色必须走 token，不许硬编码"。R7 把模块私有 --*-primary 从
    // 直接抓 var(--td-brand-color) 拆成实心阶 --color-accent-solid 与文字阶
    // --color-primary-strong —— 因为同一个变量同时当填充和文字用，必然有一头不达 AA
    // （深色主题白字压 #bfdbfe 只有 1.42:1，被 frontend-visual 门禁抓出）。
    // 因此这里接受任一条 token 路径，并额外禁止裸品牌 hex：比原来更严。
    expect(css).toMatch(
      /var\(--td-brand-color|var\(--color-accent-solid|var\(--color-primary-strong/,
    );
    expect(css).not.toMatch(/#0052d9|#2f6fed|#3164f4/);
    expect(css).toContain("var(--td-bg-color-container");
    expect(css).toContain(".enterprise-approval-table-viewport");
    expect(css).toMatch(/\.enterprise-approval-table-viewport[^{]*\{[^}]*overflow-x:\s*auto/s);
    expect(css).toMatch(/:focus-visible/);
    expect(css).toMatch(
      /\.enterprise-approval-center\s*>\s*\.enterprise-metric-strip[\s\S]*\.enterprise-metric-strip__value[^{]*\{[^}]*overflow-wrap:\s*anywhere/s,
    );
    expect(css).not.toContain("linear-gradient");
  });

  it("switches desktop tables to priority cards at 375px and 280px", () => {
    expect(css).toContain(".approval-request-desktop");
    expect(css).toContain(".approval-request-mobile");
    expect(css).toContain(".approval-request-card");
    expect(css).toMatch(
      /@media\s*\(max-width:\s*600px\)[\s\S]*\.approval-request-desktop[^{]*\{[^}]*display:\s*none/s,
    );
    expect(css).toMatch(
      /@media\s*\(max-width:\s*600px\)[\s\S]*\.approval-request-mobile[^{]*\{[^}]*display:\s*grid/s,
    );
    expect(css).toMatch(/@media\s*\(max-width:\s*375px\)/);
    expect(css).toMatch(/@media\s*\(max-width:\s*280px\)/);
  });

  it("keeps the detail drawer, timeline and rejection controls legible on narrow screens", () => {
    expect(css).toContain(".approval-detail-drawer");
    expect(css).toContain(".approval-process-timeline");
    expect(css).toContain(".approval-reject-counter");
    expect(css).toContain("prefers-reduced-motion");
  });
});
