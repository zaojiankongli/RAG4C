# Frontend approval navigation adapter

## Outcome

Approval route commits now use the shared navigation Adapter/Strategy seam:

- `D:\program_project\python_project\RAG4C\frontend\src\enterprise-approval\approvalRoute.ts`
- `D:\program_project\python_project\RAG4C\frontend\src\enterprise-admin\memberRoleApprovalNavigation.ts`
- `D:\program_project\python_project\RAG4C\frontend\src\enterprise-access\components\DatasetAclDisableDialog.tsx`
- `D:\program_project\python_project\RAG4C\frontend\src\enterprise-workspace\components\WorkspacePermissionsRolloutCenter.tsx`
- `D:\program_project\python_project\RAG4C\frontend\src\run\approvalNavigationAdapter.source.test.ts`

The shared adapter now also accepts an optional `historyState`:

- approval route/member/ACL push helpers pass the legacy explicit `{}` state;
- Workspace authorization approval omits it and preserves the existing
  `window.history.state`;
- approval request clearing uses replace history with the current state.

## Preserved behavior

- Direct and hash approval URL shapes remain unchanged.
- Hash-shaped approval links still use `pushState`/`replaceState`, not
  `location.hash`, matching the previous contract.
- Push and replace operations still emit one synthetic `popstate`.
- Request-id validation, ACL approval gating, Workspace authorization
  mutation behavior, and approval UI copy are unchanged.
- No backend, OpenAPI, database, or authorization policy contract changed.

## Verification

- Focused regression: **6 files / 33 tests passed**.
- Source guard: passed; approval modules contain no direct browser URL
  side-effect calls.
- State/popstate regressions cover explicit `{}` approval pushes, Workspace
  current-state preservation, and approval request replace navigation.
- Focused ESLint: passed.
- TypeScript and production build: passed, **7,125 modules transformed**.
- `git diff --check`: passed.
- Initial review finding was fixed; final independent review:
  **PASS**, no findings.

Design plan:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-25-frontend-approval-navigation-adapter-design.md`.
