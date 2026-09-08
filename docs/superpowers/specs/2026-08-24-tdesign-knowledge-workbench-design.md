# TDesign Knowledge Workbench Design

## Goal
Replace Ant Design completely with TDesign React and turn the knowledge-base area into a mature document operations workbench inspired by Tencent Yuanqi while retaining RAG4C-specific parsing, retrieval, graph, and observability capabilities.

## Product direction
- Use Tencent-style information architecture: explicit hierarchy, dense document table, selection-driven batch toolbar, guided import, status text plus icons, and a document/chunk inspection workspace.
- Keep RAG4C differentiation: parser routing, stage timings, contextual retrieval, graph statistics, run-to-chunk traceability, and operational metrics.
- Do not copy Tencent branding or private VHTML implementation. Use public `tdesign-react` and `tdesign-icons-react` with RAG4C tokens.

## UI architecture
- `AppProviders` owns TDesign locale and the light/dark DOM theme attribute.
- Shared notification helpers wrap TDesign `MessagePlugin` so pages do not depend on a provider-specific hook.
- Application-owned CSS classes carry layout and product identity. No `.t-*` selectors unless unavoidable; component internals must not become the main styling API.
- The app shell, shared components, and every page migrate before Ant Design dependencies are removed.

## Knowledge management
- Add single and batch document deletion. Running/queued documents cannot be deleted.
- Deletion cascades through graph artifacts, vector chunks, catalog rows, quota accounting, and cache epoch invalidation. Operations are idempotent and return per-document results.
- The table supports row selection, a contextual batch action bar, filters, single-row actions, and a detail drawer.
- Import remains compatible with server paths and recursive folder ingestion, but is presented as a guided workflow with source choice, selected source summary, capability hints, and settings.

## Parsing detail
- Persist adaptive stage telemetry in `parser_meta`: parse, clean, split, contextual, embed, insert, graph, total, counts, routing decision, and graph summary.
- Display sub-millisecond values in milliseconds instead of rounding to `0.00s`.
- Detail UI uses an overview plus a pipeline timing rail and chunk preview. Chunk editing is intentionally out of scope for this delivery; interfaces should leave room for it.

## Monitoring
- Add a catalog-derived document metrics endpoint that survives process restarts.
- Emit process metrics for ingest total/stages/errors so existing metrics history and Prometheus export include document ingestion.
- Add a TDesign document-ingestion section to Monitor with status KPIs, latency distribution, stage chart, engine/type distribution, slow documents, and recent failures.

## API contracts
- `DELETE /api/documents/{doc_id}` returns deletion counts or 409 when busy.
- `POST /api/documents/batch-delete` accepts `document_ids` (1..100) and returns requested/deleted/failed/not_found arrays.
- `GET /api/documents/metrics?dataset_id=default` returns summary, latency percentiles, distributions, slow documents, failures, and legacy metadata count.

## Safety and compatibility
- Preserve existing API behavior and offline demo fallbacks.
- Keep light/dark themes, keyboard access, non-color status labels, responsive layouts, Tauri compatibility, and existing run-monitor behavior.
- Do not leave `antd`, `@ant-design/icons`, or `.ant-*` source references after migration.
- This workspace has no Git metadata, so commits/worktrees are unavailable; verification evidence replaces commit checkpoints.

## Verification
- Frontend: Vitest, ESLint, TypeScript/Vite build, source scan for Ant references.
- Backend: focused deletion/telemetry/metrics tests, then full pytest through the available project runtime.
