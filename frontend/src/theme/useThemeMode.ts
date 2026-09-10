import { useCallback, useState } from "react";
import { applyThemeMode, type ThemeMode } from "./tokens";

const STORAGE_KEY = "rag4c.theme_mode";

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
  meta.setAttribute("content", mode === "dark" ? "#12161f" : "#ffffff");
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
