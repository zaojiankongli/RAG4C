// Vitest runs this contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const layoutSource = readFileSync(new URL("../AppLayout.tsx", import.meta.url), "utf8");
const navSource = readFileSync(new URL("./appNav.tsx", import.meta.url), "utf8");

/**
 * 可达性守卫（`appRoute.reachability.test.ts`）比的是"页面清单 vs 导航清单"，
 * 而这两份清单曾经是各写各的 —— 通知中心就是这么丢的。这一组钉住的是"只有一份"。
 */
describe("主导航清单只有一份", () => {
  it("AppLayout 读 run/appNav 的那张表，不自己再抄一张", () => {
    expect(layoutSource).toContain('import { MENU_ITEMS } from "./run/appNav"');
    expect(layoutSource).not.toMatch(/const MENU_ITEMS/);
    // 落点是 Menu 那一行：表读进来了还得真的交给侧栏，否则等于没接。
    expect(layoutSource).toMatch(/items=\{MENU_ITEMS\}/);
  });

  it("NAV_PAGE_KEYS 是从表里派生的，不是又一份手抄的键名", () => {
    expect(navSource).toMatch(/NAV_PAGE_KEYS[\s\S]{0,200}MENU_ITEMS/);
    expect(navSource).not.toMatch(/NAV_PAGE_KEYS[^=]*=\s*\[/);
  });
});
