from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import FrozenInstanceError, dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from rag_topology import (
    RagTopology,
    TopologyContext,
    TopologyEdge,
    TopologyFragment,
    TopologyNode,
    available_components_from_pipeline,
    build_cache_replay_topology,
    build_rag_topology,
    enabled_components_from_settings,
    topology_dict,
)


EXPECTED_CORE_IDS = [
    "receive",
    "complexity_gate",
    "rewrite",
    "route",
    "embed",
    "search",
    "diversity",
    "gate.retrieval",
    "generate",
    "verify",
    "gate.final",
    "finalize",
]

EXPECTED_CORE_EDGES = [
    ("receive.complexity_gate", "receive", "complexity_gate", "dependency"),
    ("complexity_gate.rewrite", "complexity_gate", "rewrite", "conditional"),
    ("complexity_gate.route", "complexity_gate", "route", "conditional"),
    ("rewrite.route", "rewrite", "route", "dependency"),
    ("route.embed", "route", "embed", "dependency"),
    ("embed.search", "embed", "search", "dependency"),
    ("search.diversity", "search", "diversity", "dependency"),
    ("diversity.gate.retrieval", "diversity", "gate.retrieval", "dependency"),
    ("gate.retrieval.generate", "gate.retrieval", "generate", "conditional"),
    ("gate.retrieval.finalize", "gate.retrieval", "finalize", "conditional"),
    ("generate.verify", "generate", "verify", "dependency"),
    ("verify.gate.final", "verify", "gate.final", "dependency"),
    ("gate.final.finalize", "gate.final", "finalize", "conditional"),
    ("verify.search.retry", "verify", "search", "retry"),
]


@dataclass(frozen=True)
class _Plugin:
    name: str
    priority: int
    nodes: tuple[TopologyNode, ...] = ()
    edges: tuple[TopologyEdge, ...] = ()

    def contribute(self, context: TopologyContext) -> TopologyFragment:
        assert context.executor == "sequential_stream"
        return TopologyFragment(nodes=self.nodes, edges=self.edges)


def _plugin_node(plugin: str, operation: str = "run") -> TopologyNode:
    return TopologyNode(
        id=f"plugin.{plugin}.{operation}",
        label=plugin,
        group="extension",
        description=f"{plugin} test node",
        optional=True,
        plugin=plugin,
    )


def _build(**overrides: object) -> RagTopology:
    kwargs: dict[str, object] = {
        "executor": "sequential_stream",
        "enabled_components": frozenset(),
        "available_components": frozenset(),
    }
    kwargs.update(overrides)
    return build_rag_topology(**kwargs)  # type: ignore[arg-type]


def test_topology_models_are_frozen() -> None:
    node = _plugin_node("frozen")
    edge = TopologyEdge(
        id="frozen.edge",
        source="receive",
        target=node.id,
        kind="dependency",
    )
    fragment = TopologyFragment(nodes=(node,), edges=(edge,))
    topology = _build(plugins=[_Plugin("frozen", 1, fragment.nodes, fragment.edges)])

    for model, attribute, value in (
        (node, "label", "changed"),
        (edge, "label", "changed"),
        (fragment, "nodes", ()),
        (topology, "revision", "changed"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(model, attribute, value)


def test_topology_attributes_are_detached_deeply_frozen_and_exported_as_plain_json() -> None:
    original: dict[str, Any] = {
        "nested": {"items": [{"state": "initial"}], "labels": ["alpha"]}
    }
    node = TopologyNode(
        id="plugin.attributes.inspect",
        label="attributes",
        group="extension",
        description="attribute immutability regression",
        plugin="attributes",
        attributes=original,
    )
    topology = _build(plugins=[_Plugin("attributes", 1, nodes=(node,))])
    expected = json.loads(json.dumps(topology_dict(topology)))
    revision = topology.revision

    original["nested"]["items"][0]["state"] = "mutated outside"
    original["nested"]["labels"].append("outside")

    assert topology_dict(topology) == expected
    assert topology.revision == revision

    stored: Any = topology.nodes[-1].attributes
    with pytest.raises(TypeError):
        stored["nested"]["items"].append({"state": "stored mutation"})
    with pytest.raises(TypeError):
        stored["nested"]["items"][0]["state"] = "stored mutation"

    exported: Any = topology_dict(topology)
    exported_node = next(
        item for item in exported["nodes"] if item["id"] == "plugin.attributes.inspect"
    )
    assert type(exported_node["attributes"]) is dict
    assert type(exported_node["attributes"]["nested"]) is dict
    assert type(exported_node["attributes"]["nested"]["items"]) is list
    assert type(exported_node["attributes"]["nested"]["items"][0]) is dict

    exported_node["attributes"]["nested"]["items"][0]["state"] = "export mutation"
    exported_node["attributes"]["nested"]["labels"].append("export")

    assert topology_dict(topology) == expected
    assert topology.revision == revision


def test_core_topology_uses_stable_semantic_ids_and_edges() -> None:
    topology = _build()

    assert [node.id for node in topology.nodes] == EXPECTED_CORE_IDS
    assert [
        (edge.id, edge.source, edge.target, edge.kind) for edge in topology.edges
    ] == EXPECTED_CORE_EDGES


def test_configured_but_unavailable_components_remain_in_snapshot() -> None:
    topology = _build(
        enabled_components=frozenset(
            {"hyde", "subqueries", "stepback", "graph", "rerank", "sentence_window"}
        ),
        available_components=frozenset({"hyde", "rerank"}),
    )
    nodes = {node.id: node for node in topology.nodes}

    assert nodes["plugin.hyde.expand"].available is True
    assert nodes["plugin.subqueries.expand"].available is False
    assert nodes["plugin.stepback.expand"].available is False
    assert nodes["graph.retrieve"].available is False
    assert nodes["rerank"].available is True
    assert nodes["sentence_window"].available is False


def test_disabled_optional_components_are_not_in_snapshot() -> None:
    topology = _build(
        enabled_components=frozenset({"hyde"}),
        available_components=frozenset({"hyde", "rerank", "graph"}),
    )

    configured_component_ids = {
        "plugin.hyde.expand",
        "plugin.subqueries.expand",
        "plugin.stepback.expand",
        "graph.retrieve",
        "rerank",
        "sentence_window",
    }
    assert [
        node.id for node in topology.nodes if node.id in configured_component_ids
    ] == ["plugin.hyde.expand"]


def test_plugin_order_does_not_change_topology_or_revision() -> None:
    plugin_a = _Plugin(name="a", priority=10, nodes=(_plugin_node("a"),))
    plugin_b = _Plugin(name="b", priority=20, nodes=(_plugin_node("b"),))
    first = _build(plugins=[plugin_b, plugin_a])
    second = _build(plugins=[plugin_a, plugin_b])

    assert first == second
    assert first.revision == second.revision
    assert [node.id for node in first.nodes[-2:]] == ["plugin.a.run", "plugin.b.run"]


def test_revision_is_canonical_truncated_sha256() -> None:
    topology = _build(
        enabled_components=frozenset({"hyde"}),
        available_components=frozenset({"hyde"}),
    )
    payload = topology_dict(topology)
    revision = payload.pop("revision")
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    assert revision == "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def test_enabled_or_available_component_set_changes_revision() -> None:
    disabled = _build()
    unavailable = _build(enabled_components=frozenset({"hyde"}))
    available = _build(
        enabled_components=frozenset({"hyde"}),
        available_components=frozenset({"hyde"}),
    )

    assert len({disabled.revision, unavailable.revision, available.revision}) == 3


def test_plugin_node_requires_own_namespace() -> None:
    plugin = _Plugin(
        name="alpha",
        priority=1,
        nodes=(_plugin_node("other"),),
    )

    with pytest.raises(ValueError, match=r"namespace plugin\.alpha"):
        _build(plugins=[plugin])


def test_plugin_cannot_override_core_or_duplicate_plugin_node() -> None:
    override = TopologyNode(
        id="receive",
        label="override",
        group="extension",
        description="invalid override",
        plugin="alpha",
    )
    with pytest.raises(ValueError, match="receive"):
        _build(plugins=[_Plugin("alpha", 1, nodes=(override,))])

    duplicate = _plugin_node("alpha")
    with pytest.raises(ValueError, match="duplicate topology node"):
        _build(
            plugins=[
                _Plugin("alpha", 1, nodes=(duplicate,)),
                _Plugin("alpha", 2, nodes=(duplicate,)),
            ]
        )


def test_plugin_edge_requires_unique_id_and_existing_endpoints() -> None:
    node = _plugin_node("alpha")
    unknown = TopologyEdge(
        id="plugin.alpha.unknown",
        source=node.id,
        target="missing",
        kind="dependency",
    )
    with pytest.raises(ValueError, match="unknown topology edge endpoint"):
        _build(plugins=[_Plugin("alpha", 1, nodes=(node,), edges=(unknown,))])

    duplicate = TopologyEdge(
        id="route.embed",
        source="route",
        target=node.id,
        kind="dependency",
    )
    with pytest.raises(ValueError, match="duplicate topology edge"):
        _build(plugins=[_Plugin("alpha", 1, nodes=(node,), edges=(duplicate,))])


def test_retry_is_only_allowed_cycle() -> None:
    topology = _build()
    retry_edges = [edge for edge in topology.edges if edge.kind == "retry"]
    assert [(edge.source, edge.target) for edge in retry_edges] == [("verify", "search")]

    node = _plugin_node("loop")
    invalid_retry = TopologyEdge(
        id="plugin.loop.retry",
        source=node.id,
        target="receive",
        kind="retry",
    )
    with pytest.raises(ValueError, match="only verify -> search"):
        _build(plugins=[_Plugin("loop", 1, nodes=(node,), edges=(invalid_retry,))])

    cycle = (
        TopologyEdge(
            id="plugin.loop.enter",
            source="finalize",
            target=node.id,
            kind="dependency",
        ),
        TopologyEdge(
            id="plugin.loop.back",
            source=node.id,
            target="receive",
            kind="dependency",
        ),
    )
    with pytest.raises(ValueError, match="cycle"):
        _build(plugins=[_Plugin("loop", 1, nodes=(node,), edges=cycle)])


def test_settings_and_pipeline_component_adapters_distinguish_intent_from_assembly() -> None:
    settings = SimpleNamespace(
        pipeline=SimpleNamespace(
            hyde_on=True,
            subqueries_on=True,
            stepback_on=False,
            graph_retrieval_on=True,
            rerank_on=True,
            sentence_window_on=True,
        )
    )
    retrieval = SimpleNamespace(
        hyde=object(),
        subqueries=None,
        stepback=object(),
        graph_retriever=object(),
        reranker=None,
        sentence_window=object(),
    )

    assert enabled_components_from_settings(settings) == frozenset(
        {"hyde", "subqueries", "graph", "rerank", "sentence_window"}
    )
    assert available_components_from_pipeline({"retrieval": retrieval}) == frozenset(
        {"hyde", "stepback", "graph", "sentence_window"}
    )
    assert available_components_from_pipeline(retrieval) == frozenset(
        {"hyde", "stepback", "graph", "sentence_window"}
    )


def test_cache_replay_topology_is_distinct_and_serializable() -> None:
    replay = build_cache_replay_topology()
    rag = _build()

    assert replay.id == "rag.cache_replay"
    assert replay.id != rag.id
    assert replay.revision != rag.revision
    assert [node.id for node in replay.nodes] == ["cache.lookup", "cache.replay", "finalize"]
    assert topology_dict(replay)["revision"] == replay.revision


def test_module_imports_without_langgraph_installed() -> None:
    script = """
import builtins
real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == 'langgraph' or name.startswith('langgraph.'):
        raise AssertionError('rag_topology imported LangGraph')
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
import rag_topology
assert rag_topology.build_rag_topology
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
