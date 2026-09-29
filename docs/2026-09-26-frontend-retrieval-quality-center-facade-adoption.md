# Frontend RetrievalQualityCenter facade adoption

## Outcome

`RetrievalQualityCenter` now routes its host-level `Button` and `Tag`
controls through the shared UI facade:

- `D:\program_project\python_project\RAG4C\frontend\src\retrieval-quality\RetrievalQualityCenter.tsx`
- `D:\program_project\python_project\RAG4C\frontend\src\ui\index.tsx`

The migration preserves the host's behavior:

- authenticated, offline, pending, and missing-scope states remain truthful;
- the dataset badge keeps `light-outline`;
- mobile tabs keep their labels, ids, tab/tabpanel roles, selected state, and
  current-panel rendering;
- the selected mobile tab maps to facade `type="primary"` while unselected
  tabs use the facade default;
- switching away from History while a detail is open records the live tab as
  the focus fallback, so Drawer close does not target an unmounted history
  button.
- the mobile-tab click boundary now carries an explicit
  `MouseEvent<HTMLButtonElement>` type because the compatibility Button facade
  intentionally exposes a legacy untyped surface.

## Verification

- RetrievalQualityCenter/UI focused suite: **3 files / 6 tests passed**
- final full frontend suite: **337/337 files passed**
- final full-suite manifest:
  `90159677fd274a9a3d290cf22b0578f265d7ab33bc79c9b4dee459865efdc7ea`
- final TypeScript/build: `npx tsc --noEmit` passed; Vite build transformed
  **7,127 modules** and passed
- final full ESLint: **0 errors / 98 existing warnings**
- `git diff --check`: passed
- independent sub-agent follow-up review: **PASS**, no actionable P0–P3 findings

The build initially exposed an implicit-`any` event parameter after the host
Button migration. The handler now annotates the event boundary explicitly; the
full validation above was rerun after that fix.

## Scope boundary

No route, API, backend, storage, authorization, or persisted-state contract
changed. Retrieval-quality child surfaces and Drawer contracts remain
unchanged.

Design:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-26-frontend-retrieval-quality-center-facade-adoption-design.md`
