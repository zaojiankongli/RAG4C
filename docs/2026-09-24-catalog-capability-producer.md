# Catalog capability producer handoff (2026-09-24)

## What changed

- Added `core/catalog_capability_producers.py` with immutable
  `CatalogCapabilityPolicy`, registration-time validation, and a shared
  revision/dialect/failure-state producer.
- Reused `core.providers.ProviderRegistry` from
  `core/catalog_schema.py` for Strategy dispatch. There is no second registry
  implementation.
- Stage26 Knowledge Serving and Stage27 Knowledge Operations now declare their
  policy-specific revision, tables, dialects, labels, and existing issue
  checkers, then call the shared inspector.
- Kept the existing public inspector names, Stage26 compatibility alias,
  `(state, issues)` result shape, and schema issue-checker functions.
- Built-in policy names are reserved against dynamic replacement/removal.
  Additional in-process policies can be registered and inspected through
  `register_catalog_capability_producer` and `inspect_catalog_capability`.

## Compatibility boundary

This is a bounded slice of inventory axis #17, not a claim that the entire axis
is closed. In particular, the frozen `_KNOWLEDGE_SERVING_ORIGINAL_*` producers
and their historical compatibility wrappers were not modified. No migration,
database CHECK, public API/OpenAPI/frontend vocabulary, authorization
consumer, or catalog state meaning was changed.

## Tests and review

- TDD red phase: the new test module initially failed collection because
  `core.catalog_capability_producers` did not exist.
- `tests/test_catalog_capability_producers.py`: **8 passed**.
- Capability producer + Stage26/Stage27 readiness + Stage17 authorization
  security + shared capability consumer regression:
  **73 passed, 959 existing deprecation warnings** in 258.03s.
- `ruff check core/catalog_capability_producers.py core/catalog_schema.py
  tests/test_catalog_capability_producers.py`: passed.
- `ruff format --check` passed for the new module and test file;
  `py_compile` and `git diff --check` passed.
- Reverse validation: bypassing live registry dispatch made the dynamic
  producer assertion fail as expected. Process-local bypasses of the known
  minimum-revision and synchronous-checker guards made their registration
  regression assertions fail as expected; no source files were mutated.
- Independent review found two actionable issues: an unknown
  `minimum_revision` could pass the `-1` ordering fallback, and async checkers
  were not rejected at registration. Both are fixed with permanent tests.
  Follow-up code review: **PASS**, no remaining code findings. The reviewer then
  found stale “review pending” wording in the handoff/inventory; the entries
  were synchronized and the reviewer confirmed the documentation finding is
  closed. Final result: **PASS, no remaining findings**.

## Next

Axis #17 remains open until the other in-scope historical producers are
separately reviewed against the frozen behavior contract.
