# Frontend enterprise resource navigation adapter

## Outcome

Extended the shared navigation Adapter/Strategy seam and adopted it in the
enterprise resource centers:

- `D:\program_project\python_project\RAG4C\frontend\src\run\navigationAdapter.ts`
- `D:\program_project\python_project\RAG4C\frontend\src\enterprise-workspace\components\EnterpriseWorkspaceCenter.tsx`
- `D:\program_project\python_project\RAG4C\frontend\src\enterprise-knowledge-base\components\EnterpriseKnowledgeBaseCenter.tsx`
- `D:\program_project\python_project\RAG4C\frontend\src\run\enterpriseResourceNavigationAdapter.source.test.ts`

The adapter now supports an explicit `historyAction`:

- `push` for opening Workspace/Knowledge Base detail routes;
- `replace` for closing detail routes without adding another history entry.

The existing history/hash strategy and synthetic event behavior remain
centralized in one module.

## Lifecycle fixes found during review

The first independent review found two real Workspace route issues:

1. A direct Workspace deep link called the user-facing “open detail” handler
   during initialization, which pushed the same URL a second time.
2. Workspace Center did not listen to `popstate`/`hashchange`, so browser Back
   could change the URL without closing or switching the mounted detail
   drawer.

The final implementation separates route observation from user-initiated
route commits. Initial and browser-driven route consumption opens the detail
without writing a new URL; user clicks still push, close actions still
replace in history mode, and route events now close/switch the detail safely.

## Preserved behavior

- Workspace and Knowledge Base URL shapes, query parameters, and direct/hash
  detection are unchanged.
- History mode still emits one synthetic `popstate` after push/replace.
- Hash mode still relies on the browser hash route and does not invent a
  synthetic `popstate`.
- Resource loading, authorization, mutations, dialog state, and UI copy remain
  unchanged.
- No backend, OpenAPI, database, or public contract changed.

## Verification

- Focused regression: **4 files / 29 tests passed**.
- Source guard: passed; both resource centers contain no direct browser URL
  side effects.
- Direct deep-link initialization test confirms no duplicate `pushState`.
- Browser `popstate` back-navigation test confirms the Workspace detail closes
  without writing a new route.
- Focused ESLint: passed.
- TypeScript and production build: passed, **7,125 modules transformed**.
- `git diff --check`: passed.
- Initial independent review findings were fixed; final independent review:
  **PASS**, no findings.

Design plan:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-25-frontend-enterprise-resource-navigation-adapter-design.md`.
