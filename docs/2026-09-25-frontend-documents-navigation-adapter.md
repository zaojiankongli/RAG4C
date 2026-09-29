# Frontend Documents navigation adapter

## Outcome

Moved Documents page route commits behind the shared navigation Adapter:

- `D:\program_project\python_project\RAG4C\frontend\src\pages\DocumentsPage.tsx`
- `D:\program_project\python_project\RAG4C\frontend\src\run\documentsNavigationAdapter.source.test.ts`
- `D:\program_project\python_project\RAG4C\frontend\src\run\navigationAdapter.ts`

The Adapter now supports explicit `dispatchPopStateAfterHistory` in addition
to existing history action, history state, and hash compatibility options.

## Preserved behavior

- Document filter writes remain silent `replaceState` updates.
- Parse workspace pushes retain the `rag4cParseWorkspace` history marker and
  remain silent so the page updates local parse state directly.
- Dirty browser navigation cancellation still restores the marked parse route.
- Marked parse entries still close with `window.history.back()`.
- Direct and hash operator handoffs still emit exactly one synthetic
  `popstate`; hash handoffs retain the existing hash route.
- No document API, dirty-draft, dataset scope, parse authority, or public
  contract changed.

## Verification

- Focused regression: **3 files / 48 tests passed**.
- Source guard: passed; `DocumentsPage.tsx` has no direct URL-write or
  synthetic-popstate commit branches.
- Focused ESLint: passed.
- TypeScript and production build: passed, **7,125 modules transformed**.
- `git diff --check`: passed.
- Independent review finding on operator-handoff runtime coverage was fixed;
  final review: **PASS**, no findings.

Design plan:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-25-frontend-documents-navigation-adapter-design.md`.
