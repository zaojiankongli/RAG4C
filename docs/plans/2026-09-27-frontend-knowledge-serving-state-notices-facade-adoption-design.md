# Frontend Knowledge Serving state notices facade adoption design

## Context

Knowledge Serving still has direct TDesign state-notice consumers in the
serving helper, center capability surface, and snapshot table loading
surface. Its retryable `LoadState` also uses the shared Alert `operation`
prop, but the native Alert renderer currently drops that prop. The facade
must preserve the retry action before this consumer can migrate safely.

## Decision

- Extend the shared native `Alert` renderer to render `operation` content and
  retain the prop in the TDesign branch.
- Migrate `servingUi.tsx` state `Alert` and `Tag` consumers to shared
  `Alert`/`Tag`; retain the direct TDesign `Button` used inside the retry
  operation.
- Migrate `KnowledgeServingCenter`'s inactive capability `Alert` to shared
  `Alert`.
- Migrate `ServingTable` loading from TDesign `Loading` to shared `Spin`.
- Preserve retry behavior, loading/unavailable/error/partial/empty copy,
  authority fail-closed semantics, and existing DOM/CSS ownership.
- Do not change controller/API/model/mutation/backend contracts.

## Verification plan

1. Add native Alert-operation, source-boundary, and state behavior tests
   before implementation and observe expected failures.
2. Implement the shared Alert compatibility and Knowledge Serving consumer
   migration.
3. Run focused/shared UI tests, the full Knowledge Serving suite, TypeScript,
   focused ESLint, build, and `git diff --check`.
4. Request an independent sub-agent review, fix P0–P2 findings, rerun checks,
   and write the handoff/progress entry.

## Scope boundary

This slice does not migrate Knowledge Serving drawer/table/filter/policy
controls other than the listed state-notice controls, and does not change
state ownership or backend behavior.
