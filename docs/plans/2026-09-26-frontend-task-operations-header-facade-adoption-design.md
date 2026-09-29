# Frontend Task Operations Header facade adoption design (2026-09-26)

## Context

`TaskOperationsHeader` is a small, isolated Task Operations host that still
imports TDesign `Button` and `Tag` directly. Its sibling `TaskBoundaryTag`
also lives in the shared task UI helper but bypasses the repository's native /
TDesign compatibility facade.

The shared UI facade already owns the compatibility behavior for Button and
Tag. Leaving this header on direct TDesign keeps a presentation boundary
outside the common renderer policy and makes native/TDesign parity harder to
verify.

## Decision

Migrate only the header boundary:

- `TaskOperationsHeader` imports `Button` from `frontend/src/ui`;
- `TaskBoundaryTag` imports `Tag` from `frontend/src/ui`;
- historical TDesign `variant="outline"` maps to the facade's default Button
  vocabulary, whose native branch is the existing bordered button and whose
  TDesign branch maps to the historical outline variant;
- historical `variant="text"` maps to `type="text"`;
- header labels, icons, refresh callback, settings affordance, read-only tag,
  date formatting, DOM classes, and ARIA names remain unchanged.

No API, route, task model, persistence, or backend contract changes are
included. Other Task Operations direct TDesign consumers remain separate
slices.

## Verification

1. Add failing consumer/source tests for the facade boundary and refresh/
   read-only behavior.
2. Implement the smallest consumer/helper import and prop mapping.
3. Run focused header/Task Operations/UI tests, TypeScript, focused ESLint,
   and `git diff --check`.
4. Request independent sub-agent review; fix findings and rerun checks.
5. Write a handoff and update cumulative progress.
