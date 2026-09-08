from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
from types import SimpleNamespace

import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, func, inspect, select, update
from sqlalchemy.orm import Session

from core.chunk_catalog import ChunkCatalog
from models.orm import ChunkHead, ChunkRevision, Dataset, Document, Tenant
from models.schemas import Chunk
import scripts.backfill_chunk_authority as backfill_module
import scripts.reconcile_chunk_authority as reconcile_module
from scripts.backfill_chunk_authority import backfill_chunk_authority as _backfill_impl

ROLLOUT_SECRET = "YXdPepaKHpWMOu6ypgNSxKNac00g6vMXcEIGc0itofU"
OTHER_SECRET = "IGRAaKqyz9em254TolWmaGXjVEuW-1oiKlJVeFx_8vU"
BASE_TIME = datetime(2026, 8, 24, 8, 0, 0)


def _backfill(catalog: ChunkCatalog, milvus: object, **kwargs):
    return _backfill_impl(catalog, milvus, report_secret=ROLLOUT_SECRET, **kwargs)


class FakeMilvus:
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


def _chunk(
    chunk_id: str,
    *,
    doc_id: str,
    tenant_id: str,
    dataset_id: str,
    text: str,
    document_revision: int,
    content_revision: int = 0,
    parent_chunk_id: str | None = None,
    metadata: dict[str, object] | None = None,
    text_hash: str | None = None,
) -> Chunk:
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    return Chunk(
        chunk_id=chunk_id,
        doc_id=doc_id,
        text=text,
        text_hash=text_hash or hashlib.sha256(text.encode("utf-8")).hexdigest(),
        created_at=now,
        updated_at=now,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        document_revision=document_revision,
        content_revision=content_revision,
        parent_chunk_id=parent_chunk_id,
        metadata=dict(metadata or {}),
    )


def _state(tmp_path: Path):
    from core import catalog_schema

    url = f"sqlite:///{(tmp_path / 'catalog.db').as_posix()}"
    catalog_schema.upgrade_catalog(url)
    engine = create_engine(url)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="tenant-a", name="Tenant A"),
                Tenant(id="tenant-b", name="Tenant B"),
                Dataset(id="dataset-a", tenant_id="tenant-a", name="Dataset A"),
                Dataset(id="dataset-b", tenant_id="tenant-b", name="Dataset B"),
                Document(
                    id="doc-a1",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    name="A1",
                    content_revision=7,
                    desired_index_revision=7,
                    created_at=BASE_TIME,
                    updated_at=BASE_TIME,
                ),
                Document(
                    id="doc-a2",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    name="A2",
                    content_revision=3,
                    desired_index_revision=3,
                    created_at=BASE_TIME + timedelta(minutes=2),
                    updated_at=BASE_TIME + timedelta(minutes=2),
                ),
                Document(
                    id="doc-b1",
                    tenant_id="tenant-b",
                    dataset_id="dataset-b",
                    name="B1",
                    content_revision=2,
                    desired_index_revision=2,
                    created_at=BASE_TIME,
                    updated_at=BASE_TIME,
                ),
            ]
        )
        session.commit()
    return engine


def test_backfill_defaults_to_dry_run_is_scoped_and_resumes_by_cursor(
    tmp_path: Path,
) -> None:
    engine = _state(tmp_path)
    catalog = ChunkCatalog(engine)
    milvus = FakeMilvus(
        [
            _chunk(
                "chunk-a1",
                doc_id="doc-a1",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                text="private source alpha",
                document_revision=7,
                metadata={"chunk_index": 0},
            ),
            _chunk(
                "chunk-a2",
                doc_id="doc-a2",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                text="private source beta",
                document_revision=3,
                metadata={"chunk_index": 0},
            ),
            _chunk(
                "chunk-b1",
                doc_id="doc-b1",
                tenant_id="tenant-b",
                dataset_id="dataset-b",
                text="other tenant secret",
                document_revision=2,
                metadata={"chunk_index": 0},
            ),
        ]
    )

    preview = _backfill(
        catalog,
        milvus,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        batch_size=1,
    )

    assert preview.mode == "dry-run"
    assert preview.documents_scanned == 1
    assert preview.chunks_scanned == 1
    assert preview.would_create == 1
    assert preview.created == 0
    assert preview.complete is False
    assert preview.next_cursor
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ChunkHead)) == 0

    first = _backfill(
        catalog,
        milvus,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        batch_size=1,
        apply=True,
    )
    second = _backfill(
        catalog,
        milvus,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        batch_size=1,
        cursor=first.next_cursor,
        apply=True,
    )

    assert first.created == second.created == 1
    assert second.complete is True
    with Session(engine) as session:
        heads = list(session.scalars(select(ChunkHead).order_by(ChunkHead.id)))
    assert [head.id for head in heads] == ["chunk-a1", "chunk-a2"]
    assert all(head.tenant_id == "tenant-a" for head in heads)
    assert ("doc-b1", "tenant-b") not in milvus.queries
    engine.dispose()


def test_backfill_preserves_roles_revisions_context_metadata_and_reports_hashes(
    tmp_path: Path,
) -> None:
    engine = _state(tmp_path)
    catalog = ChunkCatalog(engine)
    parent_text = "parent source must never appear in the report"
    child_text = "child source must never appear in the report"
    disabled_text = "disabled source must never appear in the report"
    chunks = [
        _chunk(
            "parent",
            doc_id="doc-a1",
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            text=parent_text,
            document_revision=7,
            content_revision=2,
            metadata={"chunk_index": 0, "is_parent": True, "kind": "section"},
        ),
        _chunk(
            "child",
            doc_id="doc-a1",
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            text=child_text,
            text_hash="projection-supplied-wrong-hash",
            document_revision=7,
            content_revision=2,
            parent_chunk_id="parent",
            metadata={
                "chunk_index": 1,
                "context": "chapter context",
                "page": 8,
            },
        ),
        _chunk(
            "disabled",
            doc_id="doc-a1",
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            text=disabled_text,
            document_revision=7,
            content_revision=1,
            metadata={"chunk_index": 2, "enabled": False, "reason": "operator"},
        ),
    ]
    milvus = FakeMilvus(chunks)

    preview_one = _backfill(
        catalog,
        milvus,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
    )
    preview_two = _backfill(
        catalog,
        milvus,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
    )
    applied = _backfill(
        catalog,
        milvus,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        apply=True,
    )

    assert preview_one.manifest_hash == preview_two.manifest_hash == applied.manifest_hash
    assert applied.hash_mismatch_ids == ("child",)
    assert applied.created == 2
    assert applied.skipped_unsafe == 1

    parent = catalog.get_head("parent")
    with pytest.raises(Exception, match="chunk does not exist"):
        catalog.get_head("child")
    unsafe = _backfill(
        catalog,
        milvus,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        apply=True,
        allow_unsafe_mismatches=True,
    )
    assert unsafe.created == 1
    child = catalog.get_head("child")
    disabled = catalog.get_head("disabled")
    assert parent.chunk_role == "parent"
    assert parent.content_revision == 2
    assert child.chunk_role == "child"
    assert child.parent_chunk_id == "parent"
    assert child.document_revision == 7
    assert child.content_revision == 2
    assert child.context_header == "chapter context"
    assert child.chunk_metadata == {
        "chunk_index": 1,
        "context": "chapter context",
        "page": 8,
    }
    assert child.content_hash == hashlib.sha256(child_text.encode("utf-8")).hexdigest()
    assert disabled.chunk_role == "flat"
    assert disabled.enabled is False
    assert disabled.content_revision == 1

    summary = json.dumps(applied.to_summary(ROLLOUT_SECRET), ensure_ascii=False, sort_keys=True)
    assert parent_text not in summary
    assert child_text not in summary
    assert disabled_text not in summary

    repeated = _backfill(
        catalog,
        milvus,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        apply=True,
    )
    assert repeated.created == 0
    assert repeated.skipped_existing == 2
    assert repeated.skipped_unsafe == 1
    engine.dispose()


def test_is_parent_without_referencing_child_is_flat_but_referenced_parent_is_parent(
    tmp_path: Path,
) -> None:
    engine = _state(tmp_path)
    catalog = ChunkCatalog(engine)
    milvus = FakeMilvus(
        [
            _chunk(
                "standalone-secret-name",
                doc_id="doc-a1",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                text="standalone",
                document_revision=7,
                metadata={"chunk_index": 0, "is_parent": True},
            ),
            _chunk(
                "real-parent-secret-name",
                doc_id="doc-a1",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                text="parent",
                document_revision=7,
                metadata={"chunk_index": 1, "is_parent": True},
            ),
            _chunk(
                "child-secret-name",
                doc_id="doc-a1",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                text="child",
                document_revision=7,
                parent_chunk_id="real-parent-secret-name",
                metadata={"chunk_index": 2},
            ),
        ]
    )

    report = _backfill(
        catalog,
        milvus,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        apply=True,
    )

    assert report.created == 3
    assert catalog.get_head("standalone-secret-name").chunk_role == "flat"
    assert catalog.get_head("real-parent-secret-name").chunk_role == "parent"
    assert catalog.get_head("child-secret-name").chunk_role == "child"
    summary = json.dumps(report.to_summary(ROLLOUT_SECRET), sort_keys=True)
    assert "standalone-secret-name" not in summary
    assert "real-parent-secret-name" not in summary
    assert "child-secret-name" not in summary
    engine.dispose()


def test_apply_skips_document_revision_mismatch_unless_explicitly_overridden(
    tmp_path: Path,
) -> None:
    engine = _state(tmp_path)
    catalog = ChunkCatalog(engine)
    milvus = FakeMilvus(
        [
            _chunk(
                "stale-secret-chunk",
                doc_id="doc-a1",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                text="stale",
                document_revision=6,
                metadata={"chunk_index": 0},
            )
        ]
    )

    safe = _backfill(
        catalog,
        milvus,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        apply=True,
    )
    assert safe.document_revision_mismatch_ids == ("stale-secret-chunk",)
    assert safe.created == 0
    assert safe.skipped_unsafe == 1

    unsafe = _backfill(
        catalog,
        milvus,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        apply=True,
        allow_unsafe_mismatches=True,
    )
    assert unsafe.created == 1
    assert catalog.get_head("stale-secret-chunk").document_revision == 6
    engine.dispose()


def test_backfill_sets_revision_directly_without_history_and_repairs_partial_head(
    tmp_path: Path,
) -> None:
    engine = _state(tmp_path)
    catalog = ChunkCatalog(engine)
    catalog.upsert_head(
        chunk_id="partial-secret-chunk",
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        document_id="doc-a1",
        parent_chunk_id=None,
        chunk_index=0,
        chunk_role="flat",
        document_revision=7,
        source_content="partial",
        content="partial",
        content_revision=0,
        metadata={"chunk_index": 0},
    )
    milvus = FakeMilvus(
        [
            _chunk(
                "partial-secret-chunk",
                doc_id="doc-a1",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                text="complete",
                document_revision=7,
                content_revision=4,
                metadata={"chunk_index": 0, "context": "canonical context"},
            )
        ]
    )

    report = _backfill(
        catalog,
        milvus,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        apply=True,
    )

    assert report.repaired_existing == 1
    head = catalog.get_head("partial-secret-chunk")
    assert head.content == "complete"
    assert head.content_revision == 4
    assert head.desired_index_revision == 4
    assert head.context_header == "canonical context"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ChunkRevision)) == 0
    engine.dispose()


def _migration_round_trip(database_url: str) -> None:
    from alembic import command
    from core import catalog_schema

    catalog_schema.upgrade_catalog(database_url)
    engine = create_engine(database_url)
    try:
        assert catalog_schema.inspect_catalog_schema(engine).status == "current"
    finally:
        engine.dispose()
    config = catalog_schema._alembic_config(database_url)
    command.downgrade(config, "0006_source_id")
    downgraded = create_engine(database_url)
    try:
        state = catalog_schema.inspect_catalog_schema(downgraded)
        assert state.status == "behind"
        assert "chunk_heads" not in inspect(downgraded).get_table_names()
    finally:
        downgraded.dispose()
    command.upgrade(config, "head")
    upgraded = create_engine(database_url)
    try:
        assert catalog_schema.inspect_catalog_schema(upgraded).status == "current"
        assert "chunk_heads" in inspect(upgraded).get_table_names()
    finally:
        upgraded.dispose()


def test_sqlite_chunk_authority_migration_downgrades_and_reupgrades(tmp_path: Path) -> None:
    _migration_round_trip(f"sqlite:///{(tmp_path / 'round-trip.db').as_posix()}")


@pytest.mark.skipif(not os.getenv("TEST_MYSQL_URL"), reason="TEST_MYSQL_URL not configured")
def test_mysql_release_drill_uses_disposable_database() -> None:
    from scripts.verify_knowledgeops_mysql import main as verify_mysql

    assert verify_mysql([]) == 0


def test_mysql_release_drill_requires_test_mysql_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts.verify_knowledgeops_mysql import main as verify_mysql

    monkeypatch.delenv("TEST_MYSQL_URL", raising=False)
    assert verify_mysql([]) == 2
    assert "TEST_MYSQL_URL" in capsys.readouterr().out


def test_backfill_cli_enforces_shadow_rollout_and_fail_on_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("RAG4C_ROLLOUT_REPORT_SECRET", ROLLOUT_SECRET)
    engine = _state(tmp_path)
    milvus = FakeMilvus(
        [
            _chunk(
                "cli-secret-chunk",
                doc_id="doc-a1",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                text="bad hash",
                text_hash="wrong",
                document_revision=7,
                metadata={"chunk_index": 0},
            )
        ]
    )
    settings = SimpleNamespace(
        catalog=SimpleNamespace(schema_mode="verify", chunk_authority_mode="shadow")
    )
    monkeypatch.setattr(
        backfill_module,
        "_runtime_dependencies",
        lambda: (engine, milvus, settings),
    )

    exit_code = backfill_module.main(
        [
            "--tenant-id",
            "tenant-a",
            "--dataset-id",
            "dataset-a",
            "--fail-on-drift",
        ]
    )
    output = capsys.readouterr().out
    assert exit_code == 2
    assert "cli-secret-chunk" not in output

    settings.catalog.chunk_authority_mode = "active"
    exit_code = backfill_module.main(
        [
            "--tenant-id",
            "tenant-a",
            "--dataset-id",
            "dataset-a",
            "--apply",
        ]
    )
    assert exit_code == 2
    assert "shadow" in capsys.readouterr().out
    engine.dispose()


def test_backfill_sqlite_snapshot_prevents_aba_document_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _state(tmp_path)
    catalog = ChunkCatalog(engine)
    first = _backfill(
        catalog,
        FakeMilvus([]),
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        batch_size=1,
    )

    original_capture = backfill_module.capture_document_snapshot
    original_postflight = backfill_module._assert_scan_state_current
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
                .where(Document.id == "doc-a2")
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
                .where(Document.id == "doc-a2")
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

    monkeypatch.setattr(backfill_module, "capture_document_snapshot", capture_with_aba)
    monkeypatch.setattr(backfill_module, "_assert_scan_state_current", postflight_after_revert)
    milvus = FakeMilvus([])

    report = _backfill(
        catalog,
        milvus,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        batch_size=1,
        cursor=first.next_cursor,
    )

    assert moved_during_snapshot == [False]
    assert report.documents_scanned == 1
    assert milvus.queries == [("doc-a2", "tenant-a")]
    assert report.complete is True
    assert writer_thread is not None
    writer_thread.join(timeout=2)
    assert not writer_thread.is_alive()
    engine.dispose()


def test_backfill_snapshot_rejects_scope_membership_changes(tmp_path: Path) -> None:
    engine = _state(tmp_path)
    milvus = FakeMilvus([])
    catalog = ChunkCatalog(engine)

    first = _backfill(
        catalog,
        milvus,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        batch_size=1,
    )
    with Session(engine) as session:
        session.delete(session.get(Document, "doc-a1"))
        session.add(
            Document(
                id="doc-a15",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                name="A15",
                content_revision=5,
                desired_index_revision=5,
                created_at=BASE_TIME + timedelta(minutes=1),
                updated_at=BASE_TIME + timedelta(minutes=1),
            )
        )
        session.commit()

    with pytest.raises(ValueError, match="snapshot assumptions"):
        _backfill(
            catalog,
            milvus,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            batch_size=1,
            cursor=first.next_cursor,
        )
    engine.dispose()


@pytest.mark.parametrize("mutation", ["delete", "move-created-at", "content-revision"])
def test_backfill_snapshot_rejects_unprocessed_member_mutation(
    tmp_path: Path, mutation: str
) -> None:
    engine = _state(tmp_path)
    catalog = ChunkCatalog(engine)
    milvus = FakeMilvus([])
    first = _backfill(
        catalog,
        milvus,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        batch_size=1,
    )

    with Session(engine) as session:
        document = session.get(Document, "doc-a2")
        if mutation == "delete":
            session.delete(document)
        elif mutation == "move-created-at":
            document.created_at = BASE_TIME + timedelta(days=1)
        else:
            document.content_revision += 1
        session.commit()

    with pytest.raises(ValueError, match="snapshot assumptions"):
        _backfill(
            catalog,
            milvus,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            batch_size=1,
            cursor=first.next_cursor,
        )
    engine.dispose()


def test_backfill_cursor_is_authenticated_and_domain_separated(tmp_path: Path) -> None:
    engine = _state(tmp_path)
    catalog = ChunkCatalog(engine)
    milvus = FakeMilvus([])
    first = _backfill(
        catalog,
        milvus,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        batch_size=1,
    )

    with pytest.raises(ValueError, match="authenticated"):
        _backfill_impl(
            catalog,
            milvus,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            batch_size=1,
            cursor=first.next_cursor,
            report_secret=OTHER_SECRET,
        )
    with pytest.raises(ValueError, match="authenticated"):
        reconcile_module._decode_cursor(
            first.next_cursor,
            report_secret=ROLLOUT_SECRET,
        )
    engine.dispose()


@pytest.mark.parametrize("tamper", ["illegal-suffix", "extra-field"])
def test_backfill_cursor_rejects_noncanonical_or_extended_envelopes(
    tmp_path: Path, tamper: str
) -> None:
    engine = _state(tmp_path)
    first = _backfill(
        ChunkCatalog(engine),
        FakeMilvus([]),
        tenant_id="tenant-a",
        dataset_id="dataset-a",
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
        backfill_module._decode_cursor(cursor, report_secret=ROLLOUT_SECRET)
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
def test_backfill_cursor_requires_exact_semantic_field_types(
    tmp_path: Path, field: str, invalid_value: object
) -> None:
    engine = _state(tmp_path)
    first = _backfill(
        ChunkCatalog(engine),
        FakeMilvus([]),
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        batch_size=1,
    )
    padded = first.next_cursor + "=" * (-len(first.next_cursor) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    payload[field] = invalid_value
    unsigned = {key: payload[key] for key in backfill_module._CURSOR_FIELDS}
    payload["signature"] = backfill_module._hmac_hex(
        ROLLOUT_SECRET,
        domain=f"{backfill_module._DOMAIN}/cursor/signature",
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
        backfill_module._decode_cursor(cursor, report_secret=ROLLOUT_SECRET)
    engine.dispose()


def test_backfill_summary_pseudonyms_are_keyed(tmp_path: Path) -> None:
    engine = _state(tmp_path)
    report = _backfill(
        ChunkCatalog(engine),
        FakeMilvus([]),
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        batch_size=1,
    )

    first = report.to_summary(ROLLOUT_SECRET)
    second = report.to_summary(OTHER_SECRET)
    assert first["tenant_id"] != second["tenant_id"]
    assert "tenant-a" not in json.dumps(first)
    assert "next_cursor" not in first
    assert report.next_cursor not in json.dumps(first)
    assert first["resume_cursor_ref"]
    assert "manifest_hash" not in first
    assert first["manifest_ref"]
    assert first["manifest_ref"] != report.manifest_hash
    assert report.manifest_hash not in json.dumps(first, sort_keys=True)
    assert first["manifest_ref"] != second["manifest_ref"]
    engine.dispose()


@pytest.mark.parametrize("mutation", ["update", "insert"])
def test_backfill_rechecks_snapshot_after_batch_processing(
    tmp_path: Path, mutation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _state(tmp_path)

    writer_attempting = threading.Event()
    writer_finished = threading.Event()
    writer_thread: threading.Thread | None = None

    def writer() -> None:
        writer_attempting.set()
        with Session(engine) as session:
            if mutation == "update":
                document = session.get(Document, "doc-a2")
                document.created_at = BASE_TIME + timedelta(days=1)
            else:
                session.add(
                    Document(
                        id="doc-a3",
                        tenant_id="tenant-a",
                        dataset_id="dataset-a",
                        name="A3",
                        content_revision=1,
                        desired_index_revision=1,
                        created_at=BASE_TIME + timedelta(minutes=3),
                        updated_at=BASE_TIME + timedelta(minutes=3),
                    )
                )
            session.commit()
        writer_finished.set()

    class ConcurrentMutationMilvus(FakeMilvus):
        def query_chunks_by_doc(self, doc_id: str, tenant_id: str = "") -> list[Chunk]:
            nonlocal writer_thread
            if not self.queries:
                writer_thread = threading.Thread(target=writer, daemon=True)
                writer_thread.start()
                assert writer_attempting.wait(2)
                assert not writer_finished.wait(0.2)
            return super().query_chunks_by_doc(doc_id, tenant_id)

    original_postflight = backfill_module._assert_scan_state_current

    def postflight_after_writer(*args, **kwargs):
        assert writer_finished.wait(5)
        return original_postflight(*args, **kwargs)

    monkeypatch.setattr(backfill_module, "_assert_scan_state_current", postflight_after_writer)
    catalog = ChunkCatalog(engine)
    milvus = ConcurrentMutationMilvus(
        [
            _chunk(
                "concurrent-write-must-not-commit",
                doc_id="doc-a1",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                text="planned only",
                document_revision=7,
                metadata={"chunk_index": 0},
            )
        ]
    )
    with pytest.raises(ValueError, match="snapshot assumptions"):
        _backfill(
            catalog,
            milvus,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            batch_size=1,
            apply=True,
        )
    with Session(engine) as session:
        assert session.get(ChunkHead, "concurrent-write-must-not-commit") is None
    assert writer_thread is not None
    writer_thread.join(timeout=2)
    assert not writer_thread.is_alive()
    engine.dispose()


def test_backfill_default_batch_avoids_hundred_document_rescan_pagination(
    tmp_path: Path,
) -> None:
    engine = _state(tmp_path)
    with Session(engine) as session:
        session.add_all(
            [
                Document(
                    id=f"doc-extra-{index:03d}",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    name=f"Extra {index}",
                    content_revision=1,
                    desired_index_revision=1,
                    created_at=BASE_TIME + timedelta(minutes=10 + index),
                    updated_at=BASE_TIME + timedelta(minutes=10 + index),
                )
                for index in range(99)
            ]
        )
        session.commit()

    report = _backfill(
        ChunkCatalog(engine),
        FakeMilvus([]),
        tenant_id="tenant-a",
        dataset_id="dataset-a",
    )

    assert report.documents_scanned == 101
    assert report.complete is True
    assert report.next_cursor == ""
    assert report.to_summary(ROLLOUT_SECRET)["snapshot_validation"]["warning"] == ""
    engine.dispose()


def test_rollout_cost_estimate_warns_about_paginated_full_snapshot_scans() -> None:
    from scripts.rollout_snapshot import estimate_rollout_cost

    estimate = estimate_rollout_cost(document_count=50_000, batch_size=1_000)

    assert estimate.batch_count == 50
    assert estimate.fingerprint_scan_count == 100
    assert estimate.estimated_rows_scanned == 5_000_000
    assert "5,000,000" in estimate.warning


def test_backfill_cli_requires_rollout_report_secret(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("RAG4C_ROLLOUT_REPORT_SECRET", raising=False)
    monkeypatch.setattr(
        backfill_module,
        "_runtime_dependencies",
        lambda: (_ for _ in ()).throw(AssertionError("runtime must not load")),
    )

    assert backfill_module.main(["--tenant-id", "tenant-a", "--dataset-id", "dataset-a"]) == 2
    assert "RAG4C_ROLLOUT_REPORT_SECRET" in capsys.readouterr().out

    monkeypatch.setenv("RAG4C_ROLLOUT_REPORT_SECRET", "example-secret-change-me-123456789")
    assert backfill_module.main(["--tenant-id", "tenant-a", "--dataset-id", "dataset-a"]) == 2
    assert "CSPRNG-generated Base64URL key" in capsys.readouterr().out


def test_backfill_snapshot_rejects_backdated_insert_before_cursor(tmp_path: Path) -> None:
    engine = _state(tmp_path)
    catalog = ChunkCatalog(engine)
    milvus = FakeMilvus([])
    first = _backfill(
        catalog,
        milvus,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        batch_size=1,
    )
    with Session(engine) as session:
        session.add(
            Document(
                id="doc-backdated",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                name="Backdated",
                content_revision=1,
                desired_index_revision=1,
                created_at=BASE_TIME - timedelta(minutes=1),
            )
        )
        session.commit()

    with pytest.raises(ValueError, match="snapshot assumptions"):
        _backfill(
            catalog,
            milvus,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            batch_size=1,
            cursor=first.next_cursor,
        )
    engine.dispose()


def test_backfill_snapshot_rejects_dataset_recreation_generation(tmp_path: Path) -> None:
    engine = _state(tmp_path)
    catalog = ChunkCatalog(engine)
    milvus = FakeMilvus([])
    first = _backfill(
        catalog,
        milvus,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        batch_size=1,
    )
    with Session(engine) as session:
        dataset = session.get(Dataset, "dataset-a")
        dataset.created_at = BASE_TIME + timedelta(days=1)
        session.commit()

    with pytest.raises(ValueError, match="dataset generation"):
        _backfill(
            catalog,
            milvus,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            batch_size=1,
            cursor=first.next_cursor,
        )
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
def test_backfill_rejects_weak_rollout_secret(tmp_path: Path, weak_secret: str) -> None:
    engine = _state(tmp_path)
    with pytest.raises(ValueError, match="CSPRNG-generated Base64URL key"):
        _backfill_impl(
            ChunkCatalog(engine),
            FakeMilvus([]),
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            report_secret=weak_secret,
        )
    engine.dispose()


def test_rollout_secret_generator_returns_valid_canonical_key() -> None:
    from scripts.rollout_security import generate_rollout_secret, validate_rollout_secret

    secret = generate_rollout_secret()

    assert "=" not in secret
    assert len(validate_rollout_secret(secret)) >= 32


def test_rollout_secret_generator_cli(capsys: pytest.CaptureFixture[str]) -> None:
    from scripts import rollout_security

    assert rollout_security.main(["generate"]) == 0
    generated = capsys.readouterr().out.strip()
    assert rollout_security.validate_rollout_secret(generated)


def test_backfill_cli_emits_resume_cursor_only_on_sensitive_channel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    engine = _state(tmp_path)
    settings = SimpleNamespace(
        catalog=SimpleNamespace(schema_mode="verify", chunk_authority_mode="shadow")
    )
    monkeypatch.setenv("RAG4C_ROLLOUT_REPORT_SECRET", ROLLOUT_SECRET)
    monkeypatch.setattr(
        backfill_module,
        "_runtime_dependencies",
        lambda: (engine, FakeMilvus([]), settings),
    )

    assert (
        backfill_module.main(
            [
                "--tenant-id",
                "tenant-a",
                "--dataset-id",
                "dataset-a",
                "--batch-size",
                "1",
                "--emit-sensitive-resume-cursor",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    retained = json.loads(captured.out)
    sensitive = json.loads(captured.err.strip().splitlines()[-1])
    assert "next_cursor" not in retained
    assert sensitive["sensitive_resume_cursor"]
    assert sensitive["sensitive_resume_cursor"] not in captured.out
    engine.dispose()


def test_backfill_manifest_hash_is_independent_of_report_secret(tmp_path: Path) -> None:
    engine = _state(tmp_path)
    catalog = ChunkCatalog(engine)
    milvus = FakeMilvus(
        [
            _chunk(
                "manifest-secret-chunk",
                doc_id="doc-a1",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                text="stable manifest",
                document_revision=7,
                metadata={"chunk_index": 0},
            )
        ]
    )

    first = _backfill_impl(
        catalog,
        milvus,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        report_secret=ROLLOUT_SECRET,
    )
    rotated = _backfill_impl(
        catalog,
        milvus,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        report_secret=OTHER_SECRET,
    )

    assert first.manifest_hash == rotated.manifest_hash
    engine.dispose()
