# Frontend page navigation adapter adoption implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove page-level copies of the history/hash route-commit protocol and route them through the existing typed `navigationAdapter`.

**Architecture:** Keep route intent construction in `frontend/src/run/appRoute.ts` and browser side effects in `frontend/src/run/navigationAdapter.ts`. Migrate only page handlers that already produce a `NavigationIntent` and currently duplicate `pushState`/hash assignment; do not change replace-state flows or approval-specific URL contracts in this slice. The adapter exposes an explicit compatibility option for legacy page handlers that also emitted synthetic `popstate` after hash commits.

**Tech Stack:** React 18, TypeScript, Vitest, Testing Library, Vite.

---

## Scope

Migrate these page modules:

- `frontend/src/pages/ChunkWorkbenchPage.tsx`
- `frontend/src/pages/KnowledgeOverviewPage.tsx`
- `frontend/src/pages/KnowledgeTaxonomyPage.tsx`

Keep route parsing, dirty-draft confirmation, page state, and existing URL
construction unchanged. `AuthRecoveryHint`, replace-state flows, and
enterprise approval route helpers remain follow-up slices because they do not
share the same `NavigationIntent` contract or event semantics.

## Tasks

### Task 1: Add source guard coverage

**Files:**

- Create: `frontend/src/run/pageNavigationAdapter.source.test.ts`

Assert that the three migrated page modules import and call
`commitNavigationIntent`, and no longer own direct `window.history.pushState`,
`window.location.hash =`, or synthetic `popstate` route-commit branches.

### Task 2: Migrate Chunk Workbench

**Files:**

- Modify: `frontend/src/pages/ChunkWorkbenchPage.tsx`

Remove the local `navigateTo(url, mode)` implementation. Pass each existing
`NavigationIntent` directly to `commitNavigationIntent` for:

- the launcher’s “前往文档管理” action;
- `goTo` page transitions;
- document selection deep links.

Do not change dirty-draft checks or `chunkWorkbenchNavigationIntent`.

### Task 3: Migrate overview and taxonomy pages

**Files:**

- Modify: `frontend/src/pages/KnowledgeOverviewPage.tsx`
- Modify: `frontend/src/pages/KnowledgeTaxonomyPage.tsx`

Replace the local push/hash branches with `commitNavigationIntent(intent)`.
Overview and Taxonomy pass `dispatchPopStateAfterHash: true` because their
previous implementations emitted synthetic `popstate` in both deployment
modes.
Keep all existing button handlers and `navigationIntent` target selection
unchanged.

### Task 4: Run focused regression

Run:

```powershell
npm exec -- vitest run `
  src/run/navigationAdapter.test.ts `
  src/run/pageNavigationAdapter.source.test.ts `
  src/pages/KnowledgeOverviewPage.test.tsx `
  src/pages/KnowledgeTaxonomyPage.test.tsx `
  src/App.initialRoute.test.tsx `
  src/App.mobile.test.tsx
```

Expected: all selected files pass; history-mode handlers still emit one
synthetic `popstate`.

### Task 5: Verify and review

Run focused ESLint for the three page files and the source guard, then:

```powershell
npm run build
git diff --check
```

Start one independent sub-agent review of the migrated files and adapter
semantics. Fix any actionable finding, rerun the affected checks, and record
the final result in the handoff document.

### Task 6: Write handoff

**Files:**

- Create: `docs/2026-09-25-frontend-page-navigation-adapter-adoption.md`

Record changed files, preserved behavior, explicit non-goals, test/build/lint
results, and independent review status.

## Completed verification (2026-09-25)

- Focused regression: **6 files / 57 tests passed**.
- Adapter contract covers default hash behavior and the explicit legacy
  `dispatchPopStateAfterHash` compatibility option.
- Overview and Taxonomy hash-mode regressions verify the legacy synthetic
  `popstate` contract; the source guard pins that option to those pages.
- Focused ESLint: passed.
- TypeScript and production build: passed, **7,125 modules transformed**.
- `git diff --check`: passed.
- Initial independent review found one P1 event-compatibility regression and
  one P2 test-coverage gap; both were fixed.
- Final independent sub-agent re-review: **PASS**, no findings.
