from __future__ import annotations

import tempfile
from concurrent.futures import Future
from pathlib import Path

import pytest
from fastapi import HTTPException

from server import documents


@pytest.fixture()
def cat(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="rag4c-folder-ingest-")
    monkeypatch.setenv("RAG4C_CATALOG_DB_PATH", str(Path(tmp) / "catalog.db"))
    monkeypatch.setenv("RAG4C_CATALOG_DB_URL", "")
    monkeypatch.setenv("RAG4C_CATALOG_SCHEMA_MODE", "legacy")

    from config.settings import get_settings
    from core import catalog

    get_settings.cache_clear()
    catalog.reset_engine()
    yield catalog
    catalog.reset_engine()
    get_settings.cache_clear()


def test_folder_scan_recurses_and_ignores_hidden_or_unsupported_files(tmp_path: Path) -> None:
    (tmp_path / "handbook.PDF").write_bytes(b"pdf")
    (tmp_path / "notes.txt").write_text("notes", encoding="utf-8")
    nested = tmp_path / "policies"
    nested.mkdir()
    (nested / "leave.md").write_text("leave", encoding="utf-8")
    (nested / "installer.exe").write_bytes(b"exe")
    hidden = tmp_path / ".drafts"
    hidden.mkdir()
    (hidden / "secret.pdf").write_bytes(b"pdf")
    (tmp_path / ".private.md").write_text("private", encoding="utf-8")

    result = documents.discover_folder_documents(tmp_path)

    assert [path.relative_to(tmp_path).as_posix() for path in result.files] == [
        "handbook.PDF",
        "notes.txt",
        "policies/leave.md",
    ]
    assert result.discovered_count == 4
    assert result.unsupported_count == 1


def test_folder_ingest_registers_waiting_documents_and_skips_completed_paths(
    tmp_path: Path,
    cat,
    monkeypatch,
) -> None:
    completed_path = tmp_path / "existing.md"
    completed_path.write_text("existing", encoding="utf-8")
    new_path = tmp_path / "nested" / "new.txt"
    new_path.parent.mkdir()
    new_path.write_text("new", encoding="utf-8")

    existing = cat.create_document(
        "default", "default", completed_path.name, file_path=str(completed_path)
    )
    for status in ("parsing", "splitting", "indexing", "completed"):
        cat.set_document_status(existing["id"], status)

    submitted: list[list[str]] = []

    def hold_batch(doc_ids: list[str], _job) -> None:
        submitted.append(doc_ids)

    monkeypatch.setattr(documents, "_submit_batch_job", hold_batch)

    response = documents.ingest_folder(
        documents.FolderIngestRequest(folder_path=str(tmp_path))
    )

    assert response["discovered_count"] == 2
    assert response["queued_count"] == 1
    assert response["duplicate_count"] == 1
    assert response["unsupported_count"] == 0
    assert len(response["document_ids"]) == 1
    assert submitted == [response["document_ids"]]

    rows = cat.list_documents("default")
    assert sorted((row["name"], row["status"]) for row in rows) == [
        ("existing.md", "completed"),
        ("new.txt", "waiting"),
    ]


def test_folder_ingest_rejects_a_non_directory(tmp_path: Path) -> None:
    file_path = tmp_path / "one.md"
    file_path.write_text("one", encoding="utf-8")

    with pytest.raises(HTTPException) as exc:
        documents.ingest_folder(
            documents.FolderIngestRequest(folder_path=str(file_path))
        )

    assert exc.value.status_code == 400
    assert "文件夹不存在" in str(exc.value.detail)


def test_folder_ingest_rolls_back_new_rows_when_the_queue_is_full(
    tmp_path: Path,
    cat,
    monkeypatch,
) -> None:
    (tmp_path / "queued.md").write_text("queued", encoding="utf-8")

    def reject_batch(_doc_ids: list[str], _job) -> None:
        raise HTTPException(status_code=429, detail="入库任务队列已满，请稍后重试")

    monkeypatch.setattr(documents, "_submit_batch_job", reject_batch)

    with pytest.raises(HTTPException) as exc:
        documents.ingest_folder(
            documents.FolderIngestRequest(folder_path=str(tmp_path))
        )

    assert exc.value.status_code == 429
    assert cat.list_documents("default") == []


def test_batch_submission_uses_one_queue_slot_for_all_documents(monkeypatch) -> None:
    capacity = documents.threading.BoundedSemaphore(1)
    pending: Future[None] = Future()

    class ControlledExecutor:
        def submit(self, job):
            return pending

    monkeypatch.setattr(documents, "_job_capacity", capacity)
    monkeypatch.setattr(documents, "start_ingest_executor", lambda: ControlledExecutor())
    documents._jobs.clear()

    documents._submit_batch_job(["doc-a", "doc-b"], lambda: None)

    assert capacity.acquire(blocking=False) is False
    assert documents._jobs["doc-a"]["running"] is True
    assert documents._jobs["doc-b"]["running"] is True

    pending.set_result(None)
    assert capacity.acquire(blocking=False) is True
    capacity.release()
    assert documents._jobs["doc-a"]["running"] is False
    assert documents._jobs["doc-b"]["running"] is False


def test_folder_batch_executes_jobs_through_the_ledger_factory(
    tmp_path: Path, cat, monkeypatch
) -> None:
    (tmp_path / "one.md").write_text("one", encoding="utf-8")
    submitted: list[object] = []
    built: list[tuple[str, str]] = []

    class Pipeline:
        def ensure_collection(self) -> None:
            pass

    class Job:
        def run(self, _path: str):
            return {"status": "completed"}

    monkeypatch.setattr(documents, "_get_ingest_pipeline", lambda: Pipeline())
    monkeypatch.setattr(documents, "_ensure_graph_collections", lambda _pipeline: None)
    monkeypatch.setattr(
        documents,
        "_build_document_ingest_job",
        lambda _pipeline, doc_id, dataset_id: (
            built.append((doc_id, dataset_id)) or Job()
        ),
    )
    monkeypatch.setattr(
        documents,
        "_submit_batch_job",
        lambda _doc_ids, job: submitted.append(job),
    )

    response = documents.ingest_folder(
        documents.FolderIngestRequest(folder_path=str(tmp_path))
    )
    assert len(submitted) == 1

    submitted[0]()

    assert built == [(response["document_ids"][0], "default")]
