# Frontend Task Operations state notices facade adoption design

## Context

The Task Operations shared presentation helpers still import TDesign
`Alert`, `Empty`, and `Loading` directly, and `TaskOperationsCenter` imports
TDesign `Alert` for its Activity surface. These are the last direct
renderer consumers in the Task Operations flow after the panel, table,
detail drawer, and mutation dialog migrations.

The shared facade already owns `Alert`, `Empty`, and `Spin` (the
renderer-independent loading vocabulary). The notices are read-only
authority projections and must continue to distinguish loading, unavailable,
error, partial, and empty states without estimating missing facts.

## Decision

- Migrate `taskOperationsUi.tsx` to shared `Alert`, `Empty`, and `Spin`.
- Migrate `TaskOperationsCenter` ActivityPanel alerts to shared `Alert`.
- Preserve all state labels, messages, invalid-item counts, empty copy,
  activity event rendering, ARIA roles, and CSS ownership.
- Do not change controller/API/model/backend contracts.

## Verification plan

1. Add source and behavior tests before implementation and observe the
   expected direct-import boundary failure.
2. Implement the isolated consumer migration using the existing facade
   vocabulary.
3. Run state-notice/Task Operations/shared UI tests, TypeScript, focused
   ESLint, build, and `git diff --check`.
4. Request independent sub-agent review, fix findings, rerun checks, and
   write the handoff/progress entry.

## Scope boundary

This slice does not change `TaskOperationsCenter` state ownership, APIs,
mutation behavior, task model vocabulary, or backend code. It closes the
remaining direct TDesign state-notice consumers in the Task Operations flow.
