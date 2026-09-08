from __future__ import annotations

import base64
import hashlib
import json
import threading
from types import SimpleNamespace

import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session

from core.chunk_catalog import ChunkCatalog
from core.index_operations import IndexOperationQueue
from core.ingest_ledger import IngestLedger
from models.orm import Dataset, Document, DocumentIngestAttempt, IndexOperation, Tenant
from models.schemas import Chunk
import scripts.reconcile_chunk_authority as reconcile_module
from scripts.reconcile_chunk_authority import reconcile_chunk_authority as _reconcile_impl

ROLLOUT_SECRET = "YXdPepaKHpWMOu6ypgNSxKNac00g6vMXcEIGc0itofU"
OTHER_SECRET = "IGRAaKqyz9em254TolWmaGXjVEuW-1oiKlJVeFx_8vU"
BASE_TIME = datetime(2026, 8, 24, 8, 0, 0)


def _reconcile(catalog: ChunkCatalog, milvus: object, queue: object, **kwargs):
    return _reconcile_impl(catalog, milvus, queue, report_secret=ROLLOUT_SECRET, **kwargs)


class ReadOnlyMilvus:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = list(chunks)
        self.queries: list[tuple[str, str]] = []

    def query_chunks_by_doc(self, doc_id: str, tenant_id: str = "") -> list[Chunk]:
        self.queries.append((doc_id, tenant_id))
        return [
            chunk
            for chunk in self.chunks
            if chunk.doc_id == doc_id and (not tenant_id or chunk.tenant_id == tenant_id)
        ]

    def upsert_chunks(self, *_args, **_kwargs):
        raise AssertionError("reconcile must not mutate Milvus")

    def delete_by_ids(self, *_args, **_kwargs):
        raise AssertionError("reconcile must not mutate Milvus")


def _chunk(
    chunk_id: str,
    *,
    doc_id: str = "doc-1",
    text: str,
    document_revision: int = 4,
    content_revision: int = 0,
    metadata: dict[str, object] | None = None,
    parent_chunk_id: str | None = None,
) -> Chunk:
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    return Chunk(
        chunk_id=chunk_id,
        doc_id=doc_id,
        text=text,
        text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        created_at=now,
        updated_at=now,
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_revision=document_revision,
        content_revision=content_revision,
        parent_chunk_id=parent_chunk_id,
        metadata=dict(metadata or {}),
    )


def _state(tmp_path: Path, *, with_attempt: bool = True):
    from core import catalog_schema

    url = f"sqlite:///{(tmp_path / 'catalog.db').as_posix()}"
    catalog_schema.upgrade_catalog(url)
    engine = create_engine(url)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-1", name="Tenant"))
        session.add(Dataset(id="dataset-1", tenant_id="tenant-1", name="Dataset"))
        session.add(
            Document(
                id="doc-1",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name="Document",
                content_revision=4,
                desired_index_revision=4,
                created_at=BASE_TIME,
                updated_at=BASE_TIME,
            )
        )
        session.commit()
    attempt = None
    if with_attempt:
        attempt = IngestLedger(engine).start_attempt(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            document_id="doc-1",
            input_revision=4,
        )
    return engine, attempt


def _head(
    catalog: ChunkCatalog,
    chunk_id: str,
    *,
    text: str,
    role: str = "flat",
    enabled: bool = True,
    content_revision: int = 0,
    index: int,
    parent_chunk_id: str | None = None,
    context_header: str = "",
    metadata: dict[str, object] | None = None,
    document_id: str = "doc-1",
    document_revision: int = 4,
) -> None:
    catalog.upsert_head(
        chunk_id=chunk_id,
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id=document_id,
        parent_chunk_id=parent_chunk_id,
        chunk_index=index,
        chunk_role=role,
        document_revision=document_revision,
        source_content=text,
        content=text,
        enabled=enabled,
        metadata=metadata or {"chunk_index": index, "marker": chunk_id},
    )
    if context_header:
        assert catalog.update_context_header(
            chunk_id, input_revision=content_revision, context_header=context_header
        )
    for _ in range(content_revision):
        current = catalog.get_head(chunk_id)
        catalog.edit_chunk(
            chunk_id,
            expected_revision=current.content_revision,
            content=text,
            editor_id="test",
            edit_source="test",
        )


def test_reconcile_report_only_finds_missing_stale_hash_and_orphaned(
    tmp_path: Path,
) -> None:
    engine, _ = _state(tmp_path)
    catalog = ChunkCatalog(engine)
    _head(catalog, "missing", text="missing private text", index=0)
    _head(catalog, "stale-revision", text="revision private text", content_revision=2, index=1)
    _head(catalog, "stale-hash", text="authoritative private text", index=2)
    _head(catalog, "ok", text="matching private text", index=3)
    _head(catalog, "parent", text="parent private text", role="parent", index=4)
    _head(catalog, "disabled", text="disabled private text", enabled=False, index=5)

    milvus = ReadOnlyMilvus(
        [
            _chunk("stale-revision", text="revision private text", content_revision=1),
            _chunk("stale-hash", text="projection differs"),
            _chunk("ok", text="matching private text"),
            _chunk("orphan", text="orphan private text"),
            _chunk("parent", text="parent private text", metadata={"is_parent": True}),
            _chunk("disabled", text="disabled private text"),
        ]
    )
    queue = IndexOperationQueue(engine)

    report = _reconcile(
        catalog,
        milvus,
        queue,
        tenant_id="tenant-1",
        dataset_id="dataset-1",
    )

    assert report.mode == "report-only"
    assert report.missing_ids == ("missing",)
    assert report.stale_ids == ("stale-hash", "stale-revision")
    assert report.orphaned_ids == ("disabled", "orphan", "parent")
    assert report.stale_reasons == {
        "stale-hash": ("hash",),
        "stale-revision": ("content_revision",),
    }
    assert report.enqueued == 0
    assert queue.count_operations() == 0

    summary = json.dumps(report.to_summary(ROLLOUT_SECRET), ensure_ascii=False, sort_keys=True)
    for source_text in (
        "missing private text",
        "revision private text",
        "authoritative private text",
        "matching private text",
        "parent private text",
        "disabled private text",
        "orphan private text",
        "projection differs",
    ):
        assert source_text not in summary
    engine.dispose()


def test_repair_only_enqueues_deterministic_index_operation_and_is_idempotent(
    tmp_path: Path,
) -> None:
    engine, attempt = _state(tmp_path)
    catalog = ChunkCatalog(engine)
    _head(catalog, "missing", text="source must stay private", index=0)
    milvus = ReadOnlyMilvus([])
    queue = IndexOperationQueue(engine)

    preview = _reconcile(
        catalog,
        milvus,
        queue,
        tenant_id="tenant-1",
        dataset_id="dataset-1",
    )
    repaired = _reconcile(
        catalog,
        milvus,
        queue,
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        repair=True,
    )
    repeated = _reconcile(
        catalog,
        milvus,
        queue,
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        repair=True,
    )

    assert preview.manifest_hash == repaired.manifest_hash == repeated.manifest_hash
    assert repaired.mode == "repair-enqueue"
    assert repaired.enqueued == 1
    assert repeated.enqueued == 0
    operations = queue.list_operations()
    assert len(operations) == 1
    operation = operations[0]
    assert operation.attempt_id == attempt.id
    assert operation.operation == "reconcile"
    assert operation.target_store == "milvus_chunks"
    assert operation.target_revision == 4
    assert operation.payload == {
        "missing_ids": ["missing"],
        "orphaned_ids": [],
        "stale_ids": [],
        "stale_reasons": {},
        "manifest_hash": repaired.manifest_hash,
    }
    assert "source must stay private" not in json.dumps(operation.payload)
    engine.dispose()


def test_reconcile_detects_parent_role_and_context_drift(tmp_path: Path) -> None:
    engine, _ = _state(tmp_path)
    catalog = ChunkCatalog(engine)
    _head(
        catalog,
        "hierarchy-secret",
        text="same",
        role="child",
        index=0,
        parent_chunk_id="parent-a",
        context_header="authority context",
        metadata={"chunk_index": 0, "context": "authority context"},
    )
    milvus = ReadOnlyMilvus(
        [
            _chunk(
                "hierarchy-secret",
                text="same",
                parent_chunk_id="parent-b",
                metadata={
                    "chunk_index": 0,
                    "chunk_role": "flat",
                    "context": "projection context",
                },
            )
        ]
    )

    report = _reconcile(
        catalog,
        milvus,
        IndexOperationQueue(engine),
        tenant_id="tenant-1",
        dataset_id="dataset-1",
    )

    assert report.stale_reasons == {
        "hierarchy-secret": ("parent_chunk_id", "chunk_role", "context_header")
    }
    assert "hierarchy-secret" not in json.dumps(report.to_summary(ROLLOUT_SECRET), sort_keys=True)
    engine.dispose()


def test_repair_creates_dedicated_attempt_for_legacy_document(tmp_path: Path) -> None:
    engine, _ = _state(tmp_path, with_attempt=False)
    catalog = ChunkCatalog(engine)
    _head(catalog, "legacy-secret", text="missing", index=0)
    queue = IndexOperationQueue(engine)

    report = _reconcile(
        catalog,
        ReadOnlyMilvus([]),
        queue,
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        repair=True,
    )

    assert report.enqueued == 1
    assert report.repair_blocked_document_ids == ()
    operations = queue.list_operations()
    assert len(operations) == 1
    with Session(engine) as session:
        attempt = session.get(DocumentIngestAttempt, operations[0].attempt_id)
        document = session.get(Document, "doc-1")
    assert attempt is not None
    assert attempt.worker_id == "knowledgeops-reconcile"
    assert document.current_attempt_id == attempt.id
    engine.dispose()


def test_terminal_repair_operation_does_not_suppress_a_new_generation(
    tmp_path: Path,
) -> None:
    engine, _ = _state(tmp_path)
    catalog = ChunkCatalog(engine)
    _head(catalog, "terminal-secret", text="missing", index=0)
    queue = IndexOperationQueue(engine)
    milvus = ReadOnlyMilvus([])

    first = _reconcile(
        catalog,
        milvus,
        queue,
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        repair=True,
    )
    with Session(engine) as session:
        operation = session.scalar(select(IndexOperation))
        operation.status = "succeeded"
        session.commit()
    second = _reconcile(
        catalog,
        milvus,
        queue,
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        repair=True,
    )

    assert first.enqueued == 1
    assert second.enqueued == 1
    operations = queue.list_operations()
    assert len(operations) == 2
    assert operations[0].dedup_key != operations[1].dedup_key
    assert operations[1].dedup_key.endswith(":generation:2")
    engine.dispose()


def test_reconcile_cli_pseudonymizes_summary_and_fail_on_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("RAG4C_ROLLOUT_REPORT_SECRET", ROLLOUT_SECRET)
    engine, _ = _state(tmp_path)
    catalog = ChunkCatalog(engine)
    _head(catalog, "cli-reconcile-secret", text="missing", index=0)
    settings = SimpleNamespace(
        catalog=SimpleNamespace(schema_mode="verify", chunk_authority_mode="shadow")
    )
    monkeypatch.setattr(
        reconcile_module,
        "_runtime_dependencies",
        lambda: (engine, ReadOnlyMilvus([]), settings),
    )

    exit_code = reconcile_module.main(
        [
            "--tenant-id",
            "tenant-1",
            "--dataset-id",
            "dataset-1",
            "--fail-on-drift",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "cli-reconcile-secret" not in output
    assert "doc-1" not in output

    settings.catalog.chunk_authority_mode = "active"
    exit_code = reconcile_module.main(
        [
            "--tenant-id",
            "tenant-1",
            "--dataset-id",
            "dataset-1",
            "--repair",
        ]
    )
    assert exit_code == 2
    assert "shadow" in capsys.readouterr().out
    engine.dispose()


def test_reconcile_sqlite_snapshot_prevents_aba_document_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _ = _state(tmp_path, with_attempt=False)
    with Session(engine) as session:
        session.add(
            Document(
                id="doc-2",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name="Document 2",
                content_revision=4,
                desired_index_revision=4,
                created_at=BASE_TIME + timedelta(minutes=2),
                updated_at=BASE_TIME + timedelta(minutes=2),
            )
        )
        session.commit()
    catalog = ChunkCatalog(engine)
    queue = IndexOperationQueue(engine)
    first = _reconcile(
        catalog,
        ReadOnlyMilvus([]),
        queue,
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        batch_size=1,
    )

    original_capture = reconcile_module.capture_document_snapshot
    original_postflight = reconcile_module._assert_scan_state_current
    writer_attempting = threading.Event()
    moved = threading.Event()
    allow_revert = threading.Event()
    reverted = threading.Event()
    writer_started = False
    moved_during_snapshot: list[bool] = []

    def writer() -> None:
        writer_attempting.set()
        with Session(engine) as session:
            session.execute(
                update(Document)
                .where(Document.id == "doc-2")
                .values(
                    created_at=BASE_TIME + timedelta(days=1),
                    updated_at=BASE_TIME + timedelta(minutes=2),
                )
            )
            session.commit()
        moved.set()
        assert allow_revert.wait(5)
        with Session(engine) as session:
            session.execute(
                update(Document)
                .where(Document.id == "doc-2")
                .values(
                    created_at=BASE_TIME + timedelta(minutes=2),
                    updated_at=BASE_TIME + timedelta(minutes=2),
                )
            )
            session.commit()
        reverted.set()

    writer_thread: threading.Thread | None = None

    def capture_with_aba(session: Session, **kwargs):
        nonlocal writer_started, writer_thread
        snapshot = original_capture(session, **kwargs)
        if not writer_started:
            writer_started = True
            writer_thread = threading.Thread(target=writer, daemon=True)
            writer_thread.start()
            assert writer_attempting.wait(2)
            moved_during_snapshot.append(moved.wait(0.25))
        return snapshot

    def postflight_after_revert(*args, **kwargs):
        allow_revert.set()
        assert reverted.wait(5)
        return original_postflight(*args, **kwargs)

    monkeypatch.setattr(reconcile_module, "capture_document_snapshot", capture_with_aba)
    monkeypatch.setattr(reconcile_module, "_assert_scan_state_current", postflight_after_revert)
    milvus = ReadOnlyMilvus([])

    report = _reconcile(
        catalog,
        milvus,
        queue,
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        batch_size=1,
        cursor=first.next_cursor,
    )

    assert moved_during_snapshot == [False]
    assert report.documents_scanned == 1
    assert milvus.queries == [("doc-2", "tenant-1")]
    assert report.complete is True
    assert writer_thread is not None
    writer_thread.join(timeout=2)
    assert not writer_thread.is_alive()
    engine.dispose()


def test_reconcile_snapshot_rejects_scope_membership_changes(tmp_path: Path) -> None:
    engine, _ = _state(tmp_path, with_attempt=False)
    with Session(engine) as session:
        session.add(
            Document(
                id="doc-2",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name="Document 2",
                content_revision=4,
                desired_index_revision=4,
                created_at=BASE_TIME + timedelta(minutes=2),
                updated_at=BASE_TIME + timedelta(minutes=2),
            )
        )
        session.commit()
    milvus = ReadOnlyMilvus([])
    catalog = ChunkCatalog(engine)
    queue = IndexOperationQueue(engine)

    first = _reconcile(
        catalog,
        milvus,
        queue,
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        batch_size=1,
    )
    with Session(engine) as session:
        session.delete(session.get(Document, "doc-1"))
        session.add(
            Document(
                id="doc-15",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name="Document 15",
                content_revision=4,
                desired_index_revision=4,
                created_at=BASE_TIME + timedelta(minutes=1),
                updated_at=BASE_TIME + timedelta(minutes=1),
            )
        )
        session.commit()

    with pytest.raises(ValueError, match="snapshot assumptions"):
        _reconcile(
            catalog,
            milvus,
            queue,
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            batch_size=1,
            cursor=first.next_cursor,
        )
    engine.dispose()


@pytest.mark.parametrize("mutation", ["delete", "move-created-at", "desired-index-revision"])
def test_reconcile_snapshot_rejects_unprocessed_member_mutation(
    tmp_path: Path, mutation: str
) -> None:
    engine, _ = _state(tmp_path, with_attempt=False)
    with Session(engine) as session:
        session.add(
            Document(
                id="doc-2",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name="Document 2",
                content_revision=4,
                desired_index_revision=4,
                created_at=BASE_TIME + timedelta(minutes=2),
                updated_at=BASE_TIME + timedelta(minutes=2),
            )
        )
        session.commit()
    catalog = ChunkCatalog(engine)
    milvus = ReadOnlyMilvus([])
    queue = IndexOperationQueue(engine)
    first = _reconcile(
        catalog,
        milvus,
        queue,
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        batch_size=1,
    )

    with Session(engine) as session:
        document = session.get(Document, "doc-2")
        if mutation == "delete":
            session.delete(document)
        elif mutation == "move-created-at":
            document.created_at = BASE_TIME + timedelta(days=1)
        else:
            document.desired_index_revision += 1
        session.commit()

    with pytest.raises(ValueError, match="snapshot assumptions"):
        _reconcile(
            catalog,
            milvus,
            queue,
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            batch_size=1,
            cursor=first.next_cursor,
        )
    engine.dispose()


@pytest.mark.parametrize("mutation", ["update", "insert"])
def test_reconcile_rechecks_snapshot_after_batch_processing(
    tmp_path: Path, mutation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _ = _state(tmp_path, with_attempt=False)

    writer_attempting = threading.Event()
    writer_finished = threading.Event()
    writer_thread: threading.Thread | None = None

    def writer() -> None:
        writer_attempting.set()
        with Session(engine) as session:
            if mutation == "update":
                document = session.get(Document, "doc-1")
                document.desired_index_revision += 1
            else:
                session.add(
                    Document(
                        id="doc-2",
                        tenant_id="tenant-1",
                        dataset_id="dataset-1",
                        name="Document 2",
                        content_revision=1,
                        desired_index_revision=1,
                        created_at=BASE_TIME + timedelta(minutes=2),
                        updated_at=BASE_TIME + timedelta(minutes=2),
                    )
                )
            session.commit()
        writer_finished.set()

    class ConcurrentMutationMilvus(ReadOnlyMilvus):
        def query_chunks_by_doc(self, doc_id: str, tenant_id: str = "") -> list[Chunk]:
            nonlocal writer_thread
            if not self.queries:
                writer_thread = threading.Thread(target=writer, daemon=True)
                writer_thread.start()
                assert writer_attempting.wait(2)
                assert not writer_finished.wait(0.2)
            return super().query_chunks_by_doc(doc_id, tenant_id)

    original_postflight = reconcile_module._assert_scan_state_current

    def postflight_after_writer(*args, **kwargs):
        assert writer_finished.wait(5)
        return original_postflight(*args, **kwargs)

    monkeypatch.setattr(reconcile_module, "_assert_scan_state_current", postflight_after_writer)
    with pytest.raises(ValueError, match="snapshot assumptions"):
        _reconcile(
            ChunkCatalog(engine),
            ConcurrentMutationMilvus([]),
            IndexOperationQueue(engine),
            tenant_id="tenant-1",
            dataset_id="dataset-1",
        )
    assert writer_thread is not None
    writer_thread.join(timeout=2)
    assert not writer_thread.is_alive()
    engine.dispose()


def test_reconcile_cursor_rejects_wrong_secret(tmp_path: Path) -> None:
    engine, _ = _state(tmp_path, with_attempt=False)
    with Session(engine) as session:
        session.add(
            Document(
                id="doc-2",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name="Document 2",
                content_revision=4,
                desired_index_revision=4,
            )
        )
        session.commit()
    catalog = ChunkCatalog(engine)
    milvus = ReadOnlyMilvus([])
    queue = IndexOperationQueue(engine)
    first = _reconcile(
        catalog,
        milvus,
        queue,
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        batch_size=1,
    )

    with pytest.raises(ValueError, match="authenticated"):
        _reconcile_impl(
            catalog,
            milvus,
            queue,
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            batch_size=1,
            cursor=first.next_cursor,
            report_secret=OTHER_SECRET,
        )
    engine.dispose()


@pytest.mark.parametrize("tamper", ["illegal-suffix", "extra-field"])
def test_reconcile_cursor_rejects_noncanonical_or_extended_envelopes(
    tmp_path: Path, tamper: str
) -> None:
    engine, _ = _state(tmp_path, with_attempt=False)
    with Session(engine) as session:
        session.add(
            Document(
                id="doc-2",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name="Document 2",
                content_revision=1,
                desired_index_revision=1,
                created_at=BASE_TIME + timedelta(minutes=2),
                updated_at=BASE_TIME + timedelta(minutes=2),
            )
        )
        session.commit()
    first = _reconcile(
        ChunkCatalog(engine),
        ReadOnlyMilvus([]),
        IndexOperationQueue(engine),
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        batch_size=1,
    )
    cursor = first.next_cursor
    if tamper == "illegal-suffix":
        cursor += "!!!!"
    else:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        payload["unexpected"] = "must-fail-closed"
        cursor = (
            base64.urlsafe_b64encode(
                json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            )
            .decode("ascii")
            .rstrip("=")
        )

    with pytest.raises(ValueError, match="invalid|authenticated"):
        reconcile_module._decode_cursor(cursor, report_secret=ROLLOUT_SECRET)
    engine.dispose()


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("v", 5.0),
        ("snapshot_count", True),
        ("snapshot_fingerprint", "z" * 64),
        ("dataset_generation", "g" * 64),
        ("snapshot_started_at", "2026-08-25T12:00:00"),
        ("last_document_id", ""),
    ],
)
def test_reconcile_cursor_requires_exact_semantic_field_types(
    tmp_path: Path, field: str, invalid_value: object
) -> None:
    engine, _ = _state(tmp_path, with_attempt=False)
    with Session(engine) as session:
        session.add(
            Document(
                id="doc-2",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name="Document 2",
                content_revision=1,
                desired_index_revision=1,
                created_at=BASE_TIME + timedelta(minutes=2),
                updated_at=BASE_TIME + timedelta(minutes=2),
            )
        )
        session.commit()
    first = _reconcile(
        ChunkCatalog(engine),
        ReadOnlyMilvus([]),
        IndexOperationQueue(engine),
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        batch_size=1,
    )
    padded = first.next_cursor + "=" * (-len(first.next_cursor) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    payload[field] = invalid_value
    unsigned = {key: payload[key] for key in reconcile_module._CURSOR_FIELDS}
    payload["signature"] = reconcile_module._hmac_hex(
        ROLLOUT_SECRET,
        domain=f"{reconcile_module._DOMAIN}/cursor/signature",
        value=unsigned,
    )
    cursor = (
        base64.urlsafe_b64encode(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        .decode("ascii")
        .rstrip("=")
    )

    with pytest.raises(ValueError, match="invalid|authenticated"):
        reconcile_module._decode_cursor(cursor, report_secret=ROLLOUT_SECRET)
    engine.dispose()


def test_repair_rejects_resume_cursor(tmp_path: Path) -> None:
    engine, _ = _state(tmp_path, with_attempt=False)
    with Session(engine) as session:
        session.add(
            Document(
                id="doc-2",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name="Document 2",
                content_revision=1,
                desired_index_revision=1,
                created_at=BASE_TIME + timedelta(minutes=2),
                updated_at=BASE_TIME + timedelta(minutes=2),
            )
        )
        session.commit()
    catalog = ChunkCatalog(engine)
    queue = IndexOperationQueue(engine)
    first = _reconcile(
        catalog,
        ReadOnlyMilvus([]),
        queue,
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        batch_size=1,
    )

    with pytest.raises(ValueError, match="repair mode.*cursor"):
        _reconcile(
            catalog,
            ReadOnlyMilvus([]),
            queue,
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            cursor=first.next_cursor,
            repair=True,
        )
    engine.dispose()


def test_repair_scans_complete_snapshot_before_enqueueing_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _ = _state(tmp_path, with_attempt=False)
    with Session(engine) as session:
        session.add(
            Document(
                id="doc-2",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name="Document 2",
                content_revision=5,
                desired_index_revision=5,
                created_at=BASE_TIME + timedelta(minutes=2),
                updated_at=BASE_TIME + timedelta(minutes=2),
            )
        )
        session.commit()
    catalog = ChunkCatalog(engine)
    _head(catalog, "missing-1", text="first", index=0)
    _head(
        catalog,
        "missing-2",
        text="second",
        index=0,
        document_id="doc-2",
        document_revision=5,
    )
    milvus = ReadOnlyMilvus([])
    queue = IndexOperationQueue(engine)
    original_enqueue = reconcile_module._enqueue_repair_plans

    def enqueue_after_complete_scan(*args, **kwargs):
        assert [doc_id for doc_id, _tenant_id in milvus.queries] == ["doc-1", "doc-2"]
        return original_enqueue(*args, **kwargs)

    monkeypatch.setattr(reconcile_module, "_enqueue_repair_plans", enqueue_after_complete_scan)

    report = _reconcile(
        catalog,
        milvus,
        queue,
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        batch_size=1,
        repair=True,
    )

    assert report.documents_scanned == 2
    assert report.complete is True
    assert report.next_cursor == ""
    assert report.enqueued == 2
    assert len(queue.list_operations()) == 2
    with Session(engine) as session:
        assert session.get(Document, "doc-1").current_attempt_id
        assert session.get(Document, "doc-2").current_attempt_id
    engine.dispose()


def test_repair_stale_plan_rolls_back_all_attempts_and_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _ = _state(tmp_path, with_attempt=False)
    with Session(engine) as session:
        session.add(
            Document(
                id="doc-2",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name="Document 2",
                content_revision=5,
                desired_index_revision=5,
                created_at=BASE_TIME + timedelta(minutes=2),
                updated_at=BASE_TIME + timedelta(minutes=2),
            )
        )
        session.commit()
    catalog = ChunkCatalog(engine)
    _head(catalog, "missing-1", text="first", index=0)
    _head(
        catalog,
        "missing-2",
        text="second",
        index=0,
        document_id="doc-2",
        document_revision=5,
    )
    queue = IndexOperationQueue(engine)
    original_postflight = reconcile_module._assert_scan_state_current

    def mutate_after_snapshot(*args, **kwargs):
        original_postflight(*args, **kwargs)
        with Session(engine) as session:
            document = session.get(Document, "doc-2")
            document.content_revision = 6
            document.desired_index_revision = 6
            session.commit()

    monkeypatch.setattr(reconcile_module, "_assert_scan_state_current", mutate_after_snapshot)

    with pytest.raises(ValueError, match="repair plan.*stale"):
        _reconcile(
            catalog,
            ReadOnlyMilvus([]),
            queue,
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            repair=True,
        )

    assert queue.count_operations() == 0
    with Session(engine) as session:
        assert list(session.scalars(select(DocumentIngestAttempt))) == []
        assert session.get(Document, "doc-1").current_attempt_id is None
        assert session.get(Document, "doc-2").current_attempt_id is None
    engine.dispose()


def test_reconcile_cli_rejects_repair_cursor_before_runtime_loading(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("RAG4C_ROLLOUT_REPORT_SECRET", ROLLOUT_SECRET)
    monkeypatch.setattr(
        reconcile_module,
        "_runtime_dependencies",
        lambda: (_ for _ in ()).throw(AssertionError("runtime must not load")),
    )

    assert (
        reconcile_module.main(
            [
                "--tenant-id",
                "tenant-1",
                "--dataset-id",
                "dataset-1",
                "--repair",
                "--cursor",
                "opaque-sensitive-cursor",
            ]
        )
        == 2
    )
    assert "repair mode does not accept a resume cursor" in capsys.readouterr().out


def test_reconcile_cli_requires_rollout_report_secret(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("RAG4C_ROLLOUT_REPORT_SECRET", raising=False)
    monkeypatch.setattr(
        reconcile_module,
        "_runtime_dependencies",
        lambda: (_ for _ in ()).throw(AssertionError("runtime must not load")),
    )

    assert reconcile_module.main(["--tenant-id", "tenant-1", "--dataset-id", "dataset-1"]) == 2
    assert "RAG4C_ROLLOUT_REPORT_SECRET" in capsys.readouterr().out

    monkeypatch.setenv("RAG4C_ROLLOUT_REPORT_SECRET", "example-secret-change-me-123456789")
    assert reconcile_module.main(["--tenant-id", "tenant-1", "--dataset-id", "dataset-1"]) == 2
    assert "CSPRNG-generated Base64URL key" in capsys.readouterr().out


def test_reconcile_secret_rotation_dedupes_same_active_repair(tmp_path: Path) -> None:
    engine, _ = _state(tmp_path)
    catalog = ChunkCatalog(engine)
    _head(catalog, "rotation-secret", text="missing", index=0)
    queue = IndexOperationQueue(engine)
    milvus = ReadOnlyMilvus([])

    first = _reconcile_impl(
        catalog,
        milvus,
        queue,
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        report_secret=ROLLOUT_SECRET,
        repair=True,
    )
    rotated = _reconcile_impl(
        catalog,
        milvus,
        queue,
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        report_secret=OTHER_SECRET,
        repair=True,
    )

    assert first.manifest_hash == rotated.manifest_hash
    assert first.enqueued == 1
    assert rotated.enqueued == 0
    assert len(queue.list_operations()) == 1
    engine.dispose()


def test_reconcile_retained_summary_omits_operational_cursor(tmp_path: Path) -> None:
    engine, _ = _state(tmp_path, with_attempt=False)
    with Session(engine) as session:
        session.add(
            Document(
                id="doc-2",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name="Document 2",
                content_revision=4,
                desired_index_revision=4,
                created_at=BASE_TIME + timedelta(minutes=2),
                updated_at=BASE_TIME + timedelta(minutes=2),
            )
        )
        session.commit()
    report = _reconcile(
        ChunkCatalog(engine),
        ReadOnlyMilvus([]),
        IndexOperationQueue(engine),
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        batch_size=1,
    )

    summary = report.to_summary(ROLLOUT_SECRET)
    retained = json.dumps(summary, sort_keys=True)
    assert "next_cursor" not in summary
    assert report.next_cursor not in retained
    assert summary["resume_cursor_ref"]
    assert "manifest_hash" not in summary
    assert summary["manifest_ref"]
    assert summary["manifest_ref"] != report.manifest_hash
    assert report.manifest_hash not in retained
    assert summary["manifest_ref"] != report.to_summary(OTHER_SECRET)["manifest_ref"]
    assert "doc-1" not in retained
    engine.dispose()


@pytest.mark.parametrize(
    "weak_secret",
    [
        "short",
        "example-secret-change-me-123456789",
        "changeme" * 5,
        "A" * 43,
        "abcd" * 11,
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopo",
        "0123456789abcdef" * 3,
    ],
)
def test_reconcile_rejects_weak_rollout_secret(tmp_path: Path, weak_secret: str) -> None:
    engine, _ = _state(tmp_path, with_attempt=False)
    with pytest.raises(ValueError, match="CSPRNG-generated Base64URL key"):
        _reconcile_impl(
            ChunkCatalog(engine),
            ReadOnlyMilvus([]),
            IndexOperationQueue(engine),
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            report_secret=weak_secret,
        )
    engine.dispose()
