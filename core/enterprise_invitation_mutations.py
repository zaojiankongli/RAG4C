"""Transactional enterprise invitation lifecycle mutations for complete 0020 catalogs."""

from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
import hmac
import re
import secrets
from collections.abc import Iterator, Mapping
from typing import Any, Literal
import uuid

from sqlalchemy import MetaData, Table, func, inspect, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.enterprise_tenant_idempotency import (
    TenantMutationIdempotencyConflict,
    TenantMutationIdempotencyInProgress,
    TenantMutationIdempotencyValidationError,
    TenantMutationReservation,
    complete_tenant_mutation,
    engine_serialization_lock,
    idempotency_key_lock,
    reserve_tenant_mutation,
    tenant_idempotency_key_digest,
    tenant_request_hash,
)
from models.orm import Account, Tenant, TenantAuditEvent, TenantMember

InvitationRole = Literal["owner", "admin", "editor", "member"]
_REVISION = "0020_tenant_invitation_lifecycle"
_REQUIRED_TABLES = {
    "accounts",
    "tenants",
    "tenant_members",
    "tenant_audit_events",
    "tenant_invitations",
    "tenant_control_mutation_requests",
    "alembic_version",
}
_INVITATION_COLUMNS = {
    "id",
    "tenant_id",
    "email",
    "normalized_email",
    "role",
    "status",
    "token_hash",
    "expires_at",
    "accepted_at",
    "accepted_by",
    "invited_by",
    "revision",
    "created_at",
    "updated_at",
    "pending_email_key",
    "last_sent_at",
    "send_count",
    "revoked_at",
    "revoked_by",
    "updated_by",
}
_LEDGER_COLUMNS = {
    "id",
    "tenant_id",
    "actor_id",
    "idempotency_key",
    "request_hash",
    "operation",
    "resource_type",
    "resource_id",
    "status",
    "response_json",
    "http_status",
    "created_at",
    "completed_at",
}
_UNIQUES = {
    "tenant_invitations": {
        "uq_tenant_invitations_pending_email": ("tenant_id", "pending_email_key"),
        "uq_tenant_invitations_token_hash": ("tenant_id", "token_hash"),
    },
    "tenant_control_mutation_requests": {
        "uq_tenant_control_mutation_requests_actor_key": (
            "tenant_id",
            "actor_id",
            "idempotency_key",
        ),
    },
}
_FOREIGN_KEYS = {
    "tenant_invitations": {
        "fk_tenant_invitations_tenant": (("tenant_id",), "tenants", ("id",)),
        "fk_tenant_invitations_scope_inviter": (
            ("invited_by", "tenant_id"),
            "tenant_members",
            ("account_id", "tenant_id"),
        ),
        "fk_tenant_invitations_scope_acceptor": (
            ("accepted_by", "tenant_id"),
            "tenant_members",
            ("account_id", "tenant_id"),
        ),
        "fk_tenant_invitations_scope_revoker": (
            ("revoked_by", "tenant_id"),
            "tenant_members",
            ("account_id", "tenant_id"),
        ),
        "fk_tenant_invitations_scope_updater": (
            ("updated_by", "tenant_id"),
            "tenant_members",
            ("account_id", "tenant_id"),
        ),
    },
    "tenant_control_mutation_requests": {
        "fk_tenant_control_mutation_requests_tenant": (("tenant_id",), "tenants", ("id",)),
        "fk_tenant_control_mutation_requests_actor": (
            ("actor_id",),
            "accounts",
            ("id",),
        ),
    },
}
_CHECKS = {
    "tenant_invitations": {
        "ck_tenant_invitations_role": ("owner", "admin", "editor", "member"),
        "ck_tenant_invitations_status": ("pending", "accepted", "revoked", "expired"),
        "ck_tenant_invitations_revision_positive": ("revision > 0",),
        "ck_tenant_invitations_send_count_positive": ("send_count > 0",),
        "ck_tenant_invitations_pending_email_key": ("pending_email_key", "normalized_email"),
        "ck_tenant_invitations_accepted_evidence": ("accepted_at", "accepted_by"),
        "ck_tenant_invitations_revoked_evidence": ("revoked_at", "revoked_by"),
    },
    "tenant_control_mutation_requests": {
        "ck_tenant_control_mutation_requests_status": ("pending", "completed", "failed"),
        "ck_tenant_control_mutation_requests_idempotency_digest": ("length(idempotency_key)", "64"),
        "ck_tenant_control_mutation_requests_request_hash": ("length(request_hash)", "64"),
    },
}
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_CREDENTIAL_REASON = re.compile(
    r"(?i)(authorization\s*:|bearer\s+\S+|cookie\s*:|password\s*[=:]|"
    r"api[-_ ]?key\s*[=:]|sk_(?:live|test)_[A-Za-z0-9]{12,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{20,}|"
    r"[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,})"
)


class TenantInvitationError(RuntimeError):
    pass


class TenantInvitationMigrationRequired(TenantInvitationError):
    pass


class TenantInvitationUnavailable(TenantInvitationError):
    pass


class TenantInvitationForbidden(TenantInvitationError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class TenantInvitationNotFound(TenantInvitationError):
    def __init__(self, code: str = "tenant_invitation_not_found", message: str = "邀请不存在"):
        self.code = code
        super().__init__(message)


class TenantInvitationConflict(TenantInvitationError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class TenantInvitationRevisionConflict(TenantInvitationConflict):
    def __init__(self, expected_revision: int, current_revision: int):
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__("tenant_invitation_revision_conflict", "邀请已发生变化，请刷新后重试")


class TenantInvitationValidationError(ValueError):
    pass


def _clean(value: Any, field: str, maximum: int) -> str:
    result = str(value or "").strip()
    if not result or len(result) > maximum:
        raise TenantInvitationValidationError(
            f"{field} must contain between 1 and {maximum} characters"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in result):
        raise TenantInvitationValidationError(f"{field} contains control characters")
    return result


def _email(value: Any) -> tuple[str, str]:
    original = _clean(value, "email", 256)
    normalized = original.casefold()
    if not _EMAIL.fullmatch(normalized):
        raise TenantInvitationValidationError("email is invalid")
    return original, normalized


def _role(value: Any) -> InvitationRole:
    result = _clean(value, "role", 16).casefold()
    if result not in {"owner", "admin", "editor", "member"}:
        raise TenantInvitationValidationError("role is invalid")
    return result  # type: ignore[return-value]


def _positive_int(value: Any, field: str, maximum: int) -> int:
    if type(value) is not int or value < 1 or value > maximum:
        raise TenantInvitationValidationError(f"{field} must be between 1 and {maximum}")
    return value


def _reason(value: Any) -> str:
    result = _clean(value, "reason", 512)
    return "[REDACTED]" if _CREDENTIAL_REASON.search(result) else result


def _normalize_sql(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _ensure_0020(connection: Any) -> None:
    try:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        if _REQUIRED_TABLES - tables:
            raise TenantInvitationMigrationRequired("tenant invitation schema is incomplete")
        revisions = tuple(
            str(value)
            for value in connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalars()
        )
        if revisions != (_REVISION,):
            raise TenantInvitationMigrationRequired("tenant invitation schema is not stamped 0020")
        for table_name, expected in (
            ("tenant_invitations", _INVITATION_COLUMNS),
            ("tenant_control_mutation_requests", _LEDGER_COLUMNS),
        ):
            columns = {str(item["name"]): item for item in inspector.get_columns(table_name)}
            if expected - set(columns):
                raise TenantInvitationMigrationRequired(
                    f"tenant invitation columns are incomplete for {table_name}"
                )
        for table_name, expected in _UNIQUES.items():
            actual = {
                str(item.get("name")): tuple(item.get("column_names") or ())
                for item in inspector.get_unique_constraints(table_name)
            }
            if any(actual.get(name) != columns for name, columns in expected.items()):
                raise TenantInvitationMigrationRequired(
                    f"tenant invitation unique constraints are incomplete for {table_name}"
                )
        for table_name, expected in _FOREIGN_KEYS.items():
            actual = {
                str(item.get("name")): (
                    tuple(item.get("constrained_columns") or ()),
                    str(item.get("referred_table") or ""),
                    tuple(item.get("referred_columns") or ()),
                )
                for item in inspector.get_foreign_keys(table_name)
            }
            if any(actual.get(name) != contract for name, contract in expected.items()):
                raise TenantInvitationMigrationRequired(
                    f"tenant invitation foreign keys are incomplete for {table_name}"
                )
        for table_name, expected in _CHECKS.items():
            actual = {
                str(item.get("name")): _normalize_sql(item.get("sqltext"))
                for item in inspector.get_check_constraints(table_name)
            }
            for name, fragments in expected.items():
                sql = actual.get(name, "")
                if not sql or any(fragment not in sql for fragment in fragments):
                    raise TenantInvitationMigrationRequired(
                        f"tenant invitation checks are incomplete: {name}"
                    )
    except TenantInvitationMigrationRequired:
        raise
    except Exception as exc:
        raise TenantInvitationUnavailable("tenant invitation schema inspection failed") from exc


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


def _token() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def invitation_token_digest(raw_token: str) -> str:
    token = _clean(raw_token, "invite_token", 256)
    digest = hashlib.sha256()
    digest.update(b"rag4c:tenant-invitation-token:v1\x00")
    digest.update(token.encode("utf-8"))
    return digest.hexdigest()


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


def _payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "email": str(row["email"]),
        "role": str(row["role"]),
        "status": str(row["status"]),
        "invited_by": str(row["invited_by"]),
        "expires_at": _iso(row["expires_at"]),
        "revision": int(row["revision"]),
        "send_count": int(row["send_count"]),
        "last_sent_at": _iso(row["last_sent_at"]),
        "accepted_at": _iso(row["accepted_at"]),
        "accepted_by": row["accepted_by"],
        "revoked_at": _iso(row["revoked_at"]),
        "revoked_by": row["revoked_by"],
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


def _snapshot(row: Mapping[str, Any], reason: str | None = None) -> dict[str, Any]:
    result = {
        "id": str(row["id"]),
        "normalized_email": str(row["normalized_email"]),
        "role": str(row["role"]),
        "status": str(row["status"]),
        "revision": int(row["revision"]),
        "expires_at": _iso(row["expires_at"]),
        "send_count": int(row["send_count"]),
    }
    if reason is not None:
        result["reason"] = reason
    return result


def _row(session: Session, invitation_id: str, tenant_id: str) -> Mapping[str, Any]:
    table = _table(session, "tenant_invitations")
    result = (
        session.execute(
            select(table)
            .where(table.c.id == invitation_id, table.c.tenant_id == tenant_id)
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if result is None:
        raise TenantInvitationNotFound()
    return result


def _refreshed(session: Session, invitation_id: str, tenant_id: str) -> Mapping[str, Any]:
    table = _table(session, "tenant_invitations")
    return (
        session.execute(
            select(table).where(table.c.id == invitation_id, table.c.tenant_id == tenant_id)
        )
        .mappings()
        .one()
    )


def _actor_context(session: Session, tenant_id: str, actor_id: str) -> tuple[str, str, str]:
    result = session.execute(
        select(TenantMember.role, TenantMember.status, Account.name, Account.email, Tenant.status)
        .join(Account, Account.id == TenantMember.account_id)
        .join(Tenant, Tenant.id == TenantMember.tenant_id)
        .where(TenantMember.tenant_id == tenant_id, TenantMember.account_id == actor_id)
        .with_for_update()
    ).one_or_none()
    if result is None or str(result.status).casefold() != "active":
        raise TenantInvitationForbidden("tenant_invitation_forbidden", "当前身份不能管理邀请")
    if str(result[4]).casefold() != "active":
        raise TenantInvitationForbidden("tenant_invitation_forbidden", "当前租户不可用")
    role = str(result.role).casefold()
    if role not in {"owner", "admin"}:
        raise TenantInvitationForbidden("tenant_invitation_forbidden", "当前身份不能管理邀请")
    return role, str(result.name or "")[:128], str(result.email or "")[:256]


def _accept_account(
    session: Session, tenant_id: str, account_id: str, asserted_email: str
) -> tuple[str, str]:
    tenant_status = session.scalar(select(Tenant.status).where(Tenant.id == tenant_id))
    if tenant_status is None or str(tenant_status).casefold() != "active":
        raise TenantInvitationForbidden("tenant_invitation_forbidden", "当前租户不可用")
    account = session.execute(
        select(Account.name, Account.email).where(Account.id == account_id)
    ).one_or_none()
    if account is None:
        raise TenantInvitationForbidden(
            "invitation_accept_account_required", "接受邀请需要有效账号"
        )
    if str(account.email).strip().casefold() != str(asserted_email).strip().casefold():
        raise TenantInvitationForbidden(
            "tenant_invitation_email_mismatch", "账号邮箱与邀请身份不一致"
        )
    return str(account.name or "")[:128], str(account.email or "")[:256]


def _require_role(actor_role: str, invitation_role: str) -> None:
    if actor_role == "admin" and invitation_role == "owner":
        raise TenantInvitationForbidden(
            "tenant_invitation_role_forbidden", "管理员不能签发 owner 邀请"
        )


def _audit(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    actor_name: str,
    actor_email: str,
    action: str,
    invitation_id: str,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    request_id: str,
    request_ip: str,
    target_account_id: str | None = None,
) -> TenantAuditEvent:
    event = TenantAuditEvent(
        id=f"tenant-audit-{uuid.uuid4().hex}",
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_name_snapshot=actor_name,
        actor_email_snapshot=actor_email,
        action=action,
        resource_type="tenant_invitation",
        resource_id=invitation_id,
        target_account_id=target_account_id,
        before_snapshot=(dict(before) if before is not None else None),
        after_snapshot=(dict(after) if after is not None else None),
        request_id=request_id,
        request_ip=request_ip,
        occurred_at=datetime.utcnow(),
    )
    session.add(event)
    session.flush()
    return event


def _reserve(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    raw_key: str,
    request_hash: str,
    operation: str,
) -> TenantMutationReservation:
    return reserve_tenant_mutation(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        raw_idempotency_key=raw_key,
        request_hash=request_hash,
        operation=operation,
        resource_type="tenant_invitation",
    )


def _replay(reservation: TenantMutationReservation) -> dict[str, Any] | None:
    if reservation.replay is None:
        return None
    response = reservation.replay.response
    error = response.get("error")
    if isinstance(error, Mapping):
        code = str(error.get("code") or "tenant_invitation_state_conflict")
        if code == "tenant_invitation_expired":
            raise TenantInvitationConflict(code, str(error.get("message") or "邀请已过期"))
    return response


def _run_context(engine: Any, tenant_id: str, actor_id: str, key: str):
    digest = tenant_idempotency_key_digest(tenant_id, actor_id, key)
    return idempotency_key_lock(tenant_id, actor_id, digest), engine_serialization_lock(engine)


def create_invitation(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    actor_role: str,
    email: str,
    role: str,
    expires_in_days: int,
    reason: str,
    idempotency_key: str,
    request_id: str,
    request_ip: str = "",
) -> dict[str, Any]:
    tenant_id = _clean(tenant_id, "tenant_id", 64)
    actor_id = _clean(actor_id, "actor_id", 64)
    original_email, normalized_email = _email(email)
    invitation_role = _role(role)
    days = _positive_int(expires_in_days, "expires_in_days", 30)
    safe_reason = _reason(reason)
    request_id = _clean(request_id, "request_id", 128)
    key = _clean(idempotency_key, "Idempotency-Key", 128)
    request_hash = tenant_request_hash(
        operation="tenant_invitation.create",
        path_identity={"tenant_id": tenant_id},
        body={
            "email": normalized_email,
            "role": invitation_role,
            "expires_in_days": days,
            "reason": safe_reason,
        },
    )
    key_lock, engine_lock = _run_context(engine, tenant_id, actor_id, key)
    try:
        with key_lock, engine_lock, Session(engine, expire_on_commit=False) as session:
            with _transaction(session):
                _ensure_0020(session.connection())
                persisted_role, actor_name, actor_email = _actor_context(
                    session, tenant_id, actor_id
                )
                _ = actor_role
                _require_role(persisted_role, invitation_role)
                reservation = _reserve(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    raw_key=key,
                    request_hash=request_hash,
                    operation="tenant_invitation.create",
                )
                replay = _replay(reservation)
                if replay is not None:
                    return replay
                membership = session.scalar(
                    select(TenantMember.id)
                    .join(Account, Account.id == TenantMember.account_id)
                    .where(
                        TenantMember.tenant_id == tenant_id,
                        func.lower(Account.email) == normalized_email,
                    )
                )
                if membership is not None:
                    raise TenantInvitationConflict(
                        "tenant_invitation_membership_exists", "该账号已是租户成员"
                    )
                table = _table(session, "tenant_invitations")
                pending = session.scalar(
                    select(table.c.id).where(
                        table.c.tenant_id == tenant_id,
                        table.c.pending_email_key == normalized_email,
                    )
                )
                if pending is not None:
                    raise TenantInvitationConflict(
                        "tenant_invitation_duplicate_pending", "该邮箱已有待处理邀请"
                    )
                now = datetime.utcnow()
                expires_at = now + timedelta(days=days)
                raw_token = _token()
                invitation_id = f"tenant-invitation-{uuid.uuid4().hex}"
                session.execute(
                    table.insert().values(
                        id=invitation_id,
                        tenant_id=tenant_id,
                        email=original_email,
                        normalized_email=normalized_email,
                        role=invitation_role,
                        status="pending",
                        token_hash=invitation_token_digest(raw_token),
                        expires_at=expires_at,
                        accepted_at=None,
                        accepted_by=None,
                        invited_by=actor_id,
                        revision=1,
                        created_at=now,
                        updated_at=now,
                        pending_email_key=normalized_email,
                        last_sent_at=now,
                        send_count=1,
                        revoked_at=None,
                        revoked_by=None,
                        updated_by=actor_id,
                    )
                )
                row = _refreshed(session, invitation_id, tenant_id)
                _audit(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    actor_name=actor_name,
                    actor_email=actor_email,
                    action="tenant_invitation.created",
                    invitation_id=invitation_id,
                    before=None,
                    after=_snapshot(row, safe_reason),
                    request_id=request_id,
                    request_ip=request_ip,
                )
                facts = _payload(row)
                replay_response = {
                    "invitation": facts,
                    "delivery": {
                        "state": "manual_link_already_issued",
                        "expires_at": _iso(expires_at),
                    },
                }
                complete_tenant_mutation(
                    session,
                    reservation,
                    response_for_replay=replay_response,
                    http_status=201,
                    resource_id=invitation_id,
                )
                return {
                    "invitation": facts,
                    "delivery": {
                        "state": "manual_link_required",
                        "invite_token": raw_token,
                        "expires_at": _iso(expires_at),
                    },
                }
    except (
        TenantInvitationError,
        TenantMutationIdempotencyConflict,
        TenantMutationIdempotencyInProgress,
        TenantMutationIdempotencyValidationError,
    ):
        raise
    except IntegrityError as exc:
        raise TenantInvitationConflict(
            "tenant_invitation_duplicate_pending", "邀请与当前状态冲突"
        ) from exc
    except Exception as exc:
        raise TenantInvitationUnavailable("tenant invitation create failed") from exc


def _mutate_pending(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    actor_role: str,
    invitation_id: str,
    expected_revision: int,
    reason: str,
    idempotency_key: str,
    request_id: str,
    operation: Literal["resend", "revoke"],
    expires_in_days: int | None = None,
    request_ip: str = "",
) -> dict[str, Any]:
    tenant_id = _clean(tenant_id, "tenant_id", 64)
    actor_id = _clean(actor_id, "actor_id", 64)
    invitation_id = _clean(invitation_id, "invitation_id", 64)
    revision = _positive_int(expected_revision, "revision", 2_147_483_647)
    days = (
        _positive_int(expires_in_days, "expires_in_days", 30)
        if expires_in_days is not None
        else None
    )
    safe_reason = _reason(reason)
    key = _clean(idempotency_key, "Idempotency-Key", 128)
    request_id = _clean(request_id, "request_id", 128)
    op_name = f"tenant_invitation.{operation}"
    body: dict[str, Any] = {"revision": revision, "reason": safe_reason}
    if days is not None:
        body["expires_in_days"] = days
    request_hash = tenant_request_hash(
        operation=op_name,
        path_identity={"tenant_id": tenant_id, "invitation_id": invitation_id},
        body=body,
    )
    key_lock, engine_lock = _run_context(engine, tenant_id, actor_id, key)
    try:
        with key_lock, engine_lock, Session(engine, expire_on_commit=False) as session:
            with _transaction(session):
                _ensure_0020(session.connection())
                persisted_role, actor_name, actor_email = _actor_context(
                    session, tenant_id, actor_id
                )
                _ = actor_role
                reservation = _reserve(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    raw_key=key,
                    request_hash=request_hash,
                    operation=op_name,
                )
                replay = _replay(reservation)
                if replay is not None:
                    return replay
                current = _row(session, invitation_id, tenant_id)
                _require_role(persisted_role, str(current["role"]))
                if int(current["revision"]) != revision:
                    raise TenantInvitationRevisionConflict(revision, int(current["revision"]))
                if str(current["status"]) != "pending":
                    raise TenantInvitationConflict(
                        "tenant_invitation_state_conflict", "只有 pending 邀请可以变更"
                    )
                if current["expires_at"] <= datetime.utcnow():
                    raise TenantInvitationConflict("tenant_invitation_expired", "邀请已过期")
                table = _table(session, "tenant_invitations")
                before = _snapshot(current)
                now = datetime.utcnow()
                values: dict[str, Any]
                raw_token: str | None = None
                action: str
                if operation == "resend":
                    assert days is not None
                    raw_token = _token()
                    values = {
                        "token_hash": invitation_token_digest(raw_token),
                        "expires_at": now + timedelta(days=days),
                        "last_sent_at": now,
                        "send_count": int(current["send_count"]) + 1,
                        "revision": revision + 1,
                        "updated_at": now,
                        "updated_by": actor_id,
                    }
                    action = "tenant_invitation.link_rotated"
                else:
                    values = {
                        "status": "revoked",
                        "pending_email_key": None,
                        "revoked_at": now,
                        "revoked_by": actor_id,
                        "revision": revision + 1,
                        "updated_at": now,
                        "updated_by": actor_id,
                    }
                    action = "tenant_invitation.revoked"
                result = session.execute(
                    update(table)
                    .where(
                        table.c.id == invitation_id,
                        table.c.tenant_id == tenant_id,
                        table.c.revision == revision,
                    )
                    .values(**values)
                )
                if result.rowcount != 1:
                    raise TenantInvitationRevisionConflict(revision, int(current["revision"]))
                row = _refreshed(session, invitation_id, tenant_id)
                _audit(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    actor_name=actor_name,
                    actor_email=actor_email,
                    action=action,
                    invitation_id=invitation_id,
                    before=before,
                    after=_snapshot(row, safe_reason),
                    request_id=request_id,
                    request_ip=request_ip,
                )
                facts = _payload(row)
                if raw_token is None:
                    replay_response = {"invitation": facts}
                    complete_tenant_mutation(
                        session,
                        reservation,
                        response_for_replay=replay_response,
                        http_status=200,
                        resource_id=invitation_id,
                    )
                    return replay_response
                replay_response = {
                    "invitation": facts,
                    "delivery": {
                        "state": "manual_link_already_issued",
                        "expires_at": facts["expires_at"],
                    },
                }
                complete_tenant_mutation(
                    session,
                    reservation,
                    response_for_replay=replay_response,
                    http_status=200,
                    resource_id=invitation_id,
                )
                return {
                    "invitation": facts,
                    "delivery": {
                        "state": "manual_link_required",
                        "invite_token": raw_token,
                        "expires_at": facts["expires_at"],
                    },
                }
    except (
        TenantInvitationError,
        TenantMutationIdempotencyConflict,
        TenantMutationIdempotencyInProgress,
        TenantMutationIdempotencyValidationError,
    ):
        raise
    except Exception as exc:
        raise TenantInvitationUnavailable("tenant invitation mutation failed") from exc


def resend_invitation(engine: Any, **kwargs: Any) -> dict[str, Any]:
    return _mutate_pending(engine, operation="resend", **kwargs)


def revoke_invitation(engine: Any, **kwargs: Any) -> dict[str, Any]:
    return _mutate_pending(engine, operation="revoke", expires_in_days=None, **kwargs)


def accept_invitation(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    actor_email: str,
    invite_token: str,
    idempotency_key: str,
    request_id: str,
    request_ip: str = "",
) -> dict[str, Any]:
    tenant_id = _clean(tenant_id, "tenant_id", 64)
    actor_id = _clean(actor_id, "actor_id", 64)
    email = _clean(actor_email, "actor_email", 256).casefold()
    token = _clean(invite_token, "invite_token", 256)
    token_hash = invitation_token_digest(token)
    key = _clean(idempotency_key, "Idempotency-Key", 128)
    request_id = _clean(request_id, "request_id", 128)
    request_hash = tenant_request_hash(
        operation="tenant_invitation.accept",
        path_identity={"tenant_id": tenant_id},
        body={"token_digest": token_hash},
    )
    key_lock, engine_lock = _run_context(engine, tenant_id, actor_id, key)
    deferred_error: TenantInvitationConflict | None = None
    response: dict[str, Any] | None = None
    try:
        with key_lock, engine_lock, Session(engine, expire_on_commit=False) as session:
            with _transaction(session):
                _ensure_0020(session.connection())
                actor_name, persisted_email = _accept_account(session, tenant_id, actor_id, email)
                table = _table(session, "tenant_invitations")
                current = (
                    session.execute(
                        select(table)
                        .where(table.c.tenant_id == tenant_id, table.c.token_hash == token_hash)
                        .with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if current is None or not hmac.compare_digest(
                    str(current["token_hash"]), token_hash
                ):
                    raise TenantInvitationNotFound(
                        "tenant_invitation_token_invalid", "邀请令牌无效"
                    )
                if str(current["normalized_email"]) != email:
                    raise TenantInvitationForbidden(
                        "tenant_invitation_email_mismatch", "账号邮箱与邀请邮箱不匹配"
                    )
                reservation = _reserve(
                    session,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    raw_key=key,
                    request_hash=request_hash,
                    operation="tenant_invitation.accept",
                )
                replay = _replay(reservation)
                if replay is not None:
                    return replay
                existing = session.scalar(
                    select(TenantMember.id).where(
                        TenantMember.tenant_id == tenant_id,
                        TenantMember.account_id == actor_id,
                    )
                )
                if existing is not None:
                    raise TenantInvitationConflict(
                        "tenant_invitation_membership_exists", "该账号已是租户成员"
                    )
                if str(current["status"]) != "pending":
                    raise TenantInvitationConflict(
                        "tenant_invitation_state_conflict", "邀请当前状态不可接受"
                    )
                now = datetime.utcnow()
                if current["expires_at"] <= now:
                    before = _snapshot(current)
                    session.execute(
                        update(table)
                        .where(
                            table.c.id == current["id"],
                            table.c.revision == current["revision"],
                        )
                        .values(
                            status="expired",
                            pending_email_key=None,
                            revision=int(current["revision"]) + 1,
                            updated_at=now,
                            updated_by=str(current["updated_by"] or current["invited_by"]),
                        )
                    )
                    expired = _refreshed(session, str(current["id"]), tenant_id)
                    _audit(
                        session,
                        tenant_id=tenant_id,
                        actor_id=actor_id,
                        actor_name=actor_name,
                        actor_email=persisted_email,
                        action="tenant_invitation.expired",
                        invitation_id=str(current["id"]),
                        before=before,
                        after=_snapshot(expired),
                        request_id=request_id,
                        request_ip=request_ip,
                        target_account_id=actor_id,
                    )
                    complete_tenant_mutation(
                        session,
                        reservation,
                        response_for_replay={
                            "error": {
                                "code": "tenant_invitation_expired",
                                "message": "邀请已过期",
                            }
                        },
                        http_status=409,
                        resource_id=str(current["id"]),
                    )
                    deferred_error = TenantInvitationConflict(
                        "tenant_invitation_expired", "邀请已过期"
                    )
                else:
                    session.add(
                        TenantMember(
                            account_id=actor_id,
                            tenant_id=tenant_id,
                            role=str(current["role"]),
                            status="active",
                            revision=1,
                            updated_by=actor_id,
                        )
                    )
                    session.flush()
                    before = _snapshot(current)
                    result = session.execute(
                        update(table)
                        .where(
                            table.c.id == current["id"],
                            table.c.revision == current["revision"],
                        )
                        .values(
                            status="accepted",
                            pending_email_key=None,
                            accepted_at=now,
                            accepted_by=actor_id,
                            revision=int(current["revision"]) + 1,
                            updated_at=now,
                            updated_by=actor_id,
                        )
                    )
                    if result.rowcount != 1:
                        raise TenantInvitationRevisionConflict(
                            int(current["revision"]), int(current["revision"])
                        )
                    row = _refreshed(session, str(current["id"]), tenant_id)
                    _audit(
                        session,
                        tenant_id=tenant_id,
                        actor_id=actor_id,
                        actor_name=actor_name,
                        actor_email=persisted_email,
                        action="tenant_invitation.accepted",
                        invitation_id=str(current["id"]),
                        before=before,
                        after=_snapshot(row),
                        request_id=request_id,
                        request_ip=request_ip,
                        target_account_id=actor_id,
                    )
                    response = {"invitation": _payload(row)}
                    complete_tenant_mutation(
                        session,
                        reservation,
                        response_for_replay=response,
                        http_status=200,
                        resource_id=str(current["id"]),
                    )
        if deferred_error is not None:
            raise deferred_error
        assert response is not None
        return response
    except (
        TenantInvitationError,
        TenantMutationIdempotencyConflict,
        TenantMutationIdempotencyInProgress,
        TenantMutationIdempotencyValidationError,
    ):
        raise
    except Exception as exc:
        raise TenantInvitationUnavailable("tenant invitation accept failed") from exc


__all__ = [
    "TenantInvitationConflict",
    "TenantInvitationError",
    "TenantInvitationForbidden",
    "TenantInvitationMigrationRequired",
    "TenantInvitationNotFound",
    "TenantInvitationRevisionConflict",
    "TenantInvitationUnavailable",
    "TenantInvitationValidationError",
    "accept_invitation",
    "create_invitation",
    "invitation_token_digest",
    "resend_invitation",
    "revoke_invitation",
]
