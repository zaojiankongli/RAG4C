"""Explicit consumer port and Adapter for graph projection consumers.

Graph backend assembly already lives in ``core.graph_store_registry``.  This
module owns the separate operational contract required by GraphBuilder and
GraphRetriever so those consumers do not depend on whichever backend happens
to be selected.  It deliberately does not add dataset scope to graph facts or
make Graph a Catalog consistency authority.
"""

from __future__ import annotations

from collections.abc import Sequence
import inspect
from typing import Any, Protocol


class GraphProjectionAdapterError(TypeError):
    """Graph consumer backend does not satisfy the operational port."""


class GraphProjectionAdapterProtocol(Protocol):
    """Operational port shared by graph indexing and retrieval consumers."""

    @property
    def graph(self) -> Any: ...

    @property
    def mode(self) -> str: ...

    def ensure_collections(self) -> None: ...

    def healthy(self) -> bool: ...

    def upsert_entities(
        self, entities: Sequence[Any], vectors: Sequence[Any], *, tenant_id: str = ""
    ) -> Any: ...

    def upsert_relations(
        self, relations: Sequence[Any], vectors: Sequence[Any], *, tenant_id: str = ""
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
        self, ids: Sequence[str], tenant_id: str = "", *, timeout_s: float | None = None
    ) -> list[Any]: ...

    def get_entities_by_passage_ids(
        self, ids: Sequence[str], tenant_id: str = "", *, include_vectors: bool = False
    ) -> list[Any]: ...

    def get_relations_by_passage_ids(
        self, ids: Sequence[str], tenant_id: str = "", *, include_vectors: bool = False
    ) -> list[Any]: ...

    def get_entities_by_texts(self, texts: Sequence[str], tenant_id: str = "") -> Any: ...

    def get_relations_by_texts(self, texts: Sequence[str], tenant_id: str = "") -> Any: ...

    def upsert_raw_entities(self, rows: Sequence[Any], *, tenant_id: str = "") -> Any: ...

    def upsert_raw_relations(self, rows: Sequence[Any], *, tenant_id: str = "") -> Any: ...

    def delete_entities_by_ids(self, ids: Sequence[str], *, tenant_id: str = "") -> int: ...

    def delete_relations_by_ids(self, ids: Sequence[str], *, tenant_id: str = "") -> int: ...


_REQUIRED_METHODS = (
    "ensure_collections",
    "healthy",
    "upsert_entities",
    "upsert_relations",
    "search_entities",
    "search_relations",
    "get_entities_by_ids",
    "get_relations_by_ids",
    "get_entities_by_passage_ids",
    "get_relations_by_passage_ids",
    "get_entities_by_texts",
    "get_relations_by_texts",
    "upsert_raw_entities",
    "upsert_raw_relations",
    "delete_entities_by_ids",
    "delete_relations_by_ids",
)
GRAPH_CONSUMER_REQUIRED_METHODS = frozenset(_REQUIRED_METHODS)
GRAPH_BUILDER_REQUIRED_METHODS = frozenset(
    {
        "upsert_entities",
        "upsert_relations",
        "get_entities_by_ids",
        "get_relations_by_ids",
        "get_entities_by_passage_ids",
        "get_relations_by_passage_ids",
        "get_entities_by_texts",
        "get_relations_by_texts",
        "upsert_raw_entities",
        "upsert_raw_relations",
        "delete_entities_by_ids",
        "delete_relations_by_ids",
    }
)
GRAPH_RETRIEVER_REQUIRED_METHODS = frozenset(
    {
        "search_entities",
        "search_relations",
        "get_entities_by_ids",
        "get_relations_by_ids",
    }
)
GRAPH_COLLECTION_INITIALIZATION_REQUIRED_METHODS = frozenset({"ensure_collections"})
GRAPH_BUILDER_WRITE_REQUIRED_METHODS = frozenset(
    {
        "get_entities_by_texts",
        "get_relations_by_texts",
        "upsert_entities",
        "upsert_relations",
    }
)
GRAPH_BUILDER_DELETE_REQUIRED_METHODS = frozenset(
    {
        "get_relations_by_passage_ids",
        "get_entities_by_passage_ids",
        "upsert_raw_relations",
        "delete_relations_by_ids",
        "get_entities_by_ids",
        "get_relations_by_ids",
        "upsert_raw_entities",
        "delete_entities_by_ids",
    }
)
GRAPH_BUILDER_COUNT_REQUIRED_METHODS = frozenset(
    {"get_relations_by_passage_ids", "get_entities_by_passage_ids"}
)


def _validate_method_signature(backend: Any, method_name: str) -> None:
    method = getattr(backend, method_name)
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError) as exc:
        raise GraphProjectionAdapterError(
            f"graph projection method {method_name} has no inspectable signature"
        ) from exc

    probes: tuple[tuple[tuple[Any, ...], dict[str, Any]], ...] = {
        "ensure_collections": (((), {}),),
        "healthy": (((), {}),),
        "upsert_entities": ((([], []), {"tenant_id": ""}),),
        "upsert_relations": ((([], []), {"tenant_id": ""}),),
        "search_entities": ((([], 1, None), {"tenant_id": ""}),),
        "search_relations": ((([], 1, None), {"tenant_id": ""}),),
        "get_entities_by_ids": (
            (([],), {"tenant_id": "", "include_vectors": False}),
            (([],), {"tenant_id": ""}),
        ),
        "get_relations_by_ids": ((([],), {"tenant_id": ""}),),
        "get_entities_by_passage_ids": (
            (([],), {"tenant_id": "", "include_vectors": False}),
            (([],), {"tenant_id": ""}),
        ),
        "get_relations_by_passage_ids": (
            (([],), {"tenant_id": "", "include_vectors": False}),
            (([],), {"tenant_id": ""}),
        ),
        "get_entities_by_texts": ((([],), {"tenant_id": ""}),),
        "get_relations_by_texts": ((([],), {"tenant_id": ""}),),
        "upsert_raw_entities": ((([],), {"tenant_id": ""}),),
        "upsert_raw_relations": ((([],), {"tenant_id": ""}),),
        "delete_entities_by_ids": ((([],), {"tenant_id": ""}),),
        "delete_relations_by_ids": ((([],), {"tenant_id": ""}),),
    }[method_name]
    for args, kwargs in probes:
        try:
            signature.bind(*args, **kwargs)
            return
        except TypeError:
            continue
    raise GraphProjectionAdapterError(
        f"graph projection method {method_name} has an incompatible signature"
    )


def _validate_backend(
    backend: Any,
    required_methods: Sequence[str],
    *,
    require_graph: bool = True,
) -> None:
    if backend is None:
        raise GraphProjectionAdapterError("graph projection backend is required")
    if require_graph and not hasattr(backend, "graph"):
        raise GraphProjectionAdapterError(
            "graph projection backend must expose graph settings"
        )
    missing = [name for name in required_methods if not callable(getattr(backend, name, None))]
    if missing:
        raise GraphProjectionAdapterError(
            "graph projection backend is missing operational methods: "
            + ", ".join(missing)
        )
    for method_name in required_methods:
        _validate_method_signature(backend, method_name)


def _call_scoped(
    method: Any,
    ids: Sequence[str],
    tenant_id: str,
    *,
    include_vectors: bool,
    timeout_s: float | None = None,
) -> Any:
    """Adapt legacy fakes/backends that do not expose optional vectors."""

    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        parameters = {}
    accepts_vectors = "include_vectors" in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    kwargs: dict[str, Any] = {"tenant_id": tenant_id}
    if accepts_vectors:
        kwargs["include_vectors"] = include_vectors
    return _call_optional_timeout(method, (ids,), kwargs, timeout_s=timeout_s)


def _accepts_keyword(method: Any, name: str) -> bool:
    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        return False
    return name in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


def _call_optional_timeout(
    method: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    timeout_s: float | None,
) -> Any:
    if timeout_s is None:
        return method(*args, **kwargs)
    if not _accepts_keyword(method, "timeout_s"):
        raise GraphProjectionAdapterError(
            "graph query provider does not expose timeout_s for a per-call budget"
        )
    scoped_kwargs = dict(kwargs)
    scoped_kwargs["timeout_s"] = timeout_s
    return method(*args, **scoped_kwargs)


class GraphProjectionAdapter:
    """Translate a selected graph backend into the consumer operational port."""

    def __init__(
        self,
        backend: Any,
        *,
        required_methods: Sequence[str] = GRAPH_CONSUMER_REQUIRED_METHODS,
        require_graph: bool = True,
    ) -> None:
        _validate_backend(backend, required_methods, require_graph=require_graph)
        self._backend = backend

    @property
    def backend(self) -> Any:
        """Return the selected backend for diagnostics and compatibility tests."""

        return self._backend

    @property
    def graph(self) -> Any:
        return self._backend.graph

    @property
    def mode(self) -> str:
        return str(getattr(self._backend, "mode", "graph"))

    def require_methods(
        self,
        required_methods: Sequence[str],
        *,
        require_graph: bool = True,
    ) -> None:
        """Validate one consumer operation before it can cause side effects."""

        _validate_backend(
            self._backend,
            required_methods,
            require_graph=require_graph,
        )

    def supports_timeout(self, required_methods: Sequence[str]) -> bool:
        """Return whether all selected operations accept a per-call budget."""

        _validate_backend(self._backend, required_methods)
        return all(
            _accepts_keyword(getattr(self._backend, method_name), "timeout_s")
            for method_name in required_methods
        )

    def ensure_collections(self) -> None:
        self._backend.ensure_collections()

    def healthy(self) -> bool:
        return bool(self._backend.healthy())

    def upsert_entities(
        self, entities: Sequence[Any], vectors: Sequence[Any], *, tenant_id: str = ""
    ) -> Any:
        return self._backend.upsert_entities(entities, vectors, tenant_id=tenant_id)

    def upsert_relations(
        self, relations: Sequence[Any], vectors: Sequence[Any], *, tenant_id: str = ""
    ) -> Any:
        return self._backend.upsert_relations(relations, vectors, tenant_id=tenant_id)

    def search_entities(
        self,
        query_vec: Any,
        top_k: int = 20,
        threshold: float | None = None,
        tenant_id: str = "",
        *,
        timeout_s: float | None = None,
    ) -> list[Any]:
        return _call_optional_timeout(
            self._backend.search_entities,
            (query_vec, top_k, threshold),
            {"tenant_id": tenant_id},
            timeout_s=timeout_s,
        )

    def search_relations(
        self,
        query_vec: Any,
        top_k: int = 20,
        threshold: float | None = None,
        tenant_id: str = "",
        *,
        timeout_s: float | None = None,
    ) -> list[Any]:
        return _call_optional_timeout(
            self._backend.search_relations,
            (query_vec, top_k, threshold),
            {"tenant_id": tenant_id},
            timeout_s=timeout_s,
        )

    def get_entities_by_ids(
        self,
        ids: Sequence[str],
        tenant_id: str = "",
        *,
        include_vectors: bool = False,
        timeout_s: float | None = None,
    ) -> list[Any]:
        return _call_scoped(
            self._backend.get_entities_by_ids,
            ids,
            tenant_id,
            include_vectors=include_vectors,
            timeout_s=timeout_s,
        )

    def get_relations_by_ids(
        self, ids: Sequence[str], tenant_id: str = "", *, timeout_s: float | None = None
    ) -> list[Any]:
        return _call_optional_timeout(
            self._backend.get_relations_by_ids,
            (ids,),
            {"tenant_id": tenant_id},
            timeout_s=timeout_s,
        )

    def get_entities_by_passage_ids(
        self, ids: Sequence[str], tenant_id: str = "", *, include_vectors: bool = False
    ) -> list[Any]:
        return _call_scoped(
            self._backend.get_entities_by_passage_ids,
            ids,
            tenant_id,
            include_vectors=include_vectors,
        )

    def get_relations_by_passage_ids(
        self, ids: Sequence[str], tenant_id: str = "", *, include_vectors: bool = False
    ) -> list[Any]:
        return _call_scoped(
            self._backend.get_relations_by_passage_ids,
            ids,
            tenant_id,
            include_vectors=include_vectors,
        )

    def get_entities_by_texts(self, texts: Sequence[str], tenant_id: str = "") -> Any:
        return self._backend.get_entities_by_texts(texts, tenant_id=tenant_id)

    def get_relations_by_texts(self, texts: Sequence[str], tenant_id: str = "") -> Any:
        return self._backend.get_relations_by_texts(texts, tenant_id=tenant_id)

    def upsert_raw_entities(self, rows: Sequence[Any], *, tenant_id: str = "") -> Any:
        return self._backend.upsert_raw_entities(rows, tenant_id=tenant_id)

    def upsert_raw_relations(self, rows: Sequence[Any], *, tenant_id: str = "") -> Any:
        return self._backend.upsert_raw_relations(rows, tenant_id=tenant_id)

    def delete_entities_by_ids(self, ids: Sequence[str], *, tenant_id: str = "") -> int:
        return int(self._backend.delete_entities_by_ids(ids, tenant_id=tenant_id))

    def delete_relations_by_ids(self, ids: Sequence[str], *, tenant_id: str = "") -> int:
        return int(self._backend.delete_relations_by_ids(ids, tenant_id=tenant_id))


class GraphProjectionAdapterFactory:
    """Factory for the complete graph consumer Adapter."""

    @staticmethod
    def create(
        backend: Any,
        *,
        required_methods: Sequence[str] = GRAPH_CONSUMER_REQUIRED_METHODS,
        require_graph: bool = True,
    ) -> GraphProjectionAdapter:
        if isinstance(backend, GraphProjectionAdapter):
            backend.require_methods(required_methods, require_graph=require_graph)
            return backend
        return GraphProjectionAdapter(
            backend,
            required_methods=required_methods,
            require_graph=require_graph,
        )


def create_graph_projection_adapter(
    backend: Any,
    *,
    required_methods: Sequence[str] = GRAPH_CONSUMER_REQUIRED_METHODS,
    require_graph: bool = True,
) -> GraphProjectionAdapter:
    """Create or reuse the graph consumer Adapter at a consumer seam."""

    return GraphProjectionAdapterFactory.create(
        backend,
        required_methods=required_methods,
        require_graph=require_graph,
    )


__all__ = [
    "GraphProjectionAdapter",
    "GraphProjectionAdapterError",
    "GraphProjectionAdapterFactory",
    "GraphProjectionAdapterProtocol",
    "GRAPH_CONSUMER_REQUIRED_METHODS",
    "GRAPH_BUILDER_REQUIRED_METHODS",
    "GRAPH_BUILDER_WRITE_REQUIRED_METHODS",
    "GRAPH_BUILDER_DELETE_REQUIRED_METHODS",
    "GRAPH_BUILDER_COUNT_REQUIRED_METHODS",
    "GRAPH_RETRIEVER_REQUIRED_METHODS",
    "GRAPH_COLLECTION_INITIALIZATION_REQUIRED_METHODS",
    "create_graph_projection_adapter",
]
