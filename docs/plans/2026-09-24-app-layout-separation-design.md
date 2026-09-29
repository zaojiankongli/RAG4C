# AppLayout shell separation design (2026-09-24)

## Context

After the page map moved to `AppRoutes`, `App.tsx` still contains the rendered
application shell: responsive Sider/navigation, mobile backdrop/toggle,
workspace topbar, connection banner, and the main page slot. These are a
presentation boundary, but their event handlers depend on App-owned route,
theme, connection, and workspace state.

## Decision

- Extract the shell JSX into `frontend/src/AppLayout.tsx`.
- Keep route, identity, capability, notification controller, theme state,
  mobile navigation state, focus callbacks, and dirty-draft confirmation in
  `App`.
- Pass grouped, typed shell props (navigation, connection, workspace scope,
  theme) and render `AppRoutes` as the main content slot.
- Keep `OnboardingTour` and notification drawers as App-owned overlays.

## Invariants

- Preserve the same `Layout`/`Sider` dimensions, breakpoints, `Menu` items,
  class names, skip-link placement, mobile backdrop, topbar contents, status
  wording, and theme controls.
- Preserve the `aria-controls`, `aria-expanded`, `inert`, `aria-hidden`,
  focus-return, Escape, and Tab-loop behavior established by the mobile
  navigation slice.
- Preserve `App` ownership of navigation, workspace identity/authorization,
  and dirty-draft guards. `AppLayout` is not a router or state store.
- Do not change URL/history/hash behavior, page keep-alive, CSS, or visual
  tokens in this structural slice.

## Alternatives

1. **Leave the JSX in `App`:** lowest risk, but C4b's shell/layout boundary
   remains unextracted after route composition has been separated.
2. **Move mobile focus state into `AppLayout`:** rejected for this slice;
   it would couple route handoffs and focus restoration more tightly and
   enlarge the behavioral change.
3. **Introduce a layout context or routing library:** rejected; explicit
   grouped props suffice and avoid changing state ownership.

## Verification

Run focused accessibility/mobile/navigation/workspace/App route tests,
TypeScript + Vite build, ESLint, and preserve the full-suite result from the
preceding route slice or rerun the full suite if code changes overlap its
execution window.
