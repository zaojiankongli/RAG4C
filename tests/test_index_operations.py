from __future__ import annotations

import importlib
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.ingest_ledger import IngestLedger
from models.orm import Dataset, Document, Tenant


def operations_module() -> ModuleType:
    try:
        return importlib.import_module("core.index_operations")
    except ModuleNotFoundError:
        pytest.fail("core.index_operations is missing")


def schema_module() -> ModuleType:
    return importlib.import_module("core.catalog_schema")


def create_queue(tmp_path: Path):
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
    attempt = IngestLedger(engine).start_attempt(
        tenant_id="tenant-1", dataset_id="dataset-1", document_id="doc-1"
    )
    return engine, operations_module().IndexOperationQueue(engine), attempt


def enqueue(queue, attempt, suffix: str, revision: int = 1, max_retries: int = 3):
    return queue.enqueue_operation(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        attempt_id=attempt.id,
        target_store="milvus_chunks",
        operation="upsert",
        dedup_key=f"doc-1:{revision}:milvus:{suffix}",
        target_revision=revision,
        payload={"chunk_ids": [suffix]},
        max_retries=max_retries,
    )


def test_enqueue_is_idempotent_by_dedup_key(tmp_path: Path) -> None:
    engine, queue, attempt = create_queue(tmp_path)

    first = enqueue(queue, attempt, "batch-1")
    second = enqueue(queue, attempt, "batch-1")

    assert second.id == first.id
    assert queue.count_operations() == 1
    engine.dispose()


def test_claims_do_not_overlap_before_lease_expiry(tmp_path: Path) -> None:
    engine, queue, attempt = create_queue(tmp_path)
    for index in range(3):
        enqueue(queue, attempt, f"batch-{index}")
    now = datetime(2026, 8, 24, 10, 0, 0)

    first = queue.claim_operations("worker-a", limit=2, lease_seconds=60, now=now)
    second = queue.claim_operations("worker-b", limit=2, lease_seconds=60, now=now)

    assert len(first) == 2
    assert len(second) == 1
    assert {item.id for item in first}.isdisjoint({item.id for item in second})
    engine.dispose()


def test_expired_claim_is_recovered_by_another_worker(tmp_path: Path) -> None:
    engine, queue, attempt = create_queue(tmp_path)
    operation = enqueue(queue, attempt, "batch-1")
    now = datetime(2026, 8, 24, 10, 0, 0)
    queue.claim_operations("worker-a", limit=1, lease_seconds=30, now=now)

    recovered = queue.claim_operations(
        "worker-b", limit=1, lease_seconds=30, now=now + timedelta(seconds=31)
    )

    assert [item.id for item in recovered] == [operation.id]
    assert recovered[0].claimed_by == "worker-b"
    engine.dispose()


def test_retry_budget_promotes_operation_to_dead_letter(tmp_path: Path) -> None:
    engine, queue, attempt = create_queue(tmp_path)
    operation = enqueue(queue, attempt, "batch-1", max_retries=2)
    now = datetime(2026, 8, 24, 10, 0, 0)
    queue.claim_operations("worker-a", limit=1, lease_seconds=30, now=now)

    retried = queue.retry_operation(
        operation.id,
        worker_id="worker-a",
        error_code="MILVUS_TIMEOUT",
        error_message="timeout at C:\\private\\milvus.log",
        now=now,
        base_delay_seconds=10,
    )

    assert retried.status == "retry"
    assert retried.retry_count == 1
    assert retried.next_retry_at == now + timedelta(seconds=10)
    queue.claim_operations(
        "worker-b", limit=1, lease_seconds=30, now=now + timedelta(seconds=10)
    )
    dead = queue.retry_operation(
        operation.id,
        worker_id="worker-b",
        error_code="MILVUS_TIMEOUT",
        error_message="timeout again",
        now=now + timedelta(seconds=10),
        base_delay_seconds=10,
    )

    assert dead.status == "dead"
    letters = queue.list_dead_letters()
    assert len(letters) == 1
    assert letters[0].operation_id == operation.id
    assert "private" not in letters[0].last_error
    engine.dispose()


def test_dead_letter_requeue_preserves_lineage(tmp_path: Path) -> None:
    engine, queue, attempt = create_queue(tmp_path)
    operation = enqueue(queue, attempt, "batch-1", max_retries=1)
    now = datetime(2026, 8, 24, 10, 0, 0)
    queue.claim_operations("worker-a", limit=1, lease_seconds=30, now=now)
    queue.retry_operation(
        operation.id,
        worker_id="worker-a",
        error_code="BROKEN",
        error_message="broken",
        now=now,
    )
    letter = queue.list_dead_letters()[0]

    requeued = queue.requeue_dead_letter(letter.id, operator_note="verified and retried")
    refreshed = queue.list_dead_letters()[0]

    assert requeued.id != operation.id
    assert requeued.status == "pending"
    assert refreshed.requeued_to_operation_id == requeued.id
    assert refreshed.operator_note == "verified and retried"
    engine.dispose()


def test_new_revision_supersedes_older_pending_operations(tmp_path: Path) -> None:
    engine, queue, attempt = create_queue(tmp_path)
    old_one = enqueue(queue, attempt, "old-1", revision=1)
    old_two = enqueue(queue, attempt, "old-2", revision=2)
    current = enqueue(queue, attempt, "current", revision=3)

    changed = queue.supersede_stale_operations("doc-1", current_revision=3)

    assert changed == 2
    assert queue.get_operation(old_one.id).status == "superseded"
    assert queue.get_operation(old_two.id).status == "superseded"
    assert queue.get_operation(current.id).status == "pending"
    engine.dispose()
