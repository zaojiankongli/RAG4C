# Stage24 Task Operations capability producer design (2026-09-24)

## Context

Axis #17 already routes the active Stage23, Stage25, Stage26, and Stage27
catalog-capability inspectors through `CatalogCapabilityPolicy` and the shared
`ProviderRegistry` producer. Stage24 Task Operations remains a frozen,
revision-aware inspector wrapped by `_knowledge_serving_revision_compatible`.
Its public result shape is also consumed by the enterprise readiness registry.

The wrapper and `_KNOWLEDGE_SERVING_ORIGINAL_TASK_CAPABILITY` are intentional
compatibility history, not disposable duplicate definitions. Any active
producer conversion must prove equality against that frozen implementation
before replacing public dispatch.

## Decision

- Register a reserved `task_operations` policy in the existing catalog
  capability registry; keep the original inspector object unchanged under
  `_KNOWLEDGE_SERVING_ORIGINAL_TASK_CAPABILITY`. Pin the current public
  compatibility behavior with the existing
  `_knowledge_serving_revision_compatible(bind, original, ...)` call as the
  matrix oracle before replacing public dispatch.
- Route only the public active inspector through
  `inspect_catalog_capability("task_operations", bind)`.
- Extend `CatalogCapabilityPolicy` only with explicit missing-table states and
  exception behavior needed to represent the public Stage24 wrapper contract.
  Existing policies retain defaults and behavior. The active wrapper currently
  propagates probe/checker exceptions, while the frozen original function
  catches them; the new policy must preserve the active wrapper's propagation
  and retain the frozen function unchanged for direct compatibility checks.
- Add an optional validated fallback inspector for known legacy branches. The
  shared producer must close its probe connection before delegating to the
  frozen original, matching the wrapper's old two-step transaction ordering.
  Built-in producer factory identities are pinned; direct private-registry
  replacement or removal also fails closed rather than silently disabling a
  readiness capability.
- Keep `_enterprise_task_operations_capability_issues`, migration history,
  readiness capability metadata, and all public response shapes unchanged.

## Compatibility matrix to pin before switching dispatch

For each fixture, assert the active policy result equals the current public
compatibility wrapper modeled through
`_knowledge_serving_revision_compatible(bind, original, ...)`. For pre-0034,
unknown/missing revision, and multiple-revision cases, also assert the delegated
fallback equals `_KNOWLEDGE_SERVING_ORIGINAL_TASK_CAPABILITY`. At/after 0034 the
wrapper itself preempts the original checker on some missing-table cases, so its
result—not the older original's different issue text—is the compatibility
contract.

1. Known pre-0034 revision and no Task Operations tables:
   `not_available`, no issues.
2. Known pre-0034 revision with any Task Operations table present:
   `unavailable`, the frozen minimum-revision message.
3. Unknown revision, no revision table, and multiple revisions:
   preserve each frozen error state/message.
4. Revision 0034 or later with zero Task Operations tables:
   `unavailable`, exact sorted `required tables are missing: ...` issue.
5. Revision 0034 or later with partial tables:
   `unavailable`, the domain checker's exact per-table issues.
6. Complete schema and data: `ready`; stale projections, unsafe saved views,
   missing trigger, and broken event chain remain `unavailable` with their
   existing checker issues.
7. Inspection/checker exceptions: the active wrapper raises from the shared
   `_schema_connection` path, while the frozen original returns `unavailable`
   with its legacy issue. Pin both behaviors separately and configure the new
   policy to preserve the active public behavior.

Built-in policy registration must be immutable, and the public function must
exercise the real shared registry path.

## Boundaries

- No schema migration, DB CHECK, readiness consumer, API/OpenAPI, frontend, or
  authorization change.
- Do not delete the frozen original alias after switching public dispatch.
- If any row of the compatibility matrix differs, keep the active public
  wrapper unchanged and revise the policy contract rather than weakening the
  test.

## Verification

- New permanent tests compare policy dispatch with the frozen inspector across
  the matrix above and verify built-in immutability.
- Run Stage24 readiness/service/API tests, shared capability-producer tests,
  Stage23/25/26/27 readiness tests, and Stage17 authorization security tests.
- Run Ruff, `py_compile`, OpenAPI export check, and `git diff --check`.
- Independent sub-agent review after implementation.
