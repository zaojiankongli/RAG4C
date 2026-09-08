# RAG4C KnowledgeOps Database and Product Design

**Date:** 2026-08-24  
**Status:** Approved by delegated authority; implementation may proceed in phases.

## 1. Goal

Design and implement an original enterprise knowledge platform using verified lessons from Tencent-style knowledge products and RAG4C's existing strengths. The result must not be a visual clone. It must make RAG4C's evidence, revision, parsing, graph, citation, and observability capabilities first-class product concepts.

## 2. Current-state evidence

- Catalog schema is current at `0007_chunk_rev`.
- Runtime rollout modes are `schema_mode=verify`, `ingest_ledger_mode=shadow`, `chunk_authority_mode=shadow`, `sources.state_mode=dual`.
- Current MySQL counts: 97 documents; 0 chunk heads; 0 chunk revisions; 0 data sources; 0 source sync runs; 0 ingest attempts; 0 index operations; 0 dead letters.
- The frontend has ten navigation pages but no shared knowledge-base data module; multiple kept-alive pages own separate document snapshots.
- Production request failures currently fall back to demo documents in several knowledge pages.
- Chunk mutation currently writes Milvus and graph storage directly instead of using `ChunkCatalog` and durable operations.
- The repository now has a local Git baseline commit `d24bd39`.

## 3. Product identity

Product name: **RAG4C KnowledgeOps**.

Product promise: **a full-lifecycle trusted knowledge-engineering platform**.

Signature interaction: **Knowledge Lifeline** — source → parser → document version → chunk revision → search/graph projection → retrieval → citation → answer → evaluation.

## 4. Information architecture

### Knowledge Space
- Knowledge-base list and switcher
- Current knowledge base
  - Overview
  - Content: documents, QA knowledge, structured knowledge, chunks
  - Governance: folders, tags, metadata schema, permissions, lifecycle
  - Sources: connectors, schedules, sync runs, failures
  - Processing: ingest batches, attempts, spans, index operations, dead letters
  - Retrieval Lab: pure retrieval, strategy comparison, judgments
  - Consistency Console: revision drift and repair

### Quality and Analysis
- Knowledge Q&A
- Run Trace
- Evaluation
- Quality Insights
- Trusted-answer Operations

### System Operations
- Health and metrics
- Models and pipelines
- Members and roles
- Audit log
- Settings

## 5. Target data model

### Reuse and deepen existing modules

- `Dataset` remains the physical Knowledge Base row and gains product profile, owner, policy, visibility, and archival fields.
- `Document` remains stable identity and points to current document version and folder.
- `ChunkHead` and `ChunkRevision` become the only authoritative chunk-content module.
- `DataSourceRecord`, `SourceSyncRun`, `SourceSyncItem`, and `SourceDocumentState` become the source-truth module.
- `DocumentIngestAttempt`, `DocumentIngestSpan`, `IndexOperation`, and `IndexDeadLetter` become the processing-truth module.

### New authoritative entities

1. `knowledge_folders`
   - tenant_id, dataset_id, parent_id, name, path, description, sort_order, created_by, timestamps
2. `knowledge_tags`
   - tenant_id, dataset_id, name, color, description, created_by, timestamps
3. `document_tags`
   - document_id, tag_id, created_by, created_at
4. `document_versions`
   - document_id, revision, source identity/hash, parser-policy snapshot, parser metadata, source-content reference, created_by, change reason, timestamps
5. `qa_knowledge`
   - dataset_id, question, answer, status, review status, lifecycle, source link, creator/reviewer, metadata
6. `qa_alternative_questions`
   - qa_id, question, created_at
7. `knowledge_audit_events`
   - actor, tenant/dataset, action, resource, before/after snapshots, request identity, occurred_at
8. `retrieval_experiments` and `retrieval_judgments`
   - query, strategy snapshot, result snapshot, latency, relevance labels, creator, timestamps

### Transitional fields

- `documents.logical_folder_path` remains readable during folder backfill.
- `parser_meta.management.tags` remains readable during tag backfill.
- Both become compatibility projections after authoritative tables are active.

## 6. Storage ownership

- MySQL: identity, lifecycle, versions, operations, audit, permissions, source truth.
- Milvus: revision-tagged retrieval projection only.
- Graph store: revision-tagged graph projection only.
- Redis: locks, queues, caches, rate limits.
- SQLite: local/Tauri development and optional run-history deployment.

## 7. Core workflows

### Ingest
Source/upload → DocumentVersion → IngestAttempt/Spans → ChunkHeads → IndexOperations → Milvus/Graph projection → revision-fenced completion → ready.

### Chunk edit
Read head revision → submit expected revision → save immutable old revision → update head → enqueue operations → project → mark indexed/graph revision. Conflict returns HTTP 409.

### Delete
Mark deletion requested → enqueue durable delete operations → project removals → finalize Catalog state and usage → audit. Partial failures become retryable operations/dead letters.

### Expiry
Active → expiring → expired. Expired knowledge is excluded from retrieval but retained until retention removal.

### QA generation
Generated draft → pending review → active or rejected. Only active QA is projected.

## 8. Security and error handling

- All operator APIs bind current identity, tenant, dataset, and role.
- Unknown/cross-tenant resources use fail-closed 404/403 behavior.
- Database connectors are read-only, table allowlisted, timeout bounded, and result capped.
- Production mode never silently substitutes demo data for 401/403/404/5xx/network failures.
- Every mutation emits an audit event.

## 9. Frontend architecture

Create a deep `KnowledgeWorkspaceProvider` module exposing one interface:
- current knowledge base
- knowledge bases
- documents/folders/tags/sources/permissions
- explicit loading/error/capability/demo state
- refresh/invalidate/mutation helpers

Pages consume projections from this module instead of owning duplicate request snapshots.

Split large files by domain locality:
- documents: facet rail, table, import wizard, settings dialog, inspector, chunk list/editor, parse pipeline
- observability: run attention, query performance, ingest health, infrastructure, circuit breaker, metrics
- retrieval lab: composer, controls, evidence, citation audit, comparison, save-to-eval

## 10. Rollout

### P0 Governance
- Git baseline and design artifacts
- no uncoordinated same-file parallel writes

### P1 Authority and consistency
- chunk mutations through ChunkCatalog + IndexOperation
- revision conflicts and audit
- durable deletion
- explicit demo semantics

### P2 Shadow migration
- backfill 97 documents into ChunkHeads
- source-ledger backfill where evidence exists
- hash/revision reconcile reports
- stay shadow until drift is zero

### P3 Governance domain
- folders, tags, document versions, QA, expiry, audit migrations and APIs

### P4 Source operations
- connector CRUD, sync schedules/runs/items, retry/dead letter, safe web and read-only database connectors

### P5 Quality center
- pure retrieval API, A/B experiments, evidence lineage, quality and consistency console

## 11. Release gates

- Full pytest, Vitest, ESLint, TypeScript/Vite build
- Playwright core flows and axe audit
- Alembic upgrade/downgrade drills
- tenant isolation and mutation authorization tests
- ChunkHead/Milvus/graph reconcile drift = 0 before active mode
- source ledger and ingest ledger contain real shadow facts
- Git worktree clean and reviewed diff
