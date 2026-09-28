from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from config.settings import EmbeddingSettings, GraphSettings
from core.graph_query_components import (
    GraphQueryAssemblyError,
    GraphQueryBackendError,
    GraphQueryEmbeddingError,
    GraphQueryTimeoutError,
    create_graph_query_component,
)
from core.graph_store_registry import GraphStoreError


class _Backend:
    mode = "fake"
    graph_query_timeout_contract = "per_call"

    def __init__(self) -> None:
        self.graph = GraphSettings()
        self.calls: list[tuple[str, str]] = []

    def search_entities(
        self,
        query_vec,
        top_k=20,
        threshold=None,
        tenant_id="",
        *,
        timeout_s=None,
    ):
        del query_vec, threshold, timeout_s
        self.calls.append(("search_entities", tenant_id))
        return [
            {
                "id": "entity-a",
                "text": "A",
                "relation_ids": ["relation-a"],
                "passage_ids": ["passage-a"],
                "distance": 0.81234,
            }
        ][:top_k]

    def search_relations(
        self,
        query_vec,
        top_k=20,
        threshold=None,
        tenant_id="",
        *,
        timeout_s=None,
    ):
        del query_vec, threshold, timeout_s
        self.calls.append(("search_relations", tenant_id))
        return [
            {
                "id": "relation-a",
                "text": "A uses B",
                "entity_ids": ["entity-a", "entity-b"],
                "passage_ids": ["passage-a"],
                "subject": "A",
                "predicate": "uses",
                "object": "B",
                "distance": 0.73456,
            }
        ][:top_k]

    def get_entities_by_ids(
        self,
        ids,
        tenant_id="",
        include_vectors=False,
        *,
        timeout_s=None,
    ):
        del include_vectors, timeout_s
        self.calls.append(("get_entities_by_ids", tenant_id))
        return [
            {
                "id": item,
                "text": item,
                "relation_ids": ["relation-a"],
                "passage_ids": ["passage-a"],
            }
            for item in ids
        ]

    def get_relations_by_ids(self, ids, tenant_id="", *, timeout_s=None):
        del timeout_s
        self.calls.append(("get_relations_by_ids", tenant_id))
        return [
            {
                "id": item,
                "text": item,
                "entity_ids": ["entity-a", "entity-b"],
                "passage_ids": ["passage-a"],
            }
            for item in ids
        ]


class _Embedder:
    graph_query_timeout_contract = "per_call"

    def embed_query(self, query: str) -> list[float]:
        return [float(len(query)), 0.0]

    def embed_query_with_timeout(self, query: str, *, timeout_s: float) -> list[float]:
        del timeout_s
        return self.embed_query(query)


def _settings() -> Any:
    return SimpleNamespace(embedding=SimpleNamespace(provider="fake"))


def test_factory_assembles_search_component_through_injected_ports() -> None:
    backend = _Backend()
    overrides: list[str | None] = []

    def store_factory(settings, *, engine_override=None):
        assert settings is not None
        overrides.append(engine_override)
        return backend

    component = create_graph_query_component(
        _settings(),
        store_factory=store_factory,
        embedder_factory=lambda settings: _Embedder(),
    )

    result = component.search(
        "A uses B",
        entity_top_k=8,
        relation_top_k=8,
        tenant_id="tenant-a",
    )

    assert overrides == ["milvus_vector_graph"]
    assert result.entities[0]["score"] == 0.8123
    assert result.relations[0]["score"] == 0.7346
    assert ("search_entities", "tenant-a") in backend.calls
    assert ("search_relations", "tenant-a") in backend.calls


def test_subgraph_component_preserves_one_hop_expansion_and_tenant_scope() -> None:
    backend = _Backend()
    component = create_graph_query_component(
        _settings(),
        store_factory=lambda settings, *, engine_override=None: backend,
        embedder_factory=lambda settings: _Embedder(),
    )

    result = component.subgraph(
        entity_ids=["entity-a"],
        relation_ids=[],
        degree=1,
        tenant_id="tenant-a",
    )

    assert result.entities[0]["id"] == "entity-a"
    assert result.relations[0]["id"] == "relation-a"
    assert ("get_entities_by_ids", "tenant-a") in backend.calls
    assert ("get_relations_by_ids", "tenant-a") in backend.calls


def test_factory_classifies_store_and_embedding_assembly_failures() -> None:
    with pytest.raises(GraphQueryAssemblyError, match="图谱存储不可用") as store_error:
        create_graph_query_component(
            _settings(),
            store_factory=lambda settings, *, engine_override=None: (_ for _ in ()).throw(
                GraphStoreError("unknown engine")
            ),
            embedder_factory=lambda settings: _Embedder(),
        )
    assert store_error.value.kind == "store"

    with pytest.raises(GraphQueryAssemblyError, match="图谱嵌入服务不可用") as embed_error:
        create_graph_query_component(
            _settings(),
            store_factory=lambda settings, *, engine_override=None: _Backend(),
            embedder_factory=lambda settings: (_ for _ in ()).throw(
                RuntimeError("embedding unavailable")
            ),
        )
    assert embed_error.value.kind == "embedding"


def test_factory_rejects_a_store_that_does_not_satisfy_the_retriever_adapter() -> None:
    with pytest.raises(GraphQueryAssemblyError, match="Adapter 契约") as error:
        create_graph_query_component(
            _settings(),
            store_factory=lambda settings, *, engine_override=None: SimpleNamespace(
                graph=GraphSettings()
            ),
            embedder_factory=lambda settings: _Embedder(),
        )
    assert error.value.kind == "adapter"


def test_default_api_embedder_timeout_is_bounded_by_graph_budget() -> None:
    settings = SimpleNamespace(
        embedding=EmbeddingSettings(
            provider="api",
            api_timeout=120.0,
            api_key="test-key",
        )
    )
    component = create_graph_query_component(
        settings,
        timeout_s=30.0,
        store_factory=lambda settings, *, engine_override=None: _Backend(),
    )

    assert getattr(component.embedder, "timeout") == 30.0


def test_default_milvus_timeout_is_bounded_in_the_settings_snapshot() -> None:
    from config.settings import MilvusSettings, Settings

    settings = Settings(
        milvus=MilvusSettings(timeout=120.0),
        embedding=EmbeddingSettings(
            provider="api",
            api_timeout=120.0,
            api_key="test-key",
        ),
    )
    seen: list[float] = []

    def store_factory(settings, *, engine_override=None):
        del engine_override
        seen.append(float(settings.milvus.timeout))
        return _Backend()

    create_graph_query_component(
        settings,
        timeout_s=30.0,
        store_factory=store_factory,
    )

    assert seen == [30.0]


def test_custom_timeout_contract_is_required_when_a_graph_budget_is_declared() -> None:
    class UnspecifiedBackend(_Backend):
        graph_query_timeout_contract = None

    with pytest.raises(GraphQueryAssemblyError, match="timeout contract") as error:
        create_graph_query_component(
            _settings(),
            timeout_s=30.0,
            store_factory=lambda settings, *, engine_override=None: UnspecifiedBackend(),
            embedder_factory=lambda settings: _Embedder(),
        )
    assert error.value.kind == "timeout_contract"


def test_soft_timeout_contract_is_explicit_for_non_interruptible_providers() -> None:
    class SoftBackend(_Backend):
        graph_query_timeout_contract = "soft"

    class SoftEmbedder(_Embedder):
        graph_query_timeout_contract = "soft"

    component = create_graph_query_component(
        _settings(),
        timeout_s=30.0,
        store_factory=lambda settings, *, engine_override=None: SoftBackend(),
        embedder_factory=lambda settings: SoftEmbedder(),
    )

    assert component.store.graph_query_timeout_contract == "soft"
    assert component.embedder.graph_query_timeout_contract == "soft"


def test_component_classifies_request_time_embedding_and_backend_failures() -> None:
    class BrokenEmbedder:
        def embed_query(self, query):
            raise RuntimeError("embedding timeout")

    backend = _Backend()
    component = create_graph_query_component(
        _settings(),
        store_factory=lambda settings, *, engine_override=None: backend,
        embedder_factory=lambda settings: BrokenEmbedder(),
    )
    with pytest.raises(GraphQueryEmbeddingError, match="嵌入"):
        component.search("query", entity_top_k=1, relation_top_k=1)

    class BrokenBackend(_Backend):
        def search_entities(self, *args, **kwargs):
            raise RuntimeError("milvus unavailable")

    component = create_graph_query_component(
        _settings(),
        store_factory=lambda settings, *, engine_override=None: BrokenBackend(),
        embedder_factory=lambda settings: _Embedder(),
    )
    with pytest.raises(GraphQueryBackendError, match="存储"):
        component.search("query", entity_top_k=1, relation_top_k=1)


def test_component_stops_before_a_new_provider_call_when_budget_is_exhausted() -> None:
    from core.retry import deadline

    component = create_graph_query_component(
        _settings(),
        store_factory=lambda settings, *, engine_override=None: _Backend(),
        embedder_factory=lambda settings: _Embedder(),
    )

    with deadline(seconds=0):
        with pytest.raises(GraphQueryTimeoutError, match="预算已耗尽"):
            component.search("query", entity_top_k=1, relation_top_k=1)


def test_graph_routes_depend_on_the_component_port_not_raw_backend_calls() -> None:
    source = Path(__file__).resolve().parents[1] / "server" / "app.py"
    text = source.read_text(encoding="utf-8")
    search_start = text.index('@app.post("/api/graph/search")')
    subgraph_start = text.index('@app.get("/api/graph/subgraph")')
    eval_start = text.index("# ---------------------------------------------------------------------------\n# 评测端点", subgraph_start)
    graph_routes = text[search_start:eval_start]

    assert "_get_graph_query_component()" in graph_routes
    assert "embedder.embed_query" not in graph_routes
    assert "store.search_entities" not in graph_routes
    assert "store.search_relations" not in graph_routes
    assert "store.get_entities_by_ids" not in graph_routes
    assert "store.get_relations_by_ids" not in graph_routes
    assert "from functools import lru_cache" not in text
    assert "_graph_components_cache" in text
    assert graph_routes.index("with _graph_gate():") < graph_routes.index(
        "component = _get_graph_query_component()"
    )
    assert "GraphQueryAssemblyError" in text
    assert "图谱兼容 tuple 装配失败" in text
