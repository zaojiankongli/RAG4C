# App route composition separation handoff (2026-09-24)

## Change

- Added `frontend/src/AppRoutes.tsx`, which owns lazy page imports and the
  exhaustive `Record<PageKey, JSX.Element>` composition.
- `frontend/src/App.tsx` still owns URL/navigation state, mounted-page tracking,
  tenant identity, capability readiness, dirty-draft guards, and safe handoff
  callbacks. It supplies these through a grouped `AppRouteContext`.
- `AppRoutes` continues to render through `MountedPageHost`; ErrorBoundary,
  Suspense, loading state, page order, keep-alive behavior, and existing page
  props are unchanged.
- The source-guard tests for consistency and parse-intervention now inspect
  `AppRoutes.tsx` for page composition and `App.tsx` for dirty-navigation
  ownership. Their guard coverage remains, but follows the new ownership
  boundary.
- No visual CSS, URL format, page key, identity/authentication, or API behavior
  changed. Notification Drawer lazy imports remain in `App.tsx`; route page
  lazy imports remain lazy in `AppRoutes.tsx`.

## Verification

- Focused App/a11y/route/notification/stage18/stage19 regression:
  **8 files / 39 tests passed**.
- `npm exec -- eslint src/App.tsx src/AppRoutes.tsx`: passed.
- `npm run build`: passed (`tsc --noEmit`, Vite transformed 7120 modules);
  page chunks remain split.
- The first focused run had three source-guard failures because those tests
  still expected route declarations in `App.tsx`; after moving the guards to
  `AppRoutes.tsx` and retaining the dirty-navigation assertion in `App.tsx`,
  all focused tests passed.
- Full `npm test`: **291/291 test files passed**, manifest
  `78f61fd7591b7da3fb3fe5eeec06aac4e9c406aabef41b9ed06be90ed1f54eab`.
- Independent subagent code review: **PASS**, no actionable findings. It
  confirmed all 20 `PageKey` nodes/props and lazy boundaries remain, and
  identity/capability/dirty-navigation ownership stays in `App`.

## Follow-up boundary

The next C4b slice can consider extracting `AppLayout`, but should keep
navigation/identity/capability state in `App` unless there is a separate
behavioral reason to move ownership. Do not replace the router or alter
`PageKey`/history/hash semantics as part of that extraction.
