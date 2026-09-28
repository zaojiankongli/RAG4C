# Catalog capability producer design (2026-09-24)

## Context

`core/catalog_schema.py` has independent but equivalent revision ladders for the
Stage26 Knowledge Serving and Stage27 Knowledge Operations capability checks.
Both must distinguish a known pre-introduction catalog with no capability
tables (`not_available`) from an unknown, partial, or corrupt schema
(`unavailable`). Their domain-specific schema issue checkers remain distinct.

The backend extensibility standard permits a shared Template Method with
Strategy+Registry dispatch for behavior that should be addable without adding a
new ladder branch. The local WeKnora review is useful as precedent for named
factory/registry boundaries; its language/framework implementation is not
copied.

## Decision

- Add `CatalogCapabilityPolicy` and one shared revision-aware producer in
  `core/catalog_capability_producers.py`.
- Register capability producers through the existing
  `core.providers.ProviderRegistry`; do not add another registry kernel.
- Keep Stage26 and Stage27 schema-specific issue checkers and policy values as
  separate declarations.
- Keep their public `inspect_*_capability(bind)` functions and result shape
  unchanged; Stage26's `inspect_enterprise_knowledge_serving_capability` alias
  remains intact.
- Permit additional in-process policies through the common inspector, while
  reserving the two built-in names against replacement/removal.

## Invariants

- Preserve exact `ready`, `not_available`, and `unavailable` semantics,
  including known-revision ordering and the distinction between absent and
  partially present capability tables.
- Preserve dialect, multiple-revision, minimum-revision, and exception issue
  wording for the two existing inspectors.
- Reject malformed policy/checker registrations before runtime dispatch.
- Do not modify migration history, schema constraints, OpenAPI/frontend
  capability vocabulary, authorization consumers, or domain issue checkers.
- Do not change or rebind any `_KNOWLEDGE_SERVING_ORIGINAL_*` compatibility
  producer. This slice does not close all of inventory axis #17.

## Alternatives considered

1. **Leave both ladders local:** lowest change risk, but a new capability using
   this same contract must duplicate the state ladder.
2. **Unify every capability inspector, including frozen compatibility wrappers:**
   broader than the duplicate behavior identified here and risks changing
   historical fallback semantics. Deferred.
3. **Create a new generic registry:** rejected; the repository standard requires
   reuse of `ProviderRegistry`.

## Verification

- Dynamic custom policy through the same production inspector proves live
  registry dispatch.
- Registration rejects invalid checker signatures and built-in override.
- Legacy-no-table and legacy-partial-table tests pin the security-sensitive
  state distinction.
- Run Stage26/Stage27 readiness and Stage17 authorization regression suites.
- Reverse validation bypassed live registry dispatch and the two registration
  guards in process; the matching regression assertions failed as expected.
  No source bytes were modified.
