# Frontend theme mode Registry + browser chrome Adapter

## Outcome

Theme mode behavior is now declared once in:

- `D:\program_project\python_project\RAG4C\frontend\src\theme\themeModeRegistry.ts`

The registry owns each mode's native `colorScheme`, browser chrome color, and
next-mode transition. `tokens.ts` acts as the DOM Adapter and
`useThemeMode.ts` consumes the same registry for persistence parsing, browser
`theme-color`, and toggling.

This removes the previous split between a `mode === "dark"` branch in
`tokens.ts` and a second browser-color/cycle table in `useThemeMode.ts`.
Adding a mode now requires a typed registry entry plus its CSS palette, rather
than silently falling through to light/dark defaults.

## Contracts

- `ThemeModeRegistry` is key-parity typed: each registry key's `spec.mode`
  must equal that key.
- `isThemeMode` uses an own-property check, so `toString` and `__proto__`
  cannot be accepted as themes.
- Unknown local-storage values fall back to system preference.
- Storage read/write failures (private browsing, disabled storage, quota)
  degrade to in-memory state and never block app mounting.
- Existing behavior remains unchanged:
  - light → dark → anime → light;
  - anime uses `colorScheme: light`;
  - anime browser chrome remains `#fff0f4`;
  - `data-theme`, local-storage key, and CSS vocabulary are unchanged.

## Verification

- Theme-focused tests: **25 passed**
- Full frontend suite: **304/304 files passed**, manifest
  `f08941722b65bf50809baf811eb5e6efe1e5c1443ef87cb39993017bb2119d7e`
- `npm run build`: passed, 7,126 modules transformed
- Focused ESLint: passed
- Independent sub-agent follow-up review: **PASS**, no actionable P0/P1/P2/P3
  findings.

## Scope boundary

This slice changes theme metadata orchestration only. It does not rewrite the
large CSS palette, change visual colors, or add a runtime theme plugin loader.

Design:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-25-frontend-theme-mode-registry-design.md`
