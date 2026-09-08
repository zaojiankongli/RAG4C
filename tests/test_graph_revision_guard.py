from __future__ import annotations

from types import SimpleNamespace

from indexing.graph_builder import GraphBuilder
from models.schemas import Triplet


class Store:
    def __init__(self):
        self.graph = SimpleNamespace(batch_size=16)
        self.entities = []
        self.relations = []

    def get_entities_by_texts(self, texts, tenant_id=""):
        return {}

    def get_relations_by_texts(self, texts, tenant_id=""):
        return {}

    def upsert_entities(self, entities, vectors, tenant_id=""):
        self.entities.extend(entities)

    def upsert_relations(self, relations, vectors, tenant_id=""):
        self.relations.extend(relations)


class Embedder:
    def embed_texts(self, texts):
        return [[0.1, 0.2] for _ in texts]


class Extractor:
    def extract(self, text):
        return [Triplet(subject="A", predicate="uses", object="B")]


def test_graph_builder_rechecks_revision_after_extraction_before_any_write() -> None:
    store = Store()
    builder = GraphBuilder(store, Embedder(), Extractor(), extract_concurrency=1)
    checks: list[str] = []

    result = builder.build(
        [("chunk-1", "content")],
        tenant_id="tenant-1",
        before_write=lambda: checks.append("checked") or False,
    )

    assert checks == ["checked"]
    assert result.stale_skipped is True
    assert store.entities == []
    assert store.relations == []
