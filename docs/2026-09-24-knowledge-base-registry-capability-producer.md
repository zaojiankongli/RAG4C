# Stage28 Knowledge Base Registry capability producer handoff (2026-09-24)

## What changed

- Registered the reserved `knowledge_base_registry` producer in the existing
  catalog capability `ProviderRegistry`. The public
  `inspect_enterprise_knowledge_base_registry_capability()` API now dispatches
  through `inspect_catalog_capability("knowledge_base_registry", bind)`.
- Preserved `_KNOWLEDGE_SERVING_ORIGINAL_REGISTRY_CAPABILITY` unchanged and
  wired it as the policy's legacy fallback. No second registry or dispatch
  kernel was introduced.
- Added a small revision-aware checker adapter that forwards the observed
  Alembic revision as `approval_action_revision` to the existing
  `_knowledge_base_registry_capability_issues` checker.
- The producer probes only the Stage28 registry table set; dependency tables,
  ownership, idempotency, audit, approval-action checks, and data invariants
  remain in the original domain checker.
- Reserved built-in identity remains protected by public registration guards
  and runtime factory identity checks, including raw private-registry
  replacement/removal.

## Compatibility contract

- A known pre-0028 revision with no registry tables remains `not_available`.
- A pre-0028 partial schema delegates to the frozen inspector and retains its
  legacy revision issue.
- At 0028 or a later known revision, zero registry tables fail closed with the
  active wrapper's sorted `required tables are missing: ...` issue. Partial
  schemas continue through the revision-aware original domain checker.
- The checker receives the exact observed revision, preserving approval-action
  contract selection for Stage28 and later catalog revisions.
- Unknown, absent, empty, and multiple Alembic revision cases delegate to the
  frozen inspector after the shared probe connection closes.
- The active wrapper still propagates first-probe/checker exceptions. The
  legacy fallback retains its own catch-and-return behavior for fallback
  connection failures.
- Stage18 ownership/idempotency/audit semantics and later readiness consumers
  remain unchanged.

The compatibility tests compare the registered producer against
`_knowledge_serving_revision_compatible(..., revision_aware=True)` and directly
against the frozen inspector on fallback branches. They cover pre-0028/0028+,
unknown/missing/empty/multiple revisions, zero/partial tables, approval-action
forwarding, complete catalog state, probe failure, fallback connection failure,
and built-in replacement/removal.

## Verification

- Capability producer file: **112 passed**.
- Stage18 registry, Stage29–32 capability/migration, catalog schema,
  Knowledge Serving readiness, enterprise readiness API, and Stage17
  authorization-security regression selection: **375 passed** with **1,723
  existing deprecation warnings**.
- Ruff check, `.venv` `py_compile`, OpenAPI export check (**259 paths / 201
  schemas**), and `git diff --check` passed.
- Independent sub-agent review: **PASS**, no actionable findings. The reviewer
  independently ran 17 Stage28-focused tests, 36 registry-related tests, and 3
  Stage18 capability/readiness tests.

## Boundaries

- No migration, DB CHECK, public response shape, API/OpenAPI, frontend,
  readiness consumer, approval behavior, or authorization change.
- This completes only the Stage28 Knowledge Base Registry capability producer
  slice. Axis #17 remains open; other frozen wrappers and standalone
  capability inspectors require separate compatibility reviews.

Design record:
`docs/plans/2026-09-24-knowledge-base-registry-capability-producer-design.md`.
