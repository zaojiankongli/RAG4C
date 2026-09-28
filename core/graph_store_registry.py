"""Graph store EngineType + Factory + Registry (WeKnora-aligned assembly).

v1 keeps existing :class:`~core.graph_store.RagGraphStore` behavior. This module
adds a pluggable assembly surface so business code can depend on a store
protocol instead of hard-coding constructor call sites. Adding a future backend
= new factory case + engine type, not a rewrite of ingest/retrieval wiring.
"""

from __future__ import annotations

import inspect
import weakref
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from threading import RLock
from typing import Any, Protocol

from config.settings import GraphSettings
from core.graph_store import RagGraphStore, RagGraphStoreError
from core.observability import get_logger
from core.providers import ProviderRegistry
from models.schemas import GraphEntity, GraphRelation

_logger = get_logger("core.graph_store_registry")

# Engine names are an infrastructure extension axis, not a closed API enum.
GraphEngineType = str


class GraphStoreProtocol(Protocol):
    """Complete backend surface behind the graph consumer Adapter."""

    graph: GraphSettings
    mode: str

    def ensure_collections(self) -> None: ...

    def upsert_entities(
        self,
        entities: Sequence[GraphEntity],
        vectors: Sequence[Any],
        tenant_id: str = "",
    ) -> Any: ...

    def upsert_relations(
        self,
        relations: Sequence[GraphRelation],
        vectors: Sequence[Any],
        tenant_id: str = "",
    ) -> Any: ...

    def search_entities(
        self,
        query_vec: Any,
        top_k: int = 20,
        threshold: float | None = None,
        tenant_id: str = "",
        *,
        timeout_s: float | None = None,
    ) -> list[Any]: ...

    def search_relations(
        self,
        query_vec: Any,
        top_k: int = 20,
        threshold: float | None = None,
        tenant_id: str = "",
        *,
        timeout_s: float | None = None,
    ) -> list[Any]: ...

    def get_entities_by_ids(
        self,
        ids: Sequence[str],
        tenant_id: str = "",
        *,
        include_vectors: bool = False,
        timeout_s: float | None = None,
    ) -> list[Any]: ...

    def get_relations_by_ids(
        self,
        ids: Sequence[str],
        tenant_id: str = "",
        *,
        timeout_s: float | None = None,
    ) -> list[Any]: ...

    def get_entities_by_passage_ids(
        self,
        ids: Sequence[str],
        tenant_id: str = "",
        *,
        include_vectors: bool = False,
    ) -> list[Any]: ...

    def get_relations_by_passage_ids(
        self,
        ids: Sequence[str],
        tenant_id: str = "",
        *,
        include_vectors: bool = False,
    ) -> list[Any]: ...

    def get_entities_by_texts(
        self, texts: Sequence[str], tenant_id: str = ""
    ) -> dict[str, Any]: ...

    def get_relations_by_texts(
        self, texts: Sequence[str], tenant_id: str = ""
    ) -> dict[str, Any]: ...

    def upsert_raw_entities(self, rows: Sequence[Any], tenant_id: str = "") -> Any: ...

    def upsert_raw_relations(self, rows: Sequence[Any], tenant_id: str = "") -> Any: ...

    def delete_entities_by_ids(self, ids: Sequence[str], tenant_id: str = "") -> int: ...

    def delete_relations_by_ids(self, ids: Sequence[str], tenant_id: str = "") -> int: ...

    def healthy(self) -> bool: ...


class NoopGraphStore:
    """Disabled graph backend: never fail startup; search/id lookups return empty.

    Mirrors WeKnora: disabled is healthy; ingest/delete are no-ops.
    """

    mode = "noop"
    graph_query_timeout_contract = "per_call"

    def __init__(self, graph: GraphSettings | None = None) -> None:
        self.graph = graph or GraphSettings()

    def ensure_collections(self) -> None:
        return None

    def upsert_entities(
        self,
        entities: Sequence[GraphEntity],
        vectors: Sequence[Any],
        tenant_id: str = "",
    ) -> None:
        del entities, vectors, tenant_id

    def upsert_relations(
        self,
        relations: Sequence[GraphRelation],
        vectors: Sequence[Any],
        tenant_id: str = "",
    ) -> None:
        del relations, vectors, tenant_id

    def search_entities(
        self,
        query_vec: Any,
        top_k: int = 20,
        threshold: float | None = None,
        tenant_id: str = "",
        *,
        timeout_s: float | None = None,
    ) -> list[Any]:
        del query_vec, top_k, threshold, tenant_id, timeout_s
        return []

    def search_relations(
        self,
        query_vec: Any,
        top_k: int = 20,
        threshold: float | None = None,
        tenant_id: str = "",
        *,
        timeout_s: float | None = None,
    ) -> list[Any]:
        del query_vec, top_k, threshold, tenant_id, timeout_s
        return []

    def delete_by_chunk_ids(self, chunk_ids: Sequence[str]) -> None:
        del chunk_ids

    def healthy(self) -> bool:
        return True

    def get_entities_by_ids(
        self,
        ids: Sequence[str],
        tenant_id: str | None = None,
        *,
        include_vectors: bool = False,
        timeout_s: float | None = None,
    ) -> list[Any]:
        del ids, tenant_id, include_vectors, timeout_s
        return []

    def get_relations_by_ids(
        self,
        ids: Sequence[str],
        tenant_id: str | None = None,
        *,
        timeout_s: float | None = None,
    ) -> list[Any]:
        del ids, tenant_id, timeout_s
        return []

    def get_entities_by_passage_ids(
        self,
        ids: Sequence[str],
        tenant_id: str | None = None,
        *,
        include_vectors: bool = False,
    ) -> list[Any]:
        del ids, tenant_id, include_vectors
        return []

    def get_relations_by_passage_ids(
        self,
        ids: Sequence[str],
        tenant_id: str | None = None,
        *,
        include_vectors: bool = False,
    ) -> list[Any]:
        del ids, tenant_id, include_vectors
        return []

    def get_entities_by_texts(
        self, texts: Sequence[str], tenant_id: str | None = None
    ) -> dict[str, Any]:
        del texts, tenant_id
        return {}

    def get_relations_by_texts(
        self, texts: Sequence[str], tenant_id: str | None = None
    ) -> dict[str, Any]:
        del texts, tenant_id
        return {}

    def upsert_raw_entities(self, rows: Sequence[Any], tenant_id: str = "") -> None:
        del rows, tenant_id

    def upsert_raw_relations(self, rows: Sequence[Any], tenant_id: str = "") -> None:
        del rows, tenant_id

    def delete_entities_by_ids(self, ids: Sequence[str], tenant_id: str = "") -> int:
        del ids, tenant_id
        return 0

    def delete_relations_by_ids(self, ids: Sequence[str], tenant_id: str = "") -> int:
        del ids, tenant_id
        return 0


class GraphStoreError(RuntimeError):
    """Graph store assembly / unknown-engine error."""


@dataclass(frozen=True)
class GraphEngineRegistration:
    """Declaration for one graph engine and its configuration aliases."""

    name: str
    aliases: tuple[str, ...] = ()


_GRAPH_ENGINE_FACTORIES: ProviderRegistry[Any, GraphStoreProtocol] = ProviderRegistry(
    "graph engine"
)
_GRAPH_ENGINE_REGISTRATIONS: dict[str, GraphEngineRegistration] = {}
_GRAPH_ENGINE_ALIASES: dict[str, str] = {}
_GRAPH_ENGINE_LOCK = RLock()
_GRAPH_STORE_REGISTRIES: weakref.WeakSet[Any] = weakref.WeakSet()
_BUILTIN_GRAPH_ENGINE_NAMES = frozenset(
    {
        "none",
        "noop",
        "milvus_vector_graph",
        "milvus",
        "rag4c",
        "auto",
    }
)


def _normalize_graph_engine_name(value: Any, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise GraphStoreError("graph engine name must be a string")
    key = value.strip().lower()
    if not key:
        if allow_empty:
            return ""
        raise GraphStoreError("graph engine name must not be empty")
    if any(char.isspace() for char in key):
        raise GraphStoreError(f"invalid graph engine name: {value!r}")
    return key


def _validate_graph_engine_factory(factory: Callable[[Any], GraphStoreProtocol]) -> None:
    if not callable(factory):
        raise TypeError("graph engine factory must be callable")
    call = getattr(factory, "__call__", None)
    if (
        inspect.iscoroutinefunction(factory)
        or inspect.iscoroutinefunction(call)
        or inspect.isgeneratorfunction(factory)
        or inspect.isgeneratorfunction(call)
        or inspect.isasyncgenfunction(factory)
        or inspect.isasyncgenfunction(call)
    ):
        raise TypeError("graph engine factory must be a synchronous function")
    try:
        inspect.signature(factory).bind(None)
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "graph engine factory must accept exactly one settings argument"
        ) from exc


def _register_graph_engine(
    name: str,
    factory: Callable[[Any], GraphStoreProtocol],
    *,
    aliases: Sequence[str] = (),
    replace: bool = False,
    builtin: bool = False,
) -> GraphEngineRegistration:
    canonical = _normalize_graph_engine_name(name)
    if isinstance(aliases, str):
        raise TypeError("graph engine aliases must be a sequence of strings")
    normalized_aliases: list[str] = []
    seen_aliases: set[str] = set()
    for alias in aliases:
        normalized = _normalize_graph_engine_name(alias)
        if normalized == canonical or normalized in seen_aliases:
            raise GraphStoreError(
                f"duplicate graph engine alias for {canonical}: {normalized}"
            )
        seen_aliases.add(normalized)
        normalized_aliases.append(normalized)
    _validate_graph_engine_factory(factory)

    invalidate = False
    with _GRAPH_ENGINE_LOCK:
        existing = _GRAPH_ENGINE_REGISTRATIONS.get(canonical)
        if canonical in _BUILTIN_GRAPH_ENGINE_NAMES and not builtin:
            raise GraphStoreError(f"built-in graph engine is immutable: {canonical}")
        if existing is not None and not replace:
            raise GraphStoreError(f"graph engine already registered: {canonical}")
        if existing is not None and existing.name in _BUILTIN_GRAPH_ENGINE_NAMES and not builtin:
            raise GraphStoreError(f"built-in graph engine is immutable: {canonical}")

        if existing is not None and replace and not normalized_aliases:
            normalized_aliases = list(existing.aliases)

        claimed_keys = {canonical, *normalized_aliases}
        for key in claimed_keys:
            owner = _GRAPH_ENGINE_ALIASES.get(key)
            if owner is not None and owner != canonical:
                raise GraphStoreError(
                    f"graph engine name/alias already belongs to {owner}: {key}"
                )
            if key in _BUILTIN_GRAPH_ENGINE_NAMES and not builtin:
                raise GraphStoreError(f"built-in graph engine name is reserved: {key}")

        previous_aliases = set(existing.aliases) if existing is not None else set()
        _GRAPH_ENGINE_FACTORIES.register(canonical, factory, replace=replace)
        for old_alias in previous_aliases - set(normalized_aliases):
            _GRAPH_ENGINE_ALIASES.pop(old_alias, None)
        for alias in normalized_aliases:
            _GRAPH_ENGINE_ALIASES[alias] = canonical
        registration = GraphEngineRegistration(
            name=canonical,
            aliases=tuple(normalized_aliases),
        )
        _GRAPH_ENGINE_REGISTRATIONS[canonical] = registration
        invalidate = existing is not None and replace
    if invalidate:
        _invalidate_graph_store_caches(canonical)
    return registration


def register_graph_engine(
    name: str,
    factory: Callable[[Any], GraphStoreProtocol],
    *,
    aliases: Sequence[str] = (),
    replace: bool = False,
) -> GraphEngineRegistration:
    """Register a custom graph engine without editing the factory host."""

    return _register_graph_engine(
        name,
        factory,
        aliases=aliases,
        replace=replace,
    )


def unregister_graph_engine(name: str) -> None:
    """Remove a custom graph engine and all of its aliases."""

    key = _normalize_graph_engine_name(name, allow_empty=True) or "none"
    with _GRAPH_ENGINE_LOCK:
        canonical = _GRAPH_ENGINE_ALIASES.get(key, key)
        registration = _GRAPH_ENGINE_REGISTRATIONS.get(canonical)
        if registration is None:
            return
        if canonical in _BUILTIN_GRAPH_ENGINE_NAMES:
            raise GraphStoreError(f"built-in graph engine is immutable: {canonical}")
        _GRAPH_ENGINE_FACTORIES.unregister(canonical)
        _GRAPH_ENGINE_REGISTRATIONS.pop(canonical, None)
        for alias in registration.aliases:
            _GRAPH_ENGINE_ALIASES.pop(alias, None)
    _invalidate_graph_store_caches(canonical)


def graph_engine_names() -> tuple[str, ...]:
    """Return canonical graph engines currently available for construction."""

    with _GRAPH_ENGINE_LOCK:
        return tuple(sorted(_GRAPH_ENGINE_REGISTRATIONS))


def _resolve_registered_graph_engine(engine_type: Any) -> str:
    raw = (
        ""
        if engine_type is None
        else _normalize_graph_engine_name(engine_type, allow_empty=True)
    )
    key = raw or "none"
    with _GRAPH_ENGINE_LOCK:
        canonical = _GRAPH_ENGINE_ALIASES.get(key, key)
        if canonical in _GRAPH_ENGINE_REGISTRATIONS:
            return canonical
        available = ", ".join(sorted(_GRAPH_ENGINE_REGISTRATIONS)) or "(none)"
    raise GraphStoreError(
        f'graph engine "{key}" is not wired (registered: {available})'
    )


def _invalidate_graph_store_caches(canonical: str) -> None:
    """Invalidate all live instance registries after a custom engine change."""

    for registry in list(_GRAPH_STORE_REGISTRIES):
        registry.invalidate(canonical)


def _create_noop_graph_store(settings: Any = None) -> GraphStoreProtocol:
    configured_graph = getattr(settings, "graph", None)
    return NoopGraphStore(
        configured_graph if isinstance(configured_graph, GraphSettings) else None
    )


def _create_milvus_graph_store(settings: Any = None) -> GraphStoreProtocol:
    if settings is None:
        raise GraphStoreError("milvus_vector_graph requires settings")
    milvus = getattr(settings, "milvus", None)
    graph = getattr(settings, "graph", None)
    if milvus is None or graph is None:
        raise GraphStoreError(
            "settings.milvus / settings.graph required for milvus_vector_graph"
        )
    return RagGraphStore(milvus, graph)


def _validate_created_graph_store(
    store: Any,
    canonical: str,
) -> GraphStoreProtocol:
    if inspect.isawaitable(store) or inspect.isgenerator(store) or inspect.isasyncgen(store):
        close = getattr(store, "close", None)
        if callable(close):
            close()
        raise GraphStoreError(
            f'graph engine "{canonical}" factory returned a deferred value'
        )
    from core.graph_projection_adapters import (
        GraphProjectionAdapterError,
        create_graph_projection_adapter,
    )

    try:
        create_graph_projection_adapter(store)
    except GraphProjectionAdapterError as exc:
        raise GraphStoreError(
            f'graph engine "{canonical}" factory returned an invalid store: {exc}'
        ) from exc
    return store


def _register_builtin_graph_engines() -> None:
    _register_graph_engine(
        "none",
        _create_noop_graph_store,
        aliases=("noop",),
        builtin=True,
    )
    _register_graph_engine(
        "milvus_vector_graph",
        _create_milvus_graph_store,
        aliases=("milvus", "rag4c", "auto"),
        builtin=True,
    )


_register_builtin_graph_engines()


class GraphStoreFactory:
    """Create a graph store through the graph-engine Strategy registry."""

    @staticmethod
    def create(engine_type: str, settings: Any = None) -> GraphStoreProtocol:
        canonical = _resolve_registered_graph_engine(engine_type)
        store = _GRAPH_ENGINE_FACTORIES.create(canonical, settings)
        return _validate_created_graph_store(store, canonical)


class GraphStoreRegistry:
    """Process-level registry with a single active backend (env-mutex style)."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._by_type: dict[str, GraphStoreProtocol] = {}
        self._active: GraphStoreProtocol | None = None
        self._active_type: str | None = None
        _GRAPH_STORE_REGISTRIES.add(self)

    def register(self, engine_type: str, store: GraphStoreProtocol) -> None:
        key = (engine_type or "").strip().lower()
        if not key:
            raise GraphStoreError("engine_type is required")
        with self._lock:
            self._by_type[key] = store

    def get(self, engine_type: str) -> GraphStoreProtocol | None:
        with self._lock:
            return self._by_type.get((engine_type or "").strip().lower())

    def get_active(self) -> GraphStoreProtocol | None:
        with self._lock:
            return self._active

    def get_or_create(
        self,
        engine_type: str,
        factory: Callable[[], GraphStoreProtocol],
    ) -> GraphStoreProtocol:
        """Atomically reuse or construct the active store for one engine."""

        key = (engine_type or "").strip().lower()
        if not key:
            raise GraphStoreError("engine_type is required")
        with self._lock:
            existing = self._by_type.get(key)
            if existing is not None and self._active_type == key:
                return existing
            store = factory()
            self._by_type[key] = store
            self._active = store
            self._active_type = key
            return store

    def invalidate(self, engine_type: str) -> None:
        """Drop a cached engine after its global factory registration changes."""

        key = (engine_type or "").strip().lower()
        with self._lock:
            self._by_type.pop(key, None)
            if self._active_type == key:
                self._active = None
                self._active_type = None

    def get_active_projection_adapter(self) -> Any:
        """Return the active backend through the graph-consumer Adapter port."""

        with self._lock:
            active = self._active
        if active is None:
            raise GraphStoreError("no active graph engine")
        from core.graph_projection_adapters import create_graph_projection_adapter

        return create_graph_projection_adapter(active)

    @property
    def active_engine_type(self) -> str | None:
        with self._lock:
            return self._active_type

    def set_active(self, engine_type: str) -> GraphStoreProtocol:
        key = (engine_type or "").strip().lower()
        with self._lock:
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
    raw = getattr(graph, "engine", None) or "milvus_vector_graph"
    # Resolve custom names and aliases through the same live registry used by
    # construction. Unknown engines remain fail-closed.
    return _resolve_registered_graph_engine(raw)


_REGISTRY = GraphStoreRegistry()


def get_registry() -> GraphStoreRegistry:
    return _REGISTRY


def assembly_graph_engine(settings: Any = None) -> GraphEngineType:
    """Engine for feature-flagged assembly (ingest/retrieval pipelines).

    - ``pipeline.graph_retrieval_on`` or ``pipeline.graph_index_on`` → the
      configured registered graph engine (``auto`` keeps the Milvus default).
    - Otherwise fall through to :func:`resolve_graph_engine_from_settings`.
    """
    if settings is None:
        return "none"
    pipeline = getattr(settings, "pipeline", None)
    if pipeline is not None:
        if bool(getattr(pipeline, "graph_retrieval_on", False)) or bool(
            getattr(pipeline, "graph_index_on", False)
        ):
            graph = getattr(settings, "graph", None)
            configured = getattr(graph, "engine", None) if graph is not None else None
            return _resolve_registered_graph_engine(configured or "milvus_vector_graph")
    return resolve_graph_engine_from_settings(settings)


def get_graph_store(settings: Any = None, *, registry: GraphStoreRegistry | None = None) -> GraphStoreProtocol:
    """Resolve + cache active graph store from settings.engine resolution."""
    reg = registry if registry is not None else _REGISTRY
    engine = resolve_graph_engine_from_settings(settings)
    store = reg.get_or_create(
        engine,
        lambda: GraphStoreFactory.create(engine, settings),
    )
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
        engine = _resolve_registered_graph_engine(engine_override)
    else:
        engine = assembly_graph_engine(settings)
    store = reg.get_or_create(
        engine,
        lambda: GraphStoreFactory.create(engine, settings),
    )
    _logger.debug("graph store assembly engine=%s", engine)
    return store


def create_graph_projection_consumer(
    settings: Any = None,
    *,
    engine_override: GraphEngineType | None = None,
    registry: GraphStoreRegistry | None = None,
) -> Any:
    """Assemble an active graph backend and adapt it for graph consumers."""

    from core.graph_projection_adapters import create_graph_projection_adapter

    return create_graph_projection_adapter(
        create_graph_store(
            settings,
            engine_override=engine_override,
            registry=registry,
        )
    )


def reset_graph_store_registry() -> None:
    """Clear process cache (pipeline reload / tests)."""
    reset_registry_for_tests()


__all__ = [
    "GraphEngineType",
    "GraphEngineRegistration",
    "GraphStoreError",
    "GraphStoreFactory",
    "GraphStoreProtocol",
    "GraphStoreRegistry",
    "NoopGraphStore",
    "assembly_graph_engine",
    "create_graph_store",
    "create_graph_projection_consumer",
    "get_graph_store",
    "get_registry",
    "graph_engine_names",
    "register_graph_engine",
    "reset_graph_store_registry",
    "reset_registry_for_tests",
    "resolve_graph_engine_from_settings",
    "RagGraphStoreError",
    "unregister_graph_engine",
]
