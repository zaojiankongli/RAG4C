/**
 * Cross-cutting metadata for each supported theme mode.
 *
 * CSS owns the visual palette. This registry owns the behavior that must stay
 * in sync across DOM, browser chrome, persistence parsing, and mode cycling.
 */
export type ThemeMode = "light" | "dark" | "anime";
export type ThemeColorScheme = "light" | "dark";

export type ThemeModeSpec = {
  mode: ThemeMode;
  label: string;
  description: string;
  colorScheme: ThemeColorScheme;
  browserChromeColor: string;
  next: ThemeMode;
};

type ThemeModeRegistry = {
  [Mode in ThemeMode]: ThemeModeSpec & { mode: Mode };
};

export const THEME_MODE_SPECS = {
  light: {
    mode: "light",
    label: "亮色",
    description: "清晰明亮，适合日间工作",
    colorScheme: "light",
    browserChromeColor: "#ffffff",
    next: "dark",
  },
  dark: {
    mode: "dark",
    label: "暗色",
    description: "低亮度界面，适合夜间专注",
    colorScheme: "dark",
    browserChromeColor: "#12161f",
    next: "anime",
  },
  anime: {
    mode: "anime",
    label: "八奈见",
    description: "海蓝与缎带，独立的放课后皮肤",
    colorScheme: "light",
    browserChromeColor: "#f2f7fc",
    next: "light",
  },
} as const satisfies ThemeModeRegistry;

export const themeModeNames = Object.freeze(
  Object.keys(THEME_MODE_SPECS) as ThemeMode[],
) as readonly ThemeMode[];

export function isThemeMode(value: unknown): value is ThemeMode {
  return typeof value === "string" && Object.prototype.hasOwnProperty.call(THEME_MODE_SPECS, value);
}

export function parseStoredThemeMode(value: string | null): ThemeMode | undefined {
  return isThemeMode(value) ? value : undefined;
}

export function getThemeModeSpec(mode: ThemeMode): ThemeModeSpec {
  return THEME_MODE_SPECS[mode];
}

export function nextThemeMode(mode: ThemeMode): ThemeMode {
  return getThemeModeSpec(mode).next;
}
