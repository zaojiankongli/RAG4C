from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from core.catalog_schema import upgrade_catalog
from core.chunk_catalog import ChunkCatalog
from core.document_deletion import DocumentDeletionRepository
from core.index_operations import IndexOperationQueue
from core.knowledge_governance import AuditContext
from indexing.graph_builder import GraphBuilder
from indexing.index_worker import IndexOperationWorker
from indexing.projection_handlers import ProjectionHandlers
from models.orm import (
    ChunkHead,
    Dataset,
    Document,
    DocumentDeleteBatch,
    DocumentDeleteOperation,
    DocumentIngestAttempt,
    IndexDeadLetter,
    IndexOperation,
    KnowledgeAuditEvent,
    Tenant,
)


def _engine(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'dd-worker.db').as_posix()}?timeout=20"
    upgrade_catalog(url)
    engine = create_engine(url, connect_args={"timeout": 20})

    @event.listens_for(engine, "connect")
    def _foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=20000")
        cursor.close()

    with Session(engine) as session:
        session.add(Tenant(id="tenant-1", name="Tenant", doc_count=1, chunk_count=3))
        session.add(
            Dataset(
                id="dataset-1",
                tenant_id="tenant-1",
                name="Dataset",
                doc_count=1,
                chunk_count=3,
                graph_enabled=True,
            )
        )
        session.flush()
        session.add(
            Document(
                id="doc-1",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name="Document",
                status="completed",
                lifecycle_state="active",
                retrieval_enabled=True,
                content_revision=4,
                desired_index_revision=4,
                indexed_revision=4,
                graph_revision=4,
                chunk_count=3,
            )
        )
        session.flush()
        for index, role, enabled, document_revision in (
            (0, "parent", True, 4),
            (1, "child", True, 4),
            (2, "flat", True, 4),
            (3, "flat", False, 3),
        ):
            session.add(
                ChunkHead(
                    id=f"chunk-{index}",
                    tenant_id="tenant-1",
                    dataset_id="dataset-1",
                    document_id="doc-1",
                    chunk_index=index,
                    chunk_role=role,
                    document_revision=document_revision,
                    content_revision=index + 1,
                    source_content=f"source-{index}",
                    content=f"content-{index}",
                    content_hash=f"hash-{index}",
                    enabled=enabled,
                    desired_index_revision=document_revision,
                    indexed_revision=document_revision,
                    index_status="succeeded",
                )
            )
        session.commit()

    parent = DocumentDeletionRepository(engine).request_delete(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        expected_generation=0,
        idempotency_key="delete-1",
        actor=AuditContext(
            actor_id="owner-1", request_id="request-1", request_ip="127.0.0.1"
        ),
        reason="cleanup",
        origin="operator",
    )
    return engine, parent


class FakeEmbedder:
    def embed_texts(self, texts):
        return [[0.1] for _ in texts]


class FakeMilvus:
    def __init__(self):
        self.ids = {"chunk-0", "chunk-1", "chunk-2", "chunk-3", "milvus-only"}
        self.deleted_docs: list[str] = []
        self.flush_calls = 0
        self.transactions_seen: list[bool] = []
        self.engine = None

    def delete_by_doc_id(self, doc_id: str) -> int:
        if self.engine is not None:
            with self.engine.connect() as connection:
                self.transactions_seen.append(connection.in_transaction())
        self.deleted_docs.append(doc_id)
        count = len(self.ids)
        self.ids.clear()
        return count

    def flush(self) -> None:
        self.flush_calls += 1

    def count_chunks_by_document(
        self, doc_id: str, tenant_id: str = "", *, consistency: str = "strong"
    ) -> int:
        assert doc_id == "doc-1"
        assert tenant_id == "tenant-1"
        assert consistency == "strong"
        return len(self.ids)


class FakeGraph:
    def __init__(self):
        self.ids = {"chunk-0", "chunk-1", "chunk-2", "chunk-3", "graph-only"}
        self.deleted_batches: list[list[str]] = []
        self.transactions_seen: list[bool] = []
        self.engine = None

    def delete_by_chunk_ids(self, ids, tenant_id: str = ""):
        assert tenant_id == "tenant-1"
        if self.engine is not None:
            with self.engine.connect() as connection:
                self.transactions_seen.append(connection.in_transaction())
        batch = list(ids)
        self.deleted_batches.append(batch)
        self.ids.difference_update(batch)
        return len(batch), 0

    def count_facts_by_chunk_ids(self, ids, tenant_id: str = "") -> int:
        assert tenant_id == "tenant-1"
        return len(self.ids.intersection(ids))


def _worker(engine, milvus, graph, *, lease_seconds: int = 30):
    handlers = ProjectionHandlers(
        chunk_catalog=ChunkCatalog(engine),
        embedder=FakeEmbedder(),
        milvus=milvus,
        graph_builder=graph,
    )
    return IndexOperationWorker(
        IndexOperationQueue(engine),
        handlers=handlers.as_mapping(),
        worker_id="worker-1",
        lease_seconds=lease_seconds,
    )


def _run_until_idle(worker: IndexOperationWorker, limit: int = 10):
    results = []
    for _ in range(limit):
        result = worker.run_once()
        results.append(result)
        if result.claimed == 0:
            break
    return results


def test_graph_delete_removes_deleted_passage_from_shared_facts() -> None:
    class Store:
        graph = SimpleNamespace(batch_size=2)

        def __init__(self):
            self.relations = {
                "shared": {
                    "id": "shared",
                    "text": "shared relation",
                    "entity_ids": ["entity-shared"],
                    "passage_ids": ["chunk-0", "other-chunk"],
                    "tenant_id": "tenant-1",
                    "vector": [0.1],
                },
                "owned": {
                    "id": "owned",
                    "text": "owned relation",
                    "entity_ids": ["entity-owned"],
                    "passage_ids": ["chunk-0"],
                    "tenant_id": "tenant-1",
                    "vector": [0.2],
                },
            }
            self.entities = {
                "entity-shared": {
                    "id": "entity-shared",
                    "text": "shared",
                    "relation_ids": ["shared"],
                    "passage_ids": ["chunk-0", "other-chunk"],
                    "tenant_id": "tenant-1",
                    "vector": [0.3],
                },
                "entity-owned": {
                    "id": "entity-owned",
                    "text": "owned",
                    "relation_ids": ["owned"],
                    "passage_ids": ["chunk-0"],
                    "tenant_id": "tenant-1",
                    "vector": [0.4],
                },
            }

        def get_relations_by_passage_ids(self, ids, tenant_id="", include_vectors=False):
            wanted = set(ids)
            return [dict(row) for row in self.relations.values() if wanted.intersection(row["passage_ids"])]

        def get_entities_by_passage_ids(self, ids, tenant_id="", include_vectors=False):
            wanted = set(ids)
            return [dict(row) for row in self.entities.values() if wanted.intersection(row["passage_ids"])]

        def get_entities_by_ids(self, ids, tenant_id="", include_vectors=False):
            return [dict(self.entities[item]) for item in ids if item in self.entities]

        def get_relations_by_entity_ids(self, ids, tenant_id=""):
            wanted = set(ids)
            return [
                dict(row)
                for row in self.relations.values()
                if wanted.intersection(row["entity_ids"])
            ]

        def get_relations_by_ids(self, ids, tenant_id=""):
            return [dict(self.relations[item]) for item in ids if item in self.relations]

        def upsert_raw_relations(self, rows):
            for row in rows:
                self.relations[row["id"]] = dict(row)

        def upsert_raw_entities(self, rows):
            for row in rows:
                self.entities[row["id"]] = dict(row)

        def delete_relations_by_ids(self, ids):
            for item in ids:
                self.relations.pop(item, None)
            return len(ids)

        def delete_entities_by_ids(self, ids):
            for item in ids:
                self.entities.pop(item, None)
            return len(ids)

    store = Store()
    builder = GraphBuilder(store, FakeEmbedder(), extractor=SimpleNamespace())

    builder.delete_by_chunk_ids(["chunk-0"], tenant_id="tenant-1")

    assert set(store.relations) == {"shared"}
    assert store.relations["shared"]["passage_ids"] == ["other-chunk"]
    assert set(store.entities) == {"entity-shared"}
    assert store.entities["entity-shared"]["passage_ids"] == ["other-chunk"]
    assert store.entities["entity-shared"]["relation_ids"] == ["shared"]
    assert builder.count_facts_by_chunk_ids(["chunk-0"], tenant_id="tenant-1") == 0

    # Replay after a crash that deleted the relation but left its entity stale.
    store.entities["entity-owned"] = {
        "id": "entity-owned",
        "text": "owned",
        "relation_ids": ["owned"],
        "passage_ids": ["chunk-0"],
        "tenant_id": "tenant-1",
        "vector": [0.4],
    }
    builder.delete_by_chunk_ids(["chunk-0"], tenant_id="tenant-1")
    assert "entity-owned" not in store.entities
    assert builder.count_facts_by_chunk_ids(["chunk-0"], tenant_id="tenant-1") == 0


def test_delete_handlers_use_authority_ids_verify_absence_and_do_not_hold_db_transaction(
    tmp_path: Path,
) -> None:
    engine, parent = _engine(tmp_path)
    milvus = FakeMilvus()
    graph = FakeGraph()
    milvus.engine = engine
    graph.engine = engine

    worker = _worker(engine, milvus, graph)
    first = worker.run_once()
    second = worker.run_once()

    assert first.succeeded == 1
    assert second.succeeded == 1
    assert milvus.deleted_docs == ["doc-1"]
    assert milvus.flush_calls == 1
    assert milvus.ids == set()
    deleted_graph_ids = [item for batch in graph.deleted_batches for item in batch]
    assert deleted_graph_ids == ["chunk-0", "chunk-1", "chunk-2", "chunk-3"]
    assert "graph-only" in graph.ids
    assert milvus.transactions_seen == [False]
    assert graph.transactions_seen and all(value is False for value in graph.transactions_seen)

    with Session(engine) as session:
        row = session.get(DocumentDeleteOperation, parent.id)
        assert row.status == "finalizing"
        assert row.completed_store_count == 2
        finalizers = list(
            session.scalars(
                select(IndexOperation).where(
                    IndexOperation.delete_operation_id == parent.id,
                    IndexOperation.operation == "finalize_document_delete",
                )
            )
        )
        assert len(finalizers) == 1
        assert finalizers[0].status == "pending"
    engine.dispose()


def test_graph_manifest_drift_retries_without_external_delete(tmp_path: Path) -> None:
    engine, parent = _engine(tmp_path)
    with Session(engine) as session:
        session.add(
            ChunkHead(
                id="chunk-drift",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                document_id="doc-1",
                chunk_index=99,
                chunk_role="flat",
                document_revision=2,
                content_revision=1,
                source_content="drift",
                content="drift",
                content_hash="drift",
                enabled=False,
            )
        )
        session.commit()
        graph_op = session.scalar(
            select(IndexOperation).where(
                IndexOperation.delete_operation_id == parent.id,
                IndexOperation.target_store == "graph_projection",
            )
        )
        graph_op.created_at = datetime(2020, 1, 1)
        session.commit()

    graph = FakeGraph()
    worker = _worker(engine, FakeMilvus(), graph)
    result = worker.run_once()

    assert result.retried == 1
    assert graph.deleted_batches == []
    engine.dispose()


def test_last_projection_completion_and_finalizer_enqueue_are_atomic(tmp_path: Path) -> None:
    engine, parent = _engine(tmp_path)
    repository = DocumentDeletionRepository(engine)
    queue = IndexOperationQueue(engine)
    claimed = queue.claim_operations("worker-a", limit=1, lease_seconds=30)[0]
    repository.complete_projection_operation(claimed.id, worker_id="worker-a")
    claimed = queue.claim_operations("worker-a", limit=1, lease_seconds=30)[0]

    original = repository._add_index_operation

    def fail_finalizer(session, operation):
        if operation.operation == "finalize_document_delete":
            raise RuntimeError("injected finalizer enqueue failure")
        return original(session, operation)

    repository._add_index_operation = fail_finalizer
    with pytest.raises(RuntimeError, match="injected finalizer"):
        repository.complete_projection_operation(claimed.id, worker_id="worker-a")

    with Session(engine) as session:
        child = session.get(IndexOperation, claimed.id)
        parent_row = session.get(DocumentDeleteOperation, parent.id)
        assert child.status == "claimed"
        assert parent_row.completed_store_count == 1
        assert parent_row.status in {"queued", "projecting"}
        assert (
            session.scalar(
                select(func.count())
                .select_from(IndexOperation)
                .where(IndexOperation.operation == "finalize_document_delete")
            )
            == 0
        )
    engine.dispose()


def test_finalizer_commits_tombstone_quota_attempt_batch_and_audit_once(tmp_path: Path) -> None:
    engine, parent = _engine(tmp_path)
    worker = _worker(engine, FakeMilvus(), FakeGraph())

    results = _run_until_idle(worker)
    assert sum(item.succeeded for item in results) == 3

    with Session(engine) as session:
        document = session.get(Document, "doc-1")
        tenant = session.get(Tenant, "tenant-1")
        dataset = session.get(Dataset, "dataset-1")
        parent_row = session.get(DocumentDeleteOperation, parent.id)
        attempt = session.get(DocumentIngestAttempt, parent.attempt_id)
        batch = session.get(DocumentDeleteBatch, parent.batch_id)
        heads = list(
            session.scalars(select(ChunkHead).where(ChunkHead.document_id == "doc-1"))
        )
        assert document.lifecycle_state == "deleted"
        assert document.retrieval_enabled is False
        assert document.chunk_count == 0
        assert document.deleted_at is not None
        assert document.active_delete_operation_id is None
        assert document.usage_released_at is not None
        assert tenant.doc_count == 0 and tenant.chunk_count == 0
        assert dataset.doc_count == 0 and dataset.chunk_count == 0
        assert parent_row.status == "completed"
        assert parent_row.finalized_at is not None
        assert attempt.state == "completed" and attempt.finished_at is not None
        assert batch.status == "completed"
        assert batch.completed_count == 1 and batch.failed_count == 0
        assert all(head.enabled is False and head.index_status == "deleted" for head in heads)
        assert (
            session.scalar(
                select(func.count())
                .select_from(KnowledgeAuditEvent)
                .where(KnowledgeAuditEvent.action == "document.deleted")
            )
            == 1
        )

    # A replayed finalizer must not release usage or create a second audit.
    with Session(engine) as session:
        finalizer = session.scalar(
            select(IndexOperation).where(
                IndexOperation.delete_operation_id == parent.id,
                IndexOperation.operation == "finalize_document_delete",
            )
        )
        assert finalizer.status == "succeeded"
    repository = DocumentDeletionRepository(engine)
    projection = repository.finalize_document_delete(
        finalizer.id, worker_id="worker-1"
    )
    assert projection.status == "completed"
    with Session(engine) as session:
        assert session.get(Tenant, "tenant-1").doc_count == 0
        assert session.get(Dataset, "dataset-1").chunk_count == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(KnowledgeAuditEvent)
                .where(KnowledgeAuditEvent.action == "document.deleted")
            )
            == 1
        )
    engine.dispose()


def test_projection_delete_replays_idempotently_after_completion_crash(
    tmp_path: Path,
) -> None:
    engine, parent = _engine(tmp_path)
    with Session(engine) as session:
        graph_child = session.scalar(
            select(IndexOperation).where(
                IndexOperation.delete_operation_id == parent.id,
                IndexOperation.target_store == "graph_projection",
            )
        )
        graph_child.status = "succeeded"
        graph_child.finished_at = datetime(2026, 8, 25, 9, 59, 0)
        parent_row = session.get(DocumentDeleteOperation, parent.id)
        parent_row.completed_store_count = 1
        session.commit()
    milvus = FakeMilvus()
    worker = _worker(engine, milvus, FakeGraph())
    original = worker.deletion_repository.complete_projection_operation
    calls = 0

    def crash_before_commit(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("injected completion crash")
        return original(*args, **kwargs)

    worker.deletion_repository.complete_projection_operation = crash_before_commit
    first = worker.run_once(now=datetime(2026, 8, 25, 10, 0, 0))
    second = worker.run_once(now=datetime(2026, 8, 25, 10, 0, 5))

    assert first.retried == 1
    assert second.succeeded == 1
    assert milvus.deleted_docs == ["doc-1", "doc-1"]
    assert milvus.ids == set()
    with Session(engine) as session:
        parent_row = session.get(DocumentDeleteOperation, parent.id)
        assert parent_row.completed_store_count == 2
        assert parent_row.status == "finalizing"
    engine.dispose()


def test_finalizer_quota_failure_rolls_back_and_retries(tmp_path: Path) -> None:
    engine, parent = _engine(tmp_path)
    worker = _worker(engine, FakeMilvus(), FakeGraph())
    worker.run_once()
    worker.run_once()

    original = worker.deletion_repository._release_usage

    def fail_after_quota_mutation(**kwargs):
        original(**kwargs)
        raise RuntimeError("injected quota failure")

    worker.deletion_repository._release_usage = fail_after_quota_mutation
    result = worker.run_once()

    assert result.retried == 1
    with Session(engine) as session:
        document = session.get(Document, "doc-1")
        tenant = session.get(Tenant, "tenant-1")
        dataset = session.get(Dataset, "dataset-1")
        parent_row = session.get(DocumentDeleteOperation, parent.id)
        assert document.lifecycle_state != "deleted"
        assert document.usage_released_at is None
        assert tenant.doc_count == 1 and tenant.chunk_count == 3
        assert dataset.doc_count == 1 and dataset.chunk_count == 3
        assert parent_row.status == "finalizing"
    engine.dispose()


def test_finalizer_audit_failure_rolls_back_and_worker_retries(tmp_path: Path) -> None:
    engine, parent = _engine(tmp_path)
    worker = _worker(engine, FakeMilvus(), FakeGraph())
    worker.run_once()
    worker.run_once()

    original = DocumentDeletionRepository._add_audit_event

    def fail_audit(self, session, event):
        if event.action == "document.deleted":
            raise RuntimeError("injected audit failure")
        return original(self, session, event)

    DocumentDeletionRepository._add_audit_event = fail_audit
    try:
        result = worker.run_once()
    finally:
        DocumentDeletionRepository._add_audit_event = original

    assert result.retried == 1
    with Session(engine) as session:
        document = session.get(Document, "doc-1")
        tenant = session.get(Tenant, "tenant-1")
        parent_row = session.get(DocumentDeleteOperation, parent.id)
        assert document.lifecycle_state != "deleted"
        assert document.usage_released_at is None
        assert tenant.doc_count == 1 and tenant.chunk_count == 3
        assert parent_row.status == "finalizing"
    engine.dispose()


def test_dead_projection_atomically_marks_parent_attempt_document_batch_and_dead_letter(
    tmp_path: Path,
) -> None:
    engine, parent = _engine(tmp_path)
    with Session(engine) as session:
        child = session.scalar(
            select(IndexOperation).where(
                IndexOperation.delete_operation_id == parent.id,
                IndexOperation.target_store == "milvus_chunks",
            )
        )
        child.max_retries = 1
        child.created_at = datetime(2020, 1, 1)
        graph_child = session.scalar(
            select(IndexOperation).where(
                IndexOperation.delete_operation_id == parent.id,
                IndexOperation.target_store == "graph_projection",
            )
        )
        graph_child.created_at = datetime(2020, 1, 2)
        session.commit()

    class FailingMilvus(FakeMilvus):
        def delete_by_doc_id(self, doc_id: str) -> int:
            raise RuntimeError("milvus unavailable")

    result = _worker(engine, FailingMilvus(), FakeGraph()).run_once()
    assert result.dead == 1
    with Session(engine) as session:
        parent_row = session.get(DocumentDeleteOperation, parent.id)
        document = session.get(Document, "doc-1")
        attempt = session.get(DocumentIngestAttempt, parent.attempt_id)
        batch = session.get(DocumentDeleteBatch, parent.batch_id)
        assert parent_row.status == "failed"
        assert parent_row.failed_store_count == 1
        assert document.lifecycle_state == "delete_failed"
        assert document.retrieval_enabled is False
        assert attempt.state == "failed" and attempt.finished_at is not None
        assert batch.failed_count == 1
        assert batch.status == "failed"
        sibling = session.scalar(
            select(IndexOperation).where(
                IndexOperation.delete_operation_id == parent.id,
                IndexOperation.target_store == "graph_projection",
            )
        )
        assert sibling.status == "superseded"
        assert session.scalar(select(func.count()).select_from(IndexDeadLetter)) == 1
    engine.dispose()


def test_long_delete_call_keeps_lease_and_prevents_duplicate_external_effect(tmp_path: Path) -> None:
    engine, parent = _engine(tmp_path)
    with Session(engine) as session:
        graph_child = session.scalar(
            select(IndexOperation).where(
                IndexOperation.delete_operation_id == parent.id,
                IndexOperation.target_store == "graph_projection",
            )
        )
        graph_child.status = "succeeded"
        graph_child.finished_at = datetime.utcnow()
        parent_row = session.get(DocumentDeleteOperation, parent.id)
        parent_row.completed_store_count = 1
        session.commit()
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    class SlowMilvus(FakeMilvus):
        def delete_by_doc_id(self, doc_id: str) -> int:
            calls.append(doc_id)
            started.set()
            assert release.wait(timeout=5)
            return super().delete_by_doc_id(doc_id)

    slow = SlowMilvus()
    worker_a = _worker(engine, slow, FakeGraph(), lease_seconds=1)
    worker_b = _worker(engine, slow, FakeGraph(), lease_seconds=1)
    worker_b.worker_id = "worker-2"
    results = []

    thread = threading.Thread(target=lambda: results.append(worker_a.run_once()))
    thread.start()
    assert started.wait(timeout=3)
    time.sleep(1.2)
    second = worker_b.run_once()
    release.set()
    thread.join(timeout=3)

    assert second.claimed == 0
    assert calls == ["doc-1"]
    assert results[0].succeeded == 1
    engine.dispose()
