# RAG4C Domain Context

## Product thesis

RAG4C KnowledgeOps is a trusted knowledge-engineering platform. Its defining experience is the **Knowledge Lifeline**: every answer can be traced backward through citation, retrieval, index projection, chunk revision, document version, parser run, and source identity.

## Domain language

- **Knowledge Base**: a tenant-scoped governed collection of knowledge, currently represented by `Dataset`.
- **Knowledge Lifeline**: the source → parse → chunk → project → retrieve → cite → answer → evaluate lineage.
- **Document Identity**: the stable logical document regardless of uploaded or synchronized versions.
- **Document Version**: an immutable source-content snapshot and parser-policy snapshot.
- **Chunk Head**: the authoritative current chunk content in MySQL.
- **Chunk Revision**: an immutable prior chunk content snapshot.
- **Projection**: a derived Milvus or graph representation of an authoritative revision.
- **Projection Fence**: the expected revision check that prevents stale work from overwriting newer data.
- **Index Operation**: durable work required to project authoritative state into Milvus or graph storage.
- **Knowledge Source**: a configured connector that owns sync cursor, runs, items, and document identity.
- **Ingest Attempt**: one durable document processing attempt with stage spans.
- **Knowledge Folder**: a first-class hierarchical organizer, not a path string embedded in a document.
- **Knowledge Tag**: a governed reusable label, not an unvalidated JSON string.
- **QA Knowledge**: a governed question/answer record with alternatives, review status, and lifecycle.
- **Evidence Chain**: the set of retrieved chunk revisions and verification results supporting an answer.
- **Consistency Console**: the operator view of desired/indexed/graph revisions, operations, drift, and dead letters.

## Single sources of truth

- MySQL Catalog owns identity, lifecycle, versions, operations, audit, and permissions.
- Milvus owns no business truth; it is a search projection.
- Graph storage owns no business truth; it is a graph projection.
- Redis owns only bounded transient state.
- Run History is operational evidence and must not be confused with knowledge truth.

## Invariants

1. A manual chunk edit must create an immutable revision before changing the head.
2. Projection writes must be revision fenced.
3. Deletes are durable operations, not synchronous best-effort multi-store transactions.
4. Production failures are visible; demo data appears only in explicit demo mode.
5. Every mutating operator action is tenant scoped and audited.
6. Knowledge expiration disables retrieval but preserves data until an explicit retention policy removes it.
7. Automatic QA generation requires review before becoming retrievable.
