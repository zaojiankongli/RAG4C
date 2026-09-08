"""Immutable scope snapshot helpers for KnowledgeOps rollout tools."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from models.orm import Document


DEFAULT_ROLLOUT_BATCH_SIZE = 1_000


@dataclass(frozen=True)
class DocumentSnapshot:
    count: int
    fingerprint: str


@dataclass(frozen=True)
class RolloutCostEstimate:
    batch_count: int
    fingerprint_scan_count: int
    estimated_rows_scanned: int
    warning: str


def estimate_rollout_cost(*, document_count: int, batch_size: int) -> RolloutCostEstimate:
    """Estimate full-snapshot validation work for a paginated rollout."""
    if document_count < 0:
        raise ValueError("document_count must not be negative")
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    batch_count = max(1, math.ceil(document_count / batch_size))
    fingerprint_scan_count = batch_count * 2
    estimated_rows_scanned = document_count * fingerprint_scan_count
    warning = ""
    if batch_count > 1:
        warning = (
            "paginated snapshot validation will scan approximately "
            f"{estimated_rows_scanned:,} document rows across {batch_count:,} batches; "
            "increase --batch-size or use a maintenance window"
        )
    return RolloutCostEstimate(
        batch_count=batch_count,
        fingerprint_scan_count=fingerprint_scan_count,
        estimated_rows_scanned=estimated_rows_scanned,
        warning=warning,
    )


def _datetime_text(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat(timespec="microseconds")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


@contextmanager
def rollout_snapshot_session(engine: Engine) -> Iterator[Session]:
    """Yield one database-level stable snapshot for a complete rollout scan.

    SQLite uses ``BEGIN IMMEDIATE`` because pysqlite deferred read transactions
    can observe commits from other connections between SELECT statements. MySQL
    and PostgreSQL use an explicit REPEATABLE READ transaction. The caller may
    perform external read-only Milvus work while this maintenance transaction is
    open, but must not commit through the yielded Session.
    """
    connection = engine.connect()
    session: Session | None = None
    try:
        dialect = connection.dialect.name
        if dialect == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            if dialect in {"mysql", "mariadb", "postgresql"}:
                connection = connection.execution_options(isolation_level="REPEATABLE READ")
            connection.begin()
        session = Session(bind=connection, expire_on_commit=False)
        yield session
    finally:
        if session is not None:
            session.close()
        if connection.in_transaction():
            connection.rollback()
        connection.close()


def capture_document_snapshot(
    session: Session,
    *,
    tenant_id: str,
    dataset_id: str,
) -> DocumentSnapshot:
    """Fingerprint every rollout-relevant document fact in a dataset scope.

    This intentionally performs an O(N) scan. Rollout safety is more important
    than throughput: any insert, delete, ordering-key change, status change, or
    revision change invalidates the resume cursor and requires a fresh scan.
    """
    rows = session.execute(
        select(
            Document.id,
            Document.created_at,
            Document.updated_at,
            Document.content_revision,
            Document.desired_index_revision,
            Document.status,
        )
        .where(
            Document.tenant_id == tenant_id,
            Document.dataset_id == dataset_id,
        )
        .order_by(Document.id)
    )
    digest = hashlib.sha256()
    digest.update(_canonical({"tenant_id": tenant_id, "dataset_id": dataset_id}))
    count = 0
    for row in rows:
        digest.update(b"\n")
        digest.update(
            _canonical(
                {
                    "id": row.id,
                    "created_at": _datetime_text(row.created_at),
                    "updated_at": _datetime_text(row.updated_at),
                    "content_revision": int(row.content_revision or 0),
                    "desired_index_revision": int(row.desired_index_revision or 0),
                    "status": str(row.status or ""),
                }
            )
        )
        count += 1
    return DocumentSnapshot(count=count, fingerprint=digest.hexdigest())
