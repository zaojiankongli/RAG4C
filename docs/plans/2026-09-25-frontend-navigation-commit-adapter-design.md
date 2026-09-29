# Frontend navigation commit adapter design (2026-09-25)

## Context

`App.tsx` already centralizes route parsing and builds a `NavigationIntent`,
but several handoff handlers still repeat the deployment-mode branch:
`history.pushState(...)` plus `popstate`, or `location.hash = ...`.
Every new handoff is another copy of that side-effect protocol.

## Design

1. Add `frontend/src/run/navigationAdapter.ts` as the side-effect boundary for
   committing a `NavigationIntent`.
2. Keep two explicit strategies, `history` and `hash`, behind one adapter
   function. Both preserve the existing URL and `popstate` behavior.
3. Replace duplicated commit branches in `App.tsx` with the adapter; route
   parsing, dirty-draft confirmation, mobile focus, and handoff validation stay
   in their current owners.
4. Add source and behavior tests proving both strategies are exercised and App
   no longer owns duplicate URL-write branches.

## Compatibility

- No route keys, URL formats, deployment-mode detection, or browser events
  change.
- No backend/OpenAPI/frontend public contract changes.
- This is an Adapter/Strategy refactor only; the current page state and
  keep-alive behavior remain in `App`.

## Verification

- Navigation adapter unit tests.
- Existing App route/mobile/notification/stage regressions.
- Source guard test ensures `App.tsx` delegates browser URL writes to the
  adapter instead of reintroducing direct `pushState`/hash branches.
- TypeScript, ESLint, and production build.
- Independent sub-agent review before handoff.

## Completed verification (2026-09-25)

- Focused regression: **9 files / 34 tests passed**.
- Focused ESLint: passed.
- TypeScript and production build: passed, **7,125 modules transformed**.
- `git diff --check`: passed.
- Independent sub-agent review: **PASS**, no findings.
