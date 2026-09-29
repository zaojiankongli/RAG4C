# App route composition separation design (2026-09-24)

## Context

`frontend/src/App.tsx` now delegates keep-alive rendering, per-page error
isolation, and Suspense fallback to `MountedPageHost`, but still owns all lazy
page imports and the typed `PageKey → JSX.Element` composition map. The prior
C4b handoff explicitly identifies route/page-map extraction as the next
low-risk boundary.

The page composition is coupled to App-owned tenant identity, capability
readiness, dirty-workspace callbacks, and notification/task handoffs. Moving
those states or URL navigation into a new router/layout would widen the change
and risk enterprise authorization behavior.

## Decision

- Add `AppRoutes` as the composition boundary for lazy page imports and
  `Record<PageKey, JSX.Element>`.
- Keep navigation parsing, mounted-page tracking, tenant identity, capability
  state, dirty-draft guards, and handoff callbacks owned by `App`.
- Pass a grouped, typed route context from `App` to `AppRoutes`; `AppRoutes`
  continues to render through `MountedPageHost`.
- Preserve the same page keys, JSX props, capability fallbacks, order,
  keep-alive behavior, ErrorBoundary/Suspense placement, CSS, URLs, and direct
  route behavior.

## Alternatives

1. **Move only the low-level host:** already completed as `MountedPageHost`;
   repeating that extraction would not isolate the route composition.
2. **Extract the complete `AppLayout`:** deferred because shell callbacks and
   enterprise state are interdependent and have a larger prop/authorization
   surface.
3. **Adopt React Router:** rejected for this slice; it changes direct URL,
   hash/history, keep-alive, and dirty-navigation contracts.
4. **Move route state into a new context/store:** rejected; no demonstrated
   need and would change state ownership.

## Invariants and verification

- `PageKey` remains the route identity and `Record<PageKey, JSX.Element`
  remains total at compile time.
- Existing `App.initialRoute`, parse-workbench, consistency, Stage18/19,
  notification, capability, dirty-navigation, and accessibility behavior must
  remain unchanged.
- No visual/style adjustment is part of this route-composition slice.
- Run the focused route/App tests, TypeScript + Vite production build, ESLint,
  and the full frontend test suite if runtime permits.
