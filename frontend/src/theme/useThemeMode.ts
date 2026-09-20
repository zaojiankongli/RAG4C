import { useCallback, useState } from "react";
import { applyThemeMode, type ThemeMode } from "./tokens";

const STORAGE_KEY = "rag4c.theme_mode";

/**
 * `theme-color` 决定移动端浏览器外壳（状态栏/地址栏）的着色。
 * 原先写的是 `mode === "dark" ? "#12161f" : "#ffffff"`——在**三值**枚举上做**二元**判断，
 * anime 会静默落到 else 拿到纯白，而它的画布顶部其实是 #fff0f4 粉。
 * 改成显式映射：再加主题时 TS 会强制补一行，而不是继续掉进兜底分支。
 * 取每个主题画布**顶边**的颜色，因为那正是状态栏压住的位置。
 */
const BROWSER_THEME_COLOR: Record<ThemeMode, string> = {
  light: "#ffffff",
  dark: "#12161f",
  anime: "#fff0f4",
};

function systemPrefersDark(): boolean {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

function syncBrowserTheme(mode: ThemeMode): void {
  applyThemeMode(mode);
  let meta = document.querySelector('meta[name="theme-color"]');
  if (!meta) {
    meta = document.createElement("meta");
    meta.setAttribute("name", "theme-color");
    document.head.appendChild(meta);
  }
  meta.setAttribute("content", BROWSER_THEME_COLOR[mode]);
}

function loadInitial(): ThemeMode {
  const stored = localStorage.getItem(STORAGE_KEY);
  const mode: ThemeMode =
    stored === "light" || stored === "dark" || stored === "anime"
      ? stored
      : systemPrefersDark()
        ? "dark"
        : "light";
  syncBrowserTheme(mode);
  return mode;
}

export function useThemeMode() {
  const [mode, setModeState] = useState<ThemeMode>(loadInitial);

  const setMode = useCallback((next: ThemeMode) => {
    syncBrowserTheme(next);
    setModeState(next);
    localStorage.setItem(STORAGE_KEY, next);
  }, []);

  // 三态循环：light -> dark -> anime -> light
  const toggle = useCallback(() => {
    setModeState((previous) => {
      const next: ThemeMode =
        previous === "light" ? "dark" : previous === "dark" ? "anime" : "light";
      syncBrowserTheme(next);
      localStorage.setItem(STORAGE_KEY, next);
      return next;
    });
  }, []);

  return { mode, setMode, toggle };
}
