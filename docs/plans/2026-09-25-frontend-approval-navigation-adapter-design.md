# Frontend approval navigation adapter design (2026-09-25)

## Context

Approval navigation is still implemented in several modules with direct
`history.pushState`/`replaceState` calls. The URL builders correctly preserve
direct versus hash route shapes, but the browser commit protocol is duplicated
across:

- the approval route helper;
- member-role approval navigation;
- Workspace authorization approval;
- Dataset ACL approval.

## Design

1. Extend the shared navigation adapter with an optional `historyState`, while
   retaining current-state behavior when it is omitted.
2. Route approval push and replace operations through the adapter's explicit
   `history` strategy. Approval URLs may contain a leading `#`; this is
   intentional because the legacy approval contract uses `history.pushState`
   or `history.replaceState` even for hash-shaped URLs.
3. Keep approval URL construction, request-id validation, route parsing,
   approval state, and authorization behavior in their current modules.
4. Add source guards and preserve existing direct/hash route assertions,
   including exact `{}` history state for approval push helpers.

## Compatibility

- Direct and hash approval URL shapes remain unchanged.
- Approval route helpers and Dataset ACL approval still call
  `pushState({}, "", url)` through the adapter and dispatch one synthetic
  `popstate`.
- Workspace authorization approval preserves the existing
  `window.history.state` through the adapter and dispatches one synthetic
  `popstate`.
- Approval request clearing still calls `replaceState(window.history.state,
  "", url)` and dispatches one synthetic `popstate`.
- No approval API, persistence, authorization, or public contract changes.

## Verification

- Focused approval route, member navigation, ACL dialog, Workspace
  authorization, and adapter tests.
- Focused ESLint, TypeScript/build, and `git diff --check`.
- Independent sub-agent review before handoff.

## Completed verification (2026-09-25)

- Focused regression: **6 files / 33 tests passed**.
- Adapter tests cover push/replace, explicit history state, and one synthetic
  `popstate`.
- Approval route tests cover direct/hash URL shapes, request clearing with
  current history state, and one synthetic `popstate`.
- Workspace authorization regression confirms its existing host
  `history.state` is preserved; ACL/member approval helpers retain explicit
  `{}` state.
- Focused ESLint: passed.
- TypeScript and production build: passed, **7,125 modules transformed**.
- `git diff --check`: passed.
- Initial independent review found a Workspace `history.state` regression and
  a documentation/test gap; both were fixed.
- Final independent sub-agent review: **PASS**, no findings.
