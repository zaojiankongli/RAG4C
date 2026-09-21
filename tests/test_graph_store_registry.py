from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.graph_store_registry import (
    GraphStoreError,
    GraphStoreFactory,
    GraphStoreRegistry,
    NoopGraphStore,
    get_graph_store,
    reset_registry_for_tests,
    resolve_graph_engine_from_settings,
)


class _MilvusSettings:
    def __init__(self, enabled=True, engine="milvus_vector_graph"):
        self.milvus = SimpleNamespace(uri="http://127.0.0.1:19530")
        self.graph = SimpleNamespace(enabled=enabled, engine=engine, entity_collection="e", relation_collection="r")


def test_resolve_engine_none_when_disabled_or_missing():
    assert resolve_graph_engine_from_settings(None) == "none"
    assert resolve_graph_engine_from_settings(SimpleNamespace()) == "none"
    assert resolve_graph_engine_from_settings(SimpleNamespace(graph=SimpleNamespace(enabled=False))) == "none"


def test_resolve_engine_milvus_when_enabled():
    assert resolve_graph_engine_from_settings(_MilvusSettings()) == "milvus_vector_graph"


def test_resolve_unknown_engine_raises():
    with pytest.raises(GraphStoreError, match="not wired"):
        resolve_graph_engine_from_settings(_MilvusSettings(engine="nebula"))


def test_factory_noop_and_unknown():
    store = GraphStoreFactory.create("none")
    assert isinstance(store, NoopGraphStore)
    assert store.healthy() is True
    assert store.search_entities([0.1]) == []
    store.ensure_collections()
    store.upsert_entities([], [])
    store.delete_by_chunk_ids([])
    with pytest.raises(GraphStoreError, match="not wired"):
        GraphStoreFactory.create("neo4j", _MilvusSettings())


def test_registry_and_resolve_are_single_active_backend():
    reset_registry_for_tests()
    reg = GraphStoreRegistry()
    none_store = GraphStoreFactory.create("none")
    reg.register("none", none_store)
    reg.set_active("none")
    assert reg.get_active() is none_store
    # 未注册引擎不可 activate（互斥装配）
    with pytest.raises(GraphStoreError):
        reg.set_active("milvus_vector_graph")
    # disabled settings → process store is noop
    s = get_graph_store(SimpleNamespace(graph=SimpleNamespace(enabled=False)), registry=reg)
    assert isinstance(s, NoopGraphStore)
    assert reg.active_engine_type == "none"


def test_get_graph_store_uses_settings(monkeypatch):
    reset_registry_for_tests()
    store = get_graph_store(SimpleNamespace(graph=SimpleNamespace(enabled=False)))
    assert isinstance(store, NoopGraphStore)
    # milvus path requires real RagGraphStore construction — patch factory
    def _fake_create(engine, settings=None):
        return NoopGraphStore()

    monkeypatch.setattr("core.graph_store_registry.GraphStoreFactory.create", staticmethod(_fake_create))
    reset_registry_for_tests()
    s2 = get_graph_store(_MilvusSettings())
    assert s2.healthy() is True
