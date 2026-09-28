# Stage24 Task source kind strategy registry handoff (2026-09-25)

## What changed

- Added `core/task_source_kinds.py` as the single validated strategy registry
  for Task source-kind data policy and route schemas.
- A `TaskSourceKindSpec` now owns each source's public/storage category,
  public/storage route, default route, allowed route scope, and source-specific
  route parameter extraction.
- A `TaskRouteSpec` registry now owns route allowed/required parameters,
  including release-quality source-specific requirements.
- `core/catalog_schema.py` derives `ENTERPRISE_TASK_SOURCE_KINDS` from the
  built-in registry order. Core canonicalization and route projection resolve
  source kind/category/default route/route schema through the registry.
- Task Operations service category/route/route-parameter behavior now uses the
  same registry. The existing `SOURCE_ADAPTER_REGISTRY` remains the separate
  database adapter Strategy boundary.
- Built-in source contracts are protected against public and raw-registry
  replacement/removal; invalid category, duplicate route requirements,
  noncanonical source kind, and missing required route parameters fail closed.

## Compatibility contract

- All seven built-in kinds retain their previous category, route, default
  route, route aliases, route parameter names, and required route schema.
- `source_kinds` adapter order remains registry insertion order, while runtime
  reconciliation now reads the live adapter registry so a dynamically added
  adapter cannot fail because of a stale order snapshot.
- Category filtering derives its source-kind set from the strategy registry,
  so a registered kind using an existing category is not silently omitted.
- A custom eighth source kind plus adapter can pass canonicalization, source
  validation, category dispatch, and source collection without editing the
  host modules. Persistence remains intentionally closed by the existing task
  projection CHECK and therefore requires a separate migration before the new
  kind can be stored.
- API/OpenAPI vocabulary remains the built-in catalog contract; this slice does
  not widen the DB CHECK or public generated enum.

## Verification

- Task source registry and Task Operations core/service/API/migration/ORM/
  readiness plus Knowledge Serving and enterprise readiness regressions:
  **199 passed** with **408 existing deprecation warnings**.
- Independent sub-agent review: **PASS**, no actionable findings. The reviewer
  independently checked built-in parity, dynamic source collection/category
  dispatch, noncanonical/invalid contract rejection, required route
  parameters, duplicate route requirements, and raw built-in registry guards.
- Ruff, `.venv` `py_compile`, OpenAPI export check (**259 paths / 201
  schemas**), and `git diff --check` passed.

## Boundaries

- No database migration, task projection CHECK widening, or OpenAPI enum
  widening was made.
- This completes the Python strategy/adapter boundary for Task source kinds;
  persistence and a production adapter for any new kind remain separate,
  explicit follow-up work.

Design record:
`docs/plans/2026-09-25-task-source-kind-strategy-registry-design.md`.
