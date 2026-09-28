# Stage24 Task Operations capability producer handoff (2026-09-24)

## What changed

- Added the reserved `task_operations` policy to the existing catalog capability
  `ProviderRegistry`; `inspect_enterprise_task_operations_capability()` now
  dispatches through `inspect_catalog_capability("task_operations", bind)`.
- Kept `_KNOWLEDGE_SERVING_ORIGINAL_TASK_CAPABILITY` unchanged. The policy
  preserves the legacy fallback path for missing/unknown/multiple revisions
  and known pre-0034 revisions, including the probe-then-fallback connection
  ordering. It only uses the shared ladder directly at and after Stage24.
- Extended `CatalogCapabilityPolicy` with explicit missing-table disposition,
  inspection-error mode, and an optional validated synchronous legacy fallback.
  Other built-in policies retain their previous defaults.
- Preserved the Stage24 contract:
  - known pre-0034 with no capability tables: `not_available`;
  - known pre-0034 with partial tables: `unavailable` with the legacy
    minimum-revision message;
  - 0034+ with no tables: `unavailable` with the sorted
    `required tables are missing: ...` issue;
  - partial tables use the original domain checker;
  - the active wrapper propagates probe/checker errors, while the frozen
    original retains its own catch-and-return behavior.
- Pinned built-in factory identity and reject raw private-registry replacement
  or removal during dispatch, in addition to the public registration guards.

## Verification

- Capability producer + Task Operations readiness + Automation readiness +
  enterprise readiness API + Stage17 authorization-security regression:
  **186 passed**, with **1,179 existing deprecation warnings**.
- Ruff check, focused Ruff format check, `.venv` `py_compile`, and
  `git diff --check` passed.
- Independent sub-agent review: **PASS**. It confirmed the status/message
  matrix, two-stage fallback exception behavior, unchanged defaults for the
  other capability policies, runtime registry dispatch, and fail-closed
  built-in replacement/removal behavior.

## Boundaries

- No schema migration, DB CHECK, authorization consumer, API/OpenAPI, or
  frontend contract changed.
- Stage24 is now an active registry-backed producer. Its frozen original
  remains for legacy fallback and parity tests.
- Axis #17 remains open; other historical capability producers still need
  separate behavior-equivalence slices.

Design record: `docs/plans/2026-09-24-task-operations-capability-producer-design.md`.
