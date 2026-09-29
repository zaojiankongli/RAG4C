# Frontend enterprise capability policy registry design (2026-09-25)

## Context

`frontend/src/App.tsx` currently repeats the same enterprise capability
decision in five places: actor-token presence, tenant identity match, backend
capability state, service degradation/read-only state, and the extra
`knowledge.manage` permission required by serving. Adding another enterprise
capability requires copying the policy into App and then threading the result
through the route context.

This is a frontend policy axis, not a route or permission bypass. The frontend
must remain fail-closed when identity/capability evidence is absent.

## Design

1. Add `frontend/src/enterprise-admin/capabilityPolicy.ts` with a declarative
   capability policy table and one shared resolver.
2. Each policy declares the backend capability key and optional required
   permission. The resolver returns the existing `{ ready, readOnly }` shape;
   it does not invent identity, capability, or workspace scope.
3. Replace the five repeated `App.tsx` expressions with one registry lookup.
   `AppRouteContext` and page props remain unchanged, so page components do not
   learn about the registry.
4. Keep route navigation, workspace selection, notification controllers, and
   dirty-draft ownership in `App`; this slice only centralizes the pure
   capability projection.
5. Unknown capability states, missing identity, tenant mismatch, missing actor
   token, degraded service, and missing required permission remain
   fail-closed/read-only according to the existing behavior.

## Verification

- Pure policy tests cover every registered capability, tenant/token mismatch,
  limited/unavailable states, degraded/offline service, and the serving
  permission gate.
- App focused tests, TypeScript/build, and ESLint remain green.
- An independent sub-agent reviews the registry and the App integration.
- No backend, OpenAPI, or frontend public API contract changes.
