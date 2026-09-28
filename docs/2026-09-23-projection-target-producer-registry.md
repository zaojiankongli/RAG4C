# Projection target producer registry handoff (2026-09-23)

## What changed

`core/projection_target_producers.py` now stores immutable per-target producer policies in the existing `core.providers.ProviderRegistry`. A policy declares deterministic enqueue order, stable dedup identity and a synchronous predicate over `ProjectionTargetProductionContext` (`include_graph`). Built-in contracts are immutable: `milvus_chunks` remains required and always enabled with legacy dedup suffix `milvus`; `graph_projection` remains optional and follows the existing `include_graph` decision with legacy suffix `graph`.

`DocumentIngestJob` resolves and validates the complete target snapshot before persisting chunk authority or enqueuing outbox work. The target producer host contains no built-in target-store values or graph conditional. Operation kind, payload, writer-permit generations, attempt/document/dataset scope, session use, shadow/pending status and existing built-in dedup values are preserved.

New target identity is stable: custom `dedup_key_component` must be a native `str` exactly equal to its canonical `target_store`. `milvus` and `graph` are permanently reserved legacy aliases; built-in policies cannot be replaced or unregistered. Enumeration checks built-in policy identity/fields and required registration. Policies are resolved once in a locked snapshot and that same snapshot is used for validation and target selection, preventing stateful factories or callback equality overloads from bypassing checks.

## Boundaries

A producer policy alone is not an executable target plugin. The runtime preflight requires registered `upsert`, `reconcile`, `delete`, and `delete_document` handlers, a revision strategy, an attempt lifecycle policy, coverage by the durable-delete barrier, and the consistency/requeue operation contract. `ProjectionHandlers.as_mapping()` now exposes registered target handlers to the worker without a target-specific host edit. Durable-delete stores are policy-registered; custom activation uses `register_projection_delete_target_runtime`, and delete requests rerun its runtime check before changing authority.

The default durable-delete target set remains Milvus and Graph. An otherwise valid `custom_vector` producer is rejected before any outbox operation until worker/revision/lifecycle, consistency/requeue, and durable-delete policies are all registered. Read-side consistency projection remains a separate open boundary.

## Verification

- Real `DocumentIngestJob.run` integration: graph-off emits Milvus only and graph-on enqueues Milvus then Graph. A registered but incomplete `custom_vector` is rejected before enqueue; a regression asserts no outbox rows are left behind.
- A real `IndexOperationWorker.run_once` regression proves a fully registered custom target handler/revision/lifecycle path can dispatch and complete without a host branch. A durable-delete integration test proves a custom target is persisted as a child and remains part of the finalizer barrier after deregistration; incomplete runtime configuration is rejected before the request mutates state.
- Registration/runtime guards cover callback shape, canonical identity, duplicate target registration, reserved aliases, unregister/reuse attempts, built-in replacement/removal, direct registry tampering, string-subclass spoofing and stateful factory double resolution.
- A source guard confirms target selection has no built-in target literals or `if include_graph` branch, and the runtime preflight is invoked before authority writes/enqueue.
- Relevant regression command:

```text
pytest -q tests/test_projection_target_producers.py tests/test_projection_handlers.py tests/test_index_worker.py tests/test_document_delete_worker_v2.py
```

Focused/full results and reverse validation are recorded in the 2026-09-24 runtime-preflight and durable-delete target handoffs.

## Independent review

Earlier independent review found and helped close dedup alias reuse after unregister, non-string/`str`-subclass component spoofing, built-in policy replacement/removal, callback equality overload, and stateful factory double resolution. Later reviews identified incomplete worker and delete/consistency paths; the 2026-09-24 runtime preflight, shared contract, and ordered delete-target registry address those boundaries. Follow-up review results are recorded in the corresponding handoffs.
