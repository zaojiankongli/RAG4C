# Frontend page navigation adapter adoption

## Outcome

Migrated page-level route commits to the shared typed adapter:

- `D:\program_project\python_project\RAG4C\frontend\src\pages\ChunkWorkbenchPage.tsx`
- `D:\program_project\python_project\RAG4C\frontend\src\pages\KnowledgeOverviewPage.tsx`
- `D:\program_project\python_project\RAG4C\frontend\src\pages\KnowledgeTaxonomyPage.tsx`

The three modules still construct route intents through
`frontend/src/run/appRoute.ts`, but no longer duplicate browser
`pushState`/hash/popstate side effects. All commits now go through
`commitNavigationIntent`.

## Compatibility decision

The first review caught a real event-contract difference: Overview and
Taxonomy historically dispatched a synthetic `popstate` after both history and
hash commits, while the App-level adapter intentionally does not invent a
`popstate` for hash commits.

The adapter now accepts the explicit
`dispatchPopStateAfterHash` option. Overview and Taxonomy enable it; App and
Chunk Workbench keep the default disabled behavior. This preserves each
caller’s existing event semantics without making every hash navigation emit a
duplicate event.

## Preserved behavior

- Route URL construction, page keys, hash/history detection, and navigation
  targets are unchanged.
- Chunk Workbench dirty-draft confirmation and cleanup still happen before
  committing the route.
- Existing history-mode `pushState` plus synthetic `popstate` behavior remains.
- Overview and Taxonomy hash-mode compatibility is covered by integration
  tests.
- Replace-state flows, approval-specific navigation, `AuthRecoveryHint`, and
  backend/OpenAPI/database contracts were intentionally not changed.

## Verification

- Focused regression: **6 files / 57 tests passed**.
- Source guard: passed; the three pages contain no direct URL-write or
  synthetic-popstate commit branch.
- Focused ESLint: passed.
- TypeScript and production build: passed, **7,125 modules transformed**.
- `git diff --check`: passed.
- Independent sub-agent initial review findings were fixed; final re-review:
  **PASS**, no findings.

Design plan:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-25-frontend-page-navigation-adapter-adoption-design.md`.
