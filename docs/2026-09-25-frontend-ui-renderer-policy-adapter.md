# Frontend UI renderer policy + Adapter

## Outcome

The TDesign compatibility facade now resolves its native/TDesign branch
through one typed policy registry and Adapter:

- `D:\program_project\python_project\RAG4C\frontend\src\ui\rendererPolicy.ts`
- `D:\program_project\python_project\RAG4C\frontend\src\ui\index.tsx`

The registry declares 27 conditional facade components. Every current entry
selects `native`, so the production rendering path is unchanged. A future
renderer rollout can opt in a specific component by changing one typed
registry entry instead of editing unrelated component branches.

## Design

`rendererPolicy.ts` provides:

- `uiRendererComponentNames` as the closed component vocabulary;
- `UiRenderer = "native" | "tdesign"`;
- `UiRendererRegistry` with mapped-type key/spec parity;
- `createUiRendererAdapter`;
- the shared `uiRendererAdapter`.

The Adapter is fail-closed:

- unknown, `toString`, and `__proto__` keys resolve to `native`;
- only the 27 declared component keys are copied into an immutable snapshot;
- the exported 27-key component vocabulary is frozen against runtime edits;
- the exported registry and all default entries are frozen;
- source registry mutation cannot change an already-created Adapter;
- `null`, throwing getters, malformed entries, and throwing `Proxy` entries
  resolve to `native` rather than breaking application mount.

`index.tsx` now passes an explicit component key at every former
`canRenderTDesign()` boundary. The source contract checks both complete key
coverage and declaration-to-key pairing, so a swapped renderer key cannot pass
only by having the same final key set.

Always-TDesign aliases (`ConfigProvider`, `Statistic`, `Layout.Content`,
`Menu`, `Skeleton`, and `Collapse`) remain outside this slice because they do
not yet have native counterparts in the facade.

## Review

An independent sub-agent review was run against the working tree. It first
found and the implementation then fixed:

1. shallow registry freezing;
2. custom-registry unknown-key admission;
3. malformed registry and entry exception handling;
4. insufficient source coverage for component/key swaps.

The final follow-up review returned **PASS** with no actionable P0/P1/P2/P3
findings. A final sanity review also passed after freezing the exported
component-name vocabulary.

## Verification

- UI-focused compatibility suite: **11 files / 51 tests passed**
- Full frontend suite: **306/306 files passed**
- Full-suite manifest:
  `ac46011c489d6025a3a7acf3e66e0ff61d342c63a2cd1634f1f3f27a41ac4712`
- `npm run build`: passed, **7,127 modules transformed**
- focused ESLint: passed
- full ESLint: **0 errors / 98 warnings** (existing repository warning baseline)
- `git diff --check`: passed

## Scope boundary

This slice changes renderer selection orchestration only. It does not:

- enable any TDesign conditional branch;
- change CSS, DOM vocabulary, event signatures, routes, API contracts, or
  persisted state;
- add runtime plugin discovery or a second mutable component registry;
- implement a native replacement for the always-TDesign aliases listed above.

Design:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-25-frontend-ui-renderer-policy-adapter-design.md`
