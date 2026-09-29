# Frontend auth recovery navigation adapter

## Outcome

`AuthRecoveryHint` now routes “打开系统设置” through the shared navigation
adapter:

- `D:\program_project\python_project\RAG4C\frontend\src\components\AuthRecoveryHint.tsx`
- `D:\program_project\python_project\RAG4C\frontend\src\components\AuthRecoveryHint.test.tsx`
- `D:\program_project\python_project\RAG4C\frontend\src\components\AuthRecoveryHint.navigation.source.test.ts`

The component builds the existing `config` `PageKey` intent using the current
location, then delegates the browser side effect to
`commitNavigationIntent`.

## Behavior

- Direct-route deployment: the action commits `/config` and emits the
  adapter's normal synthetic `popstate`.
- Hash deployment: the action commits `#/config` without inventing a
  synthetic `popstate`.
- The existing title, description, retry callback, and auth-error detection
  behavior are unchanged.
- This also fixes the old direct-route case where writing only `#/config`
  could be ignored because the app gives a recognized pathname precedence.

## Verification

- Focused regression: **4 files / 12 tests passed**.
- Source guard: passed; the component has no direct hash write.
- Focused ESLint: passed with the existing Fast Refresh warning and **0
  errors**.
- TypeScript and production build: passed, **7,125 modules transformed**.
- `git diff --check`: passed.
- Initial review finding was fixed; final independent sub-agent review:
  **PASS**, no findings.

Design plan:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-25-frontend-auth-recovery-navigation-adapter-design.md`.
