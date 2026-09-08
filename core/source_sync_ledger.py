"""Durable source configuration, sync runs, items and current state."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Engine, and_, func, literal, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.catalog import sanitize_error_message
from core.db_clock import db_utc_expression, read_db_utc
from core.knowledge_governance import AuditContext, sanitize_audit_snapshot
from models.orm import (
    DataSourceRecord,
    Dataset,
    KnowledgeAuditEvent,
    Document,
    DocumentIngestAttempt,
    IndexOperation,
    SourceDocumentState,
    SourceSchedule,
    SourceSyncItem,
    SourceSyncRun,
)

_SECRET_KEY = re.compile(r"(?:password|passwd|token|secret|api[_-]?key|credential)", re.I)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(password|passwd|token|secret|api[_-]?key|credential)\s*([=:])\s*([^\s,;]+)"
)
_SAFE_CREDENTIAL_REF = re.compile(r"^(?:secret|vault)://[^/?#\s]+(?:/[^?#\s]+)+$")


def _sanitize_config(value: Any, key: str = "") -> Any:
    normalized_key = str(key).strip().casefold().replace("-", "_")
    if normalized_key == "credential_ref":
        reference = str(value or "").strip()
        return reference if _SAFE_CREDENTIAL_REF.fullmatch(reference) else "<redacted>"
    if key and _SECRET_KEY.search(key):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(k): _sanitize_config(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_config(item) for item in value]
    return value


def _sanitize_source_error(value: Any) -> str:
    text = sanitize_error_message(str(value or ""))
    return _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text)


def sanitize_source_config(value: Any, key: str = "") -> Any:
    """Return connector configuration with inline credentials removed."""
    return _sanitize_config(value, key)


def sanitize_source_error(value: Any) -> str:
    """Return an operator-safe source error without paths or credential assignments."""
    return _sanitize_source_error(value)


def _sanitize_source_audit(value: Any, key: str = "") -> Any:
    normalized = str(key).strip().casefold().replace("-", "_")
    if normalized == "credential_ref":
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(child_key): _sanitize_source_audit(child, str(child_key))
            for child_key, child in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_source_audit(child, key) for child in value]
    return value


def _lease_deadline(
    database_now: datetime,
    explicit_deadline: datetime | None,
    lease_seconds: float | None,
) -> datetime:
    if explicit_deadline is not None:
        return explicit_deadline
    if lease_seconds is None or lease_seconds <= 0:
        raise ValueError("positive lease_seconds is required when deadline is omitted")
    return database_now + timedelta(seconds=lease_seconds)


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class SourceSyncLedgerConflict(RuntimeError):
    pass


class SourceSyncLedgerNotFound(SourceSyncLedgerConflict):
    pass


class SourceSyncIdempotencyConflict(SourceSyncLedgerConflict):
    pass


@dataclass(frozen=True)
class SourceRunRequestResult:
    run: SourceSyncRun
    created: bool


class SourceSyncLedger:
    def __init__(self, engine: Engine):
        self.engine = engine

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:16]}"

    @staticmethod
    def _source_snapshot(source: DataSourceRecord) -> dict[str, Any]:
        effective = source.effective_config if isinstance(source.effective_config, dict) else {}
        return sanitize_audit_snapshot(
            {
                "id": source.id,
                "tenant_id": source.tenant_id,
                "dataset_id": source.dataset_id,
                "name": source.name,
                "kind": source.source_type,
                "config": _sanitize_source_audit(effective.get("params", {})),
                "metadata": effective.get("metadata", {}),
                "status": source.status,
                "generation": int(source.mutation_generation or 0),
            }
        )

    @staticmethod
    def _event(
        session: Session,
        *,
        source: DataSourceRecord,
        audit: AuditContext,
        action: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        resource_id: str | None = None,
        resource_type: str = "knowledge_source",
    ) -> None:
        session.add(
            KnowledgeAuditEvent(
                id=SourceSyncLedger._new_id("audit"),
                tenant_id=source.tenant_id,
                dataset_id=source.dataset_id,
                actor_id=audit.actor_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id or source.id,
                before_snapshot=sanitize_audit_snapshot(before),
                after_snapshot=sanitize_audit_snapshot(after),
                request_id=audit.request_id,
                request_ip=audit.request_ip,
                occurred_at=datetime.utcnow(),
            )
        )

    @staticmethod
    def _lock_dataset_source(
        session: Session,
        tenant_id: str,
        dataset_id: str,
        source_id: str,
    ) -> tuple[Dataset, DataSourceRecord]:
        dataset = session.scalar(
            select(Dataset)
            .where(Dataset.id == dataset_id, Dataset.tenant_id == tenant_id)
            .with_for_update()
        )
        source = session.scalar(
            select(DataSourceRecord)
            .where(
                DataSourceRecord.id == source_id,
                DataSourceRecord.tenant_id == tenant_id,
                DataSourceRecord.dataset_id == dataset_id,
            )
            .with_for_update()
        )
        if dataset is None or source is None:
            raise SourceSyncLedgerNotFound("source or dataset does not exist")
        return dataset, source

    @classmethod
    def _lock_run_scope(
        cls,
        session: Session,
        run_id: str,
    ) -> tuple[Dataset, DataSourceRecord, SourceSyncRun]:
        scope = session.execute(
            select(
                SourceSyncRun.tenant_id,
                SourceSyncRun.dataset_id,
                SourceSyncRun.source_id,
            ).where(SourceSyncRun.id == run_id)
        ).one_or_none()
        if scope is None:
            raise SourceSyncLedgerNotFound("sync run does not exist")
        dataset, source = cls._lock_dataset_source(
            session, str(scope.tenant_id), str(scope.dataset_id), str(scope.source_id)
        )
        run = session.scalar(
            select(SourceSyncRun).where(SourceSyncRun.id == run_id).with_for_update()
        )
        if run is None:
            raise SourceSyncLedgerNotFound("sync run does not exist")
        return dataset, source, run

    @staticmethod
    def _scoped_source(
        session: Session,
        tenant_id: str,
        dataset_id: str,
        source_id: str,
        *,
        lock: bool = False,
    ) -> DataSourceRecord:
        query = select(DataSourceRecord).where(
            DataSourceRecord.id == source_id,
            DataSourceRecord.tenant_id == tenant_id,
            DataSourceRecord.dataset_id == dataset_id,
        )
        if lock:
            query = query.with_for_update()
        source = session.scalar(query)
        if source is None:
            raise SourceSyncLedgerNotFound("source does not exist")
        return source

    @staticmethod
    def _assert_execution_owner_in_session(
        run: SourceSyncRun,
        execution_owner: str | None,
        now: datetime,
    ) -> None:
        outbox_run = bool(run.idempotency_key or run.retry_of_run_id)
        if not outbox_run:
            return
        if (
            not execution_owner
            or run.execution_state != "executing"
            or run.execution_owner != execution_owner
            or run.execution_lease_until is None
            or run.execution_lease_until < now
        ):
            raise SourceSyncLedgerConflict("execution lease ownership changed")

    @staticmethod
    def _scoped_run(
        session: Session,
        tenant_id: str,
        dataset_id: str,
        source_id: str,
        run_id: str,
        *,
        lock: bool = False,
    ) -> SourceSyncRun:
        query = select(SourceSyncRun).where(
            SourceSyncRun.id == run_id,
            SourceSyncRun.source_id == source_id,
            SourceSyncRun.tenant_id == tenant_id,
            SourceSyncRun.dataset_id == dataset_id,
        )
        if lock:
            query = query.with_for_update()
        run = session.scalar(query)
        if run is None:
            raise SourceSyncLedgerNotFound("sync run does not exist")
        return run

    @staticmethod
    def _execution_authority_is_current(
        session: Session,
        dataset: Dataset,
        source: DataSourceRecord,
        run: SourceSyncRun,
    ) -> bool:
        schedule_matches = True
        if run.trigger == "scheduled":
            if (
                run.schedule_id is None
                or run.schedule_revision is None
                or run.planned_at is None
            ):
                schedule_matches = False
            else:
                schedule = session.scalar(
                    select(SourceSchedule)
                    .where(
                        SourceSchedule.id == run.schedule_id,
                        SourceSchedule.tenant_id == run.tenant_id,
                        SourceSchedule.dataset_id == run.dataset_id,
                        SourceSchedule.source_id == run.source_id,
                    )
                    .with_for_update()
                )
                schedule_matches = bool(
                    schedule is not None
                    and schedule.status == "active"
                    and int(schedule.revision) == int(run.schedule_revision)
                )
        return bool(
            dataset.status == "active"
            and source.status == "active"
            and source.tenant_id == run.tenant_id
            and source.dataset_id == run.dataset_id
            and int(source.mutation_generation or 0) == int(run.source_generation or 0)
            and int(dataset.mutation_generation or 0) == int(run.dataset_generation or 0)
            and schedule_matches
        )

    @staticmethod
    def _supersede_before_execution(run: SourceSyncRun, now: datetime) -> None:
        if run.status != "running":
            return
        run.status = "superseded"
        run.finished_at = now
        run.execution_state = "completed"
        run.execution_owner = ""
        run.execution_lease_until = None
        run.execution_heartbeat_at = None
        run.execution_next_attempt_at = None
        run.execution_finished_at = now
        run.reservation_owner = ""
        run.reservation_lease_until = None

    @classmethod
    def _advance_source_generation(
        cls,
        session: Session,
        source: DataSourceRecord,
        *,
        now: datetime,
    ) -> int:
        source.mutation_generation = int(source.mutation_generation or 0) + 1
        cls._supersede_generation_writers(
            session,
            now=now,
            source_id=source.id,
            source_generation=int(source.mutation_generation),
        )
        session.execute(
            update(SourceSyncRun)
            .where(
                SourceSyncRun.source_id == source.id,
                SourceSyncRun.status == "running",
            )
            .values(status="superseded", finished_at=now)
        )
        return int(source.mutation_generation)

    @staticmethod
    def _supersede_generation_writers(
        session: Session,
        *,
        now: datetime,
        source_id: str | None = None,
        source_generation: int | None = None,
        dataset_id: str | None = None,
        dataset_generation: int | None = None,
    ) -> None:
        query = (
            select(IndexOperation)
            .join(Document, Document.id == IndexOperation.document_id)
            .where(
                IndexOperation.status.in_(("pending", "retry", "claimed")),
                IndexOperation.operation.in_(("upsert", "reconcile")),
            )
        )
        if source_id is not None:
            query = query.where(Document.source_id == source_id)
        if dataset_id is not None:
            query = query.where(IndexOperation.dataset_id == dataset_id)
        attempt_ids: set[str] = set()
        for operation in session.scalars(query):
            payload = operation.payload if isinstance(operation.payload, dict) else {}
            stale_source = bool(
                source_generation is not None
                and payload.get("source_generation") != source_generation
            )
            stale_dataset = bool(
                dataset_generation is not None
                and payload.get("dataset_generation") != dataset_generation
            )
            if not stale_source and not stale_dataset:
                continue
            operation.status = "superseded"
            operation.claimed_by = ""
            operation.lease_until = None
            operation.finished_at = now
            attempt_ids.add(str(operation.attempt_id))
        if not attempt_ids:
            return
        session.flush()
        for attempt_id in attempt_ids:
            remaining = int(
                session.scalar(
                    select(func.count(IndexOperation.id)).where(
                        IndexOperation.attempt_id == attempt_id,
                        IndexOperation.status.in_(("pending", "retry", "claimed")),
                    )
                )
                or 0
            )
            if remaining:
                continue
            attempt = session.get(DocumentIngestAttempt, attempt_id)
            if attempt is not None and attempt.state not in {
                "completed",
                "failed",
                "cancelled",
                "superseded",
            }:
                attempt.state = "superseded"
                attempt.finished_at = now

    def ensure_source(self, spec: Any) -> DataSourceRecord:
        tenant_id = str(spec.tenant_id or "")
        raw_config = {
            "type": str(spec.type),
            "dataset_id": str(spec.dataset_id),
            "params": dict(spec.params or {}),
            "metadata": dict(spec.metadata or {}),
        }
        safe_config = _sanitize_config(raw_config)
        with Session(self.engine, expire_on_commit=False) as session:
            source = session.scalar(
                select(DataSourceRecord).where(
                    DataSourceRecord.tenant_id == tenant_id,
                    DataSourceRecord.name == spec.name,
                ).with_for_update()
            )
            fingerprint = _fingerprint(raw_config)
            next_status = "active" if spec.enabled else "disabled"
            if source is None:
                source = DataSourceRecord(
                    id=self._new_id("source"),
                    tenant_id=tenant_id,
                    dataset_id=spec.dataset_id,
                    name=spec.name,
                    source_type=spec.type,
                    mutation_generation=0,
                )
                session.add(source)
            else:
                changed = (
                    source.dataset_id != spec.dataset_id
                    or source.source_type != spec.type
                    or source.config_fingerprint != fingerprint
                    or source.status != next_status
                )
                if changed:
                    source.mutation_generation = int(source.mutation_generation or 0) + 1
                    now = datetime.utcnow()
                    self._supersede_generation_writers(
                        session,
                        now=now,
                        source_id=source.id,
                        source_generation=int(source.mutation_generation),
                    )
                    session.execute(
                        update(SourceSyncRun)
                        .where(
                            SourceSyncRun.source_id == source.id,
                            SourceSyncRun.status == "running",
                        )
                        .values(status="superseded", finished_at=now)
                    )
            source.dataset_id = spec.dataset_id
            source.source_type = spec.type
            source.effective_config = safe_config
            source.config_fingerprint = fingerprint
            source.status = next_status
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                source = session.scalar(
                    select(DataSourceRecord).where(
                        DataSourceRecord.tenant_id == tenant_id,
                        DataSourceRecord.name == spec.name,
                    )
                )
                if source is None:
                    raise
            return source

    def list_sources(
        self,
        tenant_id: str,
        dataset_id: str,
        *,
        status: str | None = None,
    ) -> list[DataSourceRecord]:
        with Session(self.engine, expire_on_commit=False) as session:
            query = select(DataSourceRecord).where(
                DataSourceRecord.tenant_id == tenant_id,
                DataSourceRecord.dataset_id == dataset_id,
            )
            if status is not None:
                query = query.where(DataSourceRecord.status == status)
            return list(query_result for query_result in session.scalars(query.order_by(DataSourceRecord.created_at.desc(), DataSourceRecord.id.desc())))

    def get_source_scoped(
        self, tenant_id: str, dataset_id: str, source_id: str
    ) -> DataSourceRecord:
        with Session(self.engine, expire_on_commit=False) as session:
            return self._scoped_source(session, tenant_id, dataset_id, source_id)

    def create_source(
        self,
        tenant_id: str,
        dataset_id: str,
        *,
        name: str,
        kind: str,
        config: dict[str, Any],
        metadata: dict[str, Any],
        enabled: bool,
        audit: AuditContext,
    ) -> DataSourceRecord:
        with Session(self.engine, expire_on_commit=False) as session:
            dataset = session.scalar(
                select(Dataset).where(
                    Dataset.id == dataset_id,
                    Dataset.tenant_id == tenant_id,
                    Dataset.status == "active",
                )
            )
            if dataset is None:
                raise SourceSyncLedgerNotFound("dataset does not exist")
            effective_config = _sanitize_config(
                {
                    "type": kind,
                    "dataset_id": dataset_id,
                    "params": dict(config),
                    "metadata": dict(metadata),
                }
            )
            source = DataSourceRecord(
                id=self._new_id("source"),
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                name=name,
                source_type=kind,
                effective_config=effective_config,
                config_fingerprint=_fingerprint(effective_config),
                status="active" if enabled else "disabled",
                mutation_generation=0,
            )
            session.add(source)
            self._event(
                session,
                source=source,
                audit=audit,
                action="source.create",
                before=None,
                after=self._source_snapshot(source),
            )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise SourceSyncLedgerConflict("source name already exists") from exc
            return source

    def update_source(
        self,
        tenant_id: str,
        dataset_id: str,
        source_id: str,
        *,
        expected_generation: int,
        name: str | None = None,
        kind: str | None = None,
        config: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        audit: AuditContext,
    ) -> DataSourceRecord:
        with Session(self.engine, expire_on_commit=False) as session:
            _dataset, source = self._lock_dataset_source(
                session, tenant_id, dataset_id, source_id
            )
            if int(source.mutation_generation or 0) != int(expected_generation):
                raise SourceSyncLedgerConflict("source generation conflict")
            before = self._source_snapshot(source)
            effective = source.effective_config if isinstance(source.effective_config, dict) else {}
            next_kind = kind if kind is not None else source.source_type
            next_config = dict(config) if config is not None else dict(effective.get("params") or {})
            next_metadata = (
                dict(metadata) if metadata is not None else dict(effective.get("metadata") or {})
            )
            next_effective = _sanitize_config(
                {
                    "type": next_kind,
                    "dataset_id": dataset_id,
                    "params": next_config,
                    "metadata": next_metadata,
                }
            )
            if name is not None:
                source.name = name
            source.source_type = next_kind
            source.effective_config = next_effective
            source.config_fingerprint = _fingerprint(next_effective)
            self._advance_source_generation(session, source, now=read_db_utc(session))
            self._event(
                session,
                source=source,
                audit=audit,
                action="source.update",
                before=before,
                after=self._source_snapshot(source),
            )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise SourceSyncLedgerConflict("source name already exists") from exc
            return source

    def set_source_enabled(
        self,
        tenant_id: str,
        dataset_id: str,
        source_id: str,
        *,
        expected_generation: int,
        enabled: bool,
        audit: AuditContext,
    ) -> DataSourceRecord:
        with Session(self.engine, expire_on_commit=False) as session:
            _dataset, source = self._lock_dataset_source(
                session, tenant_id, dataset_id, source_id
            )
            if int(source.mutation_generation or 0) != int(expected_generation):
                raise SourceSyncLedgerConflict("source generation conflict")
            target = "active" if enabled else "disabled"
            if source.status == target:
                raise SourceSyncLedgerConflict(f"source is already {target}")
            before = self._source_snapshot(source)
            source.status = target
            self._advance_source_generation(session, source, now=read_db_utc(session))
            action = "source.enable" if enabled else "source.disable"
            self._event(
                session,
                source=source,
                audit=audit,
                action=action,
                before=before,
                after=self._source_snapshot(source),
            )
            session.commit()
            return source

    def request_run(
        self,
        tenant_id: str,
        dataset_id: str,
        source_id: str,
        *,
        trigger: str,
        force_full: bool,
        dry_run: bool,
        audit: AuditContext,
        idempotency_key: str,
        request_hash: str,
        cursor_before: dict[str, Any] | None = None,
    ) -> SourceRunRequestResult:
        with Session(self.engine, expire_on_commit=False) as session:
            dataset, source = self._lock_dataset_source(
                session, tenant_id, dataset_id, source_id
            )
            if source.status != "active" or dataset.status != "active":
                raise SourceSyncLedgerConflict("source or dataset is not active")
            generation = int(source.mutation_generation or 0)
            dataset_generation = int(dataset.mutation_generation or 0)
            existing = session.scalar(
                select(SourceSyncRun).where(
                    SourceSyncRun.source_id == source.id,
                    SourceSyncRun.source_generation == generation,
                    SourceSyncRun.dataset_generation == dataset_generation,
                    SourceSyncRun.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise SourceSyncIdempotencyConflict(
                        "idempotency key was already used with a different request"
                    )
                return SourceRunRequestResult(run=existing, created=False)
            request_time = read_db_utc(session)
            run = SourceSyncRun(
                id=self._new_id("sync-run"),
                source_id=source.id,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                status="running",
                trigger=trigger,
                force_full=int(force_full),
                dry_run=int(dry_run),
                source_generation=generation,
                dataset_generation=dataset_generation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                execution_state="pending",
                execution_owner="",
                execution_lease_until=None,
                execution_heartbeat_at=None,
                execution_attempts=0,
                execution_last_error="",
                execution_started_at=None,
                execution_finished_at=None,
                execution_next_attempt_at=request_time,
                reservation_owner="",
                reservation_lease_until=None,
                reservation_attempts=0,
                cursor_before=cursor_before or source.last_cursor or {},
            )
            session.add(run)
            self._event(
                session,
                source=source,
                audit=audit,
                action="source.sync.request",
                resource_type="source_sync_run",
                resource_id=run.id,
                before=None,
                after={
                    "source_id": source.id,
                    "run_id": run.id,
                    "trigger": trigger,
                    "force_full": bool(force_full),
                    "dry_run": bool(dry_run),
                    "source_generation": run.source_generation,
                    "dataset_generation": run.dataset_generation,
                    "idempotency_key": idempotency_key,
                    "request_hash": request_hash,
                },
            )
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                existing = session.scalar(
                    select(SourceSyncRun).where(
                        SourceSyncRun.source_id == source_id,
                        SourceSyncRun.source_generation == generation,
                        SourceSyncRun.dataset_generation == dataset_generation,
                        SourceSyncRun.idempotency_key == idempotency_key,
                    )
                )
                if existing is None:
                    raise
                if existing.request_hash != request_hash:
                    raise SourceSyncIdempotencyConflict(
                        "idempotency key was already used with a different request"
                    ) from exc
                return SourceRunRequestResult(run=existing, created=False)
            return SourceRunRequestResult(run=run, created=True)

    def get_run(self, run_id: str) -> SourceSyncRun:
        with Session(self.engine, expire_on_commit=False) as session:
            run = session.get(SourceSyncRun, run_id)
            if run is None:
                raise SourceSyncLedgerNotFound("sync run does not exist")
            return run

    def get_run_scoped(
        self,
        tenant_id: str,
        dataset_id: str,
        source_id: str,
        run_id: str,
    ) -> SourceSyncRun:
        with Session(self.engine, expire_on_commit=False) as session:
            self._scoped_source(session, tenant_id, dataset_id, source_id)
            return self._scoped_run(session, tenant_id, dataset_id, source_id, run_id)

    def list_runs_scoped(
        self,
        tenant_id: str,
        dataset_id: str,
        source_id: str,
        *,
        status: str | None = None,
        trigger: str | None = None,
        before_started_at: datetime | None = None,
        before_id: str | None = None,
        limit: int = 100,
    ) -> list[SourceSyncRun]:
        with Session(self.engine, expire_on_commit=False) as session:
            self._scoped_source(session, tenant_id, dataset_id, source_id)
            query = select(SourceSyncRun).where(
                SourceSyncRun.source_id == source_id,
                SourceSyncRun.tenant_id == tenant_id,
                SourceSyncRun.dataset_id == dataset_id,
            )
            if status is not None:
                query = query.where(SourceSyncRun.status == status)
            if trigger is not None:
                query = query.where(SourceSyncRun.trigger == trigger)
            if before_started_at is not None and before_id is not None:
                query = query.where(
                    or_(
                        SourceSyncRun.started_at < before_started_at,
                        and_(
                            SourceSyncRun.started_at == before_started_at,
                            SourceSyncRun.id < before_id,
                        ),
                    )
                )
            query = query.order_by(
                SourceSyncRun.started_at.desc(), SourceSyncRun.id.desc()
            ).limit(max(1, min(int(limit), 201)))
            return list(session.scalars(query))

    def list_items_scoped(
        self,
        tenant_id: str,
        dataset_id: str,
        source_id: str,
        run_id: str,
        *,
        result: str | None = None,
        action: str | None = None,
        before_created_at: datetime | None = None,
        before_id: str | None = None,
        limit: int = 200,
    ) -> list[SourceSyncItem]:
        with Session(self.engine, expire_on_commit=False) as session:
            self._scoped_run(session, tenant_id, dataset_id, source_id, run_id)
            query = select(SourceSyncItem).where(
                SourceSyncItem.run_id == run_id,
                SourceSyncItem.source_id == source_id,
            )
            if result is not None:
                query = query.where(SourceSyncItem.result == result)
            if action is not None:
                query = query.where(SourceSyncItem.action == action)
            if before_created_at is not None and before_id is not None:
                query = query.where(
                    or_(
                        SourceSyncItem.created_at < before_created_at,
                        and_(
                            SourceSyncItem.created_at == before_created_at,
                            SourceSyncItem.id < before_id,
                        ),
                    )
                )
            query = query.order_by(
                SourceSyncItem.created_at.desc(), SourceSyncItem.id.desc()
            ).limit(max(1, min(int(limit), 501)))
            return list(session.scalars(query))

    def retry_failed_run(
        self,
        tenant_id: str,
        dataset_id: str,
        source_id: str,
        run_id: str,
        *,
        audit: AuditContext,
    ) -> SourceRunRequestResult:
        with Session(self.engine, expire_on_commit=False) as session:
            dataset, source = self._lock_dataset_source(
                session, tenant_id, dataset_id, source_id
            )
            failed = self._scoped_run(
                session, tenant_id, dataset_id, source_id, run_id, lock=True
            )
            existing = session.scalar(
                select(SourceSyncRun).where(SourceSyncRun.retry_of_run_id == failed.id)
            )
            if existing is not None:
                return SourceRunRequestResult(run=existing, created=False)
            if failed.status != "failed":
                raise SourceSyncLedgerConflict("only failed sync runs can be retried")
            if source.status != "active" or dataset.status != "active":
                raise SourceSyncLedgerConflict("source or dataset is not active")
            if (
                int(failed.source_generation or 0) != int(source.mutation_generation or 0)
                or int(failed.dataset_generation or 0) != int(dataset.mutation_generation or 0)
            ):
                raise SourceSyncLedgerConflict("failed sync run generation is stale")
            request_time = read_db_utc(session)
            retry = SourceSyncRun(
                id=self._new_id("sync-run"),
                source_id=source.id,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                status="running",
                trigger="retry",
                force_full=int(failed.force_full or 0),
                dry_run=int(failed.dry_run or 0),
                source_generation=int(source.mutation_generation or 0),
                dataset_generation=int(dataset.mutation_generation or 0),
                retry_of_run_id=failed.id,
                execution_state="pending",
                execution_owner="",
                execution_lease_until=None,
                execution_heartbeat_at=None,
                execution_attempts=0,
                execution_last_error="",
                execution_started_at=None,
                execution_finished_at=None,
                execution_next_attempt_at=request_time,
                reservation_owner="",
                reservation_lease_until=None,
                reservation_attempts=0,
                cursor_before=failed.cursor_before or source.last_cursor or {},
            )
            session.add(retry)
            self._event(
                session,
                source=source,
                audit=audit,
                action="source.sync.retry",
                resource_type="source_sync_run",
                resource_id=retry.id,
                before={"retry_of_run_id": failed.id, "status": failed.status},
                after={
                    "run_id": retry.id,
                    "retry_of_run_id": failed.id,
                    "status": retry.status,
                    "source_generation": retry.source_generation,
                    "dataset_generation": retry.dataset_generation,
                },
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.scalar(
                    select(SourceSyncRun).where(SourceSyncRun.retry_of_run_id == run_id)
                )
                if existing is None:
                    raise
                return SourceRunRequestResult(run=existing, created=False)
            return SourceRunRequestResult(run=retry, created=True)

    def reserve_dispatch_batch(
        self,
        *,
        owner: str,
        now: datetime | None = None,
        lease_until: datetime | None = None,
        lease_seconds: float | None = None,
        limit: int = 100,
    ) -> list[str]:
        reservation_owner = str(owner or "").strip()
        if not reservation_owner or len(reservation_owner) > 128:
            raise ValueError("valid reservation owner is required")
        with Session(self.engine, expire_on_commit=False) as session:
            dialect = session.bind.dialect.name if session.bind is not None else ""
            if dialect == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            clock = literal(now) if now is not None else db_utc_expression(dialect)
            query = (
                select(SourceSyncRun)
                .where(
                    SourceSyncRun.status == "running",
                    or_(
                        SourceSyncRun.execution_next_attempt_at.is_(None),
                        SourceSyncRun.execution_next_attempt_at <= clock,
                    ),
                    or_(
                        SourceSyncRun.reservation_owner == "",
                        SourceSyncRun.reservation_lease_until.is_(None),
                        SourceSyncRun.reservation_lease_until < clock,
                    ),
                    or_(
                        SourceSyncRun.execution_state.in_(("pending", "failed")),
                        and_(
                            SourceSyncRun.execution_state == "executing",
                            SourceSyncRun.execution_lease_until < clock,
                        ),
                    ),
                )
                .order_by(
                    SourceSyncRun.execution_next_attempt_at,
                    SourceSyncRun.created_at,
                    SourceSyncRun.id,
                )
                .limit(max(1, min(int(limit), 1000)))
            )
            if dialect != "sqlite":
                query = query.with_for_update(skip_locked=True)
            rows = list(session.scalars(query))
            database_now = now or read_db_utc(session)
            deadline = _lease_deadline(database_now, lease_until, lease_seconds)
            for run in rows:
                run.reservation_owner = reservation_owner
                run.reservation_lease_until = deadline
                run.reservation_attempts = int(run.reservation_attempts or 0) + 1
            session.commit()
            return [run.id for run in rows]

    def reserve_run_dispatch(
        self,
        run_id: str,
        *,
        owner: str,
        now: datetime | None = None,
        lease_until: datetime | None = None,
        lease_seconds: float | None = None,
    ) -> bool:
        with Session(self.engine, expire_on_commit=False) as session:
            if session.bind is not None and session.bind.dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            dataset, source, run = self._lock_run_scope(session, run_id)
            database_now = now or read_db_utc(session)
            if not self._execution_authority_is_current(session, dataset, source, run):
                self._supersede_before_execution(run, database_now)
                session.commit()
                return False
            deadline = _lease_deadline(database_now, lease_until, lease_seconds)
            eligible = bool(
                run.status == "running"
                and (run.execution_next_attempt_at is None or run.execution_next_attempt_at <= database_now)
                and (
                    not run.reservation_owner
                    or run.reservation_lease_until is None
                    or run.reservation_lease_until < database_now
                )
                and (
                    run.execution_state in {"pending", "failed"}
                    or (
                        run.execution_state == "executing"
                        and run.execution_lease_until is not None
                        and run.execution_lease_until < database_now
                    )
                )
            )
            if not eligible:
                session.commit()
                return False
            run.reservation_owner = owner
            run.reservation_lease_until = deadline
            run.reservation_attempts = int(run.reservation_attempts or 0) + 1
            session.commit()
            return True

    def claim_execution(
        self,
        run_id: str,
        *,
        owner: str,
        now: datetime | None = None,
        lease_until: datetime | None = None,
        lease_seconds: float | None = None,
        reservation_owner: str | None = None,
    ) -> SourceSyncRun | None:
        execution_owner = str(owner or "").strip()
        if not execution_owner or len(execution_owner) > 128:
            raise ValueError("valid execution owner is required")
        with Session(self.engine, expire_on_commit=False) as session:
            if session.bind is not None and session.bind.dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            dataset, source, run = self._lock_run_scope(session, run_id)
            database_now = now or read_db_utc(session)
            if not self._execution_authority_is_current(session, dataset, source, run):
                self._supersede_before_execution(run, database_now)
                session.commit()
                return None
            deadline = _lease_deadline(database_now, lease_until, lease_seconds)
            if deadline <= database_now:
                raise ValueError("execution lease must extend into the future")
            reservation_valid = bool(
                not run.reservation_owner
                or (
                    reservation_owner == run.reservation_owner
                    and run.reservation_lease_until is not None
                    and run.reservation_lease_until >= database_now
                )
                or (
                    run.reservation_lease_until is not None
                    and run.reservation_lease_until < database_now
                )
            )
            eligible = bool(
                run.status == "running"
                and reservation_valid
                and (run.execution_next_attempt_at is None or run.execution_next_attempt_at <= database_now)
                and (
                    run.execution_state in {"pending", "failed"}
                    or (
                        run.execution_state == "executing"
                        and run.execution_lease_until is not None
                        and run.execution_lease_until < database_now
                    )
                )
            )
            if not eligible:
                session.commit()
                return None
            run.execution_state = "executing"
            run.execution_owner = execution_owner
            run.execution_lease_until = deadline
            run.execution_heartbeat_at = database_now
            run.execution_attempts = int(run.execution_attempts or 0) + 1
            run.execution_last_error = ""
            run.execution_next_attempt_at = None
            run.execution_started_at = run.execution_started_at or database_now
            run.reservation_owner = ""
            run.reservation_lease_until = None
            run.execution_finished_at = None
            session.commit()
            return run

    def heartbeat_execution(
        self,
        run_id: str,
        *,
        owner: str,
        now: datetime | None = None,
        lease_until: datetime | None = None,
        lease_seconds: float | None = None,
    ) -> SourceSyncRun:
        with Session(self.engine, expire_on_commit=False) as session:
            if session.bind is not None and session.bind.dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            _dataset, _source, _run = self._lock_run_scope(session, run_id)
            database_now = now or read_db_utc(session)
            deadline = _lease_deadline(database_now, lease_until, lease_seconds)
            if deadline <= database_now:
                raise ValueError("heartbeat lease must extend into the future")
            result = session.execute(
                update(SourceSyncRun)
                .where(
                    SourceSyncRun.id == run_id,
                    SourceSyncRun.status == "running",
                    SourceSyncRun.execution_state == "executing",
                    SourceSyncRun.execution_owner == owner,
                    SourceSyncRun.execution_lease_until >= database_now,
                )
                .values(execution_heartbeat_at=database_now, execution_lease_until=deadline)
            )
            if result.rowcount != 1:
                session.rollback()
                raise SourceSyncLedgerConflict("execution lease ownership changed")
            session.commit()
            run = session.get(SourceSyncRun, run_id)
            if run is None:
                raise SourceSyncLedgerNotFound("sync run does not exist")
            return run

    def mark_execution_failed(
        self,
        run_id: str,
        *,
        owner: str,
        error: str,
        now: datetime | None = None,
        retry_base_seconds: float = 1.0,
        retry_max_seconds: float = 300.0,
        retry_jitter_ratio: float = 0.2,
        max_attempts: int = 10,
    ) -> SourceSyncRun:
        with Session(self.engine, expire_on_commit=False) as session:
            if session.bind is not None and session.bind.dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            _dataset, _source, run = self._lock_run_scope(session, run_id)
            database_now = now or read_db_utc(session)
            attempt = max(1, int(run.execution_attempts or 0))
            terminal = max_attempts > 0 and attempt >= max_attempts
            digest = hashlib.sha256(f"{run_id}:{attempt}".encode("utf-8")).digest()
            unit = int.from_bytes(digest[:8], "big") / float(2**64 - 1)
            jitter = (unit * 2.0 - 1.0) * max(0.0, min(retry_jitter_ratio, 1.0))
            base_delay = min(
                max(retry_base_seconds, 0.01) * (2 ** max(0, attempt - 1)),
                max(retry_max_seconds, 0.01),
            )
            next_attempt = None if terminal else database_now + timedelta(seconds=max(0.01, base_delay * (1 + jitter)))
            sanitized_error = _sanitize_source_error(error)[:2000]
            failure_values: dict[str, Any] = {
                "status": "failed" if terminal else "running",
                "execution_state": "completed" if terminal else "failed",
                "execution_owner": "",
                "execution_lease_until": None,
                "execution_heartbeat_at": None,
                "execution_last_error": sanitized_error,
                "execution_finished_at": database_now,
                "execution_next_attempt_at": next_attempt,
            }
            if terminal:
                failure_values.update(
                    finished_at=database_now,
                    fetch_error=sanitized_error,
                )
            result = session.execute(
                update(SourceSyncRun)
                .where(
                    SourceSyncRun.id == run_id,
                    SourceSyncRun.status == "running",
                    SourceSyncRun.execution_state == "executing",
                    SourceSyncRun.execution_owner == owner,
                    SourceSyncRun.execution_lease_until >= database_now,
                )
.values(**failure_values)
            )
            if result.rowcount != 1:
                session.rollback()
                raise SourceSyncLedgerConflict("execution lease ownership changed")
            session.commit()
            run = session.get(SourceSyncRun, run_id)
            if run is None:
                raise SourceSyncLedgerNotFound("sync run does not exist")
            return run

    def assert_execution_owned(
        self,
        run_id: str,
        *,
        owner: str,
        now: datetime | None = None,
    ) -> SourceSyncRun:
        with Session(self.engine, expire_on_commit=False) as session:
            check_time = now or read_db_utc(session)
            run = session.get(SourceSyncRun, run_id)
            if run is None:
                raise SourceSyncLedgerNotFound("sync run does not exist")
            if (
                run.status != "running"
                or run.execution_state != "executing"
                or run.execution_owner != owner
                or run.execution_lease_until is None
                or run.execution_lease_until < check_time
            ):
                raise SourceSyncLedgerConflict("execution lease ownership changed")
            return run

    def list_dispatchable_run_ids(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> list[str]:
        with Session(self.engine) as session:
            database_now = now or read_db_utc(session)
            return list(
                session.scalars(
                    select(SourceSyncRun.id)
                    .where(
                        SourceSyncRun.status == "running",
                        or_(
                            SourceSyncRun.execution_next_attempt_at.is_(None),
                            SourceSyncRun.execution_next_attempt_at <= database_now,
                        ),
                        or_(
                            SourceSyncRun.reservation_owner == "",
                            SourceSyncRun.reservation_lease_until.is_(None),
                            SourceSyncRun.reservation_lease_until < database_now,
                        ),
                        or_(
                            SourceSyncRun.execution_state.in_(("pending", "failed")),
                            and_(
                                SourceSyncRun.execution_state == "executing",
                                SourceSyncRun.execution_lease_until < database_now,
                            ),
                        ),
                    )
                    .order_by(SourceSyncRun.created_at, SourceSyncRun.id)
                    .limit(max(1, min(int(limit), 1000)))
                )
            )

    def find_source(self, tenant_id: str, name: str) -> DataSourceRecord | None:
        with Session(self.engine, expire_on_commit=False) as session:
            return session.scalar(
                select(DataSourceRecord).where(
                    DataSourceRecord.tenant_id == tenant_id,
                    DataSourceRecord.name == name,
                )
            )

    def get_source(self, source_id: str) -> DataSourceRecord:
        with Session(self.engine, expire_on_commit=False) as session:
            source = session.get(DataSourceRecord, source_id)
            if source is None:
                raise SourceSyncLedgerConflict("source does not exist")
            return source

    def start_run(
        self,
        source_id: str,
        *,
        trigger: str,
        force_full: bool,
        dry_run: bool,
        cursor_before: dict[str, Any] | None = None,
    ) -> SourceSyncRun:
        with Session(self.engine, expire_on_commit=False) as session:
            source = session.get(DataSourceRecord, source_id)
            if source is None:
                raise SourceSyncLedgerConflict("source does not exist")
            dataset = session.scalar(
                select(Dataset).where(
                    Dataset.id == source.dataset_id, Dataset.tenant_id == source.tenant_id
                )
            )
            if source.status != "active" or dataset is None or dataset.status != "active":
                raise SourceSyncLedgerConflict("source or dataset is not active")
            run = SourceSyncRun(
                id=self._new_id("sync-run"),
                source_id=source.id,
                tenant_id=source.tenant_id,
                dataset_id=source.dataset_id,
                status="running",
                trigger=trigger,
                force_full=int(force_full),
                dry_run=int(dry_run),
                source_generation=int(source.mutation_generation or 0),
                dataset_generation=int(dataset.mutation_generation or 0),
                cursor_before=cursor_before or {},
            )
            session.add(run)
            session.commit()
            return run

    def assert_run_current(self, run_id: str) -> SourceSyncRun:
        with Session(self.engine, expire_on_commit=False) as session:
            run = session.get(SourceSyncRun, run_id)
            if run is None:
                raise SourceSyncLedgerConflict("sync run does not exist")
            source = session.get(DataSourceRecord, run.source_id)
            dataset = session.scalar(
                select(Dataset).where(
                    Dataset.id == run.dataset_id, Dataset.tenant_id == run.tenant_id
                )
            )
            if (
                run.status != "running"
                or source is None
                or dataset is None
                or source.status != "active"
                or source.tenant_id != run.tenant_id
                or source.dataset_id != run.dataset_id
                or dataset.tenant_id != run.tenant_id
                or dataset.status != "active"
                or int(source.mutation_generation or 0) != int(run.source_generation or 0)
                or int(dataset.mutation_generation or 0) != int(run.dataset_generation or 0)
            ):
                raise SourceSyncLedgerConflict("source sync generation changed")
            return run

    def bump_source_generation(self, source_id: str, *, reason: str = "") -> int:
        del reason
        with Session(self.engine, expire_on_commit=False) as session:
            scope = session.execute(
                select(
                    DataSourceRecord.tenant_id,
                    DataSourceRecord.dataset_id,
                ).where(DataSourceRecord.id == source_id)
            ).one_or_none()
            if scope is None:
                raise SourceSyncLedgerConflict("source does not exist")
            _dataset, source = self._lock_dataset_source(
                session, str(scope.tenant_id), str(scope.dataset_id), source_id
            )
            source.mutation_generation = int(source.mutation_generation or 0) + 1
            now = read_db_utc(session)
            self._supersede_generation_writers(
                session,
                now=now,
                source_id=source.id,
                source_generation=int(source.mutation_generation),
            )
            session.execute(
                update(SourceSyncRun)
                .where(
                    SourceSyncRun.source_id == source.id,
                    SourceSyncRun.status == "running",
                )
                .values(status="superseded", finished_at=now)
            )
            session.commit()
            return int(source.mutation_generation)

    def bump_reset_generations(self, source_id: str) -> tuple[int, int]:
        with Session(self.engine, expire_on_commit=False) as session:
            scope = session.execute(
                select(
                    DataSourceRecord.tenant_id,
                    DataSourceRecord.dataset_id,
                ).where(DataSourceRecord.id == source_id)
            ).one_or_none()
            if scope is None:
                raise SourceSyncLedgerConflict("source does not exist")
            dataset, source = self._lock_dataset_source(
                session, str(scope.tenant_id), str(scope.dataset_id), source_id
            )
            source.mutation_generation = int(source.mutation_generation or 0) + 1
            dataset.mutation_generation = int(dataset.mutation_generation or 0) + 1
            now = read_db_utc(session)
            self._supersede_generation_writers(
                session,
                now=now,
                source_id=source.id,
                source_generation=int(source.mutation_generation),
            )
            self._supersede_generation_writers(
                session,
                now=now,
                dataset_id=dataset.id,
                dataset_generation=int(dataset.mutation_generation),
            )
            session.execute(
                update(SourceSyncRun)
                .where(
                    SourceSyncRun.source_id == source.id,
                    SourceSyncRun.status == "running",
                )
                .values(status="superseded", finished_at=now)
            )
            session.commit()
            return int(source.mutation_generation), int(dataset.mutation_generation)

    def record_item(
        self,
        run_id: str,
        *,
        external_id: str,
        doc_id: str,
        source_uri: str,
        content_hash: str,
        action: str,
        result: str,
        chunk_count: int = 0,
        error_code: str = "",
        error_message: str = "",
        execution_owner: str | None = None,
        now: datetime | None = None,
    ) -> SourceSyncItem:
        with Session(self.engine, expire_on_commit=False) as session:
            run = session.scalar(
                select(SourceSyncRun).where(SourceSyncRun.id == run_id).with_for_update()
            )
            if run is None:
                raise SourceSyncLedgerConflict("sync run does not exist")
            self._assert_execution_owner_in_session(
                run, execution_owner, now or read_db_utc(session)
            )
            item = session.scalar(
                select(SourceSyncItem).where(
                    SourceSyncItem.run_id == run_id,
                    SourceSyncItem.external_id == external_id,
                )
            )
            if item is not None:
                if item.result == "failed" and result != "failed":
                    item.doc_id = doc_id
                    item.source_uri = source_uri
                    item.content_hash = content_hash
                    item.action = action
                    item.result = result
                    item.chunk_count = max(0, int(chunk_count))
                    item.error_code = error_code[:64]
                    item.error_message = _sanitize_source_error(error_message)[:2000]
                    session.commit()
                return item
            item = SourceSyncItem(
                id=self._new_id("sync-item"),
                run_id=run.id,
                source_id=run.source_id,
                external_id=external_id,
                doc_id=doc_id,
                source_uri=source_uri,
                content_hash=content_hash,
                action=action,
                result=result,
                chunk_count=max(0, int(chunk_count)),
                error_code=error_code[:64],
                error_message=_sanitize_source_error(error_message)[:2000],
            )
            session.add(item)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                item = session.scalar(
                    select(SourceSyncItem).where(
                        SourceSyncItem.run_id == run_id,
                        SourceSyncItem.external_id == external_id,
                    )
                )
                if item is None:
                    raise
            return item

    def list_items(self, run_id: str) -> list[SourceSyncItem]:
        with Session(self.engine, expire_on_commit=False) as session:
            return list(
                session.scalars(
                    select(SourceSyncItem)
                    .where(SourceSyncItem.run_id == run_id)
                    .order_by(SourceSyncItem.created_at, SourceSyncItem.id)
                )
            )

    def list_runs(self, source_id: str) -> list[SourceSyncRun]:
        with Session(self.engine, expire_on_commit=False) as session:
            return list(
                session.scalars(
                    select(SourceSyncRun)
                    .where(SourceSyncRun.source_id == source_id)
                    .order_by(SourceSyncRun.started_at, SourceSyncRun.id)
                )
            )

    def upsert_state(
        self,
        source_id: str,
        *,
        doc_id: str,
        external_id: str,
        source_uri: str,
        content_hash: str,
        chunk_count: int,
        run_id: str,
        document_generation: int | None = None,
        execution_owner: str | None = None,
        now: datetime | None = None,
    ) -> SourceDocumentState:
        with Session(self.engine, expire_on_commit=False) as session:
            run = session.scalar(
                select(SourceSyncRun).where(SourceSyncRun.id == run_id).with_for_update()
            )
            if run is not None:
                self._assert_execution_owner_in_session(
                    run, execution_owner, now or read_db_utc(session)
                )
            source = session.get(DataSourceRecord, source_id)
            document = session.get(Document, doc_id)
            dataset = session.get(Dataset, run.dataset_id) if run is not None else None
            if (
                run is None
                or run.status != "running"
                or source is None
                or document is None
                or dataset is None
                or run.source_id != source_id
                or source.status != "active"
                or source.tenant_id != run.tenant_id
                or source.dataset_id != run.dataset_id
                or dataset.tenant_id != run.tenant_id
                or dataset.status != "active"
                or int(source.mutation_generation or 0) != int(run.source_generation or 0)
                or int(dataset.mutation_generation or 0) != int(run.dataset_generation or 0)
                or document.tenant_id != run.tenant_id
                or document.dataset_id != run.dataset_id
                or document.lifecycle_state != "active"
                or not bool(document.retrieval_enabled)
                or bool(document.active_delete_operation_id)
            ):
                raise SourceSyncLedgerConflict("source sync generation changed")
            expected_document_generation = (
                int(document.mutation_generation or 0)
                if document_generation is None
                else int(document_generation)
            )
            if int(document.mutation_generation or 0) != expected_document_generation:
                raise SourceSyncLedgerConflict("document generation changed")
            state = session.scalar(
                select(SourceDocumentState).where(
                    SourceDocumentState.source_id == source_id,
                    SourceDocumentState.doc_id == doc_id,
                ).with_for_update()
            )
            if state is not None and state.state in {"operator_suppressed", "delete_pending"}:
                raise SourceSyncLedgerConflict("source document is suppressed")
            if state is None:
                state = SourceDocumentState(
                    id=self._new_id("source-state"), source_id=source_id, doc_id=doc_id
                )
                session.add(state)
            state.external_id = external_id
            state.source_uri = source_uri
            state.content_hash = content_hash
            state.chunk_count = max(0, int(chunk_count))
            state.last_run_id = run_id
            state.state = "active"
            state.document_generation = expected_document_generation
            state.delete_operation_id = None
            state.suppressed_at = None
            session.commit()
            return state

    def delete_state(self, source_id: str, doc_id: str) -> None:
        """Retain a source tombstone instead of deleting source identity truth."""
        with Session(self.engine) as session:
            state = session.scalar(
                select(SourceDocumentState).where(
                    SourceDocumentState.source_id == source_id,
                    SourceDocumentState.doc_id == doc_id,
                ).with_for_update()
            )
            if state is not None:
                state.state = "upstream_absent"
                session.commit()

    def load_state(self, source_id: str) -> dict[str, dict[str, Any]]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(SourceDocumentState).where(
                    SourceDocumentState.source_id == source_id
                )
            )
            return {
                row.doc_id: {
                    "hash": row.content_hash,
                    "uri": row.source_uri,
                    "chunks": row.chunk_count,
                    "external_id": row.external_id,
                    "state": row.state,
                    "document_generation": int(row.document_generation or 0),
                    "delete_operation_id": row.delete_operation_id,
                }
                for row in rows
            }

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        cursor_after: dict[str, Any] | None,
        counts: dict[str, int],
        fetch_error: str = "",
        duration_ms: int = 0,
        execution_owner: str | None = None,
    ) -> SourceSyncRun:
        with Session(self.engine, expire_on_commit=False) as session:
            if session.bind is not None and session.bind.dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            dataset, source, run = self._lock_run_scope(session, run_id)
            if run.status != "running":
                session.commit()
                return run
            now = read_db_utc(session)
            outbox_run = bool(run.idempotency_key or run.retry_of_run_id)
            if outbox_run:
                if (
                    not execution_owner
                    or run.execution_state != "executing"
                    or run.execution_owner != execution_owner
                    or run.execution_lease_until is None
                    or run.execution_lease_until < now
                ):
                    raise SourceSyncLedgerConflict("execution lease ownership changed")
            elif execution_owner is not None:
                raise SourceSyncLedgerConflict("legacy sync run has no execution lease")
            current = bool(
                source is not None
                and dataset is not None
                and source.status == "active"
                and source.tenant_id == run.tenant_id
                and source.dataset_id == run.dataset_id
                and dataset.status == "active"
                and int(source.mutation_generation or 0) == int(run.source_generation or 0)
                and int(dataset.mutation_generation or 0) == int(run.dataset_generation or 0)
            )
            if not current:
                values: dict[str, Any] = {"status": "superseded", "finished_at": now}
                if outbox_run:
                    values.update(
                        execution_state="completed",
                        execution_owner="",
                        execution_lease_until=None,
                        execution_heartbeat_at=None,
                        execution_finished_at=now,
                    )
                session.execute(
                    update(SourceSyncRun)
                    .where(SourceSyncRun.id == run.id, SourceSyncRun.status == "running")
                    .values(**values)
                )
                session.commit()
                persisted = session.get(SourceSyncRun, run.id)
                if persisted is None:
                    raise SourceSyncLedgerConflict("sync run does not exist")
                return persisted
            values = {
                "status": status,
                "cursor_after": cursor_after or {},
                "fetch_error": _sanitize_source_error(fetch_error)[:2000],
                "duration_ms": max(0, int(duration_ms)),
                "finished_at": now,
            }
            for field in (
                "fetched",
                "ingested",
                "skipped",
                "removed",
                "pending_deletes",
                "chunks",
                "failed",
            ):
                values[field] = max(0, int(counts.get(field, 0)))
            conditions = [SourceSyncRun.id == run.id, SourceSyncRun.status == "running"]
            if outbox_run:
                conditions.extend(
                    [
                        SourceSyncRun.execution_state == "executing",
                        SourceSyncRun.execution_owner == execution_owner,
                        SourceSyncRun.execution_lease_until >= now,
                    ]
                )
                values.update(
                    execution_state="completed",
                    execution_owner="",
                    execution_lease_until=None,
                    execution_heartbeat_at=None,
                    execution_last_error="",
                    execution_finished_at=now,
                )
            result = session.execute(
                update(SourceSyncRun).where(*conditions).values(**values)
            )
            if result.rowcount != 1:
                session.rollback()
                raise SourceSyncLedgerConflict("execution lease ownership changed")
            if source is not None:
                source.last_cursor = values["cursor_after"]
                source.last_result = {
                    field: values[field]
                    for field in (
                        "fetched",
                        "ingested",
                        "skipped",
                        "removed",
                        "pending_deletes",
                        "chunks",
                        "failed",
                    )
                }
                source.last_error = values["fetch_error"]
                source.last_sync_at = now
            session.commit()
            persisted = session.get(SourceSyncRun, run.id)
            if persisted is None:
                raise SourceSyncLedgerConflict("sync run does not exist")
            return persisted
