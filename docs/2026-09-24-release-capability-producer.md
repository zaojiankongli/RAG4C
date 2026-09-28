# Stage29 Knowledge Base Release capability producer handoff (2026-09-24)

## What changed

- Registered the reserved `knowledge_base_releases` producer in the existing
  catalog capability `ProviderRegistry`. The public
  `inspect_enterprise_knowledge_base_release_capability()` API now dispatches
  through `inspect_catalog_capability("knowledge_base_releases", bind)`.
- Kept `_KNOWLEDGE_SERVING_ORIGINAL_RELEASE_CAPABILITY` unchanged and wired it
  as the policy's legacy fallback. No second registry or new dispatch kernel
  was added.
- Reused the existing Stage29 release schema/data checker unchanged. The
  `CatalogCapabilityPolicy` supplies the Stage29 minimum revision, required
  tables, missing-table behavior, and probe error mode; the shared producer
  factory supplies the revision ladder (Strategy + Template Method).
- Reserved built-in policy identity is enforced through the existing public
  registration guards and runtime factory-identity check, including raw
  private-registry replacement/removal.

## Compatibility contract

- A known pre-0029 revision with no release tables remains `not_available`.
- A pre-0029 partial schema delegates to the frozen inspector and retains its
  legacy revision issue.
- At 0029 or a later known revision, zero release tables fail closed with the
  sorted `required tables are missing: ...` issue. Partial and complete
  schemas continue through the unchanged Stage29 checker.
- Unknown, absent, empty, and multiple Alembic revision cases delegate to the
  frozen inspector after the shared probe connection closes.
- The active wrapper still propagates first-probe/checker exceptions. The
  legacy fallback retains its own catch-and-return message for a fallback
  connection failure.
- Stage30 certification remains a consumer of the same Stage29 parent
  capability; no readiness/API consumer behavior was intentionally changed.

The compatibility tests compare the registered producer against
`_knowledge_serving_revision_compatible(...)` and directly against the frozen
inspector on fallback branches. They cover pre-0029/0029+/later revisions,
zero/partial tables, unknown/missing/empty/multiple revisions, complete
catalog state, probe failure, fallback connection failure, and built-in
replacement/removal.

## Verification

- Final capability producer file: **36 passed**.
- Stage29 release, Stage30 certification migration, catalog schema,
  Knowledge Serving readiness, enterprise readiness API, and Stage17
  authorization-security regression selection: **262 passed** with **1,296
  existing deprecation warnings**.
- Ruff check, `.venv` `py_compile`, OpenAPI export check (**259 paths / 201
  schemas**), and `git diff --check` passed.
- Independent sub-agent review: **PASS**, no actionable findings; the reviewer
  independently reran the capability producer tests (**36 passed**).

## Boundaries

- No migration, DB CHECK, public response shape, API/OpenAPI, frontend,
  readiness consumer, or authorization change.
- This completes only the Stage29 Release capability producer slice.
  Axis #17 remains open; other frozen wrappers and standalone capability
  inspectors still require separate compatibility reviews.

Design record: `docs/plans/2026-09-24-release-capability-producer-design.md`.
