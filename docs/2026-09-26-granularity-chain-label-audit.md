# Granularity, end-to-end chain, and vocabulary stability audit

**Date:** 2026-09-26
**Scope:** cumulative backend extensibility work and retrieval-quality frontend
facade migration

## Sources and method

The audit reread:

- `D:\program_project\python_project\RAG4C\docs\research\2026-09-24-weknora-extension-patterns.md`
- `D:\program_project\python_project\RAG4C\docs\compose\spec\backend-extensibility-inventory.md`
- `D:\program_project\python_project\RAG4C\.planning\2026-09-23-extensible-backend\progress.md`
- recent frontend facade design/handoff documents;
- registry, strategy, factory, adapter, service, API, model, UI, and test
  boundaries identified by the independent audit agent.

The audit distinguishes a real defect from an intentional closed-contract
boundary. Runtime extensibility does not automatically widen DB/API vocabulary.

## Conclusion

### Granularity: appropriate, with two future pressure points

- Backend work is split by extensibility axis and operation contract. That is
  the right unit: each slice can add a strategy/registry/adapter and prove its
  fail-closed behavior without refactoring unrelated state machines.
- Frontend retrieval-quality work is split by one facade consumer at a time.
  This is small enough for TDD and review, while shared Adapter changes are
  validated with the full frontend suite.
- Do **not** merge the remaining `ExperimentDetailDrawer` and
  `RetrievalQualityCenter` work into one large migration.
- `frontend/src/ui/index.tsx` and `core/catalog_schema.py` are now large
  hosting modules. This is a P3 maintainability concern, not a reason for an
  immediate broad rewrite. Extract by concern only when a new slice needs it.

### End-to-end chain: mostly complete, with explicit remaining breaks

The healthy backend chain is:

`ProviderRegistry → Strategy/Factory → Adapter/port → service/worker/API → focused regression → handoff/progress`

The projection, notification, capability, graph, task-source, and automation
axes now follow that chain for their implemented runtime paths. Unknown
runtime combinations remain fail-closed.

The retrieval-quality frontend chain is now complete for:

`rendererPolicy → shared UI facade → StrategyCard / JudgmentEditor / ExperimentHistory / RetrievalComposer / ComparisonResults → native/TDesign tests → handoff`

Two direct consumer breaks remain intentionally open:

- `frontend/src/retrieval-quality/components/ExperimentDetailDrawer.tsx`
- `frontend/src/retrieval-quality/RetrievalQualityCenter.tsx`

They are the next two independent facade slices, not a reason to widen the
completed slices.

One backend validation break remains environmental rather than behavioral:
the complete `server.app` HTTP mount path is not counted as green in this
workspace because the optional `pymysql` dependency is unavailable. Component
and registry tests must not be reported as HTTP integration proof.

### Labels and vocabulary: UI tags are stable; cross-layer task vocabulary needs
an explicit contract

The shared UI `Tag` facade is stable for the current retrieval-quality states:
`success`, `warning`, `danger`, `theme`, `variant="light"`, and native
fail-closed fallback are covered by tests.

The remaining vocabulary risks are:

1. **Task categories have compatibility aliases across layers.**
   The canonical registry uses `documents/indexing/sources/compliance/quality`,
   while API/frontend compatibility also recognizes `content` and `source`.
   Existing tests protect current behavior, but a new category still requires
   synchronized edits in registry, authority, API schema, and frontend types.

2. **Task fact status and display status intentionally differ.**
   Backend fact status is `succeeded`; frontend display status is `completed`.
   The mapping is deliberate, but it is spread across model parsing and UI
   display logic. New statuses could drift unless the mapping is kept as one
   vocabulary projection contract.

3. **Projection operation values are open on the read/display side.**
   Consistency dead-letter responses expose `target_store` and `operation` as
   strings so unknown historical values can still be shown. Write/requeue/
   repair paths are registry-gated and fail closed. This distinction must stay
   explicit: unknown values may be displayed as unknown facts, but must never
   create an action button or mutation path.

These are P2 design-hardening items, not confirmed fail-open defects.

## Prioritized follow-up

1. Close the two remaining retrieval-quality direct TDesign consumers as
   separate slices:
   - `ExperimentDetailDrawer` facade adoption;
   - `RetrievalQualityCenter` facade adoption.
2. Add a task vocabulary contract covering:
   - canonical category values and legacy alias projection;
   - `succeeded` fact status versus `completed` display status;
   - complete-value-set parity across registry, service/API, and frontend model.
3. Add a projection operation vocabulary contract documenting:
   - read/display unknown-value behavior;
   - mutation/requeue/repair fail-closed behavior;
   - no UI action for unknown operations.
4. In an environment with optional `pymysql` installed, run the minimal
   `server.app` HTTP mount suite and attach its evidence to the relevant
   backend handoff. Do not replace that verification with component tests.

## Audit result

- **P0:** none
- **P1:** none confirmed
- **P2:** remaining frontend facade chain breaks, task vocabulary drift risk,
  and open-string projection read contract
- **P3:** large host modules and parallel task-source metadata

This audit does not claim the overall frontend/backend modernization is
complete. It records the current boundary and the smallest next slices.

## Follow-up closure update (2026-09-26)

- `ExperimentDetailDrawer` and `RetrievalQualityCenter` now use the shared UI
  facade; their implementation handoffs and reviews are recorded in the
  cumulative progress ledger.
- The Task Operations category/fact-status/display vocabulary now has a
  TypeScript-owned contract with generated OpenAPI parity and a corresponding
  handoff.
- Projection operation values remain open for read/display, while the
  Consistency Console now exposes requeue only for backend-verified built-in
  pairs. Unknown rows remain visible and read-only; see
  `docs/2026-09-26-projection-operation-vocabulary-contract.md`.

These close the three vocabulary/facade P2 items recorded above. Backend Axis
#3 remains open independently for Catalog/target authority boundaries; the
optional-`pymysql` app-mount validation gap remains environmental.
