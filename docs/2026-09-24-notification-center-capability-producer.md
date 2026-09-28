# Stage32 Notification Center capability producer handoff (2026-09-24)

## What changed

- Registered the reserved `notification_center` producer in the existing
  catalog capability `ProviderRegistry`. The public
  `inspect_enterprise_notification_center_capability()` API now dispatches
  through `inspect_catalog_capability("notification_center", bind)`.
- Preserved the original Stage32 inspector as
  `_KNOWLEDGE_SERVING_ORIGINAL_NOTIFICATION_CAPABILITY` and wired it as the
  policy's legacy fallback. No second registry or dispatch kernel was added.
- Reused `_enterprise_notification_center_capability_issues` unchanged for
  Stage32 schema, guard, event-chain, receipt, and data invariants. The shared
  policy supplies only the revision ladder, required-table disposition, and
  probe error behavior.
- Reserved built-in identity remains protected by public registration guards
  and runtime factory identity checks, including raw private-registry
  replacement/removal.

## Compatibility contract

- A known pre-0032 revision with no Notification Center tables remains
  `not_available`.
- A pre-0032 partial schema delegates to the frozen inspector and retains its
  legacy revision issue.
- At 0032 or a later known revision, zero Notification Center tables fail
  closed with the sorted `required tables are missing: ...` issue. Partial and
  complete schemas continue through the unchanged Stage32 checker.
- Unknown, absent, empty, and multiple Alembic revision cases delegate to the
  frozen inspector after the shared probe connection closes.
- The active wrapper still propagates first-probe/checker exceptions. The
  legacy fallback retains its own catch-and-return behavior for fallback
  connection failures.
- Notification materialization/receipt consumers and later readiness parents
  remain unchanged.

The compatibility tests compare the registered producer against
`_knowledge_serving_revision_compatible(...)` and directly against the frozen
inspector on fallback branches. They cover pre-0032/0032+/later revisions,
zero/partial tables, unknown/missing/empty/multiple revisions, complete
catalog state, probe failure, fallback connection failure, and built-in
replacement/removal.

## Verification

- Capability producer file: **93 passed**.
- Stage29–32 capability/migration, catalog schema, Notification Center
  materializer/receipt, Knowledge Serving/Automation/Task readiness,
  enterprise readiness API, and Stage17 authorization-security regression
  selection: **417 passed** with **2,164 existing deprecation warnings**.
- Ruff check, `.venv` `py_compile`, OpenAPI export check (**259 paths / 201
  schemas**), and `git diff --check` passed.
- Independent sub-agent review: **PASS**, no actionable findings. The reviewer
  independently ran 19 Stage32-focused tests, 61 Notification Center-related
  tests, and 52 Task/Automation readiness tests.

## Boundaries

- No migration, DB CHECK, public response shape, API/OpenAPI, frontend,
  readiness consumer, notification materializer/receipt behavior, or
  authorization change.
- This completes only the Stage32 Notification Center capability producer
  slice. Axis #17 remains open; other frozen wrappers and standalone
  capability inspectors require separate compatibility reviews.

Design record:
`docs/plans/2026-09-24-notification-center-capability-producer-design.md`.
