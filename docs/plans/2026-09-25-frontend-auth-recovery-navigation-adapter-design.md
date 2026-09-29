# Frontend auth recovery navigation adapter design (2026-09-25)

## Context

`AuthRecoveryHint` owns a hard-coded `window.location.hash` assignment for
the “打开系统设置” action. In a history deployment, the app gives pathname
precedence when parsing routes, so changing only the hash can leave the
operator on the current page instead of opening Config.

## Design

1. Keep the target as the existing `config` `PageKey`.
2. Build a `NavigationIntent` through `navigationIntent(window.location,
   "config")`, so the current deployment mode decides between history and
   hash.
3. Commit the intent through `commitNavigationIntent`; the component does not
   own browser URL side effects.
4. Add history/hash behavior tests and a source guard for the removed direct
   hash write.

## Compatibility

- The visible copy, retry callback, auth-error detection, and component props
  remain unchanged.
- Hash deployments still land on `#/config`; history deployments now land on
  `/config` and emit the adapter's normal synthetic `popstate`.
- No backend, OpenAPI, database, or persisted credential contract changes.

## Verification

- Focused `AuthRecoveryHint` and navigation adapter tests.
- Focused ESLint, TypeScript/build, and `git diff --check`.
- Independent sub-agent review before handoff.

## Completed verification (2026-09-25)

- Focused regression: **4 files / 12 tests passed**.
- History mode verifies `/config` plus one adapter `popstate`.
- Hash mode verifies `#/config` and no synthetic `popstate`.
- Focused ESLint: passed with the existing Fast Refresh warning and **0
  errors**.
- TypeScript and production build: passed, **7,125 modules transformed**.
- `git diff --check`: passed.
- Initial review P2 (hash test did not guard duplicate `popstate`) was fixed;
  final independent re-review: **PASS**, no findings.
