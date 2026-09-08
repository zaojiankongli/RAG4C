from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server import documents


def _chunk(chunk_id: str, text: str, seq: int, **meta):
    return SimpleNamespace(
        chunk_id=chunk_id,
        doc_id="doc-1",
        text=text,
        text_hash=f"hash-{seq}",
        created_at=datetime(2026, 8, 24, 10, seq),
        updated_at=datetime(2026, 8, 24, 11, seq),
        source="manual",
        parent_chunk_id="parent-1" if seq else None,
        metadata={"seq": seq, **meta},
    )


def test_document_chunks_returns_sorted_paginated_operator_projection(monkeypatch):
    chunks = [
        _chunk("doc-1::0002", "third policy", 2, page=3, context="chapter context"),
        _chunk("doc-1::0000", "first policy", 0, page=1),
        _chunk("doc-1::0001", "second policy", 1, page=2),
    ]
    monkeypatch.setattr(documents.catalog, "get_document", lambda _doc_id: {"id": "doc-1", "tenant_id": "t1"})
    monkeypatch.setattr(documents, "_get_ingest_pipeline", lambda: SimpleNamespace(milvus=SimpleNamespace(query_chunks_by_doc=lambda *_: chunks)))

    result = documents.get_document_chunks("doc-1", offset=1, limit=1, query="policy")

    assert result["total"] == 3
    assert result["offset"] == 1
    assert result["limit"] == 1
    assert result["items"][0]["chunk_id"] == "doc-1::0001"
    assert result["items"][0]["page"] == 2
    assert result["items"][0]["char_count"] == len("second policy")


def test_document_chunks_searches_context_and_returns_metadata(monkeypatch):
    chunks = [_chunk("doc-1::0000", "body", 0, context="expense reimbursement", heading="Finance")]
    monkeypatch.setattr(documents.catalog, "get_document", lambda _doc_id: {"id": "doc-1", "tenant_id": "t1"})
    monkeypatch.setattr(documents, "_get_ingest_pipeline", lambda: SimpleNamespace(milvus=SimpleNamespace(query_chunks_by_doc=lambda *_: chunks)))

    result = documents.get_document_chunks("doc-1", query="reimbursement")

    assert result["total"] == 1
    assert result["items"][0]["context"] == "expense reimbursement"
    assert result["items"][0]["metadata"]["heading"] == "Finance"


def test_document_chunks_rejects_unknown_document(monkeypatch):
    monkeypatch.setattr(documents.catalog, "get_document", lambda _doc_id: None)

    with pytest.raises(HTTPException) as exc:
        documents.get_document_chunks("missing")

    assert exc.value.status_code == 404


def test_active_document_chunks_read_authority_head_and_projection_state(
    tmp_path, monkeypatch
):
    from core.catalog_schema import upgrade_catalog
    from core.chunk_catalog import ChunkCatalog
    from models.orm import Dataset, Document, Tenant
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    url = f"sqlite:///{(tmp_path / 'catalog.db').as_posix()}"
    upgrade_catalog(url)
    engine = create_engine(url)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-1", name="Tenant"))
        session.add(Dataset(id="dataset-1", tenant_id="tenant-1", name="KB"))
        session.add(Document(
            id="doc-1", tenant_id="tenant-1", dataset_id="dataset-1", name="Doc",
            content_revision=2, desired_index_revision=2, chunk_count=1,
        ))
        session.commit()
    catalog = ChunkCatalog(engine)
    catalog.upsert_head(
        chunk_id="chunk-1", tenant_id="tenant-1", dataset_id="dataset-1",
        document_id="doc-1", parent_chunk_id=None, chunk_index=0, chunk_role="flat",
        document_revision=2, source_content="old", content="authority latest",
        metadata={"seq": 0, "context": "authority context"},
    )
    catalog.edit_chunk(
        "chunk-1", expected_revision=0, content="authority revision two", editor_id="test"
    )
    monkeypatch.setattr(documents, "_configured_chunk_authority_mode", lambda: "active")
    monkeypatch.setattr(documents.catalog, "get_engine", lambda: engine)
    monkeypatch.setattr(documents.catalog, "get_document", lambda _doc_id: {
        "id": "doc-1", "tenant_id": "tenant-1", "dataset_id": "dataset-1",
        "content_revision": 2,
    })
    monkeypatch.setattr(
        documents, "_get_ingest_pipeline",
        lambda: (_ for _ in ()).throw(AssertionError("active GET must not read Milvus")),
    )

    result = documents.get_document_chunks("doc-1")

    assert result["authority_mode"] == "active"
    assert result["items"][0]["text"] == "authority revision two"
    assert result["items"][0]["content_revision"] == 1
    assert result["items"][0]["indexed_revision"] == 0
    assert result["items"][0]["projection_pending"] is True
    engine.dispose()


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_off_and_shadow_document_chunks_explicitly_keep_milvus_compatibility(
    mode, monkeypatch
):
    chunks = [_chunk("chunk-legacy", "legacy projection", 0)]
    calls = []
    monkeypatch.setattr(documents, "_configured_chunk_authority_mode", lambda: mode)
    monkeypatch.setattr(documents.catalog, "get_document", lambda _doc_id: {
        "id": "doc-1", "tenant_id": "tenant-1"
    })
    monkeypatch.setattr(
        documents, "_get_ingest_pipeline",
        lambda: SimpleNamespace(milvus=SimpleNamespace(
            query_chunks_by_doc=lambda *_args: calls.append(mode) or chunks
        )),
    )

    result = documents.get_document_chunks("doc-1")

    assert result["authority_mode"] == mode
    assert result["items"][0]["text"] == "legacy projection"
    assert calls == [mode]
