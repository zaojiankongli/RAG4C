# Projection target producer registry design (2026-09-23)

## Decision

Replace `DocumentIngestJob._enqueue_projection_intent`'s hard-coded `stores = ["milvus_chunks"]` / `if include_graph` list and target-specific dedup-key ternary with registered per-store `ProjectionTargetPolicy` specs in `core/projection_target_producers.py`, using the existing `core.providers.ProviderRegistry`.

Each policy declares an order, a stable dedup-key component, and a synchronous `enabled(context)` predicate. Built-ins preserve today's order and inclusion: Milvus is always emitted first; graph is emitted only when the existing `include_graph` decision is true. The worker's producer loop consumes the registered specs generically. A third target can be added by registering one policy; `DocumentIngestJob` itself requires no store-specific branch.

## Boundaries

- Keep the public `graph` override and pipeline capability calculation untouched.
- Keep operation kind `upsert`, payload, attempt/document/dataset scope, writer permit generations, transaction/session, shadow status, and dedup-key values unchanged for built-ins.
- No durable delete, revision, attempt policy, consistency projection, schema, or OpenAPI changes in this slice.
- A new producer policy alone does not make a target end-to-end supported: handler, revision strategy, attempt lifecycle policy, delete coverage, and consistency/readiness facts remain separate requirements.

## Proof

Use real `DocumentIngestJob.run` integration tests for default targets, graph-enabled ordering, and a dynamically registered third store. Add registration validation and a source guard for no store literal branch in `_enqueue_projection_intent`. After green regression and independent subagent review, temporarily break registry enumeration and confirm the dynamic public ingest test fails; restore the source byte-for-byte.
