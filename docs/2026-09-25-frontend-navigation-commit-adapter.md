# Frontend navigation commit adapter

## Outcome

Added a single frontend Adapter/Strategy seam for committing already-parsed
`NavigationIntent` values:

- `D:\program_project\python_project\RAG4C\frontend\src\run\navigationAdapter.ts`
- `D:\program_project\python_project\RAG4C\frontend\src\run\navigationAdapter.test.ts`
- `D:\program_project\python_project\RAG4C\frontend\src\run\App.navigation.adapter.source.test.ts`

`App.tsx` now delegates every app-owned route commit to
`commitNavigationIntent`. The adapter owns the two deployment strategies:

- `history`: `pushState` followed by the existing synthetic `popstate`;
- `hash`: the existing `location.hash` assignment, with no invented
  `popstate`.

The strategy table is intentionally small and typed. New deployment-specific
commit behavior can be added behind the adapter without copying browser
side-effect branches into every handoff handler.

## Preserved behavior

- Route parsing, URL formats, deployment-mode detection, and page keys are
  unchanged.
- Dirty Documents/解析干预 drafts are still confirmed before leaving and are
  cleared only after confirmation.
- Mobile navigation close/focus behavior remains in `App`; the adapter does
  not own UI state or focus.
- Global search, notification, recovery approval, task, automation, resource
  section, and knowledge-serving handoffs still use their existing validation
  and target URLs.
- No backend, OpenAPI, database, or public frontend contract changed.

## Verification

- Focused regression: **9 files / 34 tests passed**, including both adapter
  strategies and App route/mobile/notification/stage regressions.
- Source guard: passed; `App.tsx` contains no direct
  `window.history.pushState` or `window.location.hash =` commit branch.
- Focused ESLint: passed.
- TypeScript and production build: passed, **7,125 modules transformed**.
- `git diff --check`: passed.
- Independent sub-agent code review: **PASS**, no findings.

Design plan:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-25-frontend-navigation-commit-adapter-design.md`.
