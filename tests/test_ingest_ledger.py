from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from models.orm import Dataset, Document, Tenant


def ledger_module() -> ModuleType:
    try:
        return importlib.import_module("core.ingest_ledger")
    except ModuleNotFoundError:
        pytest.fail("core.ingest_ledger is missing")


def schema_module() -> ModuleType:
    return importlib.import_module("core.catalog_schema")


def create_catalog(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'catalog.db').as_posix()}"
    schema_module().upgrade_catalog(url)
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
            )
        )
        session.commit()
    return engine


def test_attempt_numbers_are_monotonic_and_history_is_preserved(tmp_path: Path) -> None:
    engine = create_catalog(tmp_path)
    ledger = ledger_module().IngestLedger(engine)

    first = ledger.start_attempt(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        input_content_hash="hash-1",
        input_revision=1,
    )
    ledger.finish_attempt(first.id, "failed", error_code="parse_failed")
    second = ledger.start_attempt(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        input_content_hash="hash-2",
        input_revision=2,
    )

    assert first.attempt_no == 1
    assert second.attempt_no == 2
    assert [item.state for item in ledger.list_attempts("doc-1")] == ["failed", "running"]
    with Session(engine) as session:
        document = session.get(Document, "doc-1")
        assert document is not None
        assert document.current_attempt_id == second.id
    engine.dispose()


def test_span_identity_is_unique_within_one_attempt(tmp_path: Path) -> None:
    engine = create_catalog(tmp_path)
    ledger = ledger_module().IngestLedger(engine)
    attempt = ledger.start_attempt(
        tenant_id="tenant-1", dataset_id="dataset-1", document_id="doc-1"
    )

    ledger.start_span(attempt.id, span_id="parse", name="parse", kind="stage")

    with pytest.raises(ledger_module().IngestLedgerConflict, match="span already exists"):
        ledger.start_span(attempt.id, span_id="parse", name="parse", kind="stage")
    engine.dispose()


def test_terminal_attempt_cannot_return_to_running(tmp_path: Path) -> None:
    engine = create_catalog(tmp_path)
    ledger = ledger_module().IngestLedger(engine)
    attempt = ledger.start_attempt(
        tenant_id="tenant-1", dataset_id="dataset-1", document_id="doc-1"
    )
    ledger.finish_attempt(attempt.id, "completed")

    with pytest.raises(ledger_module().IngestLedgerConflict, match="terminal"):
        ledger.set_attempt_state(attempt.id, "running")
    engine.dispose()


def test_failed_span_persists_sanitized_error_and_duration(tmp_path: Path) -> None:
    engine = create_catalog(tmp_path)
    ledger = ledger_module().IngestLedger(engine)
    attempt = ledger.start_attempt(
        tenant_id="tenant-1", dataset_id="dataset-1", document_id="doc-1"
    )
    ledger.start_span(attempt.id, span_id="parse", name="parse", kind="stage")

    finished = ledger.finish_span(
        attempt.id,
        "parse",
        "failed",
        error_code="DOC_PARSE_FAILED",
        error_message="failed at C:\\private\\documents\\secret.pdf for doc-1",
    )

    assert finished.status == "failed"
    assert finished.error_code == "DOC_PARSE_FAILED"
    assert "private" not in finished.error_message
    assert finished.duration_ms is not None
    assert finished.duration_ms >= 0
    engine.dispose()


def test_attempt_scope_must_match_document(tmp_path: Path) -> None:
    engine = create_catalog(tmp_path)
    ledger = ledger_module().IngestLedger(engine)

    with pytest.raises(ledger_module().IngestLedgerConflict, match="scope mismatch"):
        ledger.start_attempt(
            tenant_id="tenant-other",
            dataset_id="dataset-1",
            document_id="doc-1",
        )
    engine.dispose()
