# Frontend Task Operations lifecycle rail facade adoption design (2026-09-26)

## Context

`TaskOperationsLifecycleRail` has one direct TDesign `Tag` consumer for the
“状态全程留痕” note. The shared UI facade already owns Tag theme/variant/size
compatibility, so this small presentation component should not bypass the
renderer policy.

## Decision

Replace only the direct Tag import with the shared `frontend/src/ui` Tag
facade. Preserve the rail's stages, summary values, status classes, icons,
connector DOM, note text, ARIA label, and CSS classes. No shared Adapter
change, API/model change, or backend change is included.

The rail also keeps authority-state semantics explicit: `partial`,
`unavailable`, and `error` summaries use warning stages rather than claiming
queue/attempt/outcome completion. A `ready` summary retains the historical
current/complete mapping.

## Verification

1. Add source and behavior tests for the facade boundary and lifecycle
   rendering.
2. Implement the import-only consumer migration.
3. Run focused lifecycle/TaskOperationsCenter tests, TypeScript, focused
   ESLint, and `git diff --check`.
4. Request independent review, fix findings, and rerun checks.
5. Write a handoff and append cumulative progress.
