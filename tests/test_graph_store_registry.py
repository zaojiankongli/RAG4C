from __future__ import annotations

import inspect
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from config.settings import GraphSettings, PipelineSettings, Settings
from core.graph_store_registry import (
    GraphStoreError,
    GraphStoreFactory,
    GraphStoreRegistry,
    NoopGraphStore,
    assembly_graph_engine,
    create_graph_store,
    graph_engine_names,
    get_graph_store,
    register_graph_engine,
    reset_registry_for_tests,
    resolve_graph_engine_from_settings,
    unregister_graph_engine,
)
from core.providers import UnknownProviderError


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


def test_assembly_engine_and_app_override(monkeypatch):
    from core.graph_store_registry import assembly_graph_engine, create_graph_store

    reset_registry_for_tests()
    s_flags_on = SimpleNamespace(
        pipeline=SimpleNamespace(graph_retrieval_on=True, graph_index_on=False),
        graph=SimpleNamespace(),
        milvus=SimpleNamespace(uri="http://x"),
    )
    assert assembly_graph_engine(s_flags_on) == "milvus_vector_graph"
    s_flags_off = SimpleNamespace(
        pipeline=SimpleNamespace(graph_retrieval_on=False, graph_index_on=False),
        graph=SimpleNamespace(),
        milvus=None,
    )
    assert assembly_graph_engine(s_flags_off) == "none"

    monkeypatch.setattr(
        "core.graph_store_registry.GraphStoreFactory.create",
        staticmethod(lambda engine, settings=None: NoopGraphStore() if engine == "none" else SimpleNamespace(mode="milvus")),
    )
    reset_registry_for_tests()
    assert create_graph_store(s_flags_off).mode == "noop"
    # app query path: force milvus even when flags off
    reset_registry_for_tests()
    store = create_graph_store(s_flags_off, engine_override="milvus_vector_graph")
    assert store.mode == "milvus"

    noop = NoopGraphStore()
    assert noop.get_entities_by_ids(["e1"]) == []
    assert noop.get_relations_by_ids(["r1"]) == []
    assert noop.delete_entities_by_ids(["e1"]) == 0


def test_production_call_sites_use_registry_create_graph_store():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    # 图谱装配从 rag.py 迁进了检索管线的组合根（retrieval/stages.py），所以宿主
    # 清单跟着改；判据没变 —— 生产侧只能经注册表建 store。
    for rel in ("retrieval/stages.py", "server/documents.py", "server/app.py"):
        text = (root / rel).read_text(encoding="utf-8")
        if rel == "server/app.py":
            assert "create_graph_query_component" in text, rel
        else:
            assert "create_graph_store" in text, rel
        assert "RagGraphStore(s.milvus" not in text, rel
        assert "RagGraphStore(settings.milvus" not in text, rel
    app = (root / "server" / "app.py").read_text(encoding="utf-8")
    assert 'engine_override="milvus_vector_graph"' in app


def test_graph_store_registry_and_resolve():
    reset_registry_for_tests()
    reg = GraphStoreRegistry()
    none_store = GraphStoreFactory.create("none")
    reg.register("none", none_store)
    reg.set_active("none")
    assert reg.get_active() is none_store
    with pytest.raises(GraphStoreError):
        reg.set_active("milvus_vector_graph")
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


def test_builtin_engine_aliases_keep_existing_factory_behavior() -> None:
    assert GraphStoreFactory.create("").mode == "noop"
    assert GraphStoreFactory.create("noop").mode == "noop"
    assert resolve_graph_engine_from_settings(_MilvusSettings(engine="milvus")) == (
        "milvus_vector_graph"
    )
    assert resolve_graph_engine_from_settings(_MilvusSettings(engine="auto")) == (
        "milvus_vector_graph"
    )
    assert {"none", "milvus_vector_graph"} <= set(graph_engine_names())


def test_graph_engine_is_part_of_the_real_settings_environment_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG4C_GRAPH_ENGINE", "none")
    settings = Settings()
    assert settings.graph.engine == "none"


def test_actual_settings_default_auto_preserves_pipeline_milvus_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RAG4C_GRAPH_ENGINE", raising=False)
    settings = Settings(
        graph=GraphSettings(),
        pipeline=PipelineSettings(graph_index_on=True),
    )
    assert settings.graph.engine == "auto"
    assert assembly_graph_engine(settings) == "milvus_vector_graph"


def test_custom_engine_registration_is_live_through_resolution_and_assembly() -> None:
    class CustomStore(NoopGraphStore):
        mode = "custom"

    register_graph_engine(
        "custom_vector",
        lambda settings: CustomStore(),
        aliases=("custom",),
    )
    try:
        settings = SimpleNamespace(
            graph=SimpleNamespace(enabled=True, engine="custom"),
            pipeline=SimpleNamespace(
                graph_retrieval_on=False,
                graph_index_on=False,
            ),
        )
        assert resolve_graph_engine_from_settings(settings) == "custom_vector"
        assert GraphStoreFactory.create("custom", settings).mode == "custom"
        registry = GraphStoreRegistry()
        store = create_graph_store(settings, registry=registry)
        assert store.mode == "custom"
        assert registry.active_engine_type == "custom_vector"
    finally:
        unregister_graph_engine("custom")


def test_pipeline_flags_use_the_registered_graph_engine_from_graph_settings() -> None:
    class CustomStore(NoopGraphStore):
        mode = "custom"

    register_graph_engine("pipeline_custom", lambda settings: CustomStore())
    try:
        settings = SimpleNamespace(
            graph=GraphSettings(engine="pipeline_custom"),
            pipeline=SimpleNamespace(
                graph_retrieval_on=True,
                graph_index_on=False,
            ),
        )
        assert assembly_graph_engine(settings) == "pipeline_custom"
        assert create_graph_store(
            settings,
            registry=GraphStoreRegistry(),
        ).mode == "custom"
    finally:
        unregister_graph_engine("pipeline_custom")


def test_graph_store_factory_dispatch_is_registry_driven() -> None:
    source = inspect.getsource(GraphStoreFactory.create)
    assert "milvus_vector_graph" not in source
    assert 'key == "none"' not in source


def test_graph_engine_registration_rejects_invalid_shapes_and_collisions() -> None:
    with pytest.raises(GraphStoreError, match="must not be empty"):
        register_graph_engine("", lambda settings: NoopGraphStore())
    with pytest.raises(GraphStoreError, match="invalid graph engine name"):
        register_graph_engine("bad name", lambda settings: NoopGraphStore())
    with pytest.raises(TypeError, match="one settings argument"):
        register_graph_engine("no_args", lambda: NoopGraphStore())

    async def async_factory(settings):
        return NoopGraphStore()

    with pytest.raises(TypeError, match="synchronous"):
        register_graph_engine("async_engine", async_factory)
    with pytest.raises(GraphStoreError, match="immutable|reserved"):
        register_graph_engine("none", lambda settings: NoopGraphStore())
    with pytest.raises(GraphStoreError, match="immutable"):
        unregister_graph_engine("milvus")

    register_graph_engine("collision_a", lambda settings: NoopGraphStore(), aliases=("shared",))
    try:
        with pytest.raises(GraphStoreError, match="already belongs"):
            register_graph_engine("collision_b", lambda settings: NoopGraphStore(), aliases=("shared",))
    finally:
        unregister_graph_engine("collision_a")


def test_factory_rejects_deferred_and_invalid_store_results() -> None:
    async def deferred_value():
        return NoopGraphStore()

    def deferred_factory(settings):
        return deferred_value()

    def invalid_factory(settings):
        return object()

    def generator_factory(settings):
        yield NoopGraphStore()

    def generator_value_factory(settings):
        return (item for item in ())

    async def async_generator_factory(settings):
        yield NoopGraphStore()

    with pytest.raises(TypeError, match="synchronous function"):
        register_graph_engine("generator_engine", generator_factory)
    with pytest.raises(TypeError, match="synchronous function"):
        register_graph_engine("async_generator_engine", async_generator_factory)

    register_graph_engine("generator_value_engine", generator_value_factory)
    register_graph_engine("deferred_engine", deferred_factory)
    register_graph_engine("invalid_store_engine", invalid_factory)
    try:
        with pytest.raises(GraphStoreError, match="deferred"):
            GraphStoreFactory.create("generator_value_engine")
        with pytest.raises(GraphStoreError, match="deferred"):
            GraphStoreFactory.create("deferred_engine")
        with pytest.raises(GraphStoreError, match="invalid store"):
            GraphStoreFactory.create("invalid_store_engine")
    finally:
        unregister_graph_engine("generator_value_engine")
        unregister_graph_engine("deferred_engine")
        unregister_graph_engine("invalid_store_engine")


def test_factory_preserves_nested_provider_errors() -> None:
    def nested_provider_failure(settings):
        raise UnknownProviderError("nested provider is unavailable")

    register_graph_engine("nested_failure", nested_provider_failure)
    try:
        with pytest.raises(UnknownProviderError, match="nested provider"):
            GraphStoreFactory.create("nested_failure")
    finally:
        unregister_graph_engine("nested_failure")


def test_graph_store_registry_constructs_one_store_concurrently() -> None:
    calls = 0

    def factory(settings):
        nonlocal calls
        calls += 1
        return NoopGraphStore()

    register_graph_engine("concurrent_engine", factory)
    try:
        settings = SimpleNamespace(
            graph=SimpleNamespace(enabled=True, engine="concurrent_engine")
        )
        registry = GraphStoreRegistry()
        with ThreadPoolExecutor(max_workers=8) as pool:
            stores = list(
                pool.map(
                    lambda _item: create_graph_store(settings, registry=registry),
                    range(8),
                )
            )
        assert calls == 1
        assert len({id(store) for store in stores}) == 1
    finally:
        unregister_graph_engine("concurrent_engine")


def test_engine_replace_and_unregister_invalidate_existing_store_caches() -> None:
    class StoreA(NoopGraphStore):
        mode = "store-a"

    class StoreB(NoopGraphStore):
        mode = "store-b"

    register_graph_engine(
        "cache_engine",
        lambda settings: StoreA(),
        aliases=("cache-alias",),
    )
    registry = GraphStoreRegistry()
    settings = SimpleNamespace(
        graph=SimpleNamespace(enabled=True, engine="cache-alias")
    )
    try:
        first = create_graph_store(settings, registry=registry)
        assert first.mode == "store-a"
        register_graph_engine(
            "cache_engine",
            lambda settings: StoreB(),
            replace=True,
        )
        assert registry.get_active() is None
        second = create_graph_store(settings, registry=registry)
        assert second.mode == "store-b"
        unregister_graph_engine("cache_engine")
        assert registry.get_active() is None
    finally:
        try:
            unregister_graph_engine("cache_engine")
        except GraphStoreError:
            pass


def test_custom_engine_can_be_replaced_and_removed_by_canonical_or_alias() -> None:
    register_graph_engine(
        "replaceable",
        lambda settings: NoopGraphStore(),
        aliases=("replace-me",),
    )
    try:
        class ReplacementStore(NoopGraphStore):
            mode = "replacement"

        register_graph_engine(
            "replaceable",
            lambda settings: ReplacementStore(),
            replace=True,
        )
        assert GraphStoreFactory.create("replace-me").mode == "replacement"
        unregister_graph_engine("replaceable")
        with pytest.raises(GraphStoreError, match="not wired"):
            GraphStoreFactory.create("replace-me")
    finally:
        # Idempotent cleanup if an assertion failed before unregistering.
        try:
            unregister_graph_engine("replaceable")
        except GraphStoreError:
            pass
