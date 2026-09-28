from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from core.embedding import ApiEmbedder
from core.graph_query_components import (
    GraphQueryAssemblyError,
    create_graph_query_component,
)
from core.graph_store import RagGraphStore
from core.graph_store_registry import NoopGraphStore
from core.retry import deadline


class _TimeoutBackend:
    mode = "timeout-fake"
    graph_query_timeout_contract = "per_call"

    def __init__(self) -> None:
        self.graph = SimpleNamespace()
        self.timeouts: list[float] = []

    def _record(self, timeout_s: float | None) -> None:
        assert timeout_s is not None
        self.timeouts.append(timeout_s)

    def search_entities(
        self,
        query_vec,
        top_k=20,
        threshold=None,
        tenant_id="",
        *,
        timeout_s=None,
    ):
        del query_vec, top_k, threshold, tenant_id
        self._record(timeout_s)
        return []

    def search_relations(
        self,
        query_vec,
        top_k=20,
        threshold=None,
        tenant_id="",
        *,
        timeout_s=None,
    ):
        del query_vec, top_k, threshold, tenant_id
        self._record(timeout_s)
        return []

    def get_entities_by_ids(
        self,
        ids,
        tenant_id="",
        include_vectors=False,
        *,
        timeout_s=None,
    ):
        del ids, tenant_id, include_vectors
        self._record(timeout_s)
        return []

    def get_relations_by_ids(self, ids, tenant_id="", *, timeout_s=None):
        del ids, tenant_id
        self._record(timeout_s)
        return []


class _TimeoutEmbedder:
    graph_query_timeout_contract = "per_call"

    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def embed_query(self, query):
        return [float(len(query))]

    def embed_query_with_timeout(self, query, *, timeout_s):
        del query
        self.timeouts.append(timeout_s)
        return [1.0]


def _settings() -> SimpleNamespace:
    return SimpleNamespace(embedding=SimpleNamespace(provider="fake"))


def test_per_call_providers_receive_remaining_budget_for_each_operation() -> None:
    backend = _TimeoutBackend()
    embedder = _TimeoutEmbedder()
    component = create_graph_query_component(
        _settings(),
        timeout_s=10.0,
        store_factory=lambda settings, *, engine_override=None: backend,
        embedder_factory=lambda settings: embedder,
    )

    with deadline(seconds=1.0):
        component.search(
            "query",
            entity_top_k=1,
            relation_top_k=1,
        )

    assert len(embedder.timeouts) == 1
    assert len(backend.timeouts) == 2
    assert all(0 < value <= 1.0 for value in embedder.timeouts + backend.timeouts)


def test_per_call_store_must_expose_timeout_aware_adapter_methods() -> None:
    class LegacyDeclaredBackend(_TimeoutBackend):
        def search_entities(self, query_vec, top_k=20, threshold=None, tenant_id=""):
            return super().search_entities(
                query_vec,
                top_k,
                threshold,
                tenant_id,
                timeout_s=None,
            )

    with pytest.raises(GraphQueryAssemblyError, match="per_call"):
        create_graph_query_component(
            _settings(),
            timeout_s=10.0,
            store_factory=lambda settings, *, engine_override=None: LegacyDeclaredBackend(),
            embedder_factory=lambda settings: _TimeoutEmbedder(),
        )


def test_milvus_graph_store_passes_remaining_budget_to_each_sdk_call() -> None:
    class FakeMilvus:
        def __init__(self) -> None:
            self.search_timeouts: list[float] = []

        def search(self, **kwargs):
            self.search_timeouts.append(float(kwargs["timeout"]))
            return [[]]

    client = FakeMilvus()
    store = RagGraphStore.__new__(RagGraphStore)
    store.config = SimpleNamespace(
        entity_collection="entities",
        index_type="HNSW",
        metric_type="COSINE",
        ef=32,
        timeout=120.0,
    )
    store.graph = SimpleNamespace()
    store._client = client

    with deadline(seconds=1.0):
        assert store.search_entities([0.1], top_k=1, threshold=0.0, timeout_s=0.25) == []

    assert client.search_timeouts == [0.25]


def test_noop_graph_store_implements_the_per_call_read_port() -> None:
    component = create_graph_query_component(
        _settings(),
        timeout_s=10.0,
        store_factory=lambda settings, *, engine_override=None: NoopGraphStore(),
        embedder_factory=lambda settings: _TimeoutEmbedder(),
    )

    result = component.search("query", entity_top_k=1, relation_top_k=1)

    assert result.as_payload() == {"entities": [], "relations": []}


def test_api_embedder_disables_nested_retries_and_can_lower_timeout_per_attempt(
    monkeypatch,
) -> None:
    class FakeEmbeddings:
        def create(self, **kwargs):
            del kwargs
            return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1, 0.2])])

    class FakeOpenAI:
        instances: list["FakeOpenAI"] = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.embeddings = FakeEmbeddings()
            self.options: list[dict[str, object]] = []
            type(self).instances.append(self)

        def with_options(self, **kwargs):
            self.options.append(kwargs)
            child = type(self)(**{**self.kwargs, **kwargs})
            return child

    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(OpenAI=FakeOpenAI),
    )
    embedder = ApiEmbedder(
        model="fake",
        base_url="http://example.test",
        api_key="key",
        timeout=120.0,
    )

    assert embedder.embed_query_with_timeout("query", timeout_s=7.0) == [0.1, 0.2]
    assert FakeOpenAI.instances[0].kwargs["max_retries"] == 0
    assert any(
        options.get("timeout") == 7.0 and options.get("max_retries") == 0
        for options in FakeOpenAI.instances[0].options
    )
