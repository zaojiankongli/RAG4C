# Tencent ADP knowledge UI study and RAG4C adaptation — 2026-08-25

## Method

The study used Playwright against Tencent Cloud's public ADP product and documentation pages. The authenticated console redirects to Tencent Cloud login, so the review is based on public product documentation and embedded current-product screenshots rather than bypassing authentication.

Public pages reviewed:

- `https://cloud.tencent.com/product/adp`
- `https://cloud.tencent.com/document/product/1759/112702`
- `https://cloud.tencent.com/document/product/1759/119287`

Local visual evidence:

- `output/playwright/tencent-kb/tencent-adp-document-management.png`
- `output/playwright/tencent-kb/tencent-adp-chunk-intervention.png`

## Observed information architecture

Tencent's knowledge workflow uses three layers:

1. An application-level navigation bar for application settings, knowledge management, workflow, evaluation, publishing, and operations.
2. A knowledge-base selector plus asset-type navigation such as documents, QA, and databases.
3. A task workspace with category rail, search/filter controls, bulk actions, table or editor content, and a compact utility area.

This is effective because application lifecycle, knowledge asset type, and current operator task are visually distinct.

## Observed document-management patterns

The public screenshot shows:

- A left category rail with category search and counts.
- A prominent Import action followed by context-sensitive batch actions.
- A compact document table with selection, name, tags, character count, status, enable switch, settings, delete, and overflow actions.
- Knowledge-base character usage and comparison/schema utilities above the table.
- Destructive and configuration actions close to the affected row rather than hidden in a separate settings page.

## Observed parsing-intervention patterns

The parsing-intervention screenshot uses a three-pane workspace:

1. Original or parsed document preview.
2. Chunk list with the current selection.
3. Chunk editor for the selected slice.

A single top-right submit action makes the edit session explicit. The user can compare source context, chunk boundaries, and edited content without switching drawers or pages.

## What RAG4C should adopt

### Adopt structurally

- Three-level navigation: product area → knowledge asset type → operator workspace.
- Persistent category/facet rail for large document sets.
- Contextual batch toolbar that activates only after selection.
- Three-pane parsing intervention.
- Row-local lifecycle/configuration actions.
- Clear distinction between configured state and effective state.

### Keep RAG4C-specific

- Knowledge Lifeline as the primary lineage model.
- MySQL authority versus Milvus/Graph projection distinction.
- Document generation, serving generation, Chunk Head, and immutable Chunk Revision.
- Durable delete operation status instead of synchronous visual deletion.
- Parse stage waterfall and millisecond timing coverage.
- Evidence lineage, citation verification, evaluation judgments, consistency console, and dead letters.
- Explicit best-effort/catalog-only labels where projection authority is incomplete.

### Do not copy

- Tencent branding, proprietary icons, exact colors, dimensions, or component styling.
- Internal component packages or private design-token implementations.
- Optimistic states that imply external projection work has completed.

RAG4C should continue using public TDesign React primitives with its own domain components and token system.

## RAG4C implementation direction

### Document workspace

Keep the existing category and batch-management foundation, then add an asset switcher that links Documents, QA Governance, and Source Control without duplicating route state.

Recommended toolbar order:

```text
Import → Move category → Add/remove tags → Reindex → Delete → More
```

Only operations valid for every selected document should remain enabled. The UI must retain the current 100-document operation ceiling and durable-delete semantics.

### Parse intervention workspace

Create a dedicated three-pane `ParseInterventionWorkspace`:

- Left: parsed document outline, source/version facts, parser engine, page/section navigation, and stage timings.
- Center: virtualized chunk list with enabled state, revision, token/character count, parent/child relation, projection state, and validation warnings.
- Right: selected chunk editor with content diff, metadata, revision CAS, save reason, and projection impact preview.

The save flow remains authoritative:

```text
edit
→ immutable ChunkRevision
→ ChunkHead CAS
→ durable Milvus/Graph operations
→ projection monitoring
```

The UI must never report projection completion merely because the Chunk Head transaction succeeded.

### Enterprise visual direction

- Dense but readable 12-column desktop grid.
- Quiet neutral surfaces, one primary blue action, semantic lifecycle colors, and visible focus rings.
- 6–8 px control spacing in tables, 16–24 px section spacing, restrained radius, and limited shadows.
- TDesign Table, Drawer, Dialog, Tabs, Tag, Alert, Pagination, Form, Tree, and Loading as base primitives.
- Domain icons may use Morphicons where licensing and bundling are verified; Uiverse patterns may inspire micro-interactions but should not replace accessible TDesign controls.

## Next implementation slices

1. Three-pane Parse Intervention Workspace.
2. Document asset switcher and category/batch toolbar refinement.
3. Retrieval Experiment/Judgment frontend.
4. Source schedule schema and scheduler UI only after backend authority exists.
5. Read-only database and web connectors with SSRF/query-safety policy.
6. Durable delete administrator retry/recovery controls.
