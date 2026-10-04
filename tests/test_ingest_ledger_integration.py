from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core import catalog
from core.chunk_catalog import ChunkCatalog
from core.index_operations import IndexOperationQueue
from core.ingest_ledger import IngestLedger
from indexing.state_machine import DocumentIngestJob


class FakeChunk:
    def __init__(self, chunk_id: str):
        self.chunk_id = chunk_id
        self.doc_id = "doc"
        self.text = f"content for {chunk_id}"
        self.text_hash = f"hash-{chunk_id}"
        self.parent_chunk_id = None
        self.metadata = {"chunk_index": int(chunk_id.rsplit("-", 1)[-1])}
        self.tenant_id = "tenant-1"
        self.dataset_id = "dataset-1"
        self.document_revision = 0
        self.content_revision = 0


class SuccessfulPipeline:
    def __init__(self):
        self.calls: list[str] = []

    def parse_and_chunk(self, *args, progress=None, **kwargs):
        self.calls.append("parse")
        progress("parsing", 1.0, "parsed")
        progress("splitting", 1.0, "split")
        return [FakeChunk("chunk-1"), FakeChunk("chunk-2")]

    def embed_and_insert(self, chunks, *args, progress=None, **kwargs):
        self.calls.append("index")
        progress("indexing", 1.0, "indexed")
        return SimpleNamespace(chunk_count=len(chunks), graph=None)

    def ingest_meta(self, result):
        return {}


class FailingPipeline(SuccessfulPipeline):
    def parse_and_chunk(self, *args, progress=None, **kwargs):
        progress("parsing", 0.5, "half parsed")
        raise RuntimeError("failed at C:\\private\\document.pdf")


class BrokenLedger:
    def start_attempt(self, **kwargs):
        raise RuntimeError("ledger unavailable")


class BrokenQueue:
    def enqueue_operation(self, **kwargs):
        raise RuntimeError("queue unavailable")


def configure_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):

    # 目录库用 session 级模板库（tests/_catalog_template.py）：建一次已迁移到
    # head 的 SQLite、各用例拷文件，实测 4.0ms/次 vs 重跑迁移 13.9s（3432x）。
    # 本文件不需要某个特定 revision（不验证「从某 revision 升到 head」的
    # 过程本身），所以拷模板与重跑迁移等价。
    from _catalog_template import head_db_url
    url = head_db_url(tmp_path / "catalog.db")
    catalog.reset_engine()
    monkeypatch.setattr(catalog, "_resolve_db_url", lambda: (url, None))
    monkeypatch.setattr(catalog, "_catalog_schema_mode", lambda: "verify")
    catalog.ensure_tenant("tenant-1", "Tenant")
    catalog.ensure_dataset("tenant-1", "dataset-1", "KB")
    document = catalog.create_document(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        name="Document",
        file_path=str(tmp_path / "document.md"),
    )
    return catalog.get_engine(), document


def test_shadow_mode_records_attempt_spans_operation_and_revisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, document = configure_catalog(tmp_path, monkeypatch)
    file_path = tmp_path / "document.md"
    file_path.write_text("hello", encoding="utf-8")
    pipeline = SuccessfulPipeline()
    ledger = IngestLedger(engine)
    queue = IndexOperationQueue(engine)
    job = DocumentIngestJob(
        pipeline,
        document["id"],
        dataset_id="dataset-1",
        ledger=ledger,
        operation_queue=queue,
        chunk_catalog=ChunkCatalog(engine),
        ledger_mode="shadow",
    )

    result = job.run(str(file_path))

    assert result == {"status": "completed", "chunk_count": 2}
    assert pipeline.calls == ["parse", "index"]
    attempts = ledger.list_attempts(document["id"])
    assert [item.state for item in attempts] == ["completed"]
    assert attempts[0].primary_index_ready_at is not None
    spans = ledger.list_spans(attempts[0].id)
    assert [(item.span_id, item.status) for item in spans] == [
        ("parsing", "done"),
        ("splitting", "done"),
        ("indexing", "done"),
    ]
    chunk_head = ChunkCatalog(engine).get_head("chunk-1")
    assert chunk_head.document_revision == 1
    assert chunk_head.content_revision == 0
    assert chunk_head.content == "content for chunk-1"
    operation = queue.list_operations()[0]
    assert operation.status == "shadow"
    assert operation.target_revision == 1
    assert operation.payload == {
        "chunk_ids": ["chunk-1", "chunk-2"],
        "document_generation": 0,
        "dataset_generation": 0,
    }
    refreshed = catalog.get_document(document["id"])
    assert refreshed is not None
    assert refreshed["content_revision"] == 1
    assert refreshed["desired_index_revision"] == 1
    assert refreshed["indexed_revision"] == 1
    catalog.reset_engine()


def test_shadow_mode_marks_attempt_and_open_span_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, document = configure_catalog(tmp_path, monkeypatch)
    file_path = tmp_path / "document.md"
    file_path.write_text("hello", encoding="utf-8")
    ledger = IngestLedger(engine)
    job = DocumentIngestJob(
        FailingPipeline(),
        document["id"],
        dataset_id="dataset-1",
        ledger=ledger,
        operation_queue=IndexOperationQueue(engine),
        ledger_mode="shadow",
    )

    result = job.run(str(file_path))

    assert result["status"] == "error"
    attempt = ledger.list_attempts(document["id"])[0]
    assert attempt.state == "failed"
    span = ledger.list_spans(attempt.id)[0]
    assert span.status == "failed"
    assert "private" not in span.error_message
    catalog.reset_engine()


def test_shadow_ledger_failure_never_breaks_legacy_ingestion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, document = configure_catalog(tmp_path, monkeypatch)
    file_path = tmp_path / "document.md"
    file_path.write_text("hello", encoding="utf-8")
    pipeline = SuccessfulPipeline()
    job = DocumentIngestJob(
        pipeline,
        document["id"],
        dataset_id="dataset-1",
        ledger=BrokenLedger(),
        operation_queue=BrokenQueue(),
        ledger_mode="shadow",
    )

    result = job.run(str(file_path))

    assert result == {"status": "completed", "chunk_count": 2}
    assert pipeline.calls == ["parse", "index"]
    catalog.reset_engine()


def test_server_job_factory_wires_shadow_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, document = configure_catalog(tmp_path, monkeypatch)
    from server import documents

    monkeypatch.setattr(documents, "_configured_ingest_ledger_mode", lambda: "shadow", raising=False)
    monkeypatch.setattr(catalog, "get_engine", lambda: engine)

    job = documents._build_document_ingest_job(
        SuccessfulPipeline(), document["id"], "dataset-1"
    )

    assert job.ledger_mode == "shadow"
    assert isinstance(job.ledger, IngestLedger)
    assert isinstance(job.operation_queue, IndexOperationQueue)
    assert isinstance(job.chunk_catalog, ChunkCatalog)
    catalog.reset_engine()


def test_active_mode_only_enqueues_projection_and_does_not_call_legacy_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, document = configure_catalog(tmp_path, monkeypatch)
    file_path = tmp_path / "document.md"
    file_path.write_text("hello", encoding="utf-8")
    pipeline = SuccessfulPipeline()
    ledger = IngestLedger(engine)
    queue = IndexOperationQueue(engine)
    job = DocumentIngestJob(
        pipeline,
        document["id"],
        dataset_id="dataset-1",
        ledger=ledger,
        operation_queue=queue,
        chunk_catalog=ChunkCatalog(engine),
        ledger_mode="active",
    )

    result = job.run(str(file_path))

    assert result == {"status": "indexing", "chunk_count": 2, "queued": True}
    assert pipeline.calls == ["parse"]
    assert [item.status for item in queue.list_operations()] == ["pending"]
    refreshed = catalog.get_document(document["id"])
    assert refreshed is not None
    assert refreshed["status"] == "indexing"
    assert ledger.list_attempts(document["id"])[0].state == "running"
    catalog.reset_engine()
