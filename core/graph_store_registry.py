"""Graph store EngineType + Factory + Registry (WeKnora-aligned assembly).

v1 keeps existing :class:`~core.graph_store.RagGraphStore` behavior. This module
adds a pluggable assembly surface so business code can depend on a store
protocol instead of hard-coding constructor call sites. Adding a future backend
= new factory case + engine type, not a rewrite of ingest/retrieval wiring.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, Sequence

from core.graph_store import RagGraphStore, RagGraphStoreError
from core.observability import get_logger
from models.schemas import GraphEntity, GraphRelation

_logger = get_logger("core.graph_store_registry")

GraphEngineType = Literal["milvus_vector_graph", "none"]


class GraphStoreProtocol(Protocol):
    """Subset of graph-store operations used by ingest/retrieval/query assembly."""

    def ensure_collections(self) -> None: ...

    def upsert_entities(self, entities: Sequence[GraphEntity], vectors: Sequence[Any]) -> None: ...

    def upsert_relations(self, relations: Sequence[GraphRelation], vectors: Sequence[Any]) -> None: ...

    def search_entities(self, query_vec: Any, top_k: int = 20, threshold: float | None = None) -> list[Any]: ...

    def search_relations(self, query_vec: Any, top_k: int = 20, threshold: float | None = None) -> list[Any]: ...

    def delete_by_chunk_ids(self, chunk_ids: Sequence[str]) -> None: ...

    def healthy(self) -> bool: ...

    def get_entities_by_ids(self, ids: Sequence[str], tenant_id: str | None = None) -> list[Any]: ...

    def get_relations_by_ids(self, ids: Sequence[str], tenant_id: str | None = None) -> list[Any]: ...


class NoopGraphStore:
    """Disabled graph backend: never fail startup; search/id lookups return empty.

    Mirrors WeKnora: disabled is healthy; ingest/delete are no-ops.
    """

    mode = "noop"

    def ensure_collections(self) -> None:
        return None

    def upsert_entities(self, entities: Sequence[GraphEntity], vectors: Sequence[Any]) -> None:
        del entities, vectors

    def upsert_relations(self, relations: Sequence[GraphRelation], vectors: Sequence[Any]) -> None:
        del relations, vectors

    def search_entities(self, query_vec: Any, top_k: int = 20, threshold: float | None = None) -> list[Any]:
        del query_vec, top_k, threshold
        return []

    def search_relations(self, query_vec: Any, top_k: int = 20, threshold: float | None = None) -> list[Any]:
        del query_vec, top_k, threshold
        return []

    def delete_by_chunk_ids(self, chunk_ids: Sequence[str]) -> None:
        del chunk_ids

    def healthy(self) -> bool:
        return True

    def get_entities_by_ids(self, ids: Sequence[str], tenant_id: str | None = None) -> list[Any]:
        del ids, tenant_id
        return []

    def get_relations_by_ids(self, ids: Sequence[str], tenant_id: str | None = None) -> list[Any]:
        del ids, tenant_id
        return []

    def get_entities_by_passage_ids(self, ids: Sequence[str], tenant_id: str | None = None) -> list[Any]:
        del ids, tenant_id
        return []

    def get_relations_by_passage_ids(self, ids: Sequence[str], tenant_id: str | None = None) -> list[Any]:
        del ids, tenant_id
        return []

    def get_entities_by_texts(self, texts: Sequence[str], tenant_id: str | None = None) -> list[Any]:
        del texts, tenant_id
        return []

    def get_relations_by_texts(self, texts: Sequence[str], tenant_id: str | None = None) -> list[Any]:
        del texts, tenant_id
        return []

    def upsert_raw_entities(self, rows: Sequence[Any]) -> None:
        del rows

    def upsert_raw_relations(self, rows: Sequence[Any]) -> None:
        del rows

    def delete_entities_by_ids(self, ids: Sequence[str]) -> int:
        del ids
        return 0

    def delete_relations_by_ids(self, ids: Sequence[str]) -> int:
        del ids
        return 0


class GraphStoreError(RuntimeError):
    """Graph store assembly / unknown-engine error."""


class GraphStoreFactory:
    """Create a graph store from engine type + settings."""

    @staticmethod
    def create(engine_type: str, settings: Any = None) -> GraphStoreProtocol:
        key = (engine_type or "none").strip().lower()
        if key in ("none", "noop", ""):
            return NoopGraphStore()
        if key == "milvus_vector_graph":
            if settings is None:
                raise GraphStoreError("milvus_vector_graph requires settings")
            milvus = getattr(settings, "milvus", None)
            graph = getattr(settings, "graph", None)
            if milvus is None or graph is None:
                raise GraphStoreError("settings.milvus / settings.graph required for milvus_vector_graph")
            return RagGraphStore(milvus, graph)
        raise GraphStoreError(f'graph engine "{key}" is not wired')


class GraphStoreRegistry:
    """Process-level registry with a single active backend (env-mutex style)."""

    def __init__(self) -> None:
        self._by_type: dict[str, GraphStoreProtocol] = {}
        self._active: GraphStoreProtocol | None = None
        self._active_type: str | None = None

    def register(self, engine_type: str, store: GraphStoreProtocol) -> None:
        key = (engine_type or "").strip().lower()
        if not key:
            raise GraphStoreError("engine_type is required")
        self._by_type[key] = store

    def get(self, engine_type: str) -> GraphStoreProtocol | None:
        return self._by_type.get((engine_type or "").strip().lower())

    def get_active(self) -> GraphStoreProtocol | None:
        return self._active

    @property
    def active_engine_type(self) -> str | None:
        return self._active_type

    def set_active(self, engine_type: str) -> GraphStoreProtocol:
        key = (engine_type or "").strip().lower()
        store = self._by_type.get(key)
        if store is None:
            raise GraphStoreError(f"graph engine not registered: {key}")
        self._active = store
        self._active_type = key
        return store


def resolve_graph_engine_from_settings(settings: Any = None) -> GraphEngineType:
    """Resolve active engine from settings.

    Disabled/missing graph → ``none``. Enabled defaults to milvus vector-graph.
    Unknown engine names **raise** (fail closed to a visible error, not silent none).
    """
    if settings is None:
        return "none"
    graph = getattr(settings, "graph", None)
    if graph is None:
        return "none"
    enabled = bool(getattr(graph, "enabled", False) or getattr(graph, "graph_index_on", False))
    if not enabled:
        return "none"
    raw = str(getattr(graph, "engine", None) or "milvus_vector_graph").strip().lower()
    if raw in ("milvus", "milvus_vector_graph", "rag4c", ""):
        return "milvus_vector_graph"
    if raw in ("none", "noop"):
        return "none"
    # 未接线的引擎：显式拒绝，避免运维以为图谱已开
    raise GraphStoreError(f'graph engine "{raw}" is not wired')


_REGISTRY = GraphStoreRegistry()


def get_registry() -> GraphStoreRegistry:
    return _REGISTRY


def assembly_graph_engine(settings: Any = None) -> GraphEngineType:
    """Engine for feature-flagged assembly (ingest/retrieval pipelines).

    - ``pipeline.graph_retrieval_on`` or ``pipeline.graph_index_on`` → milvus
      (production GraphSettings has no ``enabled`` flag; pipeline switches are truth).
    - Otherwise fall through to :func:`resolve_graph_engine_from_settings`.
    """
    if settings is None:
        return "none"
    pipeline = getattr(settings, "pipeline", None)
    if pipeline is not None:
        if bool(getattr(pipeline, "graph_retrieval_on", False)) or bool(
            getattr(pipeline, "graph_index_on", False)
        ):
            return "milvus_vector_graph"
    return resolve_graph_engine_from_settings(settings)


def get_graph_store(settings: Any = None, *, registry: GraphStoreRegistry | None = None) -> GraphStoreProtocol:
    """Resolve + cache active graph store from settings.engine resolution."""
    reg = registry if registry is not None else _REGISTRY
    engine = resolve_graph_engine_from_settings(settings)
    existing = reg.get(engine)
    if existing is not None and reg.active_engine_type == engine:
        return existing
    store = GraphStoreFactory.create(engine, settings)
    reg.register(engine, store)
    reg.set_active(engine)
    _logger.debug("graph store active engine=%s", engine)
    return store


def reset_registry_for_tests() -> None:
    global _REGISTRY
    _REGISTRY = GraphStoreRegistry()


def create_graph_store(
    settings: Any = None,
    *,
    engine_override: GraphEngineType | None = None,
    registry: GraphStoreRegistry | None = None,
) -> GraphStoreProtocol:
    """Production assembly helper (registry-backed, cached per engine).

    - ``engine_override``: force a backend (e.g. query API observability always
      reads milvus even when ingest/retrieval flags are off — preserve pre-wiring
      ``/api/graph/*`` behavior).
    - Else engine = :func:`assembly_graph_engine` (pipeline flags → milvus; else
      settings resolve; disabled → noop).
    """
    reg = registry if registry is not None else _REGISTRY
    if engine_override is not None:
        engine: GraphEngineType = engine_override
    else:
        engine = assembly_graph_engine(settings)
    existing = reg.get(engine)
    if existing is not None and reg.active_engine_type == engine:
        return existing
    store = GraphStoreFactory.create(engine, settings)
    reg.register(engine, store)
    reg.set_active(engine)
    _logger.debug("graph store assembly engine=%s", engine)
    return store


def reset_graph_store_registry() -> None:
    """Clear process cache (pipeline reload / tests)."""
    reset_registry_for_tests()


__all__ = [
    "GraphEngineType",
    "GraphStoreError",
    "GraphStoreFactory",
    "GraphStoreProtocol",
    "GraphStoreRegistry",
    "NoopGraphStore",
    "assembly_graph_engine",
    "create_graph_store",
    "get_graph_store",
    "get_registry",
    "reset_graph_store_registry",
    "reset_registry_for_tests",
    "resolve_graph_engine_from_settings",
    "RagGraphStoreError",
]
