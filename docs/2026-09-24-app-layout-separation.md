# AppLayout shell separation handoff (2026-09-24)

## Change

- Added `frontend/src/AppLayout.tsx` for the responsive shell markup:
  `Layout`/`Sider`, skip link, primary nav, mobile backdrop/toggle,
  `WorkspaceScopeBar`, connection status, and main content slot.
- `App.tsx` remains the owner of route/navigation state, mobile focus callbacks,
  workspace identity/authorization, connection/theme state, and handoff logic.
  It passes those through typed props and grouped scope/connection objects.
- `AppRoutes` remains the child page-composition boundary. The notification
  drawers are passed as an `overlays` slot and rendered inside the same
  `Layout`, preserving their prior DOM parent and stacking context.
- No CSS/tokens, copy, breakpoints, routing, API, or business behavior changed.
  The skip link, focus, `inert`, `aria-hidden`, backdrop, keyboard loop,
  breakpoint handoff, and drawer return-focus contracts remain intact.

## Verification

- Focused App/route/accessibility/mobile/notification/stage18/19:
  **8 files / 39 tests passed**.
- Focused ESLint passed.
- `npm run build`: passed (`tsc --noEmit`, Vite transformed 7121 modules);
  route page chunking remains intact.
- First focused run found one real regression: passing the optional
  `closeMobileNavigation(focusTarget?)` callback directly to `onClick` forwarded
  the MouseEvent as the focus target. Wrapped the event handlers to call the
  callback with no argument; the 39-test focused suite then passed.
- The first full `npm test` run reached batch 97 but failed one stale
  `appNav.source.test.ts` assertion that still expected `App.tsx` to import
  `MENU_ITEMS`. The source guard was moved to `AppLayout.tsx`; its prior batch
  (20 files / 100 tests) passed. The full rerun passed:
  **291/291 test files**, manifest
  `78f61fd7591b7da3fb3fe5eeec06aac4e9c406aabef41b9ed06be90ed1f54eab`.
- Independent subagent review: **PASS**, no actionable findings. It confirmed
  the DOM/CSS classes, mobile focus/inert/backdrop behavior, drawer overlay
  nesting, and App-owned state/handler parameters are preserved.

## Ownership contract

`AppLayout` is a presentation shell, not a router/state container. Keep
`AppRouteContext` composition and authority state in `App`; continue to render
`AppRoutes` through `MountedPageHost` inside the shell.
