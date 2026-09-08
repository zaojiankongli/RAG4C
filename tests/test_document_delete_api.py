from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server import documents
from server.document_operations import delete_document_artifacts


class FakeCatalog:
    def __init__(self, doc=None):
        self.doc = doc
        self.removed = []

    def get_document(self, doc_id):
        return self.doc if self.doc and self.doc["id"] == doc_id else None

    def remove_document(self, doc_id):
        self.removed.append(doc_id)
        return {"id": doc_id, "chunk_count": self.doc.get("chunk_count", 0)}


class FakeMilvus:
    def __init__(self, chunks=None, fail_delete=False):
        self.chunks = chunks or []
        self.fail_delete = fail_delete
        self.deleted = []

    def query_chunks_by_doc(self, doc_id, tenant_id=""):
        return self.chunks

    def delete_by_doc_id(self, doc_id):
        if self.fail_delete:
            raise RuntimeError("milvus unavailable")
        self.deleted.append(doc_id)
        return len(self.chunks)


class FakeGraph:
    def __init__(self):
        self.deleted = []

    def delete_by_chunk_ids(self, chunk_ids, tenant_id=""):
        self.deleted.append((list(chunk_ids), tenant_id))
        return 2, 1


def test_delete_document_artifacts_cascades_graph_vector_catalog_and_cache(monkeypatch):
    doc = {"id": "doc-1", "tenant_id": "tenant-a", "dataset_id": "default", "chunk_count": 2}
    catalog = FakeCatalog(doc)
    milvus = FakeMilvus([SimpleNamespace(chunk_id="c1"), SimpleNamespace(chunk_id="c2")])
    graph = FakeGraph()
    bumped = []
    monkeypatch.setattr(
        "server.document_operations.cache_epoch.bump",
        lambda tenant, reason="": bumped.append((tenant, reason)),
    )

    result = delete_document_artifacts(
        "doc-1",
        catalog_api=catalog,
        pipeline=SimpleNamespace(milvus=milvus, graph_builder=graph),
    )

    assert result == {
        "document_id": "doc-1",
        "removed_chunks": 2,
        "removed_relations": 2,
        "removed_entities": 1,
    }
    assert graph.deleted == [(["c1", "c2"], "tenant-a")]
    assert milvus.deleted == ["doc-1"]
    assert catalog.removed == ["doc-1"]
    assert bumped and bumped[0][0] == "tenant-a"


def test_delete_document_artifacts_keeps_catalog_when_vector_delete_fails(monkeypatch):
    doc = {"id": "doc-1", "tenant_id": "tenant-a", "dataset_id": "default", "chunk_count": 1}
    catalog = FakeCatalog(doc)
    milvus = FakeMilvus([SimpleNamespace(chunk_id="c1")], fail_delete=True)
    monkeypatch.setattr("server.document_operations.cache_epoch.bump", lambda *args, **kwargs: None)

    with pytest.raises(RuntimeError, match="milvus unavailable"):
        delete_document_artifacts(
            "doc-1",
            catalog_api=catalog,
            pipeline=SimpleNamespace(milvus=milvus, graph_builder=None),
        )

    assert catalog.removed == []


def test_legacy_delete_endpoint_is_gone_without_running_synchronous_deletion(monkeypatch):
    monkeypatch.setattr(
        documents,
        "_delete_registered_document",
        lambda _doc_id: pytest.fail("legacy synchronous deletion must not run"),
    )

    with pytest.raises(HTTPException) as exc:
        documents.delete_document("doc-busy")

    assert exc.value.status_code == 410
    assert exc.value.detail["code"] == "document_delete_endpoint_gone"


def test_legacy_batch_delete_endpoint_is_gone_without_item_processing(monkeypatch):
    monkeypatch.setattr(
        documents,
        "_delete_registered_document",
        lambda _doc_id: pytest.fail("legacy synchronous deletion must not run"),
    )

    with pytest.raises(HTTPException) as exc:
        documents.batch_delete()

    assert exc.value.status_code == 410
    assert exc.value.detail["code"] == "document_delete_endpoint_gone"
