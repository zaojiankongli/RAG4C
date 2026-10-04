from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.index_operations import IndexOperationQueue
from core.ingest_ledger import IngestLedger
from models.orm import Dataset, Document, Tenant


def reconciler_module() -> ModuleType:
    try:
        return importlib.import_module("indexing.reconciler")
    except ModuleNotFoundError:
        pytest.fail("indexing.reconciler is missing")


def create_reconciler(tmp_path: Path):

    # 目录库用 session 级模板库（tests/_catalog_template.py）：建一次已迁移到
    # head 的 SQLite、各用例拷文件，实测 4.0ms/次 vs 重跑迁移 13.9s（3432x）。
    # 本文件不需要某个特定 revision（不验证「从某 revision 升到 head」的
    # 过程本身），所以拷模板与重跑迁移等价。
    from _catalog_template import head_db_url
    url = head_db_url(tmp_path / "catalog.db")
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
                content_revision=3,
                desired_index_revision=3,
                indexed_revision=1,
            )
        )
        session.commit()
    attempt = IngestLedger(engine).start_attempt(
        tenant_id="tenant-1", dataset_id="dataset-1", document_id="doc-1"
    )
    queue = IndexOperationQueue(engine)
    return engine, queue, attempt, reconciler_module().IndexReconciler(engine, queue)


def test_reconciler_enqueues_one_deduplicated_revision_repair(tmp_path: Path) -> None:
    engine, queue, attempt, reconciler = create_reconciler(tmp_path)

    first = reconciler.scan_document("doc-1")
    second = reconciler.scan_document("doc-1")

    assert first.enqueued == 1
    assert second.enqueued == 0
    operation = queue.list_operations()[0]
    assert operation.attempt_id == attempt.id
    assert operation.operation == "reconcile"
    assert operation.target_revision == 3
    assert operation.status == "pending"
    engine.dispose()


def test_reconciler_does_nothing_when_projection_is_current(tmp_path: Path) -> None:
    engine, queue, _, reconciler = create_reconciler(tmp_path)
    with Session(engine) as session:
        document = session.get(Document, "doc-1")
        assert document is not None
        document.indexed_revision = document.desired_index_revision
        session.commit()

    result = reconciler.scan_document("doc-1")

    assert result.enqueued == 0
    assert queue.count_operations() == 0
    engine.dispose()
