// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { useThemeMode } from "./useThemeMode";

function themeColor(): string | null {
  return document.querySelector('meta[name="theme-color"]')?.getAttribute("content") ?? null;
}

afterEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  document.documentElement.style.colorScheme = "";
  document.querySelector('meta[name="theme-color"]')?.remove();
});

/**
 * 这里守的是「在 3 值枚举上做 2 元判断」这一类缺陷：
 * `theme-color` 原先写成 `mode === "dark" ? "#12161f" : "#ffffff"`，
 * anime 静默落到 else 拿到纯白，而它的画布顶边是 #fff0f4 粉。
 * 逐主题给一张表，任何一列被改回兜底分支都会红在这里。
 *
 * 第三列 `colorScheme` 同样按主题钉住，但含义相反：
 * anime 的 `light` 是**有意**的（tokens.ts 注释：anime 是浅色底的自定义主题），
 * 记下来是为了让下一个读到 `mode === "dark" ? "dark" : "light"` 的人
 * 能立刻判断这是设计还是漏分支，而不是靠猜。
 */
const CHROME_BY_MODE = [
  ["light", "#ffffff", "light"],
  ["dark", "#12161f", "dark"],
  ["anime", "#fff0f4", "light"],
] as const;

describe("useThemeMode browser chrome sync", () => {
  it.each(CHROME_BY_MODE)(
    "gives the %s theme its own chrome instead of falling through to the default",
    (mode, expectedColor, expectedScheme) => {
      const { result } = renderHook(() => useThemeMode());

      act(() => result.current.setMode(mode));

      expect(themeColor()).toBe(expectedColor);
      expect(document.documentElement.dataset.theme).toBe(mode);
      expect(document.documentElement.style.colorScheme).toBe(expectedScheme);
    },
  );

  it("keeps the cycle light -> dark -> anime -> light chrome in sync at every step", () => {
    const { result } = renderHook(() => useThemeMode());
    act(() => result.current.setMode("light"));

    for (const [mode, expectedColor] of CHROME_BY_MODE.slice(1)) {
      act(() => result.current.toggle());
      expect(document.documentElement.dataset.theme).toBe(mode);
      expect(themeColor()).toBe(expectedColor);
    }
    act(() => result.current.toggle());
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(themeColor()).toBe("#ffffff");
  });
});
