"""Immutable semantic topology snapshots for RAG query runs."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from core.run_events import JsonValue


TopologyGroup = Literal[
    "input",
    "understand",
    "retrieve",
    "generate",
    "verify",
    "output",
    "extension",
]
TopologyEdgeKind = Literal["dependency", "conditional", "retry", "failure"]
RagExecutor = Literal["sequential_stream", "sequential", "langgraph"]


class _FrozenDict(dict[str, JsonValue]):
    """Detached JSON object that rejects mutation after publication."""

    def _immutable(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("published topology attributes are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


class _FrozenList(list[JsonValue]):
    """Detached JSON array that rejects mutation after publication."""

    def _immutable(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("published topology attributes are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    append = _immutable
    clear = _immutable
    extend = _immutable
    insert = _immutable
    pop = _immutable
    remove = _immutable
    reverse = _immutable
    sort = _immutable
    __iadd__ = _immutable
    __imul__ = _immutable


def _freeze_json(value: Any) -> JsonValue:
    if isinstance(value, Mapping):
        return _FrozenDict({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return _FrozenList(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> JsonValue:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_thaw_json(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class TopologyNode:
    id: str
    label: str
    group: TopologyGroup
    description: str
    optional: bool = False
    repeatable: bool = False
    available: bool = True
    plugin: str | None = None
    attributes: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", _freeze_json(self.attributes))


@dataclass(frozen=True, slots=True)
class TopologyEdge:
    id: str
    source: str
    target: str
    kind: TopologyEdgeKind
    label: str | None = None


@dataclass(frozen=True, slots=True)
class RagTopology:
    id: str
    revision: str
    executor: str
    nodes: tuple[TopologyNode, ...]
    edges: tuple[TopologyEdge, ...]


@dataclass(frozen=True, slots=True)
class TopologyContext:
    executor: RagExecutor
    enabled_components: frozenset[str]
    available_components: frozenset[str]


@dataclass(frozen=True, slots=True)
class TopologyFragment:
    nodes: tuple[TopologyNode, ...] = ()
    edges: tuple[TopologyEdge, ...] = ()


class RagTopologyPlugin(Protocol):
    name: str
    priority: int

    def contribute(self, context: TopologyContext) -> TopologyFragment: ...


CORE_NODES = (
    TopologyNode("receive", "Receive query", "input", "Accept and validate the query."),
    TopologyNode(
        "complexity_gate",
        "Complexity gate",
        "understand",
        "Decide whether query rewriting is needed.",
    ),
    TopologyNode(
        "rewrite",
        "Rewrite query",
        "understand",
        "Produce a retrieval-oriented query when needed.",
        optional=True,
    ),
    TopologyNode("route", "Route query", "understand", "Select the retrieval route."),
    TopologyNode("embed", "Embed query", "retrieve", "Create the retrieval embedding."),
    TopologyNode("search", "Search", "retrieve", "Retrieve candidate chunks."),
    TopologyNode(
        "diversity",
        "Source diversity",
        "retrieve",
        "Apply configured grouping or diversity policy.",
        optional=True,
    ),
    TopologyNode(
        "gate.retrieval",
        "Retrieval gate",
        "verify",
        "Decide whether retrieval evidence is sufficient.",
    ),
    TopologyNode("generate", "Generate answer", "generate", "Generate a grounded answer."),
    TopologyNode(
        "verify",
        "Verify answer",
        "verify",
        "Verify citations and answer support.",
        repeatable=True,
    ),
    TopologyNode(
        "gate.final",
        "Final gate",
        "verify",
        "Accept the answer or request one retrieval retry.",
    ),
    TopologyNode("finalize", "Finalize", "output", "Return the terminal query result."),
)


CORE_EDGES = (
    TopologyEdge("receive.complexity_gate", "receive", "complexity_gate", "dependency"),
    TopologyEdge(
        "complexity_gate.rewrite",
        "complexity_gate",
        "rewrite",
        "conditional",
        "rewrite",
    ),
    TopologyEdge(
        "complexity_gate.route",
        "complexity_gate",
        "route",
        "conditional",
        "already simple",
    ),
    TopologyEdge("rewrite.route", "rewrite", "route", "dependency"),
    TopologyEdge("route.embed", "route", "embed", "dependency"),
    TopologyEdge("embed.search", "embed", "search", "dependency"),
    TopologyEdge("search.diversity", "search", "diversity", "dependency"),
    TopologyEdge(
        "diversity.gate.retrieval",
        "diversity",
        "gate.retrieval",
        "dependency",
    ),
    TopologyEdge(
        "gate.retrieval.generate",
        "gate.retrieval",
        "generate",
        "conditional",
        "sufficient evidence",
    ),
    TopologyEdge(
        "gate.retrieval.finalize",
        "gate.retrieval",
        "finalize",
        "conditional",
        "abstain",
    ),
    TopologyEdge("generate.verify", "generate", "verify", "dependency"),
    TopologyEdge("verify.gate.final", "verify", "gate.final", "dependency"),
    TopologyEdge(
        "gate.final.finalize",
        "gate.final",
        "finalize",
        "conditional",
        "accepted",
    ),
    TopologyEdge(
        "verify.search.retry",
        "verify",
        "search",
        "retry",
        "retry retrieval",
    ),
)


@dataclass(frozen=True, slots=True)
class _OptionalComponent:
    name: str
    node: TopologyNode
    edges: tuple[TopologyEdge, ...]


OPTIONAL_COMPONENTS = (
    _OptionalComponent(
        "hyde",
        TopologyNode(
            "plugin.hyde.expand",
            "HyDE expansion",
            "extension",
            "Generate a hypothetical answer for retrieval embedding.",
            optional=True,
            plugin="hyde",
        ),
        (
            TopologyEdge(
                "route.plugin.hyde.expand", "route", "plugin.hyde.expand", "dependency"
            ),
            TopologyEdge(
                "plugin.hyde.expand.embed", "plugin.hyde.expand", "embed", "dependency"
            ),
        ),
    ),
    _OptionalComponent(
        "subqueries",
        TopologyNode(
            "plugin.subqueries.expand",
            "Subquery expansion",
            "extension",
            "Retrieve and merge decomposed subqueries.",
            optional=True,
            plugin="subqueries",
        ),
        (
            TopologyEdge(
                "search.plugin.subqueries.expand",
                "search",
                "plugin.subqueries.expand",
                "dependency",
            ),
            TopologyEdge(
                "plugin.subqueries.expand.diversity",
                "plugin.subqueries.expand",
                "diversity",
                "dependency",
            ),
        ),
    ),
    _OptionalComponent(
        "stepback",
        TopologyNode(
            "plugin.stepback.expand",
            "Step-back expansion",
            "extension",
            "Retrieve and merge a more abstract step-back query.",
            optional=True,
            plugin="stepback",
        ),
        (
            TopologyEdge(
                "search.plugin.stepback.expand",
                "search",
                "plugin.stepback.expand",
                "dependency",
            ),
            TopologyEdge(
                "plugin.stepback.expand.diversity",
                "plugin.stepback.expand",
                "diversity",
                "dependency",
            ),
        ),
    ),
    _OptionalComponent(
        "graph",
        TopologyNode(
            "graph.retrieve",
            "Graph retrieval",
            "retrieve",
            "Retrieve and merge graph evidence.",
            optional=True,
            plugin="graph",
        ),
        (
            TopologyEdge(
                "diversity.graph.retrieve", "diversity", "graph.retrieve", "conditional"
            ),
            TopologyEdge(
                "graph.retrieve.gate.retrieval",
                "graph.retrieve",
                "gate.retrieval",
                "dependency",
            ),
        ),
    ),
    _OptionalComponent(
        "rerank",
        TopologyNode(
            "rerank",
            "Rerank",
            "retrieve",
            "Rerank candidate chunks.",
            optional=True,
            plugin="rerank",
        ),
        (
            TopologyEdge("diversity.rerank", "diversity", "rerank", "dependency"),
            TopologyEdge(
                "rerank.gate.retrieval", "rerank", "gate.retrieval", "dependency"
            ),
        ),
    ),
    _OptionalComponent(
        "sentence_window",
        TopologyNode(
            "sentence_window",
            "Sentence window",
            "retrieve",
            "Expand matching child chunks to their parent context.",
            optional=True,
            plugin="sentence_window",
        ),
        (
            TopologyEdge(
                "diversity.sentence_window",
                "diversity",
                "sentence_window",
                "dependency",
            ),
            TopologyEdge(
                "sentence_window.gate.retrieval",
                "sentence_window",
                "gate.retrieval",
                "dependency",
            ),
        ),
    ),
)


def _node_dict(node: TopologyNode) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "id": node.id,
        "label": node.label,
        "group": node.group,
        "description": node.description,
        "optional": node.optional,
        "repeatable": node.repeatable,
        "available": node.available,
        "attributes": _thaw_json(node.attributes),
    }
    if node.plugin is not None:
        payload["plugin"] = node.plugin
    return payload


def _edge_dict(edge: TopologyEdge) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "id": edge.id,
        "source": edge.source,
        "target": edge.target,
        "kind": edge.kind,
    }
    if edge.label is not None:
        payload["label"] = edge.label
    return payload


def _topology_payload(
    topology_id: str,
    executor: str,
    nodes: Iterable[TopologyNode],
    edges: Iterable[TopologyEdge],
) -> dict[str, JsonValue]:
    return {
        "id": topology_id,
        "executor": executor,
        "nodes": [_node_dict(node) for node in sorted(nodes, key=lambda item: item.id)],
        "edges": [_edge_dict(edge) for edge in sorted(edges, key=lambda item: item.id)],
    }


def _topology_revision(payload: Mapping[str, JsonValue]) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def topology_dict(topology: RagTopology) -> dict[str, JsonValue]:
    """Return the canonical JSON-compatible topology snapshot."""

    payload = _topology_payload(topology.id, topology.executor, topology.nodes, topology.edges)
    return {"revision": topology.revision, **payload}


def _validate_topology(
    nodes: Iterable[TopologyNode],
    edges: Iterable[TopologyEdge],
) -> None:
    node_ids: set[str] = set()
    for node in nodes:
        if node.id in node_ids:
            raise ValueError(f"duplicate topology node: {node.id}")
        node_ids.add(node.id)

    edge_ids: set[str] = set()
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for edge in edges:
        if edge.id in edge_ids:
            raise ValueError(f"duplicate topology edge: {edge.id}")
        edge_ids.add(edge.id)
        if edge.source not in node_ids or edge.target not in node_ids:
            raise ValueError(f"unknown topology edge endpoint: {edge.id}")
        if edge.kind == "retry":
            if (edge.source, edge.target) != ("verify", "search"):
                raise ValueError("only verify -> search may be a topology retry edge")
            continue
        adjacency[edge.source].append(edge.target)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise ValueError(f"topology contains a non-retry cycle at {node_id}")
        if node_id in visited:
            return
        visiting.add(node_id)
        for target in adjacency[node_id]:
            visit(target)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in sorted(node_ids):
        visit(node_id)


def build_rag_topology(
    *,
    executor: RagExecutor,
    enabled_components: frozenset[str],
    available_components: frozenset[str],
    plugins: Iterable[RagTopologyPlugin] = (),
) -> RagTopology:
    """Build one immutable topology from effective configuration and assembly."""

    context = TopologyContext(
        executor=executor,
        enabled_components=enabled_components,
        available_components=available_components,
    )
    nodes = list(CORE_NODES)
    edges = list(CORE_EDGES)
    for component in OPTIONAL_COMPONENTS:
        if component.name not in enabled_components:
            continue
        node = component.node
        if component.name not in available_components:
            node = TopologyNode(
                id=node.id,
                label=node.label,
                group=node.group,
                description=node.description,
                optional=node.optional,
                repeatable=node.repeatable,
                available=False,
                plugin=node.plugin,
                attributes=node.attributes,
            )
        nodes.append(node)
        edges.extend(component.edges)

    node_ids = {node.id for node in nodes}
    edge_ids = {edge.id for edge in edges}
    for plugin in sorted(plugins, key=lambda item: (item.priority, item.name)):
        fragment = plugin.contribute(context)
        for node in fragment.nodes:
            if node.id in node_ids:
                raise ValueError(f"duplicate topology node: {node.id}")
            if not node.id.startswith(f"plugin.{plugin.name}."):
                raise ValueError(
                    f"plugin node must use namespace plugin.{plugin.name}: {node.id}"
                )
            node_ids.add(node.id)
            nodes.append(node)
        for edge in fragment.edges:
            if edge.id in edge_ids:
                raise ValueError(f"duplicate topology edge: {edge.id}")
            if edge.source not in node_ids or edge.target not in node_ids:
                raise ValueError(f"unknown topology edge endpoint: {edge.id}")
            edge_ids.add(edge.id)
            edges.append(edge)

    _validate_topology(nodes, edges)
    payload = _topology_payload("rag.query", executor, nodes, edges)
    return RagTopology(
        id="rag.query",
        revision=_topology_revision(payload),
        executor=executor,
        nodes=tuple(nodes),
        edges=tuple(edges),
    )


def build_cache_replay_topology() -> RagTopology:
    """Build the distinct topology used when an answer is replayed from cache."""

    nodes = (
        TopologyNode(
            "cache.lookup",
            "Cache lookup",
            "input",
            "Resolve the cached answer for this request.",
        ),
        TopologyNode(
            "cache.replay",
            "Cache replay",
            "output",
            "Replay the cached answer without executing RAG.",
        ),
        TopologyNode("finalize", "Finalize", "output", "Return the terminal query result."),
    )
    edges = (
        TopologyEdge("cache.lookup.replay", "cache.lookup", "cache.replay", "dependency"),
        TopologyEdge("cache.replay.finalize", "cache.replay", "finalize", "dependency"),
    )
    _validate_topology(nodes, edges)
    payload = _topology_payload("rag.cache_replay", "cache_replay", nodes, edges)
    return RagTopology(
        id="rag.cache_replay",
        revision=_topology_revision(payload),
        executor="cache_replay",
        nodes=nodes,
        edges=edges,
    )


_SETTING_COMPONENTS = (
    ("hyde", "hyde_on"),
    ("subqueries", "subqueries_on"),
    ("stepback", "stepback_on"),
    ("graph", "graph_retrieval_on"),
    ("rerank", "rerank_on"),
    ("sentence_window", "sentence_window_on"),
)

_PIPELINE_COMPONENTS = (
    ("hyde", "hyde"),
    ("subqueries", "subqueries"),
    ("stepback", "stepback"),
    ("graph", "graph_retriever"),
    ("rerank", "reranker"),
    ("sentence_window", "sentence_window"),
)


def enabled_components_from_settings(settings: Any) -> frozenset[str]:
    """Return optional components requested by the effective settings."""

    pipeline_settings = getattr(settings, "pipeline", settings)
    return frozenset(
        name
        for name, setting_name in _SETTING_COMPONENTS
        if bool(getattr(pipeline_settings, setting_name, False))
    )


def available_components_from_pipeline(pipeline: Any) -> frozenset[str]:
    """Return optional components actually assembled in a pipeline snapshot."""

    retrieval = pipeline.get("retrieval") if isinstance(pipeline, Mapping) else pipeline
    if retrieval is None:
        return frozenset()
    return frozenset(
        name
        for name, attribute_name in _PIPELINE_COMPONENTS
        if getattr(retrieval, attribute_name, None) is not None
    )


__all__ = [
    "RagTopology",
    "RagTopologyPlugin",
    "TopologyContext",
    "TopologyEdge",
    "TopologyFragment",
    "TopologyNode",
    "available_components_from_pipeline",
    "build_cache_replay_topology",
    "build_rag_topology",
    "enabled_components_from_settings",
    "topology_dict",
]
