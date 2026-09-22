from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.chunk_catalog import ChunkCatalog, ChunkRevisionConflict
from core.index_operations import IndexOperationQueue
from core.ingest_ledger import IngestLedger
from models.orm import Dataset, Document, Tenant
from models.schemas import Chunk
from server import documents
from server.chunk_operations import (
    delete_document_chunk_artifacts,
    update_document_chunk_artifacts,
)


def _legacy_chunk(*, content_revision: int = 0) -> Chunk:
    return Chunk(
        chunk_id="chunk-1",
        doc_id="doc-1",
        text="original body",
        text_hash="legacy-hash",
        created_at=datetime(2026, 8, 24, 10, 0),
        updated_at=datetime(2026, 8, 24, 10, 0),
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        metadata={"context": "chapter context", "seq": 3, "chunk_role": "child"},
        document_revision=2,
        content_revision=content_revision,
    )


class FakeCatalog:
    def __init__(self) -> None:
        self.doc = {
            "id": "doc-1",
            "tenant_id": "tenant-1",
            "dataset_id": "dataset-1",
            "status": "completed",
            "chunk_count": 1,
            "parser_meta": {"chunk_count": 1},
            "content_revision": 2,
            "desired_index_revision": 2,
        }
        self.status_updates: list[tuple[str, str, dict[str, object]]] = []

    def get_document(self, doc_id: str):
        return self.doc if doc_id == "doc-1" else None

    def set_document_status(self, doc_id: str, status: str, **kwargs: object) -> None:
        self.status_updates.append((doc_id, status, kwargs))


class FakeMilvus:
    def __init__(self) -> None:
        self.chunks = [_legacy_chunk()]
        self.upserted: list[object] = []
        self.deleted: list[list[str]] = []

    def query_chunks_by_doc(self, *_args, **_kwargs):
        return list(self.chunks)

    def upsert_chunks(self, chunks, vectors):
        self.upserted.append((chunks, vectors))
        return [chunk.chunk_id for chunk in chunks]

    def delete_by_ids(self, ids):
        self.deleted.append(list(ids))
        return len(ids)


class FakeEmbedder:
    def __init__(self) -> None:
        self.texts: list[list[str]] = []

    def embed_texts(self, texts):
        self.texts.append(list(texts))
        return [[0.1, 0.2]]


class FakeGraph:
    def __init__(self) -> None:
        self.deleted: list[object] = []
        self.built: list[object] = []

    def delete_by_chunk_ids(self, ids, tenant_id=""):
        self.deleted.append((list(ids), tenant_id))
        return 2, 1

    def build(self, chunks, tenant_id=""):
        self.built.append((list(chunks), tenant_id))
        return SimpleNamespace(entity_count=3, relation_count=4)


def _authority_state(tmp_path: Path):
    from core.catalog_schema import upgrade_catalog

    url = f"sqlite:///{(tmp_path / 'catalog.db').as_posix()}"
    upgrade_catalog(url)
    engine = create_engine(url)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-1", name="Tenant"))
        session.add(Dataset(id="dataset-1", tenant_id="tenant-1", name="KB"))
        session.add(
            Document(
                id="doc-1",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name="Document",
                status="completed",
                content_revision=2,
                desired_index_revision=2,
                indexed_revision=2,
                graph_revision=2,
                chunk_count=1,
            )
        )
        session.commit()
    return engine, ChunkCatalog(engine), IndexOperationQueue(engine), IngestLedger(engine)


def test_active_edit_writes_immutable_authority_and_queues_projection_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, chunk_catalog, queue, ledger = _authority_state(tmp_path)
    milvus = FakeMilvus()
    graph = FakeGraph()
    pipeline = SimpleNamespace(
        milvus=milvus,
        embedder=FakeEmbedder(),
        graph_builder=graph,
        _embedding_text=lambda chunk: chunk.text,
    )
    monkeypatch.setattr("server.chunk_operations.cache_epoch.bump", lambda *_args, **_kwargs: None)
    chunk_catalog.upsert_head(
        chunk_id="chunk-1",
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        parent_chunk_id=None,
        chunk_index=0,
        chunk_role="flat",
        document_revision=2,
        source_content="original body",
        content="original body",
        metadata={"context": "chapter context"},
    )

    result = update_document_chunk_artifacts(
        "doc-1",
        "chunk-1",
        "edited body",
        expected_revision=0,
        editor_id="operator-1",
        authority_mode="active",
        catalog_api=FakeCatalog(),
        pipeline=pipeline,
        chunk_catalog=chunk_catalog,
        operation_queue=queue,
        ledger=ledger,
    )

    head = chunk_catalog.get_head("chunk-1")
    assert head.content == "edited body"
    assert head.content_revision == 1
    assert head.enabled is True
    assert [(item.revision, item.content) for item in chunk_catalog.list_revisions("tenant-1", "dataset-1", "doc-1", "chunk-1")] == [
        (0, "original body")
    ]
    operations = queue.list_operations()
    assert {(item.target_store, item.operation, item.status) for item in operations} == {
        ("milvus_chunks", "upsert", "pending"),
        ("graph_projection", "upsert", "pending"),
    }
    assert all(item.target_revision == 2 for item in operations)
    assert all(item.payload["chunk_id"] == "chunk-1" for item in operations)
    assert all(item.payload["expected_content_revision"] == 1 for item in operations)
    assert result.chunk.content_revision == 1
    assert milvus.upserted == []
    assert graph.deleted == []
    assert graph.built == []
    engine.dispose()


def test_active_delete_tombstones_head_and_queues_delete_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, chunk_catalog, queue, ledger = _authority_state(tmp_path)
    chunk_catalog.upsert_head(
        chunk_id="chunk-1",
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        parent_chunk_id=None,
        chunk_index=0,
        chunk_role="flat",
        document_revision=2,
        source_content="original body",
        content="original body",
    )
    milvus = FakeMilvus()
    graph = FakeGraph()
    pipeline = SimpleNamespace(milvus=milvus, graph_builder=graph)
    monkeypatch.setattr("server.chunk_operations.cache_epoch.bump", lambda *_args, **_kwargs: None)

    result = delete_document_chunk_artifacts(
        "doc-1",
        "chunk-1",
        expected_revision=0,
        editor_id="operator-1",
        authority_mode="active",
        catalog_api=FakeCatalog(),
        pipeline=pipeline,
        chunk_catalog=chunk_catalog,
        operation_queue=queue,
        ledger=ledger,
    )

    head = chunk_catalog.get_head("chunk-1")
    assert head.enabled is False
    assert head.content_revision == 1
    assert [(item.revision, item.enabled) for item in chunk_catalog.list_revisions("tenant-1", "dataset-1", "doc-1", "chunk-1")] == [
        (0, True)
    ]
    operations = queue.list_operations()
    assert {(item.target_store, item.operation, item.status) for item in operations} == {
        ("milvus_chunks", "delete", "pending"),
        ("graph_projection", "delete", "pending"),
    }
    assert all(item.payload["expected_content_revision"] == 1 for item in operations)
    assert result["content_revision"] == 1
    assert result["projection_pending"] is True
    assert milvus.deleted == []
    assert graph.deleted == []
    engine.dispose()


def test_shadow_edit_dual_writes_authority_facts_and_legacy_projections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, chunk_catalog, queue, ledger = _authority_state(tmp_path)
    milvus = FakeMilvus()
    graph = FakeGraph()
    embedder = FakeEmbedder()
    pipeline = SimpleNamespace(
        milvus=milvus,
        embedder=embedder,
        graph_builder=graph,
        _embedding_text=lambda chunk: chunk.text,
    )
    monkeypatch.setattr("server.chunk_operations.cache_epoch.bump", lambda *_args, **_kwargs: None)

    update_document_chunk_artifacts(
        "doc-1",
        "chunk-1",
        "shadow body",
        expected_revision=0,
        editor_id="operator-1",
        authority_mode="shadow",
        catalog_api=FakeCatalog(),
        pipeline=pipeline,
        chunk_catalog=chunk_catalog,
        operation_queue=queue,
        ledger=ledger,
    )

    assert chunk_catalog.get_head("chunk-1").content == "shadow body"
    assert {item.status for item in queue.list_operations()} == {"shadow"}
    assert milvus.upserted[0][0][0].text == "shadow body"
    assert graph.deleted == [(["chunk-1"], "tenant-1")]
    assert graph.built == [([("chunk-1", "shadow body")], "tenant-1")]
    engine.dispose()


def test_api_maps_stale_expected_revision_to_409(monkeypatch: pytest.MonkeyPatch) -> None:
    documents._jobs.clear()
    monkeypatch.setattr(documents.catalog, "get_document", lambda _doc_id: {"id": "doc-1"})
    monkeypatch.setattr(documents, "_get_ingest_pipeline", lambda: SimpleNamespace())
    monkeypatch.setattr(documents, "_configured_chunk_authority_mode", lambda: "active")

    def stale(*_args, **_kwargs):
        raise ChunkRevisionConflict("chunk revision conflict")

    monkeypatch.setattr("server.chunk_operations.update_document_chunk_artifacts", stale)

    with pytest.raises(HTTPException) as exc:
        documents.update_document_chunk(
            "doc-1",
            "chunk-1",
            documents.ChunkUpdateRequest(text="new body", expected_revision=4),
        )

    assert exc.value.status_code == 409
    assert "revision conflict" in str(exc.value.detail)
    documents._jobs.clear()


def test_delete_api_forwards_expected_revision_and_maps_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documents._jobs.clear()
    monkeypatch.setattr(documents.catalog, "get_document", lambda _doc_id: {"id": "doc-1"})
    monkeypatch.setattr(documents, "_get_ingest_pipeline", lambda: SimpleNamespace())
    monkeypatch.setattr(documents, "_configured_chunk_authority_mode", lambda: "active")
    seen: dict[str, object] = {}

    def stale(*_args, **kwargs):
        seen.update(kwargs)
        raise ChunkRevisionConflict("chunk revision conflict")

    monkeypatch.setattr("server.chunk_operations.delete_document_chunk_artifacts", stale)

    with pytest.raises(HTTPException) as exc:
        documents.delete_document_chunk("doc-1", "chunk-1", expected_revision=7)

    assert exc.value.status_code == 409
    assert seen["expected_revision"] == 7
    documents._jobs.clear()


def test_operation_enqueue_failure_rolls_back_head_revision_attempt_and_outbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from models.orm import DocumentIngestAttempt
    from sqlalchemy import func, select

    engine, chunk_catalog, _queue, ledger = _authority_state(tmp_path)
    chunk_catalog.upsert_head(
        chunk_id="chunk-1", tenant_id="tenant-1", dataset_id="dataset-1",
        document_id="doc-1", parent_chunk_id=None, chunk_index=0,
        chunk_role="flat", document_revision=2, source_content="original body",
        content="original body", metadata={"context": "chapter context"},
    )

    class FailingQueue(IndexOperationQueue):
        def enqueue_chunk_projection_operations(self, **_kwargs):
            raise RuntimeError("injected outbox failure")

    monkeypatch.setattr("server.chunk_operations.cache_epoch.bump", lambda *_args, **_kwargs: None)
    pipeline = SimpleNamespace(milvus=FakeMilvus(), graph_builder=FakeGraph())

    with pytest.raises(RuntimeError, match="injected outbox failure"):
        update_document_chunk_artifacts(
            "doc-1", "chunk-1", "must roll back", expected_revision=0,
            authority_mode="active", catalog_api=FakeCatalog(), pipeline=pipeline,
            chunk_catalog=chunk_catalog, operation_queue=FailingQueue(engine), ledger=ledger,
        )

    assert chunk_catalog.get_head("chunk-1").content_revision == 0
    assert chunk_catalog.get_head("chunk-1").content == "original body"
    assert chunk_catalog.list_revisions("tenant-1", "dataset-1", "doc-1", "chunk-1") == []
    with Session(engine) as session:
        assert session.scalar(select(func.count(DocumentIngestAttempt.id))) == 0
    assert IndexOperationQueue(engine).count_operations() == 0
    engine.dispose()


def test_active_mode_rejects_incomplete_document_authority_without_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.chunk_operations import ChunkAuthorityIncomplete

    engine, chunk_catalog, queue, ledger = _authority_state(tmp_path)
    milvus = FakeMilvus()
    monkeypatch.setattr("server.chunk_operations.cache_epoch.bump", lambda *_args, **_kwargs: None)

    with pytest.raises(ChunkAuthorityIncomplete, match="authority.*complete"):
        update_document_chunk_artifacts(
            "doc-1", "chunk-1", "rejected", expected_revision=0,
            authority_mode="active", catalog_api=FakeCatalog(),
            pipeline=SimpleNamespace(milvus=milvus, graph_builder=None),
            chunk_catalog=chunk_catalog, operation_queue=queue, ledger=ledger,
        )

    assert milvus.upserted == []
    assert queue.count_operations() == 0
    engine.dispose()


def test_repeated_tombstone_is_conflict_then_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, chunk_catalog, queue, ledger = _authority_state(tmp_path)
    chunk_catalog.upsert_head(
        chunk_id="chunk-1", tenant_id="tenant-1", dataset_id="dataset-1",
        document_id="doc-1", parent_chunk_id=None, chunk_index=0,
        chunk_role="flat", document_revision=2, source_content="original body",
        content="original body",
    )
    monkeypatch.setattr("server.chunk_operations.cache_epoch.bump", lambda *_args, **_kwargs: None)
    kwargs = dict(
        authority_mode="active", catalog_api=FakeCatalog(),
        pipeline=SimpleNamespace(milvus=FakeMilvus(), graph_builder=FakeGraph()),
        chunk_catalog=chunk_catalog, operation_queue=queue, ledger=ledger,
    )

    first = delete_document_chunk_artifacts(
        "doc-1", "chunk-1", expected_revision=0, **kwargs
    )
    with pytest.raises(ChunkRevisionConflict):
        delete_document_chunk_artifacts(
            "doc-1", "chunk-1", expected_revision=0, **kwargs
        )
    repeated = delete_document_chunk_artifacts(
        "doc-1", "chunk-1", expected_revision=1, **kwargs
    )

    assert first["content_revision"] == repeated["content_revision"] == 1
    assert first["remaining_chunks"] == repeated["remaining_chunks"] == 0
    assert repeated["remaining_chunks"] == chunk_catalog.count_document_heads(
        "doc-1", document_revision=2, enabled_only=True
    )
    assert repeated["projection_pending"] is False
    assert repeated["operation_ids"] == []
    assert len(chunk_catalog.list_revisions("tenant-1", "dataset-1", "doc-1", "chunk-1")) == 1
    assert queue.count_operations() == 2
    engine.dispose()


def test_active_edit_and_delete_run_through_real_worker_and_invalidate_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from indexing.index_worker import IndexOperationWorker
    from indexing.projection_handlers import ProjectionHandlers
    from models.orm import Dataset, Document, Tenant

    engine, chunk_catalog, queue, ledger = _authority_state(tmp_path)
    with Session(engine) as session:
        tenant = session.get(Tenant, "tenant-1")
        dataset = session.get(Dataset, "dataset-1")
        assert tenant is not None and dataset is not None
        tenant.chunk_count = dataset.chunk_count = 1
        session.commit()
    chunk_catalog.upsert_head(
        chunk_id="chunk-1", tenant_id="tenant-1", dataset_id="dataset-1",
        document_id="doc-1", parent_chunk_id=None, chunk_index=0,
        chunk_role="flat", document_revision=2, source_content="original body",
        content="original body", metadata={"context": "chapter context"},
    )
    milvus = FakeMilvus()
    graph = FakeGraph()
    embedder = FakeEmbedder()
    pipeline = SimpleNamespace(milvus=milvus, embedder=embedder, graph_builder=graph)
    bumps: list[tuple[str, str]] = []
    monkeypatch.setattr("server.chunk_operations.cache_epoch.bump", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "indexing.index_worker.cache_epoch.bump",
        lambda tenant, reason="": bumps.append((tenant, reason)),
    )
    catalog_api = FakeCatalog()

    update_document_chunk_artifacts(
        "doc-1", "chunk-1", "edited body", expected_revision=0,
        authority_mode="active", catalog_api=catalog_api, pipeline=pipeline,
        chunk_catalog=chunk_catalog, operation_queue=queue, ledger=ledger,
    )
    handlers = ProjectionHandlers(
        chunk_catalog=chunk_catalog, embedder=embedder, milvus=milvus, graph_builder=graph
    )
    worker = IndexOperationWorker(
        queue, handlers=handlers.as_mapping(), worker_id="worker-authority"
    )
    edit_results = [worker.run_once(limit=10), worker.run_once(limit=10)]
    assert [result.claimed for result in edit_results] == [1, 1]
    assert sum(result.succeeded for result in edit_results) == 2
    assert embedder.texts == [["chapter context\n\nedited body"]]
    assert graph.deleted == [(["chunk-1"], "tenant-1")]
    assert graph.built == [([("chunk-1", "chapter context\n\nedited body")], "tenant-1")]
    assert bumps == [
        ("tenant-1", "编辑切片 chunk-1"),
        ("tenant-1", "切片投影完成 doc-1"),
    ]

    delete_document_chunk_artifacts(
        "doc-1", "chunk-1", expected_revision=1, authority_mode="active",
        catalog_api=catalog_api, pipeline=pipeline, chunk_catalog=chunk_catalog,
        operation_queue=queue, ledger=ledger,
    )
    delete_results = [worker.run_once(limit=10), worker.run_once(limit=10)]
    assert [result.claimed for result in delete_results] == [1, 1]
    assert sum(result.succeeded for result in delete_results) == 2
    assert graph.built == [([("chunk-1", "chapter context\n\nedited body")], "tenant-1")]
    assert graph.deleted[-1] == (["chunk-1"], "tenant-1")
    with Session(engine) as session:
        document = session.get(Document, "doc-1")
        tenant = session.get(Tenant, "tenant-1")
        dataset = session.get(Dataset, "dataset-1")
        assert document is not None and tenant is not None and dataset is not None
        assert document.chunk_count == 0
        assert tenant.chunk_count == dataset.chunk_count == 0
    assert bumps[-2:] == [
        ("tenant-1", "删除切片 chunk-1"),
        ("tenant-1", "切片投影完成 doc-1"),
    ]
    engine.dispose()


def test_parent_and_child_preserve_historical_document_and_quota_count_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from indexing.index_worker import IndexOperationWorker
    from indexing.projection_handlers import ProjectionHandlers
    from models.orm import Dataset, Document, Tenant

    engine, chunk_catalog, queue, ledger = _authority_state(tmp_path)
    with Session(engine) as session:
        document = session.get(Document, "doc-1")
        tenant = session.get(Tenant, "tenant-1")
        dataset = session.get(Dataset, "dataset-1")
        assert document is not None and tenant is not None and dataset is not None
        document.chunk_count = 2
        tenant.chunk_count = dataset.chunk_count = 2
        session.commit()
    chunk_catalog.upsert_head(
        chunk_id="parent-1", tenant_id="tenant-1", dataset_id="dataset-1",
        document_id="doc-1", parent_chunk_id=None, chunk_index=0,
        chunk_role="parent", document_revision=2, source_content="parent", content="parent",
    )
    chunk_catalog.upsert_head(
        chunk_id="chunk-1", tenant_id="tenant-1", dataset_id="dataset-1",
        document_id="doc-1", parent_chunk_id="parent-1", chunk_index=1,
        chunk_role="child", document_revision=2, source_content="child", content="child",
    )
    catalog_api = FakeCatalog()
    catalog_api.doc["chunk_count"] = 2
    pipeline = SimpleNamespace(milvus=FakeMilvus(), embedder=FakeEmbedder(), graph_builder=None)
    monkeypatch.setattr("server.chunk_operations.cache_epoch.bump", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("indexing.index_worker.cache_epoch.bump", lambda *_args, **_kwargs: None)

    update_document_chunk_artifacts(
        "doc-1", "chunk-1", "edited child", expected_revision=0,
        authority_mode="active", catalog_api=catalog_api, pipeline=pipeline,
        chunk_catalog=chunk_catalog, operation_queue=queue, ledger=ledger,
    )
    handlers = ProjectionHandlers(
        chunk_catalog=chunk_catalog, embedder=pipeline.embedder, milvus=pipeline.milvus
    )
    worker = IndexOperationWorker(
        queue, handlers=handlers.as_mapping(), worker_id="worker-parent-child"
    )

    assert worker.run_once(limit=10).succeeded == 1
    with Session(engine) as session:
        document = session.get(Document, "doc-1")
        tenant = session.get(Tenant, "tenant-1")
        dataset = session.get(Dataset, "dataset-1")
        assert document is not None and tenant is not None and dataset is not None
        assert document.chunk_count == 2
        assert tenant.chunk_count == dataset.chunk_count == 2
    engine.dispose()


def test_document_chunk_list_includes_disabled_heads_and_exact_projection_facts_only_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, chunk_catalog, _queue, _ledger = _authority_state(tmp_path)
    chunk_catalog.upsert_head(
        chunk_id="chunk-enabled", tenant_id="tenant-1", dataset_id="dataset-1",
        document_id="doc-1", parent_chunk_id=None, chunk_index=0,
        chunk_role="flat", document_revision=2, source_content="enabled source",
        content="enabled body", metadata={"seq": 0, "heading": "Enabled"},
    )
    chunk_catalog.upsert_head(
        chunk_id="chunk-tombstone", tenant_id="tenant-1", dataset_id="dataset-1",
        document_id="doc-1", parent_chunk_id="parent-1", chunk_index=1,
        chunk_role="child", document_revision=2, source_content="deleted source",
        content="deleted body", metadata={"seq": 1, "page": 2},
    )
    chunk_catalog.tombstone_chunk(
        "chunk-tombstone", expected_revision=0, editor_id="manual-operator"
    )
    catalog_api = FakeCatalog()
    catalog_api.get_engine = lambda: engine  # type: ignore[attr-defined]
    monkeypatch.setattr(documents, "catalog", catalog_api)
    monkeypatch.setattr(documents, "_configured_chunk_authority_mode", lambda: "active")

    default_page = documents.get_document_chunks("doc-1")
    assert [item["chunk_id"] for item in default_page["items"]] == ["chunk-enabled"]

    inclusive_page = documents.get_document_chunks("doc-1", include_disabled=True)
    assert [item["chunk_id"] for item in inclusive_page["items"]] == [
        "chunk-enabled", "chunk-tombstone"
    ]
    tombstone = inclusive_page["items"][1]
    assert {
        "tenant_id": tombstone["tenant_id"],
        "dataset_id": tombstone["dataset_id"],
        "document_revision": tombstone["document_revision"],
        "enabled": tombstone["enabled"],
        "chunk_role": tombstone["chunk_role"],
        "desired_index_revision": tombstone["desired_index_revision"],
        "indexed_revision": tombstone["indexed_revision"],
        "index_status": tombstone["index_status"],
        "projection_pending": tombstone["projection_pending"],
    } == {
        "tenant_id": "tenant-1",
        "dataset_id": "dataset-1",
        "document_revision": 2,
        "enabled": False,
        "chunk_role": "child",
        "desired_index_revision": 1,
        "indexed_revision": 0,
        "index_status": "pending",
        "projection_pending": True,
    }
    engine.dispose()
