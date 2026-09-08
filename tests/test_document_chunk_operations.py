from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from models.schemas import Chunk
from server import documents
from server.chunk_operations import delete_document_chunk_artifacts, update_document_chunk_artifacts


def _chunk(text="old body"):
    return Chunk(
        chunk_id="doc-1::0000::stable",
        doc_id="doc-1",
        text=text,
        text_hash="old-hash",
        created_at=datetime(2026, 8, 24, 10, 0),
        updated_at=datetime(2026, 8, 24, 10, 0),
        tenant_id="tenant-a",
        dataset_id="default",
        metadata={"context": "chapter context", "seq": 0},
    )


class FakeCatalog:
    def __init__(self):
        self.doc = {
            "id": "doc-1",
            "tenant_id": "tenant-a",
            "status": "completed",
            "chunk_count": 2,
            "parser_meta": {"chunk_count": 2},
        }
        self.status_updates = []

    def get_document(self, doc_id):
        return self.doc if doc_id == "doc-1" else None

    def set_document_status(self, doc_id, status, **kwargs):
        self.status_updates.append((doc_id, status, kwargs))


class FakeMilvus:
    def __init__(self):
        self.chunks = [
            _chunk(),
            _chunk("other").model_copy(update={"chunk_id": "doc-1::0001::stable"}),
        ]
        self.upserted = []
        self.deleted = []

    def query_chunks_by_doc(self, *_args):
        return list(self.chunks)

    def upsert_chunks(self, chunks, vectors):
        self.upserted.append((chunks, vectors))
        return [chunk.chunk_id for chunk in chunks]

    def delete_by_ids(self, ids):
        self.deleted.append(list(ids))
        return len(ids)


class FakeEmbedder:
    def __init__(self):
        self.texts = []

    def embed_texts(self, texts):
        self.texts.append(list(texts))
        return [[0.1, 0.2]]


class FakeGraph:
    def __init__(self):
        self.deleted = []
        self.built = []

    def delete_by_chunk_ids(self, ids, tenant_id=""):
        self.deleted.append((list(ids), tenant_id))
        return 2, 1

    def build(self, chunks, tenant_id=""):
        self.built.append((list(chunks), tenant_id))
        return SimpleNamespace(entity_count=3, relation_count=4)


def test_update_chunk_reembeds_preserves_identity_and_rebuilds_graph(monkeypatch):
    catalog = FakeCatalog()
    milvus = FakeMilvus()
    embedder = FakeEmbedder()
    graph = FakeGraph()
    bumped = []
    monkeypatch.setattr(
        "server.chunk_operations.cache_epoch.bump",
        lambda tenant, reason="": bumped.append((tenant, reason)),
    )
    pipeline = SimpleNamespace(
        milvus=milvus,
        embedder=embedder,
        graph_builder=graph,
        _embedding_text=lambda chunk: chunk.metadata["context"] + "\n\n" + chunk.text,
    )

    result = update_document_chunk_artifacts(
        "doc-1", "doc-1::0000::stable", "new body", catalog_api=catalog, pipeline=pipeline
    )

    assert result.chunk.chunk_id == "doc-1::0000::stable"
    assert result.chunk.text == "new body"
    assert result.chunk.text_hash != "old-hash"
    assert embedder.texts == [["chapter context\n\nnew body"]]
    assert milvus.upserted[0][0][0].text == "new body"
    assert graph.deleted == [(["doc-1::0000::stable"], "tenant-a")]
    assert graph.built == [([("doc-1::0000::stable", "new body")], "tenant-a")]
    assert bumped


def test_delete_chunk_updates_graph_vector_count_and_cache(monkeypatch):
    catalog = FakeCatalog()
    milvus = FakeMilvus()
    graph = FakeGraph()
    bumped = []
    monkeypatch.setattr(
        "server.chunk_operations.cache_epoch.bump",
        lambda tenant, reason="": bumped.append((tenant, reason)),
    )
    pipeline = SimpleNamespace(milvus=milvus, graph_builder=graph)

    result = delete_document_chunk_artifacts(
        "doc-1", "doc-1::0000::stable", catalog_api=catalog, pipeline=pipeline
    )

    assert result["removed_chunks"] == 1
    assert milvus.deleted == [["doc-1::0000::stable"]]
    assert graph.deleted == [(["doc-1::0000::stable"], "tenant-a")]
    assert catalog.status_updates[0][2]["chunk_count"] == 1
    assert catalog.status_updates[0][2]["parser_meta"]["chunk_count"] == 1
    assert bumped


def test_update_chunk_rejects_unknown_chunk():
    catalog = FakeCatalog()
    milvus = FakeMilvus()
    pipeline = SimpleNamespace(milvus=milvus)
    with pytest.raises(KeyError, match="切片不存在"):
        update_document_chunk_artifacts(
            "doc-1", "missing", "new", catalog_api=catalog, pipeline=pipeline
        )


def test_chunk_edit_endpoint_rejects_busy_document(monkeypatch):
    documents._jobs.clear()
    documents._jobs["doc-1"] = {"running": True}
    monkeypatch.setattr(documents.catalog, "get_document", lambda _doc_id: {"id": "doc-1"})
    with pytest.raises(HTTPException) as exc:
        documents.update_document_chunk(
            "doc-1", "chunk-1", documents.ChunkUpdateRequest(text="new", expected_revision=0)
        )
    assert exc.value.status_code == 409
    documents._jobs.clear()
