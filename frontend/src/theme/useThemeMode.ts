import { useCallback, useState } from "react";
import {
  getThemeModeSpec,
  nextThemeMode,
  parseStoredThemeMode,
  type ThemeMode,
} from "./themeModeRegistry";
import { applyThemeMode } from "./tokens";

const STORAGE_KEY = "rag4c.theme_mode";

function readStoredThemeMode(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function persistThemeMode(mode: ThemeMode): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, mode);
  } catch {
    // Private browsing, disabled storage, and quota failures degrade to
    // in-memory theme state; they must not prevent the app from mounting.
  }
}

function systemPrefersDark(): boolean {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

function syncBrowserTheme(mode: ThemeMode): void {
  applyThemeMode(mode);
  const spec = getThemeModeSpec(mode);
  let meta = document.querySelector('meta[name="theme-color"]');
  if (!meta) {
    meta = document.createElement("meta");
    meta.setAttribute("name", "theme-color");
    document.head.appendChild(meta);
  }
  meta.setAttribute("content", spec.browserChromeColor);
}

function loadInitial(): ThemeMode {
  const stored = parseStoredThemeMode(readStoredThemeMode());
  const mode: ThemeMode = stored ?? (systemPrefersDark() ? "dark" : "light");
  syncBrowserTheme(mode);
  return mode;
}

export function useThemeMode() {
  const [mode, setModeState] = useState<ThemeMode>(loadInitial);

  const setMode = useCallback((next: ThemeMode) => {
    syncBrowserTheme(next);
    setModeState(next);
    persistThemeMode(next);
  }, []);

  const toggle = useCallback(() => {
    setModeState((previous) => {
      const next = nextThemeMode(previous);
      syncBrowserTheme(next);
      persistThemeMode(next);
      return next;
    });
  }, []);

  return { mode, setMode, toggle };
}
