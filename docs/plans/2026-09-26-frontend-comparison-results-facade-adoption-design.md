# Frontend ComparisonResults facade adoption design (2026-09-26)

## Context

`ComparisonResults` is the remaining retrieval-quality result surface that
still imports `Alert`, `Card`, and `Tag` directly from `tdesign-react`. The
shared UI facade already supports the exact historical props used here:

- `Card bordered` and app-owned summary classes;
- `Tag theme` plus `variant="light"`;
- `Alert theme`, `title`, and `message`.

The evidence table, lineage strip, and trace panel already own their
respective semantic/facade boundaries and are outside this slice.

## Decision

Migrate only `ComparisonResults` to `../../ui`:

- keep the empty state, immutable generation rail, state-to-color mapping,
  status copy, failure code, evidence alignment, lineage, trace sanitization,
  neutral note, and responsive CSS unchanged;
- reuse existing `Card`, `Tag`, and `Alert` adapters without adding a new
  registry or component-specific adapter;
- add source and component regressions for native and TDesign renderer paths.

## Compatibility and failure boundary

- completed, failed, and no-hit states remain explicitly represented by both
  text and status metadata;
- native and TDesign cards/tags/alerts retain their semantic content and
  visual state mapping;
- the evidence table remains named `证据排名对比`, and its horizontal-scroll
  region remains named;
- sensitive traces remain sanitized and are never widened by facade adoption;
- no route, API, backend, storage, authorization, or persisted-state change.

## Verification

1. Add failing source/component tests for the facade boundary, empty state,
   state alerts/tags, evidence table/scroll region, and sanitized traces.
2. Implement the smallest import-only consumer migration.
3. Run focused ComparisonResults/UI tests, TypeScript, and focused ESLint.
4. Request an independent sub-agent review and fix valid findings, including
   native/TDesign semantic parity.
5. Write the handoff/progress entry, then rerun the full frontend suite, build,
   lint, and `git diff --check`.
