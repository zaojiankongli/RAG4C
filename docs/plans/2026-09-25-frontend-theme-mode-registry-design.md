# Frontend theme mode Registry + browser chrome Adapter design (2026-09-25)

## Context

Theme selection currently has two independent behavior tables:

- `frontend/src/theme/tokens.ts` contains the `colorScheme` rule;
- `frontend/src/theme/useThemeMode.ts` contains browser `theme-color` values and
  the light → dark → anime cycle.

Adding a theme therefore requires finding multiple branches. This is the same
extension risk already being removed from backend provider and engine
selection: a new implementation can be declared but one consumer silently
falls back to an old default.

## Decision

- Add `frontend/src/theme/themeModeRegistry.ts` as the single pure registry for
  theme mode metadata:
  - native `colorScheme`;
  - browser chrome color;
  - next-mode transition.
- Expose typed lookup/guard helpers:
  - `themeModeNames`;
  - `isThemeMode`;
  - `parseStoredThemeMode`;
  - `getThemeModeSpec`;
  - `nextThemeMode`.
- Make `tokens.ts`'s DOM adapter consume the registry instead of branching on
  `"dark"`.
- Make `useThemeMode.ts` consume the same registry instead of maintaining a
  second browser-color table and nested ternary cycle.
- Keep the public `ThemeMode` union, local-storage key, DOM `data-theme`
  attribute, and existing light/dark/anime visual CSS unchanged.

This is a frontend Strategy/Registry + DOM Adapter seam, not a runtime plugin
loader. The CSS still owns visual palette values; the registry owns only
cross-cutting mode metadata.

## Failure contract

- Unknown local-storage values parse to `undefined`; the existing system-dark
  fallback remains.
- Unknown runtime values are rejected by the typed registry lookup rather than
  silently treated as dark or light.
- Every declared `ThemeMode` must have a registry entry. TypeScript's
  `Record<ThemeMode, ThemeModeSpec>` and registry parity tests enforce this.

## Compatibility boundary

- No CSS selector, palette, route, API, persisted key, or DOM vocabulary
  changes.
- Anime remains a light `colorScheme` with `#fff0f4` browser chrome.
- Toggle order remains light → dark → anime → light.

## Verification plan

- Registry unit tests cover completeness, parsing, browser metadata, and
  cycle order.
- Existing `useThemeMode`, token, frontend build, lint, and full Vitest
  regressions remain green.
- Add a source guard proving the old duplicate browser-color table and direct
  dark-only branch are gone.
- Request an independent sub-agent review and write a handoff document.
