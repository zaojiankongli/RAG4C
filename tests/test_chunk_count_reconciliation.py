"""Active-mode chunk_count reconciliation against the ChunkHead authority.

One active-mode tombstone used to leave ``documents.chunk_count`` behind while the
enabled-head count dropped.  The completeness fence compared exactly those two
numbers *before* the write, so the drift locked the whole document out: every later
chunk write on it was rejected -- including edits to chunks nobody had touched, and
including the restore/revert verbs shipped in the same slice as this test.

The alignment now happens inside the writer's own ``FOR UPDATE`` transaction, both
before the write (so a document that already drifted heals on its next write instead
of staying 409 forever) and after it (so the count a concurrent request reads is
never the pre-write one).  Tenant / dataset aggregates move by the same delta, which
is what ``catalog.set_document_status`` does on its own path -- it cannot be called
from inside this transaction without deadlocking on the row lock held here.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.chunk_catalog import ChunkCatalog
from core.index_operations import IndexOperationQueue
from models.orm import Dataset, Document, Tenant
from server.chunk_operations import (
    delete_document_chunk_artifacts,
    update_document_chunk_artifacts,
)


class _Catalog:
    """A catalog double that behaves like the real one: reads and writes persist."""

    def __init__(self, engine) -> None:
        self.engine = engine
        self.status_calls: list[tuple[str, int | None]] = []

    def get_engine(self):
        return self.engine

    def get_document(self, doc_id: str) -> dict:
        with Session(self.engine) as session:
            row = session.get(Document, doc_id)
            assert row is not None
            return {
                "id": row.id,
                "tenant_id": row.tenant_id,
                "dataset_id": row.dataset_id,
                "status": row.status,
                "lifecycle_state": "active",
                "retrieval_enabled": True,
                "content_revision": row.content_revision,
                "desired_index_revision": row.desired_index_revision,
                "chunk_count": row.chunk_count,
                "parser_meta": row.parser_meta or {},
            }

    def set_document_status(
        self, doc_id, status, *, detail="", chunk_count=None, parser_meta=None
    ) -> None:
        self.status_calls.append((doc_id, None if chunk_count is None else int(chunk_count)))
        with Session(self.engine) as session:
            row = session.get(Document, doc_id)
            if row is not None:
                if chunk_count is not None:
                    row.chunk_count = int(chunk_count)
                if parser_meta is not None:
                    row.parser_meta = dict(parser_meta)
                session.commit()


@pytest.fixture()
def active_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # 目录库用 session 级模板库（tests/_catalog_template.py）：建一次已迁移到
    # head 的 SQLite、各用例拷文件，实测 4.0ms/次 vs 重跑迁移 13.9s（3432x）。
    # 本文件不需要 BASELINE（不验证「从 baseline 升到 head」的过程本身），
    # 所以拷模板与重跑迁移等价。
    from _catalog_template import head_db_url
    url = head_db_url(tmp_path / "catalog.db")
    engine = create_engine(url)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-1", name="T"))
        session.add(Dataset(id="dataset-1", tenant_id="tenant-1", name="KB"))
        session.add(
            Document(
                id="doc-1",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name="D",
                status="completed",
                content_revision=2,
                desired_index_revision=2,
                indexed_revision=2,
                graph_revision=2,
                chunk_count=2,
            )
        )
        session.commit()
    chunks = ChunkCatalog(engine)
    for offset, chunk_id in enumerate(("chunk-A", "chunk-B")):
        chunks.upsert_head(
            chunk_id=chunk_id,
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            document_id="doc-1",
            parent_chunk_id=None,
            chunk_index=offset,
            chunk_role="flat",
            document_revision=2,
            source_content=f"body {chunk_id}",
            content=f"body {chunk_id}",
        )
    monkeypatch.setattr("server.chunk_operations.cache_epoch.bump", lambda *_a, **_k: None)
    catalog_api = _Catalog(engine)
    kwargs = dict(
        authority_mode="active",
        catalog_api=catalog_api,
        pipeline=SimpleNamespace(milvus=None, embedder=None, graph_builder=None),
        chunk_catalog=chunks,
        operation_queue=IndexOperationQueue(engine),
    )
    yield engine, chunks, catalog_api, kwargs
    engine.dispose()


def _counts(engine, chunks) -> tuple[int, int]:
    with Session(engine) as session:
        declared = int(session.get(Document, "doc-1").chunk_count)
    return declared, chunks.count_document_heads(
        "doc-1", document_revision=2, enabled_only=True
    )


def test_tombstone_reconciles_chunk_count_with_the_authority(active_document) -> None:
    engine, chunks, _catalog_api, kwargs = active_document
    assert _counts(engine, chunks) == (2, 2)

    result = delete_document_chunk_artifacts("doc-1", "chunk-A", expected_revision=0, **kwargs)

    assert _counts(engine, chunks) == (1, 1)
    assert result["remaining_chunks"] == 1
    assert result["projection_pending"] is True
    assert len(result["operation_ids"]) == 1


def test_a_later_edit_on_an_untouched_chunk_still_writes(active_document) -> None:
    """The regression itself: an unrelated chunk must not inherit the wedge."""
    engine, chunks, _catalog_api, kwargs = active_document
    delete_document_chunk_artifacts("doc-1", "chunk-A", expected_revision=0, **kwargs)

    edited = update_document_chunk_artifacts(
        "doc-1", "chunk-B", "edited body", expected_revision=0, **kwargs
    )

    assert edited.chunk.content_revision == 1
    assert edited.projection_pending is True
    assert len(edited.operation_ids) == 1
    assert _counts(engine, chunks) == (1, 1)


def test_restoring_a_tombstone_requeues_projection_work_and_rebalances_count(
    active_document,
) -> None:
    engine, chunks, _catalog_api, kwargs = active_document
    delete_document_chunk_artifacts("doc-1", "chunk-A", expected_revision=0, **kwargs)

    restored = update_document_chunk_artifacts(
        "doc-1",
        "chunk-A",
        "",
        enabled=True,
        reason="恢复误删",
        expected_revision=1,
        **kwargs,
    )
    head = chunks.get_head("chunk-A")

    assert head.enabled is True
    assert head.content_revision == 2
    assert head.chunk_metadata["edit_reason"] == "恢复误删"
    # Restoring must actually put the vector back, not just flip the flag.
    assert len(restored.operation_ids) == 1
    assert _counts(engine, chunks) == (2, 2)


def test_consistent_counts_do_not_write_the_document_row(active_document) -> None:
    """No gratuitous UPDATE per edit: reconcile only when the numbers actually differ."""
    _engine, _chunks, catalog_api, kwargs = active_document

    update_document_chunk_artifacts(
        "doc-1", "chunk-B", "edited body", expected_revision=0, **kwargs
    )

    assert catalog_api.status_calls == []


def test_pre_existing_drift_heals_itself_instead_of_locking_the_document(
    active_document,
) -> None:
    """漂移必须能自愈，不是只能预防。

    计数对账若跑在完整性检查之后，已经漂移的文档就永远进不到那次写入：fence 先拒绝，
    对账就永远不会发生。旧代码留下的正是这种文档——它们在这次修复上线之前就被锁死了。
    """
    engine, chunks, _catalog_api, kwargs = active_document
    with Session(engine) as session:
        session.get(Document, "doc-1").chunk_count = 5
        session.commit()

    edited = update_document_chunk_artifacts(
        "doc-1", "chunk-B", "edited body", expected_revision=0, **kwargs
    )

    assert edited.chunk.content_revision == 1
    assert _counts(engine, chunks) == (2, 2)


def test_repeat_tombstone_does_not_inflate_the_manual_delete_audit(
    active_document,
) -> None:
    """幂等重放不改权威，也就不该再写删除审计计数（不变量 5：审计只记真实改动）。"""
    engine, _chunks, _catalog_api, kwargs = active_document
    delete_document_chunk_artifacts("doc-1", "chunk-A", expected_revision=0, **kwargs)
    with Session(engine) as session:
        first = dict(session.get(Document, "doc-1").parser_meta or {})
    assert first["manual_chunk_deletes"] == 1

    repeated = delete_document_chunk_artifacts(
        "doc-1", "chunk-A", expected_revision=1, **kwargs
    )
    with Session(engine) as session:
        second = dict(session.get(Document, "doc-1").parser_meta or {})

    assert repeated["remaining_chunks"] == 1
    assert second["manual_chunk_deletes"] == 1
    assert second["last_chunk_delete_at"] == first["last_chunk_delete_at"]
