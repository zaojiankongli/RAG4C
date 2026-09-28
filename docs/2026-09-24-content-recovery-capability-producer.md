# Stage23 Content Recovery capability producer handoff (2026-09-24)

## Change

- Kept the public `inspect_enterprise_content_recovery_capability(bind)` name
  and `(state, issues)` return shape; it now dispatches through the shared
  `inspect_catalog_capability("content_recovery", bind)` registry path.
- Added a reserved `content_recovery` built-in
  `CatalogCapabilityPolicy`, using the existing 0033 minimum revision, required
  tables, `_enterprise_content_recovery_capability_issues` domain checker, and
  legacy issue text.
- Set policy-level dialect validation to `None`, preserving the existing
  Content Recovery checker as owner of dialect-specific guards.
- Left migrations, database CHECKs, the domain issue checker, readiness
  consumers, authorization behavior, API/OpenAPI, and frontend vocabulary
  unchanged.

## Compatibility preserved

- Known pre-0033 revision with no Content Recovery tables:
  `not_available`, no issues.
- Known pre-0033 revision with any capability table present:
  `unavailable` with the legacy pre-0033 issue.
- 0033+ known revision with missing capability tables, unknown/missing
  revision, or multiple revisions: `unavailable`.
- Complete schema remains `ready`; malformed schema remains `unavailable`.

## Verification

- Capability producer + Content Recovery schema/service regression:
  **24 passed**.
- Enterprise readiness API Content Recovery cases: **2 passed**.
- Ruff check, Ruff format check, `py_compile`, and `git diff --check` —
  **passed**.
- Independent sub-agent review — **PASS**, no reproducible findings. The
  reviewer independently checked the legacy revision matrix and ran **19
  targeted tests**.

Design record: `docs/plans/2026-09-24-content-recovery-capability-producer-design.md`.

Axis #17 remains open; the other historical and frozen capability producers
still need separate behavior-equivalence slices.
