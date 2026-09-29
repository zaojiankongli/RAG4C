# Frontend Visualize navigation adapter

## Outcome

Visualize URL synchronization now uses the shared navigation Adapter:

- `D:\program_project\python_project\RAG4C\frontend\src\pages\VisualizePage.tsx`
- `D:\program_project\python_project\RAG4C\frontend\src\pages\VisualizePage.test.tsx`
- `D:\program_project\python_project\RAG4C\frontend\src\run\visualizeNavigationAdapter.source.test.ts`

The page still builds canonical URLs with `runViewUrl`, but commits them as a
silent history replace through `commitNavigationIntent`.

## Preserved behavior

- Run, node, tab, view, and follow query serialization is unchanged.
- The pathname guard still prevents a kept-alive hidden Visualize page from
  overwriting another route.
- Local control changes do not emit synthetic `popstate`.
- Direct and hash routes retain their existing URL shapes.
- Existing `history.state` is preserved.
- No backend, OpenAPI, database, or public contract changed.

## Verification

- Focused regression: **4 files / 24 tests passed**.
- Source guard: passed; Visualize has no direct `replaceState` call.
- Direct route state-preservation, hash silent-replace, and hashchange
  synchronization tests passed.
- Focused ESLint: passed.
- TypeScript and production build: passed, **7,125 modules transformed**.
- `git diff --check`: passed.
- Independent review: **PASS**, no findings.

Design plan:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-25-frontend-visualize-navigation-adapter-design.md`.
