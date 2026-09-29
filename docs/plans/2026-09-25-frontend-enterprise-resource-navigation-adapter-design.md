# Frontend enterprise resource navigation adapter design (2026-09-25)

## Context

The Workspace Center and Knowledge Base Center each contain a local route
writer that duplicates the browser-side history/hash protocol. Both also need
the distinction between opening a detail route with `pushState` and closing a
detail route with `replaceState`.

The shared `frontend/src/run/navigationAdapter.ts` already owns the
history/hash strategy. Extending its typed options with an explicit history
action lets these resource centers reuse the seam without moving route
construction or resource state into `App`.

## Design

1. Add `historyAction: "push" | "replace"` to the adapter options, defaulting
   to `"push"` so existing callers are unchanged.
2. Keep history commits dispatching the existing synthetic `popstate`.
3. Keep hash commits using the existing hash assignment; the
   `dispatchPopStateAfterHash` compatibility option remains explicit.
4. Replace `syncWorkspaceRoute` and `writeKnowledgeBaseRoute` with adapter
   calls. Their existing route-intent builders, route mode detection, and
   `replace` call-site decisions remain unchanged.
5. Add source guards and route regressions for direct/hash paths and replace
   semantics.

## Compatibility

- Workspace and Knowledge Base URL formats remain unchanged.
- Opening detail routes still creates a history entry; closing detail routes
  still replaces the current entry and emits one `popstate` in history mode.
- Hash deployment still writes only the hash and does not invent a
  `popstate`.
- No backend, OpenAPI, database, authorization, or data-fetching behavior
  changes.

## Verification

- Adapter unit tests for push and replace history actions.
- Existing Workspace/Knowledge Base Center route regressions plus source guard.
- Focused ESLint, TypeScript/build, and `git diff --check`.
- Independent sub-agent review before handoff.

## Completed verification (2026-09-25)

- Focused regression: **4 files / 29 tests passed**.
- Adapter contract covers push history, replace history, default hash, and
  explicit legacy hash `popstate` behavior.
- Workspace and Knowledge Base direct/hash route regressions passed.
- Workspace browser back synchronization now has a regression test; direct
  deep-link initialization is proven not to add a duplicate `pushState`.
- Focused ESLint: passed.
- TypeScript and production build: passed, **7,125 modules transformed**.
- `git diff --check`: passed.
- Initial independent review found two P1 Workspace route lifecycle issues;
  both were fixed.
- Final independent sub-agent re-review: **PASS**, no findings.
