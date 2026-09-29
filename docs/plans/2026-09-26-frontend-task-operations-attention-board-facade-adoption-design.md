# Frontend Task Operations attention board facade adoption design (2026-09-26)

## Context

`TaskAttentionBoard` has one direct TDesign `Tag` consumer for its tenant-scope
badge. The shared UI facade already owns Tag theme/variant compatibility, so
this isolated presentation component should use the same renderer boundary as
the header and lifecycle rail.

## Decision

Replace only the direct Tag import with the shared `frontend/src/ui` Tag
facade. Preserve all metric labels/counts/hints, icon selection, section
heading, tenant badge text, region label, and CSS classes. No shared Adapter,
API, model, or backend change is included.

## Verification

1. Add source and behavior tests for the facade boundary and metric rendering.
2. Implement the import-only consumer migration.
3. Run focused AttentionBoard/TaskOperationsCenter tests, TypeScript, focused
   ESLint, and `git diff --check`.
4. Request independent review and fix findings.
5. Write a handoff and append cumulative progress.
