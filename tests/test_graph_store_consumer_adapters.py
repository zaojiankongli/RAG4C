from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from config.settings import GraphSettings
from core.graph_projection_adapters import (
    GRAPH_COLLECTION_INITIALIZATION_REQUIRED_METHODS,
    GRAPH_BUILDER_DELETE_REQUIRED_METHODS,
    GraphProjectionAdapterError,
    GraphProjectionAdapterFactory,
    create_graph_projection_adapter,
)
from core.graph_store_registry import (
    GraphStoreRegistry,
    NoopGraphStore,
    create_graph_projection_consumer,
)
from indexing.graph_builder import GraphBuilder
from models.schemas import Triplet
from retrieval.graph_retriever import GraphRetriever


class _Backend:
    mode = "fake"

    def __init__(self) -> None:
        self.graph = GraphSettings(
            batch_size=2,
            expansion_degree=0,
            final_top_k=2,
            use_llm_rerank=False,
        )
        self.calls: list[tuple[str, Any]] = []

    def ensure_collections(self) -> None:
        self.calls.append(("ensure_collections", None))

    def healthy(self) -> bool:
        return True

    def upsert_entities(self, entities, vectors, tenant_id=""):
        self.calls.append(("upsert_entities", tenant_id))
        return [entity.id for entity in entities]

    def upsert_relations(self, relations, vectors, tenant_id=""):
        self.calls.append(("upsert_relations", tenant_id))
        return [relation.id for relation in relations]

    def search_entities(self, query_vec, top_k=20, threshold=None, tenant_id=""):
        self.calls.append(("search_entities", tenant_id))
        return []

    def search_relations(self, query_vec, top_k=20, threshold=None, tenant_id=""):
        self.calls.append(("search_relations", tenant_id))
        return [
            {
                "id": "relation-a",
                "text": "A uses B",
                "entity_ids": [],
                "passage_ids": ["passage-a"],
                "distance": 0.8,
            }
        ]

    def get_entities_by_ids(self, ids, tenant_id="", include_vectors=False):
        self.calls.append(("get_entities_by_ids", (tenant_id, include_vectors)))
        return []

    def get_relations_by_ids(self, ids, tenant_id=""):
        self.calls.append(("get_relations_by_ids", tenant_id))
        return []

    def get_entities_by_passage_ids(self, ids, tenant_id="", include_vectors=False):
        self.calls.append(("get_entities_by_passage_ids", (tenant_id, include_vectors)))
        return []

    def get_relations_by_passage_ids(self, ids, tenant_id="", include_vectors=False):
        self.calls.append(("get_relations_by_passage_ids", (tenant_id, include_vectors)))
        return []

    def get_entities_by_texts(self, texts, tenant_id=""):
        self.calls.append(("get_entities_by_texts", tenant_id))
        return {}

    def get_relations_by_texts(self, texts, tenant_id=""):
        self.calls.append(("get_relations_by_texts", tenant_id))
        return {}

    def upsert_raw_entities(self, rows, tenant_id=""):
        self.calls.append(("upsert_raw_entities", (len(rows), tenant_id)))

    def upsert_raw_relations(self, rows, tenant_id=""):
        self.calls.append(("upsert_raw_relations", (len(rows), tenant_id)))

    def delete_entities_by_ids(self, ids, tenant_id=""):
        self.calls.append(("delete_entities_by_ids", (list(ids), tenant_id)))
        return len(ids)

    def delete_relations_by_ids(self, ids, tenant_id=""):
        self.calls.append(("delete_relations_by_ids", (list(ids), tenant_id)))
        return len(ids)


class _Embedder:
    def embed_texts(self, texts):
        return [[0.1, 0.2] for _ in texts]

    def embed_query(self, query):
        return [0.1, 0.2]


class _Extractor:
    def extract(self, text):
        return [Triplet(subject="A", predicate="uses", object="B")]


def test_adapter_rejects_malformed_backend_before_consumer_side_effects() -> None:
    class Broken:
        graph = SimpleNamespace(batch_size=2)

    with pytest.raises(GraphProjectionAdapterError, match="missing operational methods"):
        GraphProjectionAdapterFactory.create(Broken())


def test_reusing_a_partially_validated_adapter_rechecks_consumer_requirements() -> None:
    class RetrievalBroken(_Backend):
        search_entities = None

    adapter = GraphProjectionAdapterFactory.create(RetrievalBroken(), required_methods=())
    with pytest.raises(GraphProjectionAdapterError, match="search_entities"):
        GraphProjectionAdapterFactory.create(
            adapter,
            required_methods=("search_entities", "search_relations"),
        )


def test_adapter_forwards_tenant_and_include_vectors_contract() -> None:
    backend = _Backend()
    adapter = create_graph_projection_adapter(backend)

    adapter.get_entities_by_ids(["entity-a"], tenant_id="tenant-a", include_vectors=True)
    adapter.get_relations_by_passage_ids(
        ["passage-a"], tenant_id="tenant-a", include_vectors=True
    )

    assert ("get_entities_by_ids", ("tenant-a", True)) in backend.calls
    assert ("get_relations_by_passage_ids", ("tenant-a", True)) in backend.calls

    adapter.upsert_raw_entities([], tenant_id="tenant-a")
    adapter.delete_entities_by_ids([], tenant_id="tenant-a")
    assert ("upsert_raw_entities", (0, "tenant-a")) in backend.calls
    assert ("delete_entities_by_ids", ([], "tenant-a")) in backend.calls


def test_graph_builder_uses_adapter_for_real_write_path() -> None:
    backend = _Backend()
    builder = GraphBuilder(backend, _Embedder(), _Extractor(), extract_concurrency=1)
    plan = builder.prepare_build([("chunk-a", "content")])
    result = builder.write_prepared(plan, tenant_id="tenant-a")

    assert result.entity_count == 2
    assert builder.store is backend
    assert builder.adapter.backend is backend
    assert ("get_entities_by_texts", "tenant-a") in backend.calls
    assert ("upsert_entities", "tenant-a") in backend.calls
    assert ("upsert_relations", "tenant-a") in backend.calls

    builder.delete_by_chunk_ids(["chunk-a"], tenant_id="tenant-a")
    assert ("upsert_raw_relations", (0, "tenant-a")) in backend.calls
    assert ("delete_relations_by_ids", ([], "tenant-a")) in backend.calls
    assert ("upsert_raw_entities", (0, "tenant-a")) in backend.calls
    assert ("delete_entities_by_ids", ([], "tenant-a")) in backend.calls


def test_graph_retriever_uses_adapter_for_real_retrieval_path() -> None:
    backend = _Backend()
    retriever = GraphRetriever(backend, _Embedder())
    result = retriever.retrieve("A uses B", tenant_id="tenant-a")

    assert result.passage_ids == ["passage-a"]
    assert ("search_entities", "tenant-a") in backend.calls
    assert ("search_relations", "tenant-a") in backend.calls
    assert retriever.store is backend
    assert retriever.adapter.backend is backend


def test_registry_exposes_an_adapted_active_store_and_noop_is_healthy() -> None:
    registry = GraphStoreRegistry()
    registry.register("none", NoopGraphStore())
    registry.set_active("none")

    adapter = registry.get_active_projection_adapter()
    assert adapter.healthy() is True
    assert adapter.graph.batch_size > 0
    assert adapter.search_entities([0.1], tenant_id="tenant-a") == []
    assert adapter.delete_entities_by_ids([], tenant_id="tenant-a") == 0
    assert create_graph_projection_consumer(
        SimpleNamespace(graph=SimpleNamespace(enabled=False))
    ).healthy() is True
    with pytest.raises(Exception, match="not wired"):
        create_graph_projection_consumer(
            SimpleNamespace(graph=SimpleNamespace(enabled=True, engine="unknown"))
        )


def test_malformed_destructive_signature_is_rejected_before_operation() -> None:
    class BrokenDelete(_Backend):
        def delete_entities_by_ids(self, ids):
            return len(ids)

    with pytest.raises(GraphProjectionAdapterError, match="delete_entities_by_ids"):
        GraphProjectionAdapterFactory.create(
            BrokenDelete(),
            required_methods=GRAPH_BUILDER_DELETE_REQUIRED_METHODS,
        )


def test_bypassing_adapter_seam_fails_before_graph_write(monkeypatch: pytest.MonkeyPatch) -> None:
    import indexing.graph_builder as graph_builder_module

    backend = _Backend()
    original = graph_builder_module.create_graph_projection_adapter
    monkeypatch.setattr(
        graph_builder_module,
        "create_graph_projection_adapter",
        lambda _backend, **_kwargs: backend,
    )
    try:
        builder = GraphBuilder(backend, _Embedder(), _Extractor(), extract_concurrency=1)
        plan = builder.prepare_build([("chunk-a", "content")])
        with pytest.raises(AttributeError, match="require_methods"):
            builder.write_prepared(plan, tenant_id="tenant-a")
        assert not any(name.startswith("upsert_") for name, _ in backend.calls)
    finally:
        monkeypatch.setattr(graph_builder_module, "create_graph_projection_adapter", original)


def test_document_ingest_collection_initialization_uses_the_adapter() -> None:
    from server import documents

    backend = _Backend()
    pipeline = SimpleNamespace(
        graph_builder=SimpleNamespace(
            adapter=create_graph_projection_adapter(backend, required_methods=())
        )
    )
    documents._ensure_graph_collections(pipeline)

    assert ("ensure_collections", None) in backend.calls


def test_document_ingest_collection_initialization_adapts_legacy_store_attribute() -> None:
    from server import documents

    class LegacyCollectionStore:
        def __init__(self) -> None:
            self.calls = 0

        def ensure_collections(self) -> None:
            self.calls += 1

    store = LegacyCollectionStore()
    pipeline = SimpleNamespace(graph_builder=SimpleNamespace(store=store))

    documents._ensure_graph_collections(pipeline)

    assert store.calls == 1
    assert GRAPH_COLLECTION_INITIALIZATION_REQUIRED_METHODS == frozenset(
        {"ensure_collections"}
    )


def test_raw_graph_rewrite_rejects_unscoped_rows_for_tenant() -> None:
    from core.graph_store import RagGraphStore, RagGraphStoreError

    class Client:
        def __init__(self) -> None:
            self.calls = 0

        def upsert(self, **_kwargs: Any) -> None:
            self.calls += 1

    store = RagGraphStore.__new__(RagGraphStore)
    store.config = SimpleNamespace(entity_collection="entities")
    store.graph = SimpleNamespace(batch_size=2)
    store._client = Client()

    with pytest.raises(RagGraphStoreError, match="租户范围"):
        store.upsert_raw_entities([{"id": "entity-a"}], tenant_id="tenant-a")

    assert store._client.calls == 0


def test_collection_adapter_does_not_require_graph_settings() -> None:
    class Store:
        def ensure_collections(self) -> None:
            return None

    adapter = GraphProjectionAdapterFactory.create(
        Store(),
        required_methods=GRAPH_COLLECTION_INITIALIZATION_REQUIRED_METHODS,
        require_graph=False,
    )

    adapter.ensure_collections()


def test_invalid_raw_graph_rewrite_does_not_initialize_client() -> None:
    from core.graph_store import RagGraphStore, RagGraphStoreError

    class Store(RagGraphStore):
        def __init__(self) -> None:
            self.config = SimpleNamespace(entity_collection="entities")
            self.graph = SimpleNamespace(batch_size=2)
            self._client = None
            self.client_initializations = 0

        def _ensure_client(self) -> Any:
            self.client_initializations += 1
            raise AssertionError("client must not initialize for invalid tenant rows")

    store = Store()

    with pytest.raises(RagGraphStoreError, match="租户范围"):
        store.upsert_raw_entities([{"id": "entity-a"}], tenant_id="tenant-a")

    assert store.client_initializations == 0
