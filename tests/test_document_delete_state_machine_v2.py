from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from core.document_deletion import (
    DeleteBatchItemRequest,
    DocumentDeletionRepository,
)
from core.knowledge_content import ContentConflict, ContentNotFound
from core.knowledge_governance import AuditContext
from models.orm import (
    ChunkHead,
    DataSourceRecord,
    Dataset,
    Document,
    DocumentDeleteBatch,
    DocumentDeleteOperation,
    DocumentIngestAttempt,
    IndexOperation,
    KnowledgeAuditEvent,
    SourceDocumentState,
    Tenant,
)


def _audit(actor: str = "owner-1", request: str = "request-1") -> AuditContext:
    return AuditContext(actor_id=actor, request_id=request, request_ip="127.0.0.1")


def _engine(tmp_path: Path, *, documents: int = 2):
    tmp_path.mkdir(parents=True, exist_ok=True)
    # 目录库用 session 级模板库（tests/_catalog_template.py）：建一次已迁移到
    # head 的 SQLite、各用例拷文件，实测 4.0ms/次 vs 重跑迁移 13.9s（3432x）。
    # 本文件不需要 BASELINE（不验证「从 baseline 升到 head」的过程本身），
    # 所以拷模板与重跑迁移等价。
    from _catalog_template import head_db_url
    url = head_db_url(f"{(tmp_path / 'durable-delete.db').as_posix()}?timeout=20")
    engine = create_engine(url, connect_args={"timeout": 20})

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=20000")
        cursor.close()

    with Session(engine) as session:
        session.add(
            Tenant(id="tenant-1", name="Tenant", doc_count=documents, chunk_count=documents * 2)
        )
        session.add(
            Dataset(
                id="dataset-1",
                tenant_id="tenant-1",
                name="Dataset",
                doc_count=documents,
                chunk_count=documents * 2,
                graph_enabled=True,
            )
        )
        session.flush()
        for index in range(1, documents + 1):
            document_id = f"doc-{index}"
            session.add(
                Document(
                    id=document_id,
                    tenant_id="tenant-1",
                    dataset_id="dataset-1",
                    name=f"Document {index}",
                    status="completed",
                    lifecycle_state="active",
                    retrieval_enabled=True,
                    content_revision=4,
                    desired_index_revision=4,
                    indexed_revision=4,
                    graph_revision=4,
                    chunk_count=2,
                    source_id="source-1",
                )
            )
        session.add(
            DataSourceRecord(
                id="source-1",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name="Source",
                source_type="local_dir",
            )
        )
        session.flush()
        for index in range(1, documents + 1):
            document_id = f"doc-{index}"
            session.add(
                SourceDocumentState(
                    id=f"state-{index}",
                    source_id="source-1",
                    doc_id=document_id,
                    external_id=f"external-{index}",
                )
            )
            for chunk_index in range(2):
                chunk_id = f"chunk-{index}-{chunk_index}"
                session.add(
                    ChunkHead(
                        id=chunk_id,
                        tenant_id="tenant-1",
                        dataset_id="dataset-1",
                        document_id=document_id,
                        chunk_index=chunk_index,
                        chunk_role="flat" if chunk_index == 0 else "child",
                        document_revision=4,
                        content_revision=chunk_index + 2,
                        source_content=f"source {chunk_id}",
                        content=f"content {chunk_id}",
                        content_hash=f"hash-{chunk_id}",
                        enabled=True,
                        desired_index_revision=4,
                        indexed_revision=4,
                        index_status="succeeded",
                    )
                )
        session.commit()
    return engine


def test_request_delete_creates_generation_fence_manifest_attempt_children_and_audit(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    repository = DocumentDeletionRepository(engine)

    result = repository.request_delete(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        expected_generation=0,
        idempotency_key="delete-doc-1-v1",
        actor=_audit(),
        reason="retention request",
        origin="operator",
    )

    assert result.status == "queued"
    assert result.delete_generation == 1
    assert result.chunk_manifest_count == 2
    assert len(result.chunk_manifest_hash) == 64
    with Session(engine) as session:
        document = session.get(Document, "doc-1")
        dataset = session.get(Dataset, "dataset-1")
        source_state = session.scalar(
            select(SourceDocumentState).where(SourceDocumentState.doc_id == "doc-1")
        )
        attempt = session.get(DocumentIngestAttempt, result.attempt_id)
        operations = list(
            session.scalars(
                select(IndexOperation)
                .where(IndexOperation.delete_operation_id == result.id)
                .order_by(IndexOperation.target_store)
            )
        )
        audits = list(
            session.scalars(
                select(KnowledgeAuditEvent).where(
                    KnowledgeAuditEvent.resource_id == result.id,
                    KnowledgeAuditEvent.action == "document.delete_requested",
                )
            )
        )
    assert document.mutation_generation == 1
    assert document.lifecycle_state == "delete_requested"
    assert document.retrieval_enabled is False
    assert document.active_delete_operation_id == result.id
    assert document.deletion_requested_at is not None
    assert dataset.serving_generation == 1
    assert source_state.state == "operator_suppressed"
    assert source_state.document_generation == 1
    assert source_state.delete_operation_id == result.id
    assert source_state.suppressed_at is not None
    assert attempt.attempt_kind == "document_delete"
    assert attempt.document_generation == 1
    assert attempt.state == "running"
    assert {item.target_store for item in operations} == {"milvus_chunks", "graph_projection"}
    assert all(item.operation == "delete_document" for item in operations)
    assert all(item.document_generation == 1 for item in operations)
    assert len(audits) == 1
    engine.dispose()


def test_registered_delete_target_persists_and_remains_in_finalizer_barrier(
    tmp_path: Path,
) -> None:
    from core.projection_attempt_lifecycle import (
        ProjectionAttemptLifecyclePolicy,
        register_projection_attempt_lifecycle_policy,
        unregister_projection_attempt_lifecycle_policy,
    )
    from core.document_delete_targets import document_delete_projection_targets
    from core.document_delete_targets import _unregister_document_delete_target
    from core.projection_revision_strategies import (
        register_projection_revision_strategy,
        unregister_projection_revision_strategy,
    )
    from core.projection_target_producers import (
        ProjectionTargetPolicy,
        ProjectionTargetProductionContext,
        projection_targets,
        register_projection_target_policy,
        unregister_projection_target_policy,
    )
    from core.projection_target_contract import (
        ProjectionRequeuePolicy,
        register_projection_requeue_policy,
        unregister_projection_requeue_policy,
    )
    from core.index_operations import IndexOperationQueue
    from indexing.projection_handlers import (
        register_projection_operation,
        unregister_projection_operation,
    )
    from indexing.projection_target_runtime import (
        IncompleteProjectionTargetRuntime,
        register_projection_delete_target_runtime,
        retire_projection_delete_target_runtime,
    )

    engine = _engine(tmp_path, documents=2)
    repository = DocumentDeletionRepository(engine)
    target_store = "custom_vector"
    operations = ("upsert", "reconcile", "delete", "delete_document")

    def validate_requeue(_context) -> bool:
        return True

    requeue_operations = ("upsert", "delete", "delete_document")
    for operation in requeue_operations:
        family = "document_delete" if operation == "delete_document" else "ordinary"
        register_projection_requeue_policy(
            target_store,
            operation,
            ProjectionRequeuePolicy(family, validate_requeue),
        )
    for operation in operations:
        register_projection_operation(target_store, operation, lambda _context: None)
    register_projection_revision_strategy(target_store, lambda _context: None)
    register_projection_attempt_lifecycle_policy(
        target_store,
        ProjectionAttemptLifecyclePolicy(
            role="primary",
            blocks_finalization=False,
            is_ready=lambda _context: True,
        ),
    )
    register_projection_target_policy(
        target_store,
        ProjectionTargetPolicy(
            order=30,
            dedup_key_component=target_store,
            enabled=lambda _context: True,
        ),
    )
    register_projection_delete_target_runtime(target_store, order=30)
    retired = False
    try:
        assert document_delete_projection_targets() == (
            "milvus_chunks",
            "graph_projection",
            "custom_vector",
        )
        with pytest.raises(ValueError, match="already registered"):
            register_projection_delete_target_runtime(target_store, order=40)

        unregister_projection_operation(target_store, "delete_document")
        try:
            with pytest.raises(IncompleteProjectionTargetRuntime, match="missing worker handlers"):
                repository.request_delete(
                    tenant_id="tenant-1",
                    dataset_id="dataset-1",
                    document_id="doc-1",
                    expected_generation=0,
                    idempotency_key="incomplete-delete-target",
                    actor=_audit(),
                    reason="must not partially enqueue",
                    origin="operator",
                )
        finally:
            register_projection_operation(target_store, "delete_document", lambda _context: None)
        with Session(engine) as session:
            document = session.get(Document, "doc-1")
            assert document is not None
            assert document.mutation_generation == 0
            assert document.lifecycle_state == "active"
            assert list(session.scalars(select(IndexOperation))) == []

        result = repository.request_delete(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            document_id="doc-1",
            expected_generation=0,
            idempotency_key="custom-delete-target",
            actor=_audit(),
            reason="remove all target projections",
            origin="operator",
        )
        with Session(engine) as session:
            parent = session.get(DocumentDeleteOperation, result.id)
            children = list(
                session.scalars(
                    select(IndexOperation)
                    .where(IndexOperation.delete_operation_id == result.id)
                    .order_by(IndexOperation.created_at, IndexOperation.id)
                )
            )
            assert parent is not None
            assert parent.required_store_count == 3
            assert {child.target_store for child in children} == {
                "milvus_chunks",
                "graph_projection",
                "custom_vector",
            }

        queue = IndexOperationQueue(engine)
        for _ in children:
            claimed = queue.claim_operations("delete-target-worker", limit=1, lease_seconds=120)
            assert len(claimed) == 1
            repository.complete_projection_operation(
                claimed[0].id, worker_id="delete-target-worker"
            )

        retire_projection_delete_target_runtime(target_store)
        retired = True
        assert document_delete_projection_targets() == (
            "milvus_chunks",
            "graph_projection",
            "custom_vector",
        )
        assert {
            target.target_store
            for target in projection_targets(
                ProjectionTargetProductionContext(include_graph=True)
            )
        } == {"milvus_chunks", "graph_projection"}

        finalizer = queue.claim_operations("delete-target-worker", limit=1, lease_seconds=120)
        assert len(finalizer) == 1
        assert finalizer[0].operation == "finalize_document_delete"
        completed = repository.finalize_document_delete(
            finalizer[0].id, worker_id="delete-target-worker"
        )
        assert completed.status == "completed"
        assert completed.completed_store_count == 3
        next_delete = repository.request_delete(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            document_id="doc-2",
            expected_generation=0,
            idempotency_key="retired-target-cleanup",
            actor=_audit(),
            reason="clean old target data",
            origin="operator",
        )
        with Session(engine) as session:
            next_parent = session.get(DocumentDeleteOperation, next_delete.id)
            next_children = list(
                session.scalars(
                    select(IndexOperation).where(
                        IndexOperation.delete_operation_id == next_delete.id
                    )
                )
            )
            assert next_parent is not None
            assert next_parent.required_store_count == 3
            assert "custom_vector" in {child.target_store for child in next_children}

        for _ in next_children:
            claimed = queue.claim_operations("delete-target-worker", limit=1, lease_seconds=120)
            assert len(claimed) == 1
            repository.complete_projection_operation(
                claimed[0].id, worker_id="delete-target-worker"
            )
        next_finalizer = queue.claim_operations(
            "delete-target-worker", limit=1, lease_seconds=120
        )
        assert len(next_finalizer) == 1
        next_completed = repository.finalize_document_delete(
            next_finalizer[0].id, worker_id="delete-target-worker"
        )
        assert next_completed.status == "completed"
        assert next_completed.completed_store_count == 3
    finally:
        if not retired:
            retire_projection_delete_target_runtime(target_store)
        unregister_projection_target_policy(target_store)
        for operation in requeue_operations:
            unregister_projection_requeue_policy(target_store, operation)
        for operation in operations:
            unregister_projection_operation(target_store, operation)
        unregister_projection_revision_strategy(target_store)
        unregister_projection_attempt_lifecycle_policy(target_store)
        engine.dispose()
        _unregister_document_delete_target(target_store)


def test_manifest_comes_from_all_chunk_heads_and_authority_must_be_complete(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    repository = DocumentDeletionRepository(engine)
    with Session(engine) as session:
        session.add(
            ChunkHead(
                id="chunk-1-historical",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                document_id="doc-1",
                chunk_index=0,
                chunk_role="parent",
                document_revision=3,
                content_revision=1,
                source_content="historical source",
                content="historical content",
                content_hash="historical-hash",
                enabled=False,
                desired_index_revision=3,
                indexed_revision=3,
                index_status="succeeded",
            )
        )
        session.commit()
    first = repository.request_delete(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        expected_generation=0,
        idempotency_key="manifest-1",
        actor=_audit(),
        reason="cleanup",
        origin="operator",
    )
    with Session(engine) as session:
        manifest = repository.compute_chunk_manifest(session, "tenant-1", "dataset-1", "doc-1")
    assert first.chunk_manifest_hash == manifest.hash
    assert manifest.chunk_ids == (
        "chunk-1-0",
        "chunk-1-1",
        "chunk-1-historical",
    )
    assert first.chunk_manifest_count == 3

    with Session(engine) as session:
        document = session.get(Document, "doc-2")
        document.chunk_count = 3
        session.commit()
    with pytest.raises(ContentConflict, match="chunk authority incomplete"):
        repository.request_delete(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            document_id="doc-2",
            expected_generation=0,
            idempotency_key="manifest-2",
            actor=_audit(request="request-2"),
            reason="cleanup",
            origin="operator",
        )
    with Session(engine) as session:
        document = session.get(Document, "doc-2")
        assert document.mutation_generation == 0
        assert document.retrieval_enabled is True
        assert (
            session.scalar(
                select(func.count())
                .select_from(DocumentDeleteOperation)
                .where(DocumentDeleteOperation.requested_document_id == "doc-2")
            )
            == 0
        )
    engine.dispose()


def test_idempotency_replays_same_operation_and_conflicts_on_changed_body(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    repository = DocumentDeletionRepository(engine)
    kwargs = dict(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        expected_generation=0,
        idempotency_key="stable-key",
        actor=_audit(),
        reason="cleanup",
        origin="operator",
    )
    first = repository.request_delete(**kwargs)
    replay = repository.request_delete(**kwargs)
    assert replay.id == first.id
    assert replay.delete_generation == first.delete_generation

    with pytest.raises(ContentConflict, match="idempotency key"):
        repository.request_delete(**{**kwargs, "reason": "different body"})
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(DocumentDeleteBatch)) == 1
        assert session.scalar(select(func.count()).select_from(DocumentDeleteOperation)) == 1
    engine.dispose()


def test_source_suppression_creates_missing_scope_safe_tombstone(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with Session(engine) as session:
        state = session.scalar(
            select(SourceDocumentState).where(SourceDocumentState.doc_id == "doc-1")
        )
        session.delete(state)
        document = session.get(Document, "doc-1")
        document.external_id = "external-doc-1"
        document.source_uri = "file:///knowledge/doc-1.pdf"
        session.commit()

    result = DocumentDeletionRepository(engine).request_delete(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        expected_generation=0,
        idempotency_key="source-tombstone",
        actor=_audit(),
        reason="operator cleanup",
        origin="operator",
    )
    with Session(engine) as session:
        state = session.scalar(
            select(SourceDocumentState).where(
                SourceDocumentState.source_id == "source-1",
                SourceDocumentState.doc_id == "doc-1",
            )
        )
        assert state is not None
        assert state.external_id == "external-doc-1"
        assert state.source_uri == "file:///knowledge/doc-1.pdf"
        assert state.state == "operator_suppressed"
        assert state.document_generation == 1
        assert state.delete_operation_id == result.id
    engine.dispose()


def test_source_suppression_missing_identity_fails_closed(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with Session(engine) as session:
        state = session.scalar(
            select(SourceDocumentState).where(SourceDocumentState.doc_id == "doc-1")
        )
        session.delete(state)
        document = session.get(Document, "doc-1")
        document.external_id = None
        document.source_uri = None
        session.commit()

    with pytest.raises(ContentConflict, match="source identity"):
        DocumentDeletionRepository(engine).request_delete(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            document_id="doc-1",
            expected_generation=0,
            idempotency_key="source-identity-missing",
            actor=_audit(),
            reason="operator cleanup",
            origin="operator",
        )
    with Session(engine) as session:
        document = session.get(Document, "doc-1")
        assert document.mutation_generation == 0
        assert document.lifecycle_state == "active"
        assert document.retrieval_enabled is True
        assert session.scalar(select(func.count()).select_from(DocumentDeleteOperation)) == 0
    engine.dispose()


def test_supersedes_old_projection_writers_by_generation_and_closes_idle_attempts(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    with Session(engine) as session:
        attempts = [
            DocumentIngestAttempt(
                id="attempt-idle",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                document_id="doc-1",
                attempt_no=1,
                input_revision=4,
                state="running",
                document_generation=0,
            ),
            DocumentIngestAttempt(
                id="attempt-busy",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                document_id="doc-1",
                attempt_no=2,
                input_revision=4,
                state="running",
                document_generation=0,
            ),
            DocumentIngestAttempt(
                id="attempt-current",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                document_id="doc-1",
                attempt_no=3,
                input_revision=4,
                state="running",
                document_generation=1,
            ),
        ]
        session.add_all(attempts)
        for op_id, attempt_id, operation, status, generation in (
            ("writer-upsert", "attempt-idle", "upsert", "pending", 0),
            ("writer-reconcile", "attempt-idle", "reconcile", "claimed", 0),
            ("writer-busy", "attempt-busy", "reconcile", "retry", 0),
            ("busy-delete", "attempt-busy", "delete_document", "pending", 0),
            ("writer-current", "attempt-current", "upsert", "pending", 1),
        ):
            session.add(
                IndexOperation(
                    id=op_id,
                    tenant_id="tenant-1",
                    dataset_id="dataset-1",
                    document_id="doc-1",
                    attempt_id=attempt_id,
                    target_store="milvus_chunks",
                    operation=operation,
                    dedup_key=op_id,
                    target_revision=4,
                    document_generation=generation,
                    status=status,
                    claimed_by="worker-a" if status == "claimed" else "",
                )
            )
        session.commit()

    DocumentDeletionRepository(engine).request_delete(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        expected_generation=0,
        idempotency_key="supersede-generations",
        actor=_audit(),
        reason="cleanup",
        origin="operator",
    )
    with Session(engine) as session:
        statuses = {
            row.id: row.status
            for row in session.scalars(
                select(IndexOperation).where(
                    IndexOperation.id.in_(
                        [
                            "writer-upsert",
                            "writer-reconcile",
                            "writer-busy",
                            "busy-delete",
                            "writer-current",
                        ]
                    )
                )
            )
        }
        idle = session.get(DocumentIngestAttempt, "attempt-idle")
        busy = session.get(DocumentIngestAttempt, "attempt-busy")
        current = session.get(DocumentIngestAttempt, "attempt-current")
    assert statuses == {
        "writer-upsert": "superseded",
        "writer-reconcile": "superseded",
        "writer-busy": "superseded",
        "busy-delete": "pending",
        "writer-current": "pending",
    }
    assert idle.state == "superseded"
    assert idle.finished_at is not None
    assert busy.state == "running"
    assert busy.finished_at is None
    assert current.state == "running"
    engine.dispose()


def test_supersedes_old_active_upserts_while_preserving_non_writer_work(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with Session(engine) as session:
        attempt = DocumentIngestAttempt(
            id="attempt-old",
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            document_id="doc-1",
            attempt_no=1,
            input_revision=4,
            state="running",
        )
        session.add(attempt)
        for status, operation, suffix in (
            ("pending", "upsert", "pending"),
            ("retry", "upsert", "retry"),
            ("claimed", "upsert", "claimed"),
            ("pending", "delete", "delete"),
        ):
            session.add(
                IndexOperation(
                    id=f"op-{suffix}",
                    tenant_id="tenant-1",
                    dataset_id="dataset-1",
                    document_id="doc-1",
                    attempt_id="attempt-old",
                    target_store="milvus_chunks",
                    operation=operation,
                    dedup_key=f"old-{suffix}",
                    target_revision=4,
                    status=status,
                )
            )
        session.commit()

    DocumentDeletionRepository(engine).request_delete(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        expected_generation=0,
        idempotency_key="supersede-key",
        actor=_audit(),
        reason="cleanup",
        origin="operator",
    )
    with Session(engine) as session:
        statuses = {
            item.id: item.status
            for item in session.scalars(
                select(IndexOperation).where(IndexOperation.id.like("op-%"))
            )
        }
    assert statuses == {
        "op-pending": "superseded",
        "op-retry": "superseded",
        "op-claimed": "superseded",
        "op-delete": "pending",
    }
    engine.dispose()


def test_enqueue_and_audit_failures_roll_back_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for failure_method in ("_add_index_operation", "_add_audit_event"):
        engine = _engine(tmp_path / failure_method)
        repository = DocumentDeletionRepository(engine)

        def fail(*_args, **_kwargs):
            raise RuntimeError(f"injected {failure_method}")

        monkeypatch.setattr(repository, failure_method, fail)
        with pytest.raises(RuntimeError, match="injected"):
            repository.request_delete(
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                document_id="doc-1",
                expected_generation=0,
                idempotency_key=f"rollback-{failure_method}",
                actor=_audit(),
                reason="cleanup",
                origin="operator",
            )
        with Session(engine) as session:
            document = session.get(Document, "doc-1")
            dataset = session.get(Dataset, "dataset-1")
            source_state = session.scalar(
                select(SourceDocumentState).where(SourceDocumentState.doc_id == "doc-1")
            )
            assert document.mutation_generation == 0
            assert document.lifecycle_state == "active"
            assert document.retrieval_enabled is True
            assert document.active_delete_operation_id is None
            assert dataset.serving_generation == 0
            assert source_state.state == "active"
            assert session.scalar(select(func.count()).select_from(DocumentDeleteBatch)) == 0
            assert session.scalar(select(func.count()).select_from(DocumentDeleteOperation)) == 0
            assert session.scalar(select(func.count()).select_from(DocumentIngestAttempt)) == 0
            assert session.scalar(select(func.count()).select_from(IndexOperation)) == 0
            assert session.scalar(select(func.count()).select_from(KnowledgeAuditEvent)) == 0
        engine.dispose()
        monkeypatch.undo()


def test_concurrent_requests_only_advance_one_generation(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    barrier = Barrier(2)
    repository = DocumentDeletionRepository(engine)
    original = repository._before_document_cas

    def synchronize(*args, **kwargs):
        barrier.wait(timeout=10)
        return original(*args, **kwargs)

    repository._before_document_cas = synchronize

    def request(key: str):
        try:
            result = repository.request_delete(
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                document_id="doc-1",
                expected_generation=0,
                idempotency_key=key,
                actor=_audit(request=key),
                reason="cleanup",
                origin="operator",
            )
            return ("ok", result.id)
        except ContentConflict as exc:
            return ("conflict", str(exc))

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(request, ("concurrent-a", "concurrent-b")))
    assert sorted(item[0] for item in outcomes) == ["conflict", "ok"]
    with Session(engine) as session:
        document = session.get(Document, "doc-1")
        assert document.mutation_generation == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(DocumentDeleteOperation)
                .where(DocumentDeleteOperation.status == "queued")
            )
            == 1
        )
    engine.dispose()


def test_concurrent_same_idempotency_key_replays_one_operation(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    barrier = Barrier(2)
    repository = DocumentDeletionRepository(engine)
    original = repository._before_document_cas

    def synchronize(*args, **kwargs):
        barrier.wait(timeout=10)
        return original(*args, **kwargs)

    repository._before_document_cas = synchronize

    def request(_index: int):
        return repository.request_delete(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            document_id="doc-1",
            expected_generation=0,
            idempotency_key="concurrent-same-key",
            actor=_audit(request="same-request"),
            reason="cleanup",
            origin="operator",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(request, range(2)))
    assert outcomes[0].id == outcomes[1].id
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(DocumentDeleteBatch)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(DocumentDeleteOperation)
                .where(DocumentDeleteOperation.status == "queued")
            )
            == 1
        )
        assert session.get(Document, "doc-1").mutation_generation == 1
    engine.dispose()


def test_request_batch_preserves_deduped_order_and_persists_rejections(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    repository = DocumentDeletionRepository(engine)
    result = repository.request_batch(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        items=[
            DeleteBatchItemRequest("missing", 0),
            DeleteBatchItemRequest("doc-2", 9),
            DeleteBatchItemRequest("doc-1", 0),
            DeleteBatchItemRequest("doc-1", 0),
        ],
        idempotency_key="batch-1",
        actor=_audit(),
        reason="batch cleanup",
        origin="operator",
    )
    assert [item.requested_document_id for item in result.items] == ["missing", "doc-2", "doc-1"]
    assert [(item.status, item.result_code) for item in result.items] == [
        ("rejected", "not_found"),
        ("rejected", "document_generation_conflict"),
        ("queued", ""),
    ]
    assert result.requested_count == 3
    assert result.accepted_count == 1
    assert result.rejected_count == 2
    assert result.status == "running"

    replay = repository.request_batch(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        items=[
            DeleteBatchItemRequest("missing", 0),
            DeleteBatchItemRequest("doc-2", 9),
            DeleteBatchItemRequest("doc-1", 0),
        ],
        idempotency_key="batch-1",
        actor=_audit(),
        reason="batch cleanup",
        origin="operator",
    )
    assert replay.id == result.id
    assert [item.id for item in replay.items] == [item.id for item in result.items]
    assert repository.get_batch("tenant-1", "dataset-1", result.id).id == result.id
    accepted = next(item for item in result.items if item.status == "queued")
    assert repository.get_operation("tenant-1", "dataset-1", accepted.id).id == accepted.id
    engine.dispose()


def test_batch_limit_and_single_not_found(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    repository = DocumentDeletionRepository(engine)
    with pytest.raises(ValueError, match="at most 100"):
        repository.request_batch(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            items=[DeleteBatchItemRequest(f"doc-{index}", 0) for index in range(101)],
            idempotency_key="too-many",
            actor=_audit(),
            reason="cleanup",
            origin="operator",
        )
    with pytest.raises(ContentNotFound):
        repository.request_delete(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            document_id="missing",
            expected_generation=0,
            idempotency_key="missing-single",
            actor=_audit(),
            reason="cleanup",
            origin="operator",
        )
    engine.dispose()
