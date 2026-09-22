"""Enterprise audit compliance control-plane services for complete 0023 catalogs."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
from typing import Any
import uuid

from sqlalchemy import MetaData, Table, delete, inspect, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.audit_export_formats import (
    AuditExportFormatSpec,
    audit_export_format_names,
    register_audit_export_format,
    resolve_audit_export_format,
)
from core.enterprise_tenant_idempotency import (
    complete_tenant_mutation,
    engine_serialization_lock,
    idempotency_key_lock,
    reserve_tenant_mutation,
    tenant_idempotency_key_digest,
    tenant_request_hash,
)
from models.orm import Account, Tenant, TenantAuditEvent, TenantMember

_REVISION = "0023_enterprise_audit_compliance"
_REQUIRED_TABLES = {
    "accounts",
    "tenants",
    "tenant_members",
    "tenant_audit_events",
    "tenant_control_mutation_requests",
    "tenant_audit_retention_policies",
    "tenant_audit_legal_holds",
    "tenant_audit_export_jobs",
    "alembic_version",
}
_REQUIRED_COLUMNS = {
    "tenant_audit_retention_policies": {
        "id",
        "tenant_id",
        "audit_retention_days",
        "export_retention_days",
        "status",
        "revision",
        "last_preview_at",
        "last_execution_at",
        "last_executed_by",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
    },
    "tenant_audit_legal_holds": {
        "id",
        "tenant_id",
        "name",
        "active_name_key",
        "reason",
        "status",
        "sequence_from",
        "sequence_to",
        "time_from",
        "time_to",
        "revision",
        "created_at",
        "created_by",
        "released_at",
        "released_by",
        "updated_at",
        "updated_by",
    },
    "tenant_audit_export_jobs": {
        "id",
        "tenant_id",
        "format",
        "status",
        "filters_json",
        "sequence_from",
        "sequence_to",
        "time_from",
        "time_to",
        "object_key",
        "sha256",
        "byte_size",
        "row_count",
        "revision",
        "requested_at",
        "requested_by",
        "completed_at",
        "expires_at",
    },
}
_SECRET_RE = re.compile(
    r"(?i)(?:sk_live|pat[_-]|ghp_|github_pat_|xox[baprs]-|bearer\s+|cloud[_-]?token|api[_-]?key)"
)
_ALLOWED_FILTERS = frozenset({"action", "resource_type", "actor_id"})
_EXPORT_FIELDS = (
    "sequence",
    "id",
    "actor_id",
    "actor_name",
    "actor_email",
    "action",
    "resource_type",
    "resource_id",
    "target_account_id",
    "before_snapshot",
    "after_snapshot",
    "request_id",
    "occurred_at",
)


class ComplianceError(RuntimeError):
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class ComplianceMigrationRequired(ComplianceError):
    def __init__(self) -> None:
        super().__init__("compliance_migration_required", "审计合规需要完整 0023 数据库结构", 503)


class ComplianceForbidden(ComplianceError):
    pass


class ComplianceConflict(ComplianceError):
    pass


class ComplianceNotFound(ComplianceError):
    def __init__(self, message: str = "合规资源不存在") -> None:
        super().__init__("compliance_resource_not_found", message, 404)


class ComplianceValidation(ComplianceError):
    pass


@dataclass(frozen=True)
class ServiceResult:
    body: dict[str, Any]
    status: int = 200


@dataclass(frozen=True)
class DownloadResult:
    content: bytes
    media_type: str
    filename: str


@dataclass(frozen=True)
class _Actor:
    tenant_id: str
    account_id: str
    role: str
    name: str
    email: str


@dataclass(frozen=True)
class _Preview:
    cutoff: datetime
    candidate_ids: tuple[int, ...]
    protected_ids: tuple[int, ...]
    fingerprint: str
    sequence_from: int | None
    sequence_to: int | None


def _table(session: Session, name: str) -> Table:
    return Table(name, MetaData(), autoload_with=session.connection())


@contextmanager
def _transaction(session: Session) -> Iterator[None]:
    if str(session.get_bind().dialect.name) != "sqlite":
        with session.begin():
            yield
        return
    connection = session.connection()
    connection.exec_driver_sql("PRAGMA busy_timeout=30000")
    connection.exec_driver_sql("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        session.rollback()
        raise
    else:
        session.commit()


def _ensure_0023(connection: Any) -> None:
    try:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        if _REQUIRED_TABLES - tables:
            raise ComplianceMigrationRequired()
        revisions = tuple(
            str(value)
            for value in connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalars()
        )
        if revisions != (_REVISION,):
            raise ComplianceMigrationRequired()
        for table_name, expected in _REQUIRED_COLUMNS.items():
            actual = {str(item["name"]) for item in inspector.get_columns(table_name)}
            if expected - actual:
                raise ComplianceMigrationRequired()
    except ComplianceMigrationRequired:
        raise
    except Exception as exc:
        raise ComplianceMigrationRequired() from exc


def _clean(value: Any, field: str, maximum: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ComplianceValidation("compliance_request_invalid", f"{field} 必须是字符串", 422)
    result = value.strip()
    if (not result and not allow_empty) or len(result) > maximum:
        raise ComplianceValidation("compliance_request_invalid", f"{field} 无效", 422)
    if any(ord(character) < 32 and character not in "\t\n\r" for character in result):
        raise ComplianceValidation("compliance_request_invalid", f"{field} 无效", 422)
    return result


def _reason(value: Any) -> str:
    result = _clean(value, "reason", 512)
    return "[REDACTED]" if _SECRET_RE.search(result) else result


def _positive_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or value < minimum or value > maximum:
        raise ComplianceValidation(
            "compliance_request_invalid",
            f"{field} 必须在 {minimum} 到 {maximum} 之间",
            422,
        )
    return value


def _optional_int(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 1:
        raise ComplianceValidation("compliance_request_invalid", f"{field} 无效", 422)
    return value


def _optional_time(value: Any, field: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if not isinstance(value, str):
        raise ComplianceValidation("compliance_request_invalid", f"{field} 无效", 422)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError as exc:
        raise ComplianceValidation("compliance_request_invalid", f"{field} 无效", 422) from exc


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _actor(session: Session, tenant_id: str, actor_id: str) -> _Actor:
    row = session.execute(
        select(
            TenantMember.role,
            TenantMember.status,
            Account.name,
            Account.email,
            Tenant.status.label("tenant_status"),
        )
        .join(Account, Account.id == TenantMember.account_id)
        .join(Tenant, Tenant.id == TenantMember.tenant_id)
        .where(TenantMember.tenant_id == tenant_id, TenantMember.account_id == actor_id)
        .with_for_update()
    ).one_or_none()
    if (
        row is None
        or str(row.status).casefold() != "active"
        or str(row.tenant_status).casefold() != "active"
    ):
        raise ComplianceForbidden("compliance_forbidden", "当前身份无权访问审计合规中心", 403)
    role = str(row.role).casefold()
    if role not in {"owner", "admin", "editor", "member"}:
        raise ComplianceMigrationRequired()
    return _Actor(tenant_id, actor_id, role, str(row.name or ""), str(row.email or ""))


def _require_owner(actor: _Actor) -> None:
    if actor.role != "owner":
        raise ComplianceForbidden("compliance_owner_required", "该操作仅租户 owner 可执行", 403)


def _require_manager(actor: _Actor) -> None:
    if actor.role not in {"owner", "admin"}:
        raise ComplianceForbidden(
            "compliance_manager_required", "该操作仅租户 owner/admin 可执行", 403
        )


def _policy_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "audit_retention_days": int(row["audit_retention_days"]),
        "export_retention_days": int(row["export_retention_days"]),
        "status": str(row["status"]),
        "revision": int(row["revision"]),
        "last_preview_at": _iso(row.get("last_preview_at")),
        "last_execution_at": _iso(row.get("last_execution_at")),
        "updated_at": _iso(row["updated_at"]),
    }


def _hold_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "name": str(row["name"]),
        "reason": str(row["reason"]),
        "status": str(row["status"]),
        "sequence_from": int(row["sequence_from"])
        if row.get("sequence_from") is not None
        else None,
        "sequence_to": int(row["sequence_to"]) if row.get("sequence_to") is not None else None,
        "time_from": _iso(row.get("time_from")),
        "time_to": _iso(row.get("time_to")),
        "revision": int(row["revision"]),
        "created_at": _iso(row["created_at"]),
        "released_at": _iso(row.get("released_at")),
    }


def _export_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "format": str(row["format"]),
        "status": str(row["status"]),
        "filters": _json_value(row["filters_json"]),
        "sequence_from": int(row["sequence_from"])
        if row.get("sequence_from") is not None
        else None,
        "sequence_to": int(row["sequence_to"]) if row.get("sequence_to") is not None else None,
        "time_from": _iso(row.get("time_from")),
        "time_to": _iso(row.get("time_to")),
        "sha256": str(row["sha256"]) if row.get("sha256") else None,
        "byte_size": int(row["byte_size"]) if row.get("byte_size") is not None else None,
        "row_count": int(row["row_count"]) if row.get("row_count") is not None else None,
        "revision": int(row["revision"]),
        "requested_at": _iso(row["requested_at"]),
        "completed_at": _iso(row.get("completed_at")),
        "expires_at": _iso(row.get("expires_at")),
    }


def _audit(
    session: Session,
    *,
    actor: _Actor,
    action: str,
    resource_type: str,
    resource_id: str,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    request_id: str,
    request_ip: str,
    now: datetime,
) -> None:
    session.add(
        TenantAuditEvent(
            id=f"tenant-audit-{uuid.uuid4().hex}",
            tenant_id=actor.tenant_id,
            actor_id=actor.account_id,
            actor_name_snapshot=actor.name[:128],
            actor_email_snapshot=actor.email[:256],
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            target_account_id=None,
            before_snapshot=dict(before) if before is not None else None,
            after_snapshot=dict(after) if after is not None else None,
            request_id=str(request_id or "")[:128],
            request_ip=str(request_ip or "")[:64],
            occurred_at=now,
        )
    )
    session.flush()


def _reserve(
    session: Session,
    *,
    actor: _Actor,
    key: str,
    request_hash: str,
    operation: str,
    resource_type: str,
):
    reservation = reserve_tenant_mutation(
        session,
        tenant_id=actor.tenant_id,
        actor_id=actor.account_id,
        raw_idempotency_key=key,
        request_hash=request_hash,
        operation=operation,
        resource_type=resource_type,
    )
    if reservation.replay is not None:
        return reservation, ServiceResult(
            reservation.replay.response,
            reservation.replay.http_status,
        )
    return reservation, None


def _mutation_context(engine: Any, tenant_id: str, actor_id: str, key: str):
    digest = tenant_idempotency_key_digest(tenant_id, actor_id, key)
    return idempotency_key_lock(tenant_id, actor_id, digest), engine_serialization_lock(engine)


def get_retention_policy(engine: Any, *, tenant_id: str, actor_id: str) -> ServiceResult:
    with Session(engine) as session:
        _ensure_0023(session.connection())
        _actor(session, tenant_id, actor_id)
        table = _table(session, "tenant_audit_retention_policies")
        row = (
            session.execute(select(table).where(table.c.tenant_id == tenant_id))
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ComplianceNotFound("审计保留策略不存在")
        return ServiceResult(
            {"policy": _policy_payload(row), "execution": {"mode": "manual_execution_only"}}
        )


def update_retention_policy(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    audit_retention_days: int,
    export_retention_days: int,
    status: str,
    expected_revision: int,
    reason: str,
    idempotency_key: str,
    request_id: str,
    request_ip: str,
    now: datetime,
) -> ServiceResult:
    audit_days = _positive_int(audit_retention_days, "audit_retention_days", 30, 3650)
    export_days = _positive_int(export_retention_days, "export_retention_days", 1, 365)
    clean_status = _clean(status, "status", 16).casefold()
    if clean_status not in {"active", "paused"}:
        raise ComplianceValidation("compliance_request_invalid", "status 无效", 422)
    revision = _positive_int(expected_revision, "revision", 1, 2_147_483_647)
    safe_reason = _reason(reason)
    request_hash = tenant_request_hash(
        operation="compliance.retention_policy.update",
        path_identity={"tenant_id": tenant_id},
        body={
            "audit_retention_days": audit_days,
            "export_retention_days": export_days,
            "status": clean_status,
            "revision": revision,
            "reason": safe_reason,
        },
    )
    key_lock, engine_lock = _mutation_context(engine, tenant_id, actor_id, idempotency_key)
    with key_lock, engine_lock, Session(engine) as session:
        with _transaction(session):
            _ensure_0023(session.connection())
            actor = _actor(session, tenant_id, actor_id)
            _require_owner(actor)
            reservation, replay = _reserve(
                session,
                actor=actor,
                key=idempotency_key,
                request_hash=request_hash,
                operation="compliance.retention_policy.update",
                resource_type="tenant_audit_retention_policy",
            )
            if replay is not None:
                return replay
            table = _table(session, "tenant_audit_retention_policies")
            current = (
                session.execute(
                    select(table).where(table.c.tenant_id == tenant_id).with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if current is None:
                raise ComplianceNotFound("审计保留策略不存在")
            if int(current["revision"]) != revision:
                raise ComplianceConflict(
                    "compliance_revision_conflict", "策略 revision 已变化", 409
                )
            session.execute(
                update(table)
                .where(table.c.id == current["id"], table.c.revision == revision)
                .values(
                    audit_retention_days=audit_days,
                    export_retention_days=export_days,
                    status=clean_status,
                    revision=revision + 1,
                    updated_at=now,
                    updated_by=actor_id,
                )
            )
            updated_row = (
                session.execute(select(table).where(table.c.id == current["id"])).mappings().one()
            )
            payload = {
                "policy": _policy_payload(updated_row),
                "execution": {"mode": "manual_execution_only"},
            }
            _audit(
                session,
                actor=actor,
                action="compliance.retention_policy.updated",
                resource_type="tenant_audit_retention_policy",
                resource_id=str(current["id"]),
                before=_policy_payload(current),
                after={**_policy_payload(updated_row), "reason": safe_reason},
                request_id=request_id,
                request_ip=request_ip,
                now=now,
            )
            complete_tenant_mutation(
                session,
                reservation,
                response_for_replay=payload,
                http_status=200,
                resource_id=str(current["id"]),
            )
            return ServiceResult(payload)


def _hold_matches(row: Mapping[str, Any], hold: Mapping[str, Any]) -> bool:
    sequence = int(row["sequence"])
    occurred = row["occurred_at"]
    if isinstance(occurred, str):
        occurred = datetime.fromisoformat(occurred.replace("Z", "+00:00")).replace(tzinfo=None)
    sequence_from = hold.get("sequence_from")
    sequence_to = hold.get("sequence_to")
    time_from = hold.get("time_from")
    time_to = hold.get("time_to")
    if isinstance(time_from, str):
        time_from = datetime.fromisoformat(time_from.replace("Z", "+00:00")).replace(tzinfo=None)
    if isinstance(time_to, str):
        time_to = datetime.fromisoformat(time_to.replace("Z", "+00:00")).replace(tzinfo=None)
    return not (
        (sequence_from is not None and sequence < int(sequence_from))
        or (sequence_to is not None and sequence > int(sequence_to))
        or (time_from is not None and occurred < time_from)
        or (time_to is not None and occurred > time_to)
    )


def _preview(
    session: Session, *, tenant_id: str, policy: Mapping[str, Any], now: datetime
) -> _Preview:
    cutoff = now - timedelta(days=int(policy["audit_retention_days"]))
    rows = list(
        session.execute(
            select(TenantAuditEvent.sequence, TenantAuditEvent.occurred_at)
            .where(
                TenantAuditEvent.tenant_id == tenant_id,
                TenantAuditEvent.occurred_at < cutoff,
            )
            .order_by(TenantAuditEvent.sequence)
        ).mappings()
    )
    holds_table = _table(session, "tenant_audit_legal_holds")
    holds = list(
        session.execute(
            select(holds_table).where(
                holds_table.c.tenant_id == tenant_id,
                holds_table.c.status == "active",
            )
        ).mappings()
    )
    protected: list[int] = []
    candidates: list[int] = []
    for row in rows:
        sequence = int(row["sequence"])
        if any(_hold_matches(row, hold) for hold in holds):
            protected.append(sequence)
        else:
            candidates.append(sequence)
    canonical = json.dumps(
        {
            "tenant_id": tenant_id,
            "policy_revision": int(policy["revision"]),
            "cutoff": _iso(cutoff),
            "candidate_ids": candidates,
            "protected_ids": protected,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _Preview(
        cutoff=cutoff,
        candidate_ids=tuple(candidates),
        protected_ids=tuple(protected),
        fingerprint=hashlib.sha256(canonical).hexdigest(),
        sequence_from=min(candidates) if candidates else None,
        sequence_to=max(candidates) if candidates else None,
    )


def preview_retention(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    now: datetime,
) -> ServiceResult:
    with Session(engine) as session:
        _ensure_0023(session.connection())
        actor = _actor(session, tenant_id, actor_id)
        _require_manager(actor)
        table = _table(session, "tenant_audit_retention_policies")
        policy = (
            session.execute(select(table).where(table.c.tenant_id == tenant_id))
            .mappings()
            .one_or_none()
        )
        if policy is None:
            raise ComplianceNotFound("审计保留策略不存在")
        preview = _preview(session, tenant_id=tenant_id, policy=policy, now=now)
        return ServiceResult(
            {
                "preview": {
                    "policy_revision": int(policy["revision"]),
                    "cutoff": _iso(preview.cutoff),
                    "candidate_count": len(preview.candidate_ids),
                    "protected_count": len(preview.protected_ids),
                    "sequence_from": preview.sequence_from,
                    "sequence_to": preview.sequence_to,
                    "fingerprint": preview.fingerprint,
                },
                "execution": {"mode": "manual_execution_only"},
            }
        )


def execute_retention(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    expected_revision: int,
    preview_fingerprint: str,
    confirmation: str,
    reason: str,
    idempotency_key: str,
    request_id: str,
    request_ip: str,
    now: datetime,
) -> ServiceResult:
    revision = _positive_int(expected_revision, "revision", 1, 2_147_483_647)
    fingerprint = _clean(preview_fingerprint, "preview_fingerprint", 64).casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ComplianceValidation("compliance_request_invalid", "preview_fingerprint 无效", 422)
    if _clean(confirmation, "confirmation", 64) != "DELETE AUDIT EVENTS":
        raise ComplianceValidation("compliance_confirmation_invalid", "危险操作确认文本不匹配", 422)
    safe_reason = _reason(reason)
    request_hash = tenant_request_hash(
        operation="compliance.retention.execute",
        path_identity={"tenant_id": tenant_id},
        body={
            "revision": revision,
            "preview_fingerprint": fingerprint,
            "confirmation": "DELETE AUDIT EVENTS",
            "reason": safe_reason,
        },
    )
    key_lock, engine_lock = _mutation_context(engine, tenant_id, actor_id, idempotency_key)
    with key_lock, engine_lock, Session(engine) as session:
        with _transaction(session):
            _ensure_0023(session.connection())
            actor = _actor(session, tenant_id, actor_id)
            _require_owner(actor)
            reservation, replay = _reserve(
                session,
                actor=actor,
                key=idempotency_key,
                request_hash=request_hash,
                operation="compliance.retention.execute",
                resource_type="tenant_audit_retention_policy",
            )
            if replay is not None:
                return replay
            table = _table(session, "tenant_audit_retention_policies")
            policy = (
                session.execute(
                    select(table).where(table.c.tenant_id == tenant_id).with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if policy is None:
                raise ComplianceNotFound("审计保留策略不存在")
            if int(policy["revision"]) != revision:
                raise ComplianceConflict(
                    "compliance_revision_conflict", "策略 revision 已变化", 409
                )
            preview = _preview(session, tenant_id=tenant_id, policy=policy, now=now)
            if preview.fingerprint != fingerprint:
                raise ComplianceConflict(
                    "compliance_preview_fingerprint_conflict",
                    "保留预览已变化，请重新预览",
                    409,
                )
            if preview.candidate_ids:
                session.execute(
                    delete(TenantAuditEvent).where(
                        TenantAuditEvent.tenant_id == tenant_id,
                        TenantAuditEvent.sequence.in_(preview.candidate_ids),
                    )
                )
            session.execute(
                update(table)
                .where(table.c.id == policy["id"], table.c.revision == revision)
                .values(
                    revision=revision + 1,
                    last_execution_at=now,
                    last_executed_by=actor_id,
                    updated_at=now,
                    updated_by=actor_id,
                )
            )
            payload = {
                "execution": {
                    "deleted_count": len(preview.candidate_ids),
                    "protected_count": len(preview.protected_ids),
                    "cutoff": _iso(preview.cutoff),
                    "preview_fingerprint": preview.fingerprint,
                    "policy_revision": revision + 1,
                    "mode": "manual_execution_only",
                }
            }
            _audit(
                session,
                actor=actor,
                action="compliance.retention.executed",
                resource_type="tenant_audit_retention_policy",
                resource_id=str(policy["id"]),
                before={
                    "policy_revision": revision,
                    "candidate_count": len(preview.candidate_ids),
                    "protected_count": len(preview.protected_ids),
                },
                after={**payload["execution"], "reason": safe_reason},
                request_id=request_id,
                request_ip=request_ip,
                now=now,
            )
            complete_tenant_mutation(
                session,
                reservation,
                response_for_replay=payload,
                http_status=200,
                resource_id=str(policy["id"]),
            )
            return ServiceResult(payload)


def list_legal_holds(engine: Any, *, tenant_id: str, actor_id: str) -> ServiceResult:
    with Session(engine) as session:
        _ensure_0023(session.connection())
        _actor(session, tenant_id, actor_id)
        table = _table(session, "tenant_audit_legal_holds")
        rows = session.execute(
            select(table)
            .where(table.c.tenant_id == tenant_id)
            .order_by(table.c.updated_at.desc(), table.c.id)
        ).mappings()
        items = [_hold_payload(row) for row in rows]
        return ServiceResult({"items": items, "count": len(items)})


def create_legal_hold(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    name: str,
    reason: str,
    sequence_from: int | None,
    sequence_to: int | None,
    time_from: datetime | str | None,
    time_to: datetime | str | None,
    idempotency_key: str,
    request_id: str,
    request_ip: str,
    now: datetime,
) -> ServiceResult:
    clean_name = _clean(name, "name", 128)
    safe_reason = _reason(reason)
    seq_from = _optional_int(sequence_from, "sequence_from")
    seq_to = _optional_int(sequence_to, "sequence_to")
    from_time = _optional_time(time_from, "time_from")
    to_time = _optional_time(time_to, "time_to")
    if seq_from is not None and seq_to is not None and seq_from > seq_to:
        raise ComplianceValidation("compliance_request_invalid", "sequence 范围无效", 422)
    if from_time is not None and to_time is not None and from_time > to_time:
        raise ComplianceValidation("compliance_request_invalid", "time 范围无效", 422)
    request_hash = tenant_request_hash(
        operation="compliance.legal_hold.create",
        path_identity={"tenant_id": tenant_id},
        body={
            "name": clean_name,
            "reason": safe_reason,
            "sequence_from": seq_from,
            "sequence_to": seq_to,
            "time_from": _iso(from_time),
            "time_to": _iso(to_time),
        },
    )
    key_lock, engine_lock = _mutation_context(engine, tenant_id, actor_id, idempotency_key)
    try:
        with key_lock, engine_lock, Session(engine) as session:
            with _transaction(session):
                _ensure_0023(session.connection())
                actor = _actor(session, tenant_id, actor_id)
                _require_owner(actor)
                reservation, replay = _reserve(
                    session,
                    actor=actor,
                    key=idempotency_key,
                    request_hash=request_hash,
                    operation="compliance.legal_hold.create",
                    resource_type="tenant_audit_legal_hold",
                )
                if replay is not None:
                    return replay
                table = _table(session, "tenant_audit_legal_holds")
                hold_id = f"audit-hold-{uuid.uuid4().hex}"
                session.execute(
                    table.insert().values(
                        id=hold_id,
                        tenant_id=tenant_id,
                        name=clean_name,
                        active_name_key=clean_name.casefold(),
                        reason=safe_reason,
                        status="active",
                        sequence_from=seq_from,
                        sequence_to=seq_to,
                        time_from=from_time,
                        time_to=to_time,
                        revision=1,
                        created_at=now,
                        created_by=actor_id,
                        released_at=None,
                        released_by=None,
                        updated_at=now,
                        updated_by=actor_id,
                    )
                )
                row = session.execute(select(table).where(table.c.id == hold_id)).mappings().one()
                payload = {"legal_hold": _hold_payload(row)}
                _audit(
                    session,
                    actor=actor,
                    action="compliance.legal_hold.created",
                    resource_type="tenant_audit_legal_hold",
                    resource_id=hold_id,
                    before=None,
                    after=_hold_payload(row),
                    request_id=request_id,
                    request_ip=request_ip,
                    now=now,
                )
                complete_tenant_mutation(
                    session,
                    reservation,
                    response_for_replay=payload,
                    http_status=201,
                    resource_id=hold_id,
                )
                return ServiceResult(payload, 201)
    except IntegrityError as exc:
        raise ComplianceConflict(
            "compliance_legal_hold_name_conflict",
            "同名 active legal hold 已存在",
            409,
        ) from exc


def release_legal_hold(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    hold_id: str,
    expected_revision: int,
    reason: str,
    idempotency_key: str,
    request_id: str,
    request_ip: str,
    now: datetime,
) -> ServiceResult:
    clean_id = _clean(hold_id, "legal_hold_id", 64)
    revision = _positive_int(expected_revision, "revision", 1, 2_147_483_647)
    safe_reason = _reason(reason)
    request_hash = tenant_request_hash(
        operation="compliance.legal_hold.release",
        path_identity={"tenant_id": tenant_id, "legal_hold_id": clean_id},
        body={"revision": revision, "reason": safe_reason},
    )
    key_lock, engine_lock = _mutation_context(engine, tenant_id, actor_id, idempotency_key)
    with key_lock, engine_lock, Session(engine) as session:
        with _transaction(session):
            _ensure_0023(session.connection())
            actor = _actor(session, tenant_id, actor_id)
            _require_owner(actor)
            reservation, replay = _reserve(
                session,
                actor=actor,
                key=idempotency_key,
                request_hash=request_hash,
                operation="compliance.legal_hold.release",
                resource_type="tenant_audit_legal_hold",
            )
            if replay is not None:
                return replay
            table = _table(session, "tenant_audit_legal_holds")
            current = (
                session.execute(
                    select(table)
                    .where(table.c.id == clean_id, table.c.tenant_id == tenant_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if current is None:
                raise ComplianceNotFound("legal hold 不存在")
            if int(current["revision"]) != revision:
                raise ComplianceConflict(
                    "compliance_revision_conflict", "legal hold revision 已变化", 409
                )
            if str(current["status"]) != "active":
                raise ComplianceConflict("compliance_state_conflict", "legal hold 已释放", 409)
            session.execute(
                update(table)
                .where(table.c.id == clean_id, table.c.revision == revision)
                .values(
                    status="released",
                    active_name_key=None,
                    revision=revision + 1,
                    released_at=now,
                    released_by=actor_id,
                    updated_at=now,
                    updated_by=actor_id,
                )
            )
            row = session.execute(select(table).where(table.c.id == clean_id)).mappings().one()
            payload = {"legal_hold": _hold_payload(row)}
            _audit(
                session,
                actor=actor,
                action="compliance.legal_hold.released",
                resource_type="tenant_audit_legal_hold",
                resource_id=clean_id,
                before=_hold_payload(current),
                after={**_hold_payload(row), "release_reason": safe_reason},
                request_id=request_id,
                request_ip=request_ip,
                now=now,
            )
            complete_tenant_mutation(
                session,
                reservation,
                response_for_replay=payload,
                http_status=200,
                resource_id=clean_id,
            )
            return ServiceResult(payload)


def _filters(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ComplianceValidation("compliance_filter_invalid", "导出 filters 必须是对象", 422)
    if set(value) - _ALLOWED_FILTERS:
        raise ComplianceValidation("compliance_filter_invalid", "导出 filter 不在允许列表", 422)
    result: dict[str, str] = {}
    for key, raw in value.items():
        result[key] = _clean(raw, f"filters.{key}", 256)
    return result


def _audit_export_rows(
    session: Session,
    *,
    tenant_id: str,
    filters: Mapping[str, str],
    sequence_from: int | None,
    sequence_to: int | None,
    time_from: datetime | None,
    time_to: datetime | None,
) -> list[dict[str, Any]]:
    statement = select(TenantAuditEvent).where(TenantAuditEvent.tenant_id == tenant_id)
    for key, value in filters.items():
        statement = statement.where(getattr(TenantAuditEvent, key) == value)
    if sequence_from is not None:
        statement = statement.where(TenantAuditEvent.sequence >= sequence_from)
    if sequence_to is not None:
        statement = statement.where(TenantAuditEvent.sequence <= sequence_to)
    if time_from is not None:
        statement = statement.where(TenantAuditEvent.occurred_at >= time_from)
    if time_to is not None:
        statement = statement.where(TenantAuditEvent.occurred_at <= time_to)
    rows = session.execute(statement.order_by(TenantAuditEvent.sequence)).scalars()
    return [
        {
            "sequence": int(row.sequence),
            "id": row.id,
            "actor_id": row.actor_id,
            "actor_name": row.actor_name_snapshot,
            "actor_email": row.actor_email_snapshot,
            "action": row.action,
            "resource_type": row.resource_type,
            "resource_id": row.resource_id,
            "target_account_id": row.target_account_id,
            "before_snapshot": row.before_snapshot,
            "after_snapshot": row.after_snapshot,
            "request_id": row.request_id,
            "occurred_at": _iso(row.occurred_at),
        }
        for row in rows
    ]


def _bounded_snapshot(value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return serialized[:8192]


def _csv_safe(value: Any) -> str:
    if value is None:
        return ""
    result = str(value)
    if result.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + result
    return result


def _serialize_ndjson(rows: Sequence[Mapping[str, Any]]) -> bytes:
    lines = []
    for row in rows:
        payload = {key: row.get(key) for key in _EXPORT_FIELDS}
        payload["before_snapshot"] = row.get("before_snapshot")
        payload["after_snapshot"] = row.get("after_snapshot")
        lines.append(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
    return (("\n".join(lines) + "\n") if lines else "").encode("utf-8")


def _serialize_csv(rows: Sequence[Mapping[str, Any]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(_EXPORT_FIELDS)
    for row in rows:
        values = []
        for key in _EXPORT_FIELDS:
            value = row.get(key)
            if key in {"before_snapshot", "after_snapshot"}:
                value = _bounded_snapshot(value)
            values.append(_csv_safe(value))
        writer.writerow(values)
    return output.getvalue().encode("utf-8-sig")


def _serialize_export(rows: Sequence[Mapping[str, Any]], format_name: str) -> bytes:
    # 原先这里是一条 else 出 CSV：一个没被识别的格式名会拿到 CSV 字节，而任务行上写着别的
    # 格式。现在只认声明过的格式，认不出就拒。
    spec = resolve_audit_export_format(format_name)
    if spec is None:
        raise ComplianceValidation(
            "compliance_request_invalid", f"不支持的导出格式：{format_name!r}", 422
        )
    return spec.serialize(list(rows))


# 序列化器留在这个模块：只有这里知道一条审计行怎么变成字节（字段清单、快照脱敏、CSV 注入
# 前缀防护），注册表只负责"这个格式叫什么、叫什么扩展名、什么媒体类型"。
register_audit_export_format(
    AuditExportFormatSpec(
        format="ndjson",
        extension="ndjson",
        media_type="application/x-ndjson",
        serialize=_serialize_ndjson,
    )
)
register_audit_export_format(
    AuditExportFormatSpec(
        format="csv",
        extension="csv",
        media_type="text/csv; charset=utf-8",
        serialize=_serialize_csv,
    )
)


def _download_format(format_name: str) -> AuditExportFormatSpec:
    """How a stored export job's bytes are labelled when handed to a client.

    媒体类型与后缀都取自声明，且认不出来就拒绝：存储行里写着一个已经不认识的格式时，
    宁可报完整性失败，也不要按 CSV 的名字与 CSV 的类型把一个别的什么字节流发出去。
    """
    spec = resolve_audit_export_format(format_name)
    if spec is None:
        raise ComplianceConflict(
            "compliance_export_integrity_failed",
            "审计导出完整性校验失败",
            409,
        )
    return spec


def _contained_path(root: Path, object_key: str) -> Path:
    root_resolved = root.resolve()
    if Path(object_key).is_absolute():
        raise ComplianceConflict(
            "compliance_export_integrity_failed",
            "导出对象路径不安全",
            409,
        )
    candidate = (root_resolved / object_key).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ComplianceConflict(
            "compliance_export_integrity_failed",
            "导出对象路径不安全",
            409,
        ) from exc
    return candidate


def _write_atomic(root: Path, object_key: str, content: bytes) -> Path:
    target = _contained_path(root, object_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        return target
    finally:
        temporary.unlink(missing_ok=True)


def create_audit_export(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    export_root: Path,
    format_name: str,
    filters: Mapping[str, Any] | None,
    sequence_from: int | None,
    sequence_to: int | None,
    time_from: datetime | str | None,
    time_to: datetime | str | None,
    expires_in_days: int,
    reason: str,
    idempotency_key: str,
    request_id: str,
    request_ip: str,
    now: datetime,
) -> ServiceResult:
    clean_format = _clean(format_name, "format", 16).casefold()
    if resolve_audit_export_format(clean_format) is None:
        raise ComplianceValidation(
            "compliance_request_invalid",
            "format 仅支持 " + "/".join(audit_export_format_names()),
            422,
        )
    clean_filters = _filters(filters)
    seq_from = _optional_int(sequence_from, "sequence_from")
    seq_to = _optional_int(sequence_to, "sequence_to")
    from_time = _optional_time(time_from, "time_from")
    to_time = _optional_time(time_to, "time_to")
    if seq_from is not None and seq_to is not None and seq_from > seq_to:
        raise ComplianceValidation("compliance_request_invalid", "sequence 范围无效", 422)
    if from_time is not None and to_time is not None and from_time > to_time:
        raise ComplianceValidation("compliance_request_invalid", "time 范围无效", 422)
    expiry_days = _positive_int(expires_in_days, "expires_in_days", 1, 365)
    safe_reason = _reason(reason)
    request_hash = tenant_request_hash(
        operation="compliance.audit_export.create",
        path_identity={"tenant_id": tenant_id},
        body={
            "format": clean_format,
            "filters": clean_filters,
            "sequence_from": seq_from,
            "sequence_to": seq_to,
            "time_from": _iso(from_time),
            "time_to": _iso(to_time),
            "expires_in_days": expiry_days,
            "reason": safe_reason,
        },
    )
    key_lock, engine_lock = _mutation_context(engine, tenant_id, actor_id, idempotency_key)
    final_path: Path | None = None
    committed = False
    try:
        with key_lock, engine_lock, Session(engine) as session:
            with _transaction(session):
                _ensure_0023(session.connection())
                actor = _actor(session, tenant_id, actor_id)
                _require_manager(actor)
                reservation, replay = _reserve(
                    session,
                    actor=actor,
                    key=idempotency_key,
                    request_hash=request_hash,
                    operation="compliance.audit_export.create",
                    resource_type="tenant_audit_export_job",
                )
                if replay is not None:
                    committed = True
                    return replay
                rows = _audit_export_rows(
                    session,
                    tenant_id=tenant_id,
                    filters=clean_filters,
                    sequence_from=seq_from,
                    sequence_to=seq_to,
                    time_from=from_time,
                    time_to=to_time,
                )
                content = _serialize_export(rows, clean_format)
                digest = hashlib.sha256(content).hexdigest()
                job_id = f"audit-export-{uuid.uuid4().hex}"
                extension = str(resolve_audit_export_format(clean_format).extension)
                object_key = f"jobs/{job_id}.{extension}"
                final_path = _write_atomic(Path(export_root), object_key, content)
                table = _table(session, "tenant_audit_export_jobs")
                expires_at = now + timedelta(days=expiry_days)
                session.execute(
                    table.insert().values(
                        id=job_id,
                        tenant_id=tenant_id,
                        format=clean_format,
                        status="completed",
                        filters_json=clean_filters,
                        sequence_from=seq_from,
                        sequence_to=seq_to,
                        time_from=from_time,
                        time_to=to_time,
                        object_key=object_key,
                        sha256=digest,
                        byte_size=len(content),
                        row_count=len(rows),
                        revision=1,
                        requested_at=now,
                        requested_by=actor_id,
                        completed_at=now,
                        expires_at=expires_at,
                    )
                )
                row = session.execute(select(table).where(table.c.id == job_id)).mappings().one()
                payload = {"export": _export_payload(row)}
                _audit(
                    session,
                    actor=actor,
                    action="compliance.audit_export.completed",
                    resource_type="tenant_audit_export_job",
                    resource_id=job_id,
                    before=None,
                    after={**_export_payload(row), "reason": safe_reason},
                    request_id=request_id,
                    request_ip=request_ip,
                    now=now,
                )
                complete_tenant_mutation(
                    session,
                    reservation,
                    response_for_replay=payload,
                    http_status=201,
                    resource_id=job_id,
                )
                result = ServiceResult(payload, 201)
            committed = True
            return result
    finally:
        if final_path is not None and not committed:
            final_path.unlink(missing_ok=True)


def list_audit_exports(engine: Any, *, tenant_id: str, actor_id: str) -> ServiceResult:
    with Session(engine) as session:
        _ensure_0023(session.connection())
        actor = _actor(session, tenant_id, actor_id)
        _require_manager(actor)
        table = _table(session, "tenant_audit_export_jobs")
        rows = session.execute(
            select(table)
            .where(table.c.tenant_id == tenant_id)
            .order_by(table.c.requested_at.desc(), table.c.id)
        ).mappings()
        items = [_export_payload(row) for row in rows]
        return ServiceResult({"items": items, "count": len(items)})


def get_audit_export(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    export_id: str,
) -> ServiceResult:
    clean_id = _clean(export_id, "export_id", 64)
    with Session(engine) as session:
        _ensure_0023(session.connection())
        actor = _actor(session, tenant_id, actor_id)
        _require_manager(actor)
        table = _table(session, "tenant_audit_export_jobs")
        row = (
            session.execute(
                select(table).where(table.c.id == clean_id, table.c.tenant_id == tenant_id)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ComplianceNotFound("审计导出任务不存在")
        return ServiceResult({"export": _export_payload(row)})


def download_audit_export(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    export_id: str,
    export_root: Path,
    now: datetime,
) -> DownloadResult:
    clean_id = _clean(export_id, "export_id", 64)
    with Session(engine) as session:
        _ensure_0023(session.connection())
        actor = _actor(session, tenant_id, actor_id)
        _require_manager(actor)
        table = _table(session, "tenant_audit_export_jobs")
        row = (
            session.execute(
                select(table).where(table.c.id == clean_id, table.c.tenant_id == tenant_id)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ComplianceNotFound("审计导出任务不存在")
        expires_at = row.get("expires_at")
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00")).replace(
                tzinfo=None
            )
        if (
            str(row["status"]) != "completed"
            or not isinstance(expires_at, datetime)
            or expires_at <= now
        ):
            raise ComplianceConflict("compliance_export_unavailable", "审计导出不可下载", 409)
        object_key = str(row.get("object_key") or "")
        path = _contained_path(Path(export_root), object_key)
        if not path.is_file() or path.is_symlink():
            raise ComplianceConflict(
                "compliance_export_integrity_failed",
                "审计导出文件缺失或不安全",
                409,
            )
        content = path.read_bytes()
        expected_size = int(row["byte_size"] or -1)
        expected_hash = str(row["sha256"] or "")
        if len(content) != expected_size or hashlib.sha256(content).hexdigest() != expected_hash:
            raise ComplianceConflict(
                "compliance_export_integrity_failed",
                "审计导出完整性校验失败",
                409,
            )
        download_spec = _download_format(str(row["format"]))
        return DownloadResult(
            content=content,
            media_type=download_spec.media_type,
            filename=f"audit-export-{clean_id}.{download_spec.extension}",
        )


__all__ = [
    "ComplianceConflict",
    "ComplianceError",
    "ComplianceForbidden",
    "ComplianceMigrationRequired",
    "ComplianceNotFound",
    "ComplianceValidation",
    "DownloadResult",
    "ServiceResult",
    "create_audit_export",
    "create_legal_hold",
    "download_audit_export",
    "execute_retention",
    "get_audit_export",
    "get_retention_policy",
    "list_audit_exports",
    "list_legal_holds",
    "preview_retention",
    "release_legal_hold",
    "update_retention_policy",
]
