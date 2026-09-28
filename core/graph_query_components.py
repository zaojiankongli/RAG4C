"""Factory and Adapter port for the graph visualization query API.

The retrieval pipeline and the visualization API have different orchestration
contracts.  This module gives the API a small stable port while keeping graph
engine selection in ``core.graph_store_registry`` and backend method
translation in ``core.graph_projection_adapters``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from config.settings import EmbeddingSettings
from core.embedding import EmbeddingService, create_embedder
from core.graph_projection_adapters import (
    GRAPH_RETRIEVER_REQUIRED_METHODS,
    GraphProjectionAdapter,
    GraphProjectionAdapterError,
    create_graph_projection_adapter,
)
from core.graph_store import RagGraphStoreError
from core.graph_store_registry import (
    GraphEngineType,
    GraphStoreError,
    GraphStoreRegistry,
    GraphStoreProtocol,
    create_graph_store,
)
from core.retry import remaining_budget


class GraphQueryAssemblyError(RuntimeError):
    """Graph query dependencies could not be assembled."""

    def __init__(self, message: str, *, kind: str = "graph") -> None:
        super().__init__(message)
        self.kind = kind


GRAPH_QUERY_TIMEOUT_CONTRACTS = frozenset({"per_call", "soft"})


@runtime_checkable
class GraphQueryTimeoutContractProtocol(Protocol):
    """Provider declaration required when a graph budget is enforced."""

    graph_query_timeout_contract: str


@runtime_checkable
class GraphQueryPerCallEmbedderProtocol(Protocol):
    """Embedding port that can receive a remaining per-call budget."""

    def embed_query_with_timeout(self, text: str, *, timeout_s: float) -> list[float]: ...


class GraphQueryTimeoutContractError(GraphQueryAssemblyError):
    """A graph provider did not declare how it observes a query budget."""

    def __init__(self, message: str) -> None:
        super().__init__(message, kind="timeout_contract")


class GraphQueryEmbeddingError(RuntimeError):
    """The graph query embedder failed for one request."""


class GraphQueryBackendError(RuntimeError):
    """The graph query backend failed for one request."""


class GraphQueryTimeoutError(GraphQueryBackendError):
    """The cooperative graph query budget is exhausted between provider calls."""


@dataclass(frozen=True)
class GraphQuerySearchResult:
    """Stable result port for the visualization search route."""

    entities: list[dict[str, Any]]
    relations: list[dict[str, Any]]

    def as_payload(self) -> dict[str, Any]:
        return {"entities": self.entities, "relations": self.relations}


@dataclass(frozen=True)
class GraphQuerySubgraphResult:
    """Stable result port for the visualization subgraph route."""

    entities: list[dict[str, Any]]
    relations: list[dict[str, Any]]

    def as_payload(self) -> dict[str, Any]:
        return {"entities": self.entities, "relations": self.relations}


class GraphQueryComponentProtocol(Protocol):
    """API-facing port independent of the selected graph backend."""

    @property
    def store(self) -> GraphStoreProtocol: ...

    @property
    def embedder(self) -> EmbeddingService: ...

    def search(
        self,
        query: str,
        *,
        entity_top_k: int,
        relation_top_k: int,
        tenant_id: str = "",
    ) -> GraphQuerySearchResult: ...

    def subgraph(
        self,
        *,
        entity_ids: Sequence[str],
        relation_ids: Sequence[str],
        degree: int = 1,
        tenant_id: str = "",
    ) -> GraphQuerySubgraphResult: ...


def _bounded_timeout(current: Any, cap: float) -> float:
    """Return a positive provider timeout that never exceeds the graph budget."""

    try:
        current_value = float(current)
    except (TypeError, ValueError):
        return cap
    if current_value <= 0:
        return cap
    return min(current_value, cap)


def _bounded_settings(settings: Any, timeout_s: float | None) -> tuple[Any, Any]:
    """Cap built-in timeout-bearing settings without mutating the caller's model."""

    if not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
        return settings, getattr(settings, "embedding", None)
    cap = float(timeout_s)
    updates: dict[str, Any] = {}

    milvus = getattr(settings, "milvus", None)
    if hasattr(milvus, "model_copy"):
        updates["milvus"] = milvus.model_copy(
            update={"timeout": _bounded_timeout(getattr(milvus, "timeout", cap), cap)}
        )

    embedding = getattr(settings, "embedding", None)
    effective_embedding = embedding
    if hasattr(embedding, "model_copy"):
        effective_embedding = embedding.model_copy(
            update={
                "api_timeout": _bounded_timeout(
                    getattr(embedding, "api_timeout", cap),
                    cap,
                )
            }
        )
        if effective_embedding is not embedding:
            updates["embedding"] = effective_embedding

    if updates and hasattr(settings, "model_copy"):
        return settings.model_copy(update=updates), effective_embedding
    return settings, effective_embedding


def _validate_timeout_contract(provider: Any, *, role: str, timeout_s: float | None) -> str:
    """Require every provider to make its timeout boundary explicit."""

    if not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
        return "unbounded"
    if not isinstance(provider, GraphQueryTimeoutContractProtocol):
        raise GraphQueryTimeoutContractError(
            f"图查询 {role} 未实现 timeout contract；"
            "应声明 graph_query_timeout_contract"
        )
    contract = provider.graph_query_timeout_contract
    if type(contract) is not str or contract not in GRAPH_QUERY_TIMEOUT_CONTRACTS:
        raise GraphQueryTimeoutContractError(
            f"图查询 {role} 未声明有效 timeout contract；"
            "应为 'per_call' 或 'soft'"
        )
    return contract


def _project_entity(hit: dict[str, Any]) -> dict[str, Any]:
    return {
        key: hit.get(key)
        for key in ("id", "text", "relation_ids", "passage_ids")
    } | {"score": round(float(hit.get("distance", 0.0)), 4)}


def _project_relation(hit: dict[str, Any]) -> dict[str, Any]:
    return {
        key: hit.get(key)
        for key in (
            "id",
            "text",
            "entity_ids",
            "passage_ids",
            "subject",
            "predicate",
            "object",
        )
    } | {"score": round(float(hit.get("distance", 0.0)), 4)}


class GraphQueryComponent:
    """Concrete API component using the graph consumer Adapter port."""

    def __init__(
        self,
        *,
        store: GraphStoreProtocol,
        embedder: EmbeddingService,
        timeout_s: float | None = None,
    ) -> None:
        try:
            adapter = create_graph_projection_adapter(
                store,
                required_methods=GRAPH_RETRIEVER_REQUIRED_METHODS,
            )
        except GraphProjectionAdapterError as exc:
            raise GraphQueryAssemblyError(
                f"图查询存储不满足检索 Adapter 契约: {exc}",
                kind="adapter",
            ) from exc
        self._timeout_s = (
            float(timeout_s)
            if isinstance(timeout_s, (int, float)) and timeout_s > 0
            else None
        )
        self._store_timeout_contract = _validate_timeout_contract(
            store,
            role="存储",
            timeout_s=self._timeout_s,
        )
        self._embedder_timeout_contract = _validate_timeout_contract(
            embedder,
            role="嵌入服务",
            timeout_s=self._timeout_s,
        )
        if self._timeout_s is not None:
            if (
                self._store_timeout_contract == "per_call"
                and not adapter.supports_timeout(GRAPH_RETRIEVER_REQUIRED_METHODS)
            ):
                raise GraphQueryTimeoutContractError(
                    "图查询存储声明 per_call，但检索 Adapter 方法未接收 timeout_s"
                )
            if (
                self._embedder_timeout_contract == "per_call"
                and not isinstance(embedder, GraphQueryPerCallEmbedderProtocol)
            ):
                raise GraphQueryTimeoutContractError(
                    "图查询嵌入服务声明 per_call，但未实现 embed_query_with_timeout"
                )
        self._store = store
        self._adapter: GraphProjectionAdapter = adapter
        self._embedder = embedder

    @property
    def store(self) -> GraphStoreProtocol:
        """Compatibility projection for diagnostics and cache invalidation."""

        return self._store

    @property
    def adapter(self) -> GraphProjectionAdapter:
        return self._adapter

    @property
    def embedder(self) -> EmbeddingService:
        return self._embedder

    def _call_budget(self, stage: str) -> float | None:
        """Return the smaller of the route and component budgets."""

        budget = remaining_budget()
        if budget is not None and budget <= 0:
            raise GraphQueryTimeoutError(f"图查询预算已耗尽，未开始 {stage}")
        if self._timeout_s is None:
            return budget
        if budget is None:
            return self._timeout_s
        return min(self._timeout_s, budget)

    def search(
        self,
        query: str,
        *,
        entity_top_k: int,
        relation_top_k: int,
        tenant_id: str = "",
    ) -> GraphQuerySearchResult:
        embedding_budget = self._call_budget("嵌入")
        try:
            if (
                self._timeout_s is not None
                and self._embedder_timeout_contract == "per_call"
            ):
                query_vec = self._embedder.embed_query_with_timeout(
                    query,
                    timeout_s=embedding_budget or self._timeout_s,
                )
            else:
                query_vec = self._embedder.embed_query(query)
        except GraphQueryTimeoutError:
            raise
        except TimeoutError as exc:
            raise GraphQueryTimeoutError(f"图谱嵌入预算耗尽: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - normalize provider failures
            raise GraphQueryEmbeddingError(f"图谱查询嵌入失败: {exc}") from exc
        try:
            entity_budget = self._call_budget("实体检索")
            relation_budget: float | None = None
            if (
                self._timeout_s is not None
                and self._store_timeout_contract == "per_call"
            ):
                entity_hits = self._adapter.search_entities(
                    query_vec,
                    top_k=entity_top_k,
                    threshold=0.0,
                    tenant_id=tenant_id,
                    timeout_s=entity_budget or self._timeout_s,
                )
            else:
                entity_hits = self._adapter.search_entities(
                    query_vec,
                    top_k=entity_top_k,
                    threshold=0.0,
                    tenant_id=tenant_id,
                )
            relation_budget = self._call_budget("关系检索")
            if (
                self._timeout_s is not None
                and self._store_timeout_contract == "per_call"
            ):
                relation_hits = self._adapter.search_relations(
                    query_vec,
                    top_k=relation_top_k,
                    threshold=0.0,
                    tenant_id=tenant_id,
                    timeout_s=relation_budget or self._timeout_s,
                )
            else:
                relation_hits = self._adapter.search_relations(
                    query_vec,
                    top_k=relation_top_k,
                    threshold=0.0,
                    tenant_id=tenant_id,
                )
        except (RagGraphStoreError, GraphProjectionAdapterError) as exc:
            raise GraphQueryBackendError(f"图谱查询存储失败: {exc}") from exc
        except GraphQueryTimeoutError:
            raise
        except Exception as exc:  # noqa: BLE001 - backend implementations vary
            raise GraphQueryBackendError(f"图谱查询存储失败: {exc}") from exc
        return GraphQuerySearchResult(
            entities=[_project_entity(hit) for hit in entity_hits],
            relations=[_project_relation(hit) for hit in relation_hits],
        )

    def subgraph(
        self,
        *,
        entity_ids: Sequence[str],
        relation_ids: Sequence[str],
        degree: int = 1,
        tenant_id: str = "",
    ) -> GraphQuerySubgraphResult:
        try:
            entities: list[dict[str, Any]] = []
            relations: list[dict[str, Any]] = []
            if entity_ids:
                entity_budget = self._call_budget("实体读取")
                if (
                    self._timeout_s is not None
                    and self._store_timeout_contract == "per_call"
                ):
                    entities = self._adapter.get_entities_by_ids(
                        entity_ids,
                        tenant_id=tenant_id,
                        timeout_s=entity_budget or self._timeout_s,
                    )
                else:
                    entities = self._adapter.get_entities_by_ids(
                        entity_ids,
                        tenant_id=tenant_id,
                    )
            if relation_ids:
                relation_budget = self._call_budget("关系读取")
                if (
                    self._timeout_s is not None
                    and self._store_timeout_contract == "per_call"
                ):
                    relations = self._adapter.get_relations_by_ids(
                        relation_ids,
                        tenant_id=tenant_id,
                        timeout_s=relation_budget or self._timeout_s,
                    )
                else:
                    relations = self._adapter.get_relations_by_ids(
                        relation_ids,
                        tenant_id=tenant_id,
                    )
            if entities and degree >= 1:
                expansion_budget = self._call_budget("子图关系扩展")
                all_relation_ids = list(dict.fromkeys(relation_ids))
                for entity in entities:
                    for relation_id in entity.get("relation_ids") or []:
                        if relation_id not in all_relation_ids:
                            all_relation_ids.append(relation_id)
                if (
                    self._timeout_s is not None
                    and self._store_timeout_contract == "per_call"
                ):
                    relations = self._adapter.get_relations_by_ids(
                        all_relation_ids,
                        tenant_id=tenant_id,
                        timeout_s=expansion_budget or self._timeout_s,
                    )
                else:
                    relations = self._adapter.get_relations_by_ids(
                        all_relation_ids,
                        tenant_id=tenant_id,
                    )
        except (RagGraphStoreError, GraphProjectionAdapterError) as exc:
            raise GraphQueryBackendError(f"图谱子图读取失败: {exc}") from exc
        except GraphQueryTimeoutError:
            raise
        except Exception as exc:  # noqa: BLE001 - backend implementations vary
            raise GraphQueryBackendError(f"图谱子图读取失败: {exc}") from exc
        return GraphQuerySubgraphResult(entities=entities, relations=relations)


class GraphQueryComponentFactory:
    """Assemble the API graph component with injectable infrastructure."""

    @staticmethod
    def create(
        settings: Any,
        *,
        engine_override: GraphEngineType | None = "milvus_vector_graph",
        timeout_s: float | None = None,
        store_factory: Callable[..., GraphStoreProtocol] = create_graph_store,
        embedder_factory: Callable[[EmbeddingSettings], EmbeddingService] = create_embedder,
    ) -> GraphQueryComponent:
        effective_settings, effective_embedding_settings = _bounded_settings(
            settings,
            timeout_s,
        )
        try:
            store_kwargs: dict[str, Any] = {"engine_override": engine_override}
            if (
                store_factory is create_graph_store
                and isinstance(timeout_s, (int, float))
                and timeout_s > 0
            ):
                # ``create_graph_store`` has a process registry keyed only by
                # engine name.  Use a component-owned registry for bounded
                # query assembly so an older pipeline store with a larger
                # Milvus timeout cannot bypass the new graph budget.
                store_kwargs["registry"] = GraphStoreRegistry()
            store = store_factory(effective_settings, **store_kwargs)
        except (GraphStoreError, RagGraphStoreError) as exc:
            raise GraphQueryAssemblyError(
                f"图谱存储不可用: {exc}",
                kind="store",
            ) from exc
        except Exception as exc:  # noqa: BLE001 - factory adapters vary
            raise GraphQueryAssemblyError(
                f"图谱存储装配失败: {exc}",
                kind="store",
            ) from exc
        _validate_timeout_contract(store, role="存储", timeout_s=timeout_s)

        try:
            if effective_embedding_settings is None:
                raise ValueError("settings.embedding is required")
            embedder = embedder_factory(effective_embedding_settings)
        except Exception as exc:  # noqa: BLE001 - provider dependencies vary
            raise GraphQueryAssemblyError(
                f"图谱嵌入服务不可用: {exc}",
                kind="embedding",
            ) from exc
        _validate_timeout_contract(embedder, role="嵌入服务", timeout_s=timeout_s)

        return GraphQueryComponent(
            store=store,
            embedder=embedder,
            timeout_s=timeout_s,
        )


def create_graph_query_component(
    settings: Any,
    *,
    engine_override: GraphEngineType | None = "milvus_vector_graph",
    timeout_s: float | None = None,
    store_factory: Callable[..., GraphStoreProtocol] = create_graph_store,
    embedder_factory: Callable[[EmbeddingSettings], EmbeddingService] = create_embedder,
) -> GraphQueryComponent:
    """Public factory seam for graph API/query assembly."""

    return GraphQueryComponentFactory.create(
        settings,
        engine_override=engine_override,
        timeout_s=timeout_s,
        store_factory=store_factory,
        embedder_factory=embedder_factory,
    )


__all__ = [
    "GraphQueryAssemblyError",
    "GraphQueryBackendError",
    "GraphQueryComponent",
    "GraphQueryComponentFactory",
    "GraphQueryComponentProtocol",
    "GraphQueryEmbeddingError",
    "GraphQueryPerCallEmbedderProtocol",
    "GraphQuerySearchResult",
    "GraphQuerySubgraphResult",
    "GraphQueryTimeoutError",
    "GraphQueryTimeoutContractError",
    "GraphQueryTimeoutContractProtocol",
    "GRAPH_QUERY_TIMEOUT_CONTRACTS",
    "create_graph_query_component",
]
