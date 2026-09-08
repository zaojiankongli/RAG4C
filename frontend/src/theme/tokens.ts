/**
 * RAG4C design tokens shared by TDesign components, app CSS, and charts.
 */
export type ThemeMode = "light" | "dark";

export const BRAND = {
  primary: "#3164F4",
  primaryHover: "#2755D8",
  primaryActive: "#1F46B8",
  secondary: "#6A5AE0",
  gradient: "#3164F4",
  gradientSubtle: "#EEF3FF",
  success: "#166534",
  warning: "#6B2D0E",
  danger: "#B42318",
  info: "#3164F4",
  purple: "#7656C9",
  cyan: "#07879A",
  geekblue: "#3164F4",
  accent: "#3164F4",
} as const;

export const STATUS_COLORS = {
  ok: "green",
  weak: "orange",
  stale: "purple",
  unsupported: "red",
} as const;

export const FONT_SIZE = {
  micro: 9, xxs: 10, xs: 11, sm: 12, md: 13, base: 14, lg: 15,
  xl: 16, xxl: 18, xxxl: 20, huge: 22, display: 28,
} as const;

export const FONT_FAMILY =
  '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif';
export const MONO_FAMILY =
  '"SF Mono", "Fira Code", "JetBrains Mono", Consolas, "Liberation Mono", Menlo, monospace';

export const SHADOWS = {
  sm: "0 1px 2px rgba(16, 24, 40, 0.04)",
  md: "0 1px 2px rgba(16, 24, 40, 0.05), 0 8px 24px -8px rgba(16, 24, 40, 0.12)",
  lg: "0 2px 4px rgba(16, 24, 40, 0.05), 0 16px 36px -12px rgba(16, 24, 40, 0.16)",
  xl: "0 4px 8px rgba(16, 24, 40, 0.06), 0 24px 56px -16px rgba(16, 24, 40, 0.2)",
  glow: "0 0 0 3px rgba(49, 100, 244, 0.12)",
  glowStrong: "0 0 0 3px rgba(49, 100, 244, 0.20)",
} as const;

export const TRANSITIONS = {
  fast: "0.15s ease",
  normal: "0.25s ease",
  slow: "0.35s ease",
  spring: "0.4s cubic-bezier(0.34, 1.56, 0.64, 1)",
} as const;

/** Synchronize the app theme contract with TDesign's DOM theme contract. */
export function applyThemeMode(mode: ThemeMode): void {
  const root = document.documentElement;
  root.dataset.theme = mode;
  root.style.colorScheme = mode;
}

