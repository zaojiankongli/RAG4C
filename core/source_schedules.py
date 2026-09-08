"""Authoritative fixed-interval source schedule repository and due-run outbox."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from core.db_clock import db_utc_expression, read_db_utc
from core.knowledge_governance import AuditContext, sanitize_audit_snapshot
from models.orm import (
    DataSourceRecord,
    Dataset,
    KnowledgeAuditEvent,
    SourceSchedule,
    SourceSyncRun,
)

_MIN_INTERVAL_SECONDS = 300
_MAX_INTERVAL_SECONDS = 604_800
_WRITABLE_STATUSES = frozenset({"active", "paused"})
_SYSTEM_SCHEDULER_ACTOR = "system:source-scheduler"


class SourceScheduleError(RuntimeError):
    pass


class SourceScheduleNotFound(SourceScheduleError):
    pass


class SourceScheduleConflict(SourceScheduleError):
    pass


@dataclass(frozen=True)
class DueScheduleRun:
    schedule_id: str
    run_id: str
    planned_at: datetime
    idempotency_key: str
    request_hash: str
    created: bool


@dataclass(frozen=True)
class _DueCandidate:
    id: str
    tenant_id: str
    dataset_id: str
    source_id: str
    revision: int


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _schedule_snapshot(schedule: SourceSchedule) -> dict[str, Any]:
    return sanitize_audit_snapshot(
        {
            "id": schedule.id,
            "tenant_id": schedule.tenant_id,
            "dataset_id": schedule.dataset_id,
            "source_id": schedule.source_id,
            "revision": int(schedule.revision),
            "status": schedule.status,
            "interval_seconds": int(schedule.interval_seconds),
            "force_full": bool(schedule.force_full),
            "next_run_at": schedule.next_run_at.isoformat(timespec="microseconds"),
            "last_enqueued_at": (
                None
                if schedule.last_enqueued_at is None
                else schedule.last_enqueued_at.isoformat(timespec="microseconds")
            ),
            "last_run_id": schedule.last_run_id,
            "created_by": schedule.created_by,
            "updated_by": schedule.updated_by,
            "created_at": schedule.created_at.isoformat(timespec="microseconds"),
            "updated_at": schedule.updated_at.isoformat(timespec="microseconds"),
            "capability": "fixed_interval_utc",
        }
    )


class SourceScheduleRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:16]}"

    @staticmethod
    def _validate_expected_revision(expected_revision: int) -> int:
        revision = int(expected_revision)
        if revision < 0:
            raise ValueError("expected_revision must be nonnegative")
        return revision

    @staticmethod
    def _validate_interval(interval_seconds: int) -> int:
        interval = int(interval_seconds)
        if not _MIN_INTERVAL_SECONDS <= interval <= _MAX_INTERVAL_SECONDS:
            raise ValueError(
                f"interval_seconds must be between {_MIN_INTERVAL_SECONDS} and "
                f"{_MAX_INTERVAL_SECONDS}"
            )
        return interval

    @staticmethod
    def _event(
        session: Session,
        *,
        schedule: SourceSchedule,
        audit: AuditContext,
        action: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        occurred_at: datetime,
    ) -> None:
        session.add(
            KnowledgeAuditEvent(
                id=SourceScheduleRepository._new_id("audit"),
                tenant_id=schedule.tenant_id,
                dataset_id=schedule.dataset_id,
                actor_id=audit.actor_id,
                action=action,
                resource_type="source_schedule",
                resource_id=schedule.id,
                before_snapshot=sanitize_audit_snapshot(before),
                after_snapshot=sanitize_audit_snapshot(after),
                request_id=audit.request_id,
                request_ip=audit.request_ip,
                occurred_at=occurred_at,
            )
        )

    @staticmethod
    def _for_update(query, dialect_name: str):
        if dialect_name in {"mysql", "mariadb", "postgresql"}:
            return query.with_for_update(skip_locked=True)
        return query.with_for_update()

    @classmethod
    def _lock_scope(
        cls,
        session: Session,
        tenant_id: str,
        dataset_id: str,
        source_id: str,
    ) -> tuple[Dataset, DataSourceRecord]:
        dialect = session.bind.dialect.name if session.bind is not None else ""
        dataset = session.scalar(
            cls._for_update(
                select(Dataset).where(
                    Dataset.id == dataset_id,
                    Dataset.tenant_id == tenant_id,
                ),
                dialect,
            )
        )
        if dataset is None:
            raise SourceScheduleNotFound("source or dataset does not exist")
        source = session.scalar(
            cls._for_update(
                select(DataSourceRecord).where(
                    DataSourceRecord.id == source_id,
                    DataSourceRecord.tenant_id == tenant_id,
                    DataSourceRecord.dataset_id == dataset_id,
                ),
                dialect,
            )
        )
        if source is None:
            raise SourceScheduleNotFound("source or dataset does not exist")
        return dataset, source

    @classmethod
    def _lock_schedule(
        cls,
        session: Session,
        tenant_id: str,
        dataset_id: str,
        source_id: str,
    ) -> SourceSchedule | None:
        dialect = session.bind.dialect.name if session.bind is not None else ""
        return session.scalar(
            cls._for_update(
                select(SourceSchedule).where(
                    SourceSchedule.tenant_id == tenant_id,
                    SourceSchedule.dataset_id == dataset_id,
                    SourceSchedule.source_id == source_id,
                ),
                dialect,
            )
        )

    @staticmethod
    def _begin_write(session: Session) -> None:
        if session.bind is not None and session.bind.dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")

    @staticmethod
    def _require_active_scope(dataset: Dataset, source: DataSourceRecord) -> None:
        if dataset.status != "active" or source.status != "active":
            raise SourceScheduleConflict("source or dataset is not active")

    def get_scoped(
        self,
        tenant_id: str,
        dataset_id: str,
        source_id: str,
    ) -> SourceSchedule:
        with Session(self.engine, expire_on_commit=False) as session:
            schedule = session.scalar(
                select(SourceSchedule).where(
                    SourceSchedule.tenant_id == tenant_id,
                    SourceSchedule.dataset_id == dataset_id,
                    SourceSchedule.source_id == source_id,
                )
            )
            if schedule is None:
                raise SourceScheduleNotFound("source schedule does not exist")
            return schedule

    def put(
        self,
        tenant_id: str,
        dataset_id: str,
        source_id: str,
        *,
        expected_revision: int,
        interval_seconds: int,
        force_full: bool,
        status: str | None,
        audit: AuditContext,
    ) -> SourceSchedule:
        expected = self._validate_expected_revision(expected_revision)
        interval = self._validate_interval(interval_seconds)
        requested_status = None if status is None else str(status)
        if requested_status is not None and requested_status not in _WRITABLE_STATUSES:
            raise ValueError("status must be active or paused")
        with Session(self.engine, expire_on_commit=False) as session:
            self._begin_write(session)
            dataset, source = self._lock_scope(session, tenant_id, dataset_id, source_id)
            self._require_active_scope(dataset, source)
            schedule = self._lock_schedule(session, tenant_id, dataset_id, source_id)
            now = read_db_utc(session)
            if schedule is None:
                if expected != 0:
                    raise SourceScheduleConflict("source schedule revision conflict")
                resolved_status = requested_status or "active"
                schedule = SourceSchedule(
                    id=self._new_id("source-schedule"),
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    source_id=source_id,
                    revision=1,
                    status=resolved_status,
                    interval_seconds=interval,
                    force_full=bool(force_full),
                    next_run_at=now + timedelta(seconds=interval),
                    last_enqueued_at=None,
                    last_run_id=None,
                    created_by=audit.actor_id,
                    updated_by=audit.actor_id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(schedule)
                session.flush()
                self._event(
                    session,
                    schedule=schedule,
                    audit=audit,
                    action="source.schedule.created",
                    before=None,
                    after=_schedule_snapshot(schedule),
                    occurred_at=now,
                )
            else:
                if expected == 0 or int(schedule.revision) != expected:
                    raise SourceScheduleConflict("source schedule revision conflict")
                before = _schedule_snapshot(schedule)
                schedule.revision = expected + 1
                schedule.status = requested_status or schedule.status
                schedule.interval_seconds = interval
                schedule.force_full = bool(force_full)
                schedule.next_run_at = now + timedelta(seconds=interval)
                schedule.updated_by = audit.actor_id
                schedule.updated_at = now
                self._event(
                    session,
                    schedule=schedule,
                    audit=audit,
                    action="source.schedule.updated",
                    before=before,
                    after=_schedule_snapshot(schedule),
                    occurred_at=now,
                )
            session.commit()
            return schedule

    def _transition(
        self,
        tenant_id: str,
        dataset_id: str,
        source_id: str,
        *,
        expected_revision: int,
        required_status: str | tuple[str, ...],
        target_status: str,
        action: str,
        audit: AuditContext,
        reset_next_slot: bool,
        require_active_scope: bool,
    ) -> SourceSchedule:
        expected = self._validate_expected_revision(expected_revision)
        allowed = {required_status} if isinstance(required_status, str) else set(required_status)
        with Session(self.engine, expire_on_commit=False) as session:
            self._begin_write(session)
            dataset, source = self._lock_scope(session, tenant_id, dataset_id, source_id)
            if require_active_scope:
                self._require_active_scope(dataset, source)
            schedule = self._lock_schedule(session, tenant_id, dataset_id, source_id)
            if schedule is None:
                raise SourceScheduleNotFound("source schedule does not exist")
            if int(schedule.revision) != expected:
                raise SourceScheduleConflict("source schedule revision conflict")
            if schedule.status not in allowed:
                required_label = " or ".join(sorted(allowed))
                raise SourceScheduleConflict(f"source schedule must be {required_label}")
            now = read_db_utc(session)
            before = _schedule_snapshot(schedule)
            schedule.revision = expected + 1
            schedule.status = target_status
            if reset_next_slot:
                schedule.next_run_at = now + timedelta(seconds=int(schedule.interval_seconds))
            schedule.updated_by = audit.actor_id
            schedule.updated_at = now
            self._event(
                session,
                schedule=schedule,
                audit=audit,
                action=action,
                before=before,
                after=_schedule_snapshot(schedule),
                occurred_at=now,
            )
            session.commit()
            return schedule

    def pause(
        self,
        tenant_id: str,
        dataset_id: str,
        source_id: str,
        *,
        expected_revision: int,
        audit: AuditContext,
    ) -> SourceSchedule:
        return self._transition(
            tenant_id,
            dataset_id,
            source_id,
            expected_revision=expected_revision,
            required_status="active",
            target_status="paused",
            action="source.schedule.paused",
            audit=audit,
            reset_next_slot=False,
            require_active_scope=False,
        )

    def resume(
        self,
        tenant_id: str,
        dataset_id: str,
        source_id: str,
        *,
        expected_revision: int,
        audit: AuditContext,
    ) -> SourceSchedule:
        return self._transition(
            tenant_id,
            dataset_id,
            source_id,
            expected_revision=expected_revision,
            required_status="paused",
            target_status="active",
            action="source.schedule.resumed",
            audit=audit,
            reset_next_slot=True,
            require_active_scope=True,
        )

    def archive(
        self,
        tenant_id: str,
        dataset_id: str,
        source_id: str,
        *,
        expected_revision: int,
        audit: AuditContext,
    ) -> SourceSchedule:
        return self._transition(
            tenant_id,
            dataset_id,
            source_id,
            expected_revision=expected_revision,
            required_status=("active", "paused"),
            target_status="archived",
            action="source.schedule.archived",
            audit=audit,
            reset_next_slot=False,
            require_active_scope=False,
        )

    @staticmethod
    def _next_future_slot(planned_at: datetime, now: datetime, interval_seconds: int) -> datetime:
        elapsed_seconds = max(0.0, (now - planned_at).total_seconds())
        steps = int(elapsed_seconds // interval_seconds) + 1
        return planned_at + timedelta(seconds=steps * interval_seconds)

    @staticmethod
    def _intent(schedule: SourceSchedule, planned_at: datetime) -> tuple[str, str]:
        planned_token = planned_at.strftime("%Y%m%dT%H%M%S.%f")
        key = f"schedule:{schedule.id}:r{int(schedule.revision)}:{planned_token}Z"
        request_hash = _fingerprint(
            {
                "capability": "fixed_interval_utc",
                "schedule_id": schedule.id,
                "schedule_revision": int(schedule.revision),
                "planned_at": planned_at.isoformat(timespec="microseconds"),
            }
        )
        return key, request_hash

    def _due_candidates(self, limit: int) -> list[_DueCandidate]:
        bounded = max(1, min(int(limit), 500))
        with Session(self.engine) as session:
            dialect = session.bind.dialect.name if session.bind is not None else ""
            rows = session.execute(
                select(
                    SourceSchedule.id,
                    SourceSchedule.tenant_id,
                    SourceSchedule.dataset_id,
                    SourceSchedule.source_id,
                    SourceSchedule.revision,
                )
                .where(
                    SourceSchedule.status == "active",
                    SourceSchedule.next_run_at <= db_utc_expression(dialect),
                )
                .order_by(SourceSchedule.next_run_at, SourceSchedule.id)
                .limit(bounded)
            ).all()
            return [_DueCandidate(*row) for row in rows]

    def _enqueue_candidate(self, candidate: _DueCandidate) -> DueScheduleRun | None:
        with Session(self.engine, expire_on_commit=False) as session:
            self._begin_write(session)
            try:
                dataset, source = self._lock_scope(
                    session,
                    candidate.tenant_id,
                    candidate.dataset_id,
                    candidate.source_id,
                )
            except SourceScheduleNotFound:
                session.commit()
                return None
            schedule = self._lock_schedule(
                session,
                candidate.tenant_id,
                candidate.dataset_id,
                candidate.source_id,
            )
            now = read_db_utc(session)
            if (
                schedule is None
                or schedule.id != candidate.id
                or int(schedule.revision) != int(candidate.revision)
                or schedule.status != "active"
                or dataset.status != "active"
                or source.status != "active"
                or schedule.next_run_at > now
            ):
                session.commit()
                return None

            planned_at = schedule.next_run_at
            next_run_at = self._next_future_slot(
                planned_at, now, int(schedule.interval_seconds)
            )
            idempotency_key, request_hash = self._intent(schedule, planned_at)
            existing = session.scalar(
                select(SourceSyncRun).where(
                    SourceSyncRun.source_id == source.id,
                    SourceSyncRun.idempotency_key == idempotency_key,
                )
            )
            created = existing is None
            if existing is None:
                run = SourceSyncRun(
                    id=self._new_id("sync-run"),
                    source_id=source.id,
                    tenant_id=schedule.tenant_id,
                    dataset_id=schedule.dataset_id,
                    status="running",
                    trigger="scheduled",
                    force_full=int(bool(schedule.force_full)),
                    dry_run=0,
                    source_generation=int(source.mutation_generation or 0),
                    dataset_generation=int(dataset.mutation_generation or 0),
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    retry_of_run_id=None,
                    schedule_id=schedule.id,
                    schedule_revision=int(schedule.revision),
                    planned_at=planned_at,
                    execution_state="pending",
                    execution_owner="",
                    execution_lease_until=None,
                    execution_heartbeat_at=None,
                    execution_attempts=0,
                    execution_last_error="",
                    execution_started_at=None,
                    execution_finished_at=None,
                    execution_next_attempt_at=now,
                    reservation_owner="",
                    reservation_lease_until=None,
                    reservation_attempts=0,
                    cursor_before=source.last_cursor or {},
                    started_at=now,
                    created_at=now,
                )
                session.add(run)
                session.flush()
            else:
                run = existing

            schedule.next_run_at = next_run_at
            schedule.last_enqueued_at = now
            schedule.last_run_id = run.id
            schedule.updated_by = _SYSTEM_SCHEDULER_ACTOR
            schedule.updated_at = now
            scheduler_request = f"schedule-enqueue-{request_hash[:32]}"
            self._event(
                session,
                schedule=schedule,
                audit=AuditContext.system(_SYSTEM_SCHEDULER_ACTOR, scheduler_request),
                action="source.schedule.enqueued",
                before={
                    "schedule_revision": int(schedule.revision),
                    "planned_at": planned_at.isoformat(timespec="microseconds"),
                },
                after={
                    "schedule_revision": int(schedule.revision),
                    "planned_at": planned_at.isoformat(timespec="microseconds"),
                    "next_run_at": next_run_at.isoformat(timespec="microseconds"),
                    "run_id": run.id,
                    "idempotency_key": idempotency_key,
                    "created": created,
                },
                occurred_at=now,
            )
            session.commit()
            return DueScheduleRun(
                schedule_id=schedule.id,
                run_id=run.id,
                planned_at=planned_at,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                created=created,
            )

    def enqueue_due(self, *, limit: int = 50) -> list[DueScheduleRun]:
        results: list[DueScheduleRun] = []
        for candidate in self._due_candidates(limit):
            result = self._enqueue_candidate(candidate)
            if result is not None:
                results.append(result)
        return results


__all__ = [
    "DueScheduleRun",
    "SourceScheduleConflict",
    "SourceScheduleError",
    "SourceScheduleNotFound",
    "SourceScheduleRepository",
]
