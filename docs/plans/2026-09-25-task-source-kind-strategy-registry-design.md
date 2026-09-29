# Stage24 Task source kind strategy registry design (2026-09-25)

## Context

The Task Operations read model already has a source-adapter registry, but
source-kind behavior is still split across parallel dictionaries and
conditionals:

- public/storage category mappings in the service;
- public/storage/default route mappings in the service and core;
- source-specific route parameter construction;
- route-to-source scope sets and route parameter schemas.

The API vocabulary and adapter order were previously derived, but adding a
new source still requires editing these behavior sites or silently fails at a
later layer.

## Decision

- Add `core/task_source_kinds.py` with a validated `TaskSourceKindSpec`
  strategy registry and a small `TaskRouteSpec` schema registry.
- Make one source-kind specification the authority for:
  public/storage category, public/storage route, default route, route scope,
  and source-specific route parameter extraction.
- Derive `ENTERPRISE_TASK_SOURCE_KINDS` in `core/catalog_schema.py` from the
  registry's built-in order, so API literals/list limits and the catalog
  contract cannot drift from the source-kind declaration.
- Make `core.enterprise_task_operations` resolve source/category/default route
  behavior through the registry while preserving compatibility aliases and
  existing error messages.
- Make `core.enterprise_task_operations_service` resolve category/route and
  source-route parameters through the same registry. The existing
  `SOURCE_ADAPTER_REGISTRY` remains the adapter Strategy boundary; the new
  spec registry supplies data policy, not database adapter implementations.
- Keep the storage CHECK closed. Registering a new in-process source kind
  makes the Python read/projection path extensible, but persistence still
  requires a separate migration and contract update.

## Compatibility contract

- All seven built-in kinds retain their exact public/storage categories,
  route codes, default routes, route parameters, and route schema behavior.
- Existing aliases (`enterprise_*`, `knowledge_*`) remain accepted and their
  allowed source-kind sets remain unchanged.
- Existing service tests that temporarily add an adapter without a full
  source spec keep their validation-only behavior; a source that reaches
  projection must have a registered spec and fails closed otherwise.
- Unknown kinds, duplicate source kinds, unsupported route codes, incomplete
  route parameters, and source/route mismatches remain rejected.
- No task table CHECK or OpenAPI enum is widened by this slice.

## Verification

- Add registry shape and built-in parity tests.
- Add zero-branch tests registering an eighth source kind plus an adapter and
  asserting canonicalization, route projection, service collection, and host
  branch independence.
- Add negative tests for invalid specs, duplicate route/schema declarations,
  unknown kinds, and storage CHECK boundary.
- Run Task Operations core/service/API/readiness/migration regressions,
  capability producer regressions, Ruff, `.venv` `py_compile`, OpenAPI export
  check, and `git diff --check`.
- Request an independent sub-agent review after implementation.

## Boundaries

This slice does not add a database migration or make a new source kind
persistent. It closes the Python strategy/adapter boundary only; the storage
CHECK and full end-to-end production adapter remain explicit follow-up work.
