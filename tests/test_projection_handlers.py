from __future__ import annotations

import importlib
import importlib.util
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from core.chunk_catalog import ChunkCatalog
from core.document_deletion import DocumentDeletionRepository
from core.index_operations import IndexOperationQueue
from core.ingest_ledger import IngestLedger
from core.knowledge_governance import AuditContext
from indexing.index_worker import IndexOperationWorker
from models.orm import ChunkHead, Dataset, Document, IndexOperation, Tenant
from models.schemas import Chunk


def handlers_module() -> ModuleType:
    try:
        return importlib.import_module("indexing.projection_handlers")
    except ModuleNotFoundError:
        pytest.fail("indexing.projection_handlers is missing")


def isolated_handlers_module(name: str) -> ModuleType:
    module_path = Path(__file__).parents[1] / "indexing" / "projection_handlers.py"
    spec = importlib.util.spec_from_file_location(name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FakeEmbedder:
    def __init__(self):
        self.texts: list[str] = []

    def embed_texts(self, texts: list[str]):
        self.texts.extend(texts)
        return [[0.1, 0.2] for _ in texts]


class FakeMilvus:
    def __init__(self):
        now = datetime(2026, 8, 24, tzinfo=timezone.utc)
        self.existing = [
            Chunk(
                chunk_id="orphan",
                doc_id="doc-1",
                text="old",
                text_hash="old",
                created_at=now,
                updated_at=now,
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                document_revision=0,
                content_revision=0,
            )
        ]
        self.upserted: list[Chunk] = []
        self.deleted: list[str] = []

    def query_chunks_by_doc(self, doc_id: str, tenant_id: str = ""):
        return list(self.existing)

    def upsert_chunks(self, chunks, vectors):
        self.upserted.extend(chunks)
        return [chunk.chunk_id for chunk in chunks]

    def delete_by_ids(self, ids):
        self.deleted.extend(ids)
        return len(ids)


class FakeGraphBuilder:
    def __init__(self):
        self.calls: list[tuple[list[tuple[str, str]], str]] = []
        self.deleted: list[tuple[list[str], str]] = []

    def delete_by_chunk_ids(self, ids, tenant_id: str = ""):
        self.deleted.append((list(ids), tenant_id))
        return len(ids), 0

    def build(self, chunks, tenant_id: str = ""):
        self.calls.append((list(chunks), tenant_id))
        return SimpleNamespace(entity_count=1, relation_count=1)


def create_state(tmp_path: Path, *, include_graph: bool = True, claim_direct: bool = True):
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
                content_revision=2,
                desired_index_revision=2,
            )
        )
        session.commit()
    catalog = ChunkCatalog(engine)
    catalog.upsert_head(
        chunk_id="parent",
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        parent_chunk_id=None,
        chunk_index=0,
        chunk_role="parent",
        document_revision=2,
        source_content="parent",
        content="parent",
        enabled=True,
    )
    catalog.upsert_head(
        chunk_id="child",
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        parent_chunk_id="parent",
        chunk_index=1,
        chunk_role="child",
        document_revision=2,
        source_content="child",
        content="child",
        enabled=True,
    )
    catalog.upsert_head(
        chunk_id="disabled",
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        parent_chunk_id=None,
        chunk_index=2,
        chunk_role="flat",
        document_revision=2,
        source_content="disabled",
        content="disabled",
        enabled=False,
    )
    attempt = IngestLedger(engine).start_attempt(
        tenant_id="tenant-1", dataset_id="dataset-1", document_id="doc-1"
    )
    queue = IndexOperationQueue(engine)
    milvus_op = queue.enqueue_operation(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        attempt_id=attempt.id,
        target_store="milvus_chunks",
        operation="reconcile",
        dedup_key="projection:milvus:2",
        target_revision=2,
    )
    graph_op = None
    if include_graph:
        graph_op = queue.enqueue_operation(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            document_id="doc-1",
            attempt_id=attempt.id,
            target_store="graph_projection",
            operation="reconcile",
            dedup_key="projection:graph:2",
            target_revision=2,
        )
    if claim_direct:
        claimed = queue.claim_operations(
            "direct-worker",
            limit=2 if include_graph else 1,
            lease_seconds=120,
        )
        by_store = {item.target_store: item for item in claimed}
        milvus_op = by_store["milvus_chunks"]
        graph_op = by_store.get("graph_projection")
    return engine, catalog, milvus_op, graph_op


def _persist_operation(
    engine,
    operation: IndexOperation,
    *,
    operation_name: str | None = None,
    payload: dict | None = None,
) -> None:
    with Session(engine) as session:
        row = session.get(IndexOperation, operation.id)
        if operation_name is not None:
            row.operation = operation_name
            operation.operation = operation_name
        if payload is not None:
            row.payload = dict(payload)
            operation.payload = dict(payload)
        session.commit()


def _enqueue_claimed_graph_mutation(
    engine,
    base_operation: IndexOperation,
    *,
    payload: dict,
    worker_id: str,
) -> IndexOperation:
    queue = IndexOperationQueue(engine)
    queue.enqueue_operation(
        tenant_id=base_operation.tenant_id,
        dataset_id=base_operation.dataset_id,
        document_id=base_operation.document_id,
        attempt_id=base_operation.attempt_id,
        target_store="graph_projection",
        operation="upsert",
        dedup_key=f"projection:graph:{worker_id}:{payload['expected_content_revision']}",
        target_revision=base_operation.target_revision,
        payload=payload,
    )
    return queue.claim_operations(worker_id, limit=1, lease_seconds=120)[0]


def test_milvus_handler_rebuilds_only_current_retrievable_heads(tmp_path: Path) -> None:
    engine, catalog, operation, _ = create_state(tmp_path)
    embedder = FakeEmbedder()
    milvus = FakeMilvus()
    handlers = handlers_module().ProjectionHandlers(
        chunk_catalog=catalog,
        embedder=embedder,
        milvus=milvus,
        graph_builder=None,
    )

    handlers.handle_milvus(operation)

    assert embedder.texts == ["child"]
    assert [chunk.chunk_id for chunk in milvus.upserted] == ["child"]
    assert milvus.upserted[0].document_revision == 2
    assert milvus.deleted == ["orphan"]
    assert catalog.get_head("child").index_status == "ready"
    engine.dispose()


def test_graph_handler_uses_same_revision_filtered_candidates(tmp_path: Path) -> None:
    engine, catalog, _, operation = create_state(tmp_path)
    graph = FakeGraphBuilder()
    handlers = handlers_module().ProjectionHandlers(
        chunk_catalog=catalog,
        embedder=FakeEmbedder(),
        milvus=FakeMilvus(),
        graph_builder=graph,
    )

    handlers.handle_graph(operation)

    assert graph.calls == [([("child", "child")], "tenant-1")]
    engine.dispose()


def test_worker_finalizes_document_and_attempt_after_all_projections_succeed(
    tmp_path: Path,
) -> None:
    engine, catalog, operation, _ = create_state(tmp_path, include_graph=False, claim_direct=False)
    queue = IndexOperationQueue(engine)
    handlers = handlers_module().ProjectionHandlers(
        chunk_catalog=catalog,
        embedder=FakeEmbedder(),
        milvus=FakeMilvus(),
        graph_builder=None,
    )
    worker = IndexOperationWorker(
        queue,
        handlers={"milvus_chunks": handlers.handle_milvus},
        worker_id="worker-a",
    )

    result = worker.run_once(limit=1)

    assert result.succeeded == 1
    with Session(engine) as session:
        document = session.get(Document, "doc-1")
        assert document is not None
        assert document.status == "completed"
        assert document.indexed_revision == 2
    attempts = IngestLedger(engine).list_attempts("doc-1")
    assert attempts[0].state == "completed"
    assert attempts[0].primary_index_ready_at is not None
    engine.dispose()


def test_graph_edit_deletes_revision_fenced_chunk_before_rebuild(tmp_path: Path) -> None:
    engine, catalog, _, operation = create_state(tmp_path)
    assert operation is not None
    edited = catalog.edit_chunk(
        "child", expected_revision=0, content="edited", editor_id="operator"
    )
    _persist_operation(
        engine,
        operation,
        operation_name="upsert",
        payload={
            "chunk_id": "child",
            "expected_content_revision": edited.content_revision,
            "mutation": "edit",
        },
    )
    graph = FakeGraphBuilder()
    handlers = handlers_module().ProjectionHandlers(
        chunk_catalog=catalog, embedder=FakeEmbedder(), milvus=FakeMilvus(), graph_builder=graph
    )

    handlers.handle_graph(operation)

    assert graph.deleted == [(["child"], "tenant-1")]
    assert graph.calls == [([("child", "edited")], "tenant-1")]
    engine.dispose()


def test_graph_tombstone_is_delete_only(tmp_path: Path) -> None:
    engine, catalog, _, operation = create_state(tmp_path)
    assert operation is not None
    tombstone = catalog.tombstone_chunk("child", expected_revision=0, editor_id="operator")
    _persist_operation(
        engine,
        operation,
        operation_name="delete",
        payload={
            "chunk_id": "child",
            "expected_content_revision": tombstone.content_revision,
            "mutation": "delete",
        },
    )
    graph = FakeGraphBuilder()
    handlers = handlers_module().ProjectionHandlers(
        chunk_catalog=catalog, embedder=FakeEmbedder(), milvus=FakeMilvus(), graph_builder=graph
    )

    handlers.handle_graph(operation)

    assert graph.deleted == [(["child"], "tenant-1")]
    assert graph.calls == []
    engine.dispose()


def test_stale_chunk_payload_is_rejected_before_milvus_or_graph_write(tmp_path: Path) -> None:
    engine, catalog, operation, graph_operation = create_state(tmp_path)
    assert graph_operation is not None
    catalog.edit_chunk("child", expected_revision=0, content="new", editor_id="operator")
    payload = {"chunk_id": "child", "expected_content_revision": 0, "mutation": "edit"}
    _persist_operation(engine, operation, payload=payload)
    _persist_operation(engine, graph_operation, payload=payload)
    embedder = FakeEmbedder()
    milvus = FakeMilvus()
    graph = FakeGraphBuilder()
    handlers = handlers_module().ProjectionHandlers(
        chunk_catalog=catalog, embedder=embedder, milvus=milvus, graph_builder=graph
    )

    with pytest.raises(handlers_module().StaleProjectionOperation):
        handlers.handle_milvus(operation)
    with pytest.raises(handlers_module().StaleProjectionOperation):
        handlers.handle_graph(graph_operation)

    assert embedder.texts == []
    assert milvus.upserted == []
    assert milvus.deleted == []
    assert graph.deleted == []
    assert graph.calls == []
    engine.dispose()


def test_context_header_is_canonical_for_chunk_mutation_embedding(tmp_path: Path) -> None:
    engine, catalog, operation, _ = create_state(tmp_path, include_graph=False)
    with Session(engine) as session:
        head = session.get(ChunkHead, "child")
        assert head is not None
        head.context_header = "chapter context"
        session.commit()
    edited = catalog.edit_chunk(
        "child", expected_revision=0, content="edited", editor_id="operator"
    )
    _persist_operation(
        engine,
        operation,
        payload={
            "chunk_id": "child",
            "expected_content_revision": edited.content_revision,
            "mutation": "edit",
        },
    )
    embedder = FakeEmbedder()
    handlers = handlers_module().ProjectionHandlers(
        chunk_catalog=catalog, embedder=embedder, milvus=FakeMilvus()
    )

    handlers.handle_milvus(operation)

    assert embedder.texts == ["chapter context\n\nedited"]
    engine.dispose()


def test_concurrent_graph_mutations_serialize_authority_edit_before_newer_build(
    tmp_path: Path,
) -> None:
    import threading
    from types import SimpleNamespace

    engine, catalog, _, operation = create_state(tmp_path)
    assert operation is not None
    first = catalog.edit_chunk(
        "child", expected_revision=0, content="revision one", editor_id="operator"
    )
    _persist_operation(
        engine,
        operation,
        operation_name="upsert",
        payload={
            "chunk_id": "child",
            "expected_content_revision": first.content_revision,
            "mutation": "edit",
        },
    )

    class RacingGraph:
        def __init__(self):
            self.first_delete_started = threading.Event()
            self.release_first_delete = threading.Event()
            self.newer_authority_saved = threading.Event()
            self.newer_build_finished = threading.Event()
            self.delete_calls = 0
            self.current_text = None

        def delete_by_chunk_ids(self, _ids, tenant_id=""):
            self.delete_calls += 1
            if self.delete_calls == 1:
                self.first_delete_started.set()
                assert self.release_first_delete.wait(timeout=3)
            self.current_text = None
            return 1, 0

        def build(self, chunks, tenant_id=""):
            self.current_text = list(chunks)[0][1]
            if self.current_text == "revision two":
                self.newer_build_finished.set()
            return SimpleNamespace(entity_count=1, relation_count=1)

    graph = RacingGraph()
    handlers = handlers_module().ProjectionHandlers(
        chunk_catalog=catalog,
        embedder=FakeEmbedder(),
        milvus=FakeMilvus(),
        graph_builder=graph,
    )
    errors: list[Exception] = []

    def run_first() -> None:
        try:
            handlers.handle_graph(operation)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    def edit_and_run_newer() -> None:
        try:
            second = catalog.edit_chunk(
                "child", expected_revision=1, content="revision two", editor_id="operator"
            )
            graph.newer_authority_saved.set()
            newer_operation = _enqueue_claimed_graph_mutation(
                engine,
                operation,
                payload={
                    "chunk_id": "child",
                    "expected_content_revision": second.content_revision,
                    "mutation": "edit",
                },
                worker_id="newer-worker",
            )
            handlers.handle_graph(newer_operation)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    first_thread = threading.Thread(target=run_first)
    first_thread.start()
    assert graph.first_delete_started.wait(timeout=3)
    newer_thread = threading.Thread(target=edit_and_run_newer)
    newer_thread.start()

    assert graph.newer_authority_saved.wait(timeout=1)
    assert not graph.newer_build_finished.wait(timeout=0.1)
    assert graph.delete_calls == 1
    graph.release_first_delete.set()
    first_thread.join(timeout=3)
    newer_thread.join(timeout=3)

    assert not first_thread.is_alive()
    assert not newer_thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], handlers_module().StaleProjectionOperation)
    assert graph.current_text == "revision two"
    engine.dispose()


def test_sqlite_graph_revision_fence_uses_shared_single_process_fallback(
    tmp_path: Path,
) -> None:
    import threading
    from types import SimpleNamespace

    first_module = isolated_handlers_module("projection_handlers_worker_one")
    second_module = isolated_handlers_module("projection_handlers_worker_two")
    engine, catalog, _, operation = create_state(tmp_path)
    assert operation is not None
    first = catalog.edit_chunk(
        "child", expected_revision=0, content="revision one", editor_id="operator"
    )
    _persist_operation(
        engine,
        operation,
        operation_name="upsert",
        payload={
            "chunk_id": "child",
            "expected_content_revision": first.content_revision,
            "mutation": "edit",
        },
    )

    class CrossProcessGraph:
        def __init__(self):
            self.guard = threading.Lock()
            self.first_delete_started = threading.Event()
            self.release_first_delete = threading.Event()
            self.newer_build_finished = threading.Event()
            self.delete_calls = 0
            self.current_text = None

        def delete_by_chunk_ids(self, _ids, tenant_id=""):
            with self.guard:
                self.delete_calls += 1
                call_number = self.delete_calls
            if call_number == 1:
                self.first_delete_started.set()
                assert self.release_first_delete.wait(timeout=3)
            self.current_text = None
            return 1, 0

        def build(self, chunks, tenant_id=""):
            text = list(chunks)[0][1]
            self.current_text = text
            if text == "revision two":
                self.newer_build_finished.set()
            return SimpleNamespace(entity_count=1, relation_count=1)

    graph = CrossProcessGraph()
    first_handler = first_module.ProjectionHandlers(
        chunk_catalog=catalog,
        embedder=FakeEmbedder(),
        milvus=FakeMilvus(),
        graph_builder=graph,
    )
    second_handler = second_module.ProjectionHandlers(
        chunk_catalog=catalog,
        embedder=FakeEmbedder(),
        milvus=FakeMilvus(),
        graph_builder=graph,
    )
    errors: list[Exception] = []

    def run_first() -> None:
        try:
            first_handler.handle_graph(operation)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    def edit_and_run_newer() -> None:
        try:
            second = catalog.edit_chunk(
                "child", expected_revision=1, content="revision two", editor_id="operator"
            )
            newer_operation = _enqueue_claimed_graph_mutation(
                engine,
                operation,
                payload={
                    "chunk_id": "child",
                    "expected_content_revision": second.content_revision,
                    "mutation": "edit",
                },
                worker_id="newer-worker",
            )
            second_handler.handle_graph(newer_operation)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    first_thread = threading.Thread(target=run_first)
    first_thread.start()
    assert graph.first_delete_started.wait(timeout=3)
    newer_thread = threading.Thread(target=edit_and_run_newer)
    newer_thread.start()

    graph.newer_build_finished.wait(timeout=0.25)
    graph.release_first_delete.set()
    first_thread.join(timeout=3)
    newer_thread.join(timeout=3)

    assert not first_thread.is_alive()
    assert not newer_thread.is_alive()
    assert len(errors) == 1
    assert type(errors[0]).__name__ == "StaleProjectionOperation"
    assert graph.current_text == "revision two"
    engine.dispose()


class _DeleteAwareMilvus:
    def __init__(self):
        self.chunk_ids: set[str] = set()
        self.upsert_calls: list[list[str]] = []
        self.delete_calls: list[str] = []

    def upsert_chunks(self, chunks, _vectors):
        ids = [chunk.chunk_id for chunk in chunks]
        self.upsert_calls.append(ids)
        self.chunk_ids.update(ids)
        return ids

    def query_chunks_by_doc(self, _doc_id: str, tenant_id: str = ""):
        return []

    def delete_by_ids(self, ids):
        self.chunk_ids.difference_update(ids)
        return len(ids)

    def delete_by_doc_id(self, doc_id: str):
        self.delete_calls.append(doc_id)
        removed = len(self.chunk_ids)
        self.chunk_ids.clear()
        return removed

    def flush(self):
        return None

    def count_chunks_by_document(
        self, _doc_id: str, tenant_id: str = "", *, consistency: str = "strong"
    ):
        return len(self.chunk_ids)


class _DeleteAwareGraph:
    def __init__(self):
        self.prepare_started = threading.Event()
        self.release_prepare = threading.Event()
        self.write_calls: list[list[str]] = []
        self.fact_ids: set[str] = set()

    def prepare_build(self, chunks):
        self.prepare_started.set()
        assert self.release_prepare.wait(timeout=5)
        return list(chunks)

    def write_prepared(self, prepared, tenant_id: str = ""):
        ids = [chunk_id for chunk_id, _text in prepared]
        self.write_calls.append(ids)
        self.fact_ids.update(ids)
        return SimpleNamespace(stale_skipped=False)

    def build(self, chunks, tenant_id: str = "", before_write=None):
        prepared = self.prepare_build(chunks)
        if before_write is not None and not before_write():
            return SimpleNamespace(stale_skipped=True)
        return self.write_prepared(prepared, tenant_id=tenant_id)

    def delete_by_chunk_ids(self, ids, tenant_id: str = ""):
        self.fact_ids.difference_update(ids)
        return len(ids), 0

    def count_facts_by_chunk_ids(self, ids, tenant_id: str = ""):
        return len(self.fact_ids.intersection(ids))


def _request_delete_after_writer_started(engine) -> None:
    with Session(engine) as session:
        document = session.get(Document, "doc-1")
        document.chunk_count = 2
        session.commit()
    DocumentDeletionRepository(engine).request_delete(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        expected_generation=0,
        idempotency_key="writer-race-delete",
        actor=AuditContext(actor_id="owner-1", request_id="request-1", request_ip="127.0.0.1"),
        reason="writer race",
        origin="operator",
    )


def _drain_delete_worker(engine, handlers) -> None:
    worker = IndexOperationWorker(
        IndexOperationQueue(engine),
        handlers=handlers.as_mapping(),
        worker_id="delete-worker",
        lease_seconds=1,
    )
    for _ in range(6):
        if worker.run_once().claimed == 0:
            break


def test_blocked_embedding_writer_cannot_revive_milvus_after_delete_finalizes(
    tmp_path: Path,
) -> None:
    engine, catalog, _operation, _ = create_state(tmp_path, include_graph=False, claim_direct=False)
    started = threading.Event()
    release = threading.Event()

    class BlockingEmbedder(FakeEmbedder):
        def embed_texts(self, texts):
            started.set()
            assert release.wait(timeout=5)
            return super().embed_texts(texts)

    milvus = _DeleteAwareMilvus()
    graph = _DeleteAwareGraph()
    graph.release_prepare.set()
    handlers = handlers_module().ProjectionHandlers(
        chunk_catalog=catalog,
        embedder=BlockingEmbedder(),
        milvus=milvus,
        graph_builder=graph,
    )
    writer = IndexOperationWorker(
        IndexOperationQueue(engine),
        handlers=handlers.as_mapping(),
        worker_id="writer-a",
        lease_seconds=1,
    )
    writer_results = []
    thread = threading.Thread(target=lambda: writer_results.append(writer.run_once()))
    thread.start()
    assert started.wait(timeout=3)

    _request_delete_after_writer_started(engine)
    _drain_delete_worker(engine, handlers)
    with Session(engine) as session:
        assert session.get(Document, "doc-1").lifecycle_state == "deleted"
    release.set()
    thread.join(timeout=3)

    assert not thread.is_alive()
    assert milvus.chunk_ids == set()
    assert milvus.upsert_calls == []
    engine.dispose()


def test_superseded_graph_writer_finishes_prepare_but_never_builds_after_delete(
    tmp_path: Path,
) -> None:
    engine, catalog, _milvus_operation, _graph_operation = create_state(
        tmp_path, claim_direct=False
    )
    milvus = _DeleteAwareMilvus()
    graph = _DeleteAwareGraph()
    handlers = handlers_module().ProjectionHandlers(
        chunk_catalog=catalog,
        embedder=FakeEmbedder(),
        milvus=milvus,
        graph_builder=graph,
    )
    # Make graph operation the first claim for the old writer.
    with Session(engine) as session:
        rows = list(session.scalars(select(IndexOperation).order_by(IndexOperation.id)))
        for row in rows:
            row.created_at = datetime(2026, 8, 25, 10, 0, 1)
            if row.target_store == "graph_projection":
                row.created_at = datetime(2026, 8, 25, 10, 0, 0)
        session.commit()
    writer = IndexOperationWorker(
        IndexOperationQueue(engine),
        handlers=handlers.as_mapping(),
        worker_id="graph-writer",
        lease_seconds=1,
    )
    writer_thread = threading.Thread(target=writer.run_once)
    writer_thread.start()
    assert graph.prepare_started.wait(timeout=3)

    _request_delete_after_writer_started(engine)
    delete_done = threading.Event()
    delete_thread = threading.Thread(
        target=lambda: (_drain_delete_worker(engine, handlers), delete_done.set())
    )
    delete_thread.start()
    completed_before_release = delete_done.wait(timeout=1.5)
    graph.release_prepare.set()
    writer_thread.join(timeout=3)
    delete_thread.join(timeout=3)

    assert completed_before_release is True
    assert graph.write_calls == []
    assert graph.fact_ids == set()
    with Session(engine) as session:
        assert session.get(Document, "doc-1").lifecycle_state == "deleted"
    engine.dispose()


def test_lost_operation_ownership_prevents_all_milvus_side_effects(
    tmp_path: Path,
) -> None:
    engine, catalog, operation, _ = create_state(tmp_path, include_graph=False, claim_direct=False)
    claimed = IndexOperationQueue(engine).claim_operations("writer-a", limit=1, lease_seconds=30)[0]
    with Session(engine) as session:
        row = session.get(IndexOperation, claimed.id)
        row.claimed_by = "writer-b"
        session.commit()
    milvus = _DeleteAwareMilvus()
    handlers = handlers_module().ProjectionHandlers(
        chunk_catalog=catalog,
        embedder=FakeEmbedder(),
        milvus=milvus,
    )

    with pytest.raises(handlers_module().StaleProjectionOperation):
        handlers.handle_milvus(claimed)

    assert milvus.upsert_calls == []
    assert milvus.delete_calls == []
    assert milvus.chunk_ids == set()
    engine.dispose()


@pytest.mark.parametrize("target_store", ["milvus_chunks", "graph_projection"])
def test_unknown_projection_operation_fails_before_external_side_effects(
    tmp_path: Path, target_store: str
) -> None:
    engine, catalog, milvus_operation, graph_operation = create_state(tmp_path)
    operation = milvus_operation if target_store == "milvus_chunks" else graph_operation
    operation.operation = "future_op"
    with Session(engine) as session:
        stored = session.get(IndexOperation, operation.id)
        assert stored is not None
        stored.operation = "future_op"
        session.commit()
    embedder = FakeEmbedder()
    milvus = FakeMilvus()
    graph = FakeGraphBuilder()
    handlers = handlers_module().ProjectionHandlers(
        chunk_catalog=catalog,
        embedder=embedder,
        milvus=milvus,
        graph_builder=graph,
    )

    with pytest.raises(handlers_module().UnsupportedProjectionOperation, match="future_op"):
        if target_store == "milvus_chunks":
            handlers.handle_milvus(operation)
        else:
            handlers.handle_graph(operation)

    assert embedder.texts == []
    assert milvus.upserted == []
    assert milvus.deleted == []
    assert graph.calls == []
    assert graph.deleted == []
    engine.dispose()


def test_registered_projection_operation_is_dispatched_without_host_edits(
    tmp_path: Path,
) -> None:
    engine, catalog, operation, _ = create_state(tmp_path, include_graph=False, claim_direct=False)
    calls: list[str] = []
    milvus = FakeMilvus()

    def custom_strategy(context):
        calls.append(context.operation.operation)
        context.handler._handle_milvus_projection(context.operation)

    module = handlers_module()
    module.register_projection_operation("milvus_chunks", "custom_probe", custom_strategy)
    try:
        operation.operation = "custom_probe"
        with Session(engine) as session:
            stored = session.get(IndexOperation, operation.id)
            assert stored is not None
            stored.operation = "custom_probe"
            session.commit()
        handlers = module.ProjectionHandlers(
            chunk_catalog=catalog,
            embedder=FakeEmbedder(),
            milvus=milvus,
        )
        worker = IndexOperationWorker(
            IndexOperationQueue(engine),
            handlers=handlers.as_mapping(),
            worker_id="custom-projection-worker",
        )
        result = worker.run_once(limit=1)
        assert calls == ["custom_probe"]
        assert result.succeeded == 1
        assert [chunk.chunk_id for chunk in milvus.upserted] == ["child"]
    finally:
        module.unregister_projection_operation("milvus_chunks", "custom_probe")
        engine.dispose()


def test_worker_handler_mapping_includes_registered_projection_targets(
    tmp_path: Path,
) -> None:
    from core.projection_attempt_lifecycle import (
        ProjectionAttemptLifecyclePolicy,
        register_projection_attempt_lifecycle_policy,
        unregister_projection_attempt_lifecycle_policy,
    )
    from core.projection_revision_strategies import (
        ProjectionRevisionContext,
        register_projection_revision_strategy,
        unregister_projection_revision_strategy,
    )

    engine, catalog, operation, _ = create_state(tmp_path, include_graph=False, claim_direct=False)
    module = handlers_module()
    store = "custom_vector"
    dispatched: list[str] = []

    def custom_strategy(context):
        dispatched.append(context.operation.target_store)

    def advance_revision(context: ProjectionRevisionContext) -> None:
        with Session(context.engine) as session:
            document = session.get(Document, context.operation.document_id)
            assert document is not None
            document.indexed_revision = context.operation.target_revision
            session.commit()

    module.register_projection_operation(store, "upsert", custom_strategy)
    register_projection_revision_strategy(store, advance_revision)
    register_projection_attempt_lifecycle_policy(
        store,
        ProjectionAttemptLifecyclePolicy(
            role="primary",
            blocks_finalization=False,
            is_ready=lambda context: context.desired_index_revision == context.indexed_revision,
        ),
    )
    try:
        with Session(engine) as session:
            stored = session.get(IndexOperation, operation.id)
            assert stored is not None
            stored.target_store = store
            stored.operation = "upsert"
            session.commit()
        operation.target_store = store
        operation.operation = "upsert"

        handlers = module.ProjectionHandlers(
            chunk_catalog=catalog,
            embedder=FakeEmbedder(),
            milvus=FakeMilvus(),
        )
        mapping = handlers.as_mapping()
        assert "custom_vector" in mapping
        assert callable(mapping["custom_vector"])
        assert "graph_projection" not in mapping
        worker = IndexOperationWorker(
            IndexOperationQueue(engine),
            handlers=mapping,
            worker_id="custom-target-worker",
        )
        result = worker.run_once(limit=1)
        assert result.succeeded == 1
        assert result.retried == 0
        assert dispatched == ["custom_vector"]
    finally:
        module.unregister_projection_operation(store, "upsert")
        unregister_projection_revision_strategy(store)
        unregister_projection_attempt_lifecycle_policy(store)
        engine.dispose()


def test_registered_strategy_value_error_is_not_reported_as_unknown_operation(
    tmp_path: Path,
) -> None:
    engine, catalog, operation, _ = create_state(tmp_path, include_graph=False)
    module = handlers_module()

    def broken_strategy(context):
        raise ValueError("invalid projection payload")

    module.register_projection_operation("milvus_chunks", "broken_probe", broken_strategy)
    try:
        operation.operation = "broken_probe"
        handlers = module.ProjectionHandlers(
            chunk_catalog=catalog, embedder=FakeEmbedder(), milvus=FakeMilvus()
        )
        with pytest.raises(ValueError, match="invalid projection payload") as error:
            handlers.handle_milvus(operation)
        assert type(error.value) is ValueError
    finally:
        module.unregister_projection_operation("milvus_chunks", "broken_probe")
        engine.dispose()


def test_registered_strategy_unknown_provider_error_is_not_reported_as_unknown_operation(
    tmp_path: Path,
) -> None:
    from core.providers import UnknownProviderError

    engine, catalog, operation, _ = create_state(tmp_path, include_graph=False)
    module = handlers_module()

    def broken_strategy(_context):
        raise UnknownProviderError("nested provider is unavailable")

    module.register_projection_operation("milvus_chunks", "nested_provider_probe", broken_strategy)
    try:
        operation.operation = "nested_provider_probe"
        handlers = module.ProjectionHandlers(
            chunk_catalog=catalog, embedder=FakeEmbedder(), milvus=FakeMilvus()
        )
        with pytest.raises(UnknownProviderError, match="nested provider is unavailable") as error:
            handlers.handle_milvus(operation)
        assert type(error.value) is UnknownProviderError
    finally:
        module.unregister_projection_operation("milvus_chunks", "nested_provider_probe")
        engine.dispose()


def test_projection_registration_rejects_async_and_generator_strategies() -> None:
    module = handlers_module()

    async def async_strategy(_context):
        return None

    def generator_strategy(_context):
        yield None

    async def async_generator_strategy(_context):
        yield None

    for operation, strategy in (
        ("async_probe", async_strategy),
        ("generator_probe", generator_strategy),
        ("async_generator_probe", async_generator_strategy),
    ):
        with pytest.raises(TypeError, match="must be synchronous"):
            module.register_projection_operation("milvus_chunks", operation, strategy)


def test_projection_dispatch_rejects_an_unexpected_awaitable_result(
    tmp_path: Path,
) -> None:
    engine, catalog, operation, _ = create_state(tmp_path, include_graph=False)
    module = handlers_module()
    started: list[bool] = []

    async def deferred_side_effect():
        started.append(True)

    def returns_awaitable(_context):
        return deferred_side_effect()

    module.register_projection_operation("milvus_chunks", "awaitable_probe", returns_awaitable)
    try:
        operation.operation = "awaitable_probe"
        handlers = module.ProjectionHandlers(
            chunk_catalog=catalog, embedder=FakeEmbedder(), milvus=FakeMilvus()
        )
        with pytest.raises(TypeError, match="must be synchronous"):
            handlers.handle_milvus(operation)
        assert started == []
    finally:
        module.unregister_projection_operation("milvus_chunks", "awaitable_probe")
        engine.dispose()


def test_projection_registration_refuses_duplicate_and_non_callable() -> None:
    module = handlers_module()
    with pytest.raises(ValueError, match="already registered"):
        module.register_projection_operation("milvus_chunks", "reconcile", lambda context: None)
    with pytest.raises(TypeError, match="callable"):
        module.register_projection_operation("milvus_chunks", "broken_probe", None)
    with pytest.raises(TypeError, match="accept one positional context"):
        module.register_projection_operation("milvus_chunks", "broken_probe", lambda: None)


def test_case_variant_operation_code_does_not_alias_a_builtin_strategy(
    tmp_path: Path,
) -> None:
    engine, catalog, operation, _ = create_state(tmp_path, include_graph=False)
    operation.operation = "UPSERT"
    with Session(engine) as session:
        stored = session.get(IndexOperation, operation.id)
        assert stored is not None
        stored.operation = "UPSERT"
        session.commit()
    embedder = FakeEmbedder()
    milvus = FakeMilvus()
    handlers = handlers_module().ProjectionHandlers(
        chunk_catalog=catalog, embedder=embedder, milvus=milvus
    )

    with pytest.raises(handlers_module().UnsupportedProjectionOperation):
        handlers.handle_milvus(operation)

    assert embedder.texts == []
    assert milvus.upserted == []
    assert milvus.deleted == []
    engine.dispose()
