"""Transactional dataset ACL grant mutations with revision fencing and audit."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import re
from collections.abc import Iterator, Mapping
from typing import Any, Literal
import uuid

from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, load_only

from core.enterprise_access_control import (
    DatasetAccessControlUnavailable,
    DatasetAccessDecision,
    evaluate_dataset_permissions,
)
from core.enterprise_acl_idempotency import (
    EnterpriseAclIdempotencyConflict,
    EnterpriseAclIdempotencyInProgress,
    EnterpriseAclIdempotencyValidationError,
    IdempotencyReservation,
    canonical_request_hash,
    complete_idempotency,
    engine_serialization_lock,
    idempotency_key_digest,
    idempotency_key_lock,
    normalize_idempotency_key,
    reserve_idempotency,
)
from core.knowledge_permissions import KNOWLEDGE_MANAGE
from core.enterprise_approval_control import resolve_active_approval_policy_in_session
from models.orm import (
    Account,
    Dataset,
    DatasetAccessGrant,
    Tenant,
    TenantAuditEvent,
    TenantGroup,
    TenantMember,
    TenantOrganizationUnit,
)

GrantSubjectType = Literal["account", "group", "organization_unit"]
GrantRole = Literal["viewer", "editor", "manager"]
_MUTATION_SCHEMA_REVISION = "0019_dataset_acl_control"
# Dataset ACL tables remain authoritative after later enterprise control-plane
# migrations.  The mutation gate must validate the capability contract below,
# not reject a healthy catalog merely because its Alembic head advanced.
#
# 这里曾经是一份**写死的 revision 白名单**（停在 0028），恰好违反了上一段注释：
# 每次新增迁移（0029…0036），真实 head 就掉出白名单，于是 Dataset ACL 授权写入
# 在健康库上被永久 503——不是"不安全时拒绝"，而是"升级后一直拒绝"。
# tests/test_enterprise_acl_security_review.py 的
# test_later_workspace_heads_keep_dataset_acl_mutations_available 就是钉死
# "后续企业迁移不得停用 ACL 写入"这条不变量的。
#
# 改为按**迁移顺序**判断：revision 必须是已知修订，且不早于 0019。
# 结构契约仍由下面的 _ensure_mutation_schema 逐表逐列验证（那才是真正的安全边界）。
def _revision_order(revision: str) -> int:
    from core import catalog_schema

    return catalog_schema._REVISION_ORDER_FOR_CAPABILITY(revision)


def _known_revisions() -> frozenset[str]:
    from core import catalog_schema

    return catalog_schema._known_catalog_revisions()

_REQUIRED_MUTATION_TABLES = frozenset(
    {
        "tenant_organization_units",
        "tenant_organization_unit_members",
        "tenant_groups",
        "tenant_group_members",
        "tenant_invitations",
        "dataset_access_grants",
        "tenant_audit_events",
        "dataset_acl_mutation_requests",
    }
)
_REQUIRED_MUTATION_COLUMNS = {
    "datasets": {"acl_mode", "acl_revision", "acl_enabled_at", "acl_enabled_by"},
    "dataset_acl_mutation_requests": {
        "id",
        "tenant_id",
        "dataset_id",
        "actor_id",
        "idempotency_key",
        "request_hash",
        "operation",
        "status",
        "resource_id",
        "response_json",
        "http_status",
        "created_at",
        "completed_at",
    },
}
_REQUIRED_MUTATION_NOT_NULL = {
    "datasets": {"acl_mode", "acl_revision"},
    "dataset_acl_mutation_requests": {
        "id",
        "tenant_id",
        "dataset_id",
        "actor_id",
        "idempotency_key",
        "request_hash",
        "operation",
        "status",
        "created_at",
    },
}
_REQUIRED_MUTATION_UNIQUES = {
    "uq_dataset_acl_mutation_requests_actor_key": (
        "tenant_id",
        "actor_id",
        "idempotency_key",
    )
}
_REQUIRED_MUTATION_FOREIGN_KEYS = {
    "fk_dataset_acl_mutation_requests_scope_dataset": (
        ("tenant_id", "dataset_id"),
        "datasets",
        ("tenant_id", "id"),
    ),
    "fk_dataset_acl_mutation_requests_scope_actor": (
        ("actor_id", "tenant_id"),
        "tenant_members",
        ("account_id", "tenant_id"),
    ),
}
_REQUIRED_MUTATION_CHECKS = {
    "dataset_acl_mutation_requests": {
        "ck_dataset_acl_mutation_requests_status": (
            "pending",
            "completed",
            "failed",
        )
    },
    "datasets": {
        "ck_datasets_acl_mode": ("tenant_role", "dataset_acl"),
        "ck_datasets_acl_revision_positive": ("acl_revision > 0",),
    },
}


class EnterpriseAccessGrantError(RuntimeError):
    """Base class for stable access-grant mutation failures."""


class EnterpriseAccessGrantUnavailable(EnterpriseAccessGrantError):
    """Raised when mutation state cannot be established safely."""


class EnterpriseAccessGrantMigrationRequired(EnterpriseAccessGrantUnavailable):
    """Raised when the writable catalog is not provably at complete 0019."""


class EnterpriseAccessGrantForbidden(EnterpriseAccessGrantError):
    """Raised when the actor does not have dataset-scoped manage permission."""


class EnterpriseAccessGrantNotFound(EnterpriseAccessGrantError):
    """Raised for a tenant-scoped dataset or grant that is not enumerable."""


class EnterpriseAccessGrantSubjectNotFound(EnterpriseAccessGrantNotFound):
    """Raised when a requested subject is not active in the actor tenant."""


class EnterpriseAccessGrantConflict(EnterpriseAccessGrantError):
    """Raised when a mutation conflicts with current persisted state."""


class EnterpriseAccessApprovalRequired(EnterpriseAccessGrantConflict):
    """Raised when an active policy requires approval before ACL disable."""

    def __init__(self, policy: Mapping[str, Any]) -> None:
        self.policy = dict(policy)
        super().__init__("dataset ACL disable requires approval")


class EnterpriseAccessGrantRevisionConflict(EnterpriseAccessGrantConflict):
    """Raised when optimistic revision fencing rejects a stale request."""

    def __init__(self, *, expected_revision: int, current_revision: int):
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__("dataset access grant revision is stale")


class EnterpriseAccessGrantStateConflict(EnterpriseAccessGrantConflict):
    """Raised when a requested lifecycle transition is invalid."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class EnterpriseAccessGrantValidationError(ValueError):
    """Raised when a direct service caller supplies an invalid value."""


class EnterpriseAccessControlRevisionConflict(EnterpriseAccessGrantConflict):
    """Raised when a Dataset ACL mode revision is stale."""

    def __init__(self, *, expected_revision: int, current_revision: int):
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__("dataset ACL control revision is stale")


def _clean(value: Any, *, field: str, maximum: int) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > maximum:
        raise EnterpriseAccessGrantValidationError(
            f"{field} must contain between 1 and {maximum} characters"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise EnterpriseAccessGrantValidationError(f"{field} contains control characters")
    return normalized


def _revision(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise EnterpriseAccessGrantValidationError("expected_revision must be a positive integer")
    return value


def _subject_type(value: Any) -> GrantSubjectType:
    normalized = _clean(value, field="subject_type", maximum=32).casefold()
    if normalized not in {"account", "group", "organization_unit"}:
        raise EnterpriseAccessGrantValidationError("subject_type is not supported")
    return normalized  # type: ignore[return-value]


def _role(value: Any) -> GrantRole:
    normalized = _clean(value, field="role", maximum=16).casefold()
    if normalized not in {"viewer", "editor", "manager"}:
        raise EnterpriseAccessGrantValidationError("role is not supported")
    return normalized  # type: ignore[return-value]


def _normalized_sql(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _migration_required(message: str) -> EnterpriseAccessGrantMigrationRequired:
    return EnterpriseAccessGrantMigrationRequired(message)


def _ensure_mutation_schema(engine: Any) -> None:
    """Require a stamped and structurally complete 0019 catalog."""

    try:
        inspector = sqlalchemy_inspect(engine)
        tables = set(inspector.get_table_names())
    except Exception as exc:
        raise EnterpriseAccessGrantUnavailable(
            "enterprise access graph schema inspection failed"
        ) from exc

    missing_tables = (_REQUIRED_MUTATION_TABLES | {"alembic_version"}) - tables
    if missing_tables:
        raise _migration_required("enterprise access graph mutation schema is not fully migrated")

    try:
        revisions = tuple(
            str(value)
            for value in engine.execute(text("SELECT version_num FROM alembic_version")).scalars()
        )
        if len(revisions) != 1:
            raise _migration_required("enterprise ACL mutation schema revision is unsupported")
        revision = revisions[0]
        # 未知修订（手改过 alembic_version）或早于 0019 的修订：仍拒绝。
        # 0019 及之后的任何已知修订：放行到下面的结构契约校验。
        if revision not in _known_revisions() or _revision_order(revision) < _revision_order(
            _MUTATION_SCHEMA_REVISION
        ):
            raise _migration_required("enterprise ACL mutation schema revision is unsupported")

        for table_name, required_columns in _REQUIRED_MUTATION_COLUMNS.items():
            columns = {
                str(column.get("name")): column for column in inspector.get_columns(table_name)
            }
            if required_columns - set(columns):
                raise _migration_required(
                    f"enterprise ACL mutation schema is incomplete for {table_name}"
                )
            for column_name in _REQUIRED_MUTATION_NOT_NULL.get(table_name, set()):
                if bool(columns[column_name].get("nullable", True)):
                    raise _migration_required(
                        f"enterprise ACL mutation column is nullable: {table_name}.{column_name}"
                    )

        uniques = {
            str(item.get("name")): tuple(item.get("column_names") or ())
            for item in inspector.get_unique_constraints("dataset_acl_mutation_requests")
        }
        for name, columns in _REQUIRED_MUTATION_UNIQUES.items():
            if uniques.get(name) != columns:
                raise _migration_required(
                    f"enterprise ACL mutation unique constraint is missing: {name}"
                )

        foreign_keys = {
            str(item.get("name")): (
                tuple(item.get("constrained_columns") or ()),
                str(item.get("referred_table") or ""),
                tuple(item.get("referred_columns") or ()),
            )
            for item in inspector.get_foreign_keys("dataset_acl_mutation_requests")
        }
        for name, contract in _REQUIRED_MUTATION_FOREIGN_KEYS.items():
            if foreign_keys.get(name) != contract:
                raise _migration_required(f"enterprise ACL mutation foreign key is missing: {name}")

        for table_name, required_checks in _REQUIRED_MUTATION_CHECKS.items():
            checks = {
                str(item.get("name")): _normalized_sql(item.get("sqltext"))
                for item in inspector.get_check_constraints(table_name)
            }
            for name, fragments in required_checks.items():
                sql = checks.get(name, "")
                if not sql or any(fragment not in sql for fragment in fragments):
                    raise _migration_required(
                        f"enterprise ACL mutation check constraint is missing: {name}"
                    )
    except EnterpriseAccessGrantMigrationRequired:
        raise
    except EnterpriseAccessGrantUnavailable:
        raise
    except Exception as exc:
        raise EnterpriseAccessGrantUnavailable(
            "enterprise ACL mutation schema inspection failed"
        ) from exc


def _locked(statement: Any, *, lock_for_update: bool) -> Any:
    return statement.with_for_update() if lock_for_update else statement


@contextmanager
def _mutation_transaction(session: Session) -> Iterator[None]:
    """Open the mutation transaction with a provable SQLite write fence.

    SQLite ignores ``FOR UPDATE``.  ``BEGIN IMMEDIATE`` acquires its cross-
    process write reservation before schema inspection or idempotency lookup,
    so a second writer waits and then observes the first completed ledger row.
    """

    dialect = str(getattr(session.get_bind().dialect, "name", ""))
    if dialect != "sqlite":
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


_SENSITIVE_REASON = re.compile(
    r"(?i)(authorization\s*:|bearer\s+[A-Za-z0-9._~-]+|cookie\s*:|"
    r"password\s*[=:]|api[-_ ]?key\s*[=:]|mysql(?:\+\w+)?://[^\s]+@|"
    r"[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}|"
    r"sk_(?:live|test)_[A-Za-z0-9]{12,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"gh[pousr]_[A-Za-z0-9]{20,}|glpat-[A-Za-z0-9_-]{20,}|"
    r"AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|"
    r"xox[baprs]-[A-Za-z0-9-]{20,}|hf_[A-Za-z0-9]{20,}|"
    r"sk-[A-Za-z0-9_-]{20,}|ya29\.[A-Za-z0-9_-]{20,})"
)


def _safe_reason(reason: str) -> str:
    # This is a conservative high-confidence detector, not a claim that a
    # blacklist can identify every credential format.  Raw request bodies are
    # never stored in the idempotency ledger; matching audit notes are replaced.
    return "[REDACTED]" if _SENSITIVE_REASON.search(reason) else reason


def _grant_snapshot(
    grant: DatasetAccessGrant | dict[str, Any], *, reason: str | None = None
) -> dict[str, Any]:
    def value(name: str) -> Any:
        return grant.get(name) if isinstance(grant, dict) else getattr(grant, name)

    snapshot = {
        "id": str(value("id")),
        "dataset_id": str(value("dataset_id")),
        "subject_type": str(value("subject_type")),
        "subject_id": str(value("subject_id")),
        "role": str(value("role")),
        "status": str(value("status")),
        "revision": int(value("revision")),
    }
    if reason is not None:
        snapshot["reason"] = _safe_reason(reason)
    return snapshot


def _subject_name(
    session: Session,
    *,
    tenant_id: str,
    subject_type: GrantSubjectType,
    subject_id: str,
    lock_for_update: bool,
) -> str:
    if subject_type == "account":
        statement = (
            select(Account.name)
            .join(TenantMember, TenantMember.account_id == Account.id)
            .where(
                TenantMember.tenant_id == tenant_id,
                TenantMember.account_id == subject_id,
                TenantMember.status == "active",
            )
        )
    elif subject_type == "group":
        statement = select(TenantGroup.name).where(
            TenantGroup.tenant_id == tenant_id,
            TenantGroup.id == subject_id,
            TenantGroup.status == "active",
        )
    else:
        statement = select(TenantOrganizationUnit.name).where(
            TenantOrganizationUnit.tenant_id == tenant_id,
            TenantOrganizationUnit.id == subject_id,
            TenantOrganizationUnit.status == "active",
        )
    resolved = session.scalar(_locked(statement, lock_for_update=lock_for_update))
    if resolved is None:
        raise EnterpriseAccessGrantSubjectNotFound("access grant subject is not active in tenant")
    return str(resolved)


def _grant_payload(grant: DatasetAccessGrant, *, subject_name: str) -> dict[str, Any]:
    return {
        "id": str(grant.id),
        "dataset_id": str(grant.dataset_id),
        "subject_type": str(grant.subject_type),
        "subject_id": str(grant.subject_id),
        "subject_name": subject_name,
        "role": str(grant.role),
        "status": str(grant.status),
        "revision": int(grant.revision),
    }


def _authorization_payload(
    decision: DatasetAccessDecision,
    *,
    actor_role: str,
) -> dict[str, Any]:
    return {
        "mode": decision.enforcement_mode,
        "permission": KNOWLEDGE_MANAGE,
        "actor_role": actor_role,
        "dataset_role": decision.dataset_role,
        "bypass_reason": decision.bypass_reason,
    }


def _actor_context(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    lock_for_update: bool,
) -> tuple[str, str, str]:
    row = session.execute(
        _locked(
            select(TenantMember.role, TenantMember.status, Account.name, Account.email)
            .join(Account, Account.id == TenantMember.account_id)
            .where(
                TenantMember.tenant_id == tenant_id,
                TenantMember.account_id == actor_id,
            ),
            lock_for_update=lock_for_update,
        )
    ).one_or_none()
    if row is None or str(row.status).strip().casefold() != "active":
        raise EnterpriseAccessGrantForbidden("actor is not an active tenant member")
    role = str(row.role or "").strip().casefold()
    if role not in {"owner", "admin", "editor", "member"}:
        raise EnterpriseAccessGrantUnavailable("actor tenant role is invalid")
    return role, str(row.name or "")[:128], str(row.email or "")[:256]


def _authorize(
    engine: Any,
    session: Session,
    *,
    tenant_id: str,
    dataset_id: str,
    actor_id: str,
    actor_role: str,
    lock_for_update: bool,
) -> tuple[DatasetAccessDecision, str, str, str]:
    persisted_role, actor_name, actor_email = _actor_context(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        lock_for_update=lock_for_update,
    )
    # actor_role remains an identity/audit attribute and is intentionally not
    # overwritten. The transaction derives authorization from the persisted role.
    _ = str(actor_role or "").strip().casefold()
    decision = evaluate_dataset_permissions(
        engine,
        tenant_id,
        actor_id,
        persisted_role,
        dataset_id,
        session=session,
        lock_for_update=lock_for_update,
    )
    if not decision.allows(KNOWLEDGE_MANAGE):
        raise EnterpriseAccessGrantForbidden("actor cannot manage this dataset ACL")
    return decision, persisted_role, actor_name, actor_email


def _lock_dataset(
    session: Session,
    *,
    tenant_id: str,
    dataset_id: str,
    lock_for_update: bool,
) -> Dataset:
    dataset = session.scalar(
        _locked(
            select(Dataset)
            .options(
                load_only(
                    Dataset.id,
                    Dataset.tenant_id,
                    Dataset.status,
                    Dataset.acl_mode,
                    Dataset.acl_revision,
                    Dataset.acl_enabled_at,
                    Dataset.acl_enabled_by,
                )
            )
            .where(
                Dataset.tenant_id == tenant_id,
                Dataset.id == dataset_id,
            ),
            lock_for_update=lock_for_update,
        )
    )
    if dataset is None:
        raise EnterpriseAccessGrantNotFound("dataset is outside tenant scope")
    if str(dataset.status).strip().casefold() != "active":
        raise EnterpriseAccessGrantForbidden("dataset lifecycle does not allow ACL mutation")
    return dataset


def _lock_grant(
    session: Session,
    *,
    tenant_id: str,
    dataset_id: str,
    grant_id: str,
    lock_for_update: bool,
) -> DatasetAccessGrant:
    grant = session.scalar(
        _locked(
            select(DatasetAccessGrant).where(
                DatasetAccessGrant.tenant_id == tenant_id,
                DatasetAccessGrant.dataset_id == dataset_id,
                DatasetAccessGrant.id == grant_id,
            ),
            lock_for_update=lock_for_update,
        )
    )
    if grant is None:
        raise EnterpriseAccessGrantNotFound("dataset access grant does not exist")
    return grant


def _append_audit(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    actor_name: str,
    actor_email: str,
    action: str,
    grant: DatasetAccessGrant | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    target_account_id: str | None = None,
    before_snapshot: dict[str, Any] | None,
    after_snapshot: dict[str, Any] | None,
    request_id: str,
    request_ip: str,
) -> TenantAuditEvent:
    if grant is not None:
        resource_type = resource_type or "dataset_access_grant"
        resource_id = resource_id or str(grant.id)
        if target_account_id is None and grant.subject_type == "account":
            target_account_id = str(grant.subject_id)
    if not resource_type or not resource_id:
        raise ValueError("audit resource scope is required")
    event = TenantAuditEvent(
        id=f"tenant-audit-{uuid.uuid4().hex}",
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_name_snapshot=actor_name,
        actor_email_snapshot=actor_email,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        target_account_id=target_account_id,
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        request_id=request_id,
        request_ip=request_ip,
        occurred_at=datetime.utcnow(),
    )
    session.add(event)
    session.flush()
    return event


def _result(
    grant: DatasetAccessGrant,
    *,
    subject_name: str,
    audit: TenantAuditEvent,
    decision: DatasetAccessDecision,
    actor_role: str,
) -> dict[str, Any]:
    return {
        "grant": _grant_payload(grant, subject_name=subject_name),
        "audit": {"id": str(audit.id), "sequence": int(audit.sequence)},
        "authorization": _authorization_payload(decision, actor_role=actor_role),
    }


def _public_mutation_response(
    response: dict[str, Any], *, explicit_idempotency: bool
) -> dict[str, Any]:
    """Keep the durable replay body to the smallest public projection.

    Audit and authorization details remain available to legacy direct service
    callers, but HTTP requests carrying an Idempotency-Key return/store only
    the resource projection.  This prevents internal envelopes from becoming
    durable request data while preserving exact response replay.
    """

    _ = explicit_idempotency
    if "grant" in response:
        return {"grant": response["grant"]}
    if "dataset" in response:
        return {"dataset": response["dataset"]}
    raise EnterpriseAccessGrantUnavailable("mutation response projection is invalid")


def _mutation_idempotency(
    *,
    idempotency_key: str | None,
    request_id: str,
    operation: str,
    path_identity: Mapping[str, str],
    body: Mapping[str, Any],
) -> tuple[str, str]:
    """Hash validated semantic input, including every path-bound identity."""

    if idempotency_key is None:
        raise EnterpriseAclIdempotencyValidationError(
            "Idempotency-Key is required for Dataset ACL mutations"
        )
    _ = request_id
    normalized = normalize_idempotency_key(idempotency_key)
    digest = canonical_request_hash(
        {
            "operation": operation,
            "path": dict(path_identity),
            "body": dict(body),
        }
    )
    return normalized, digest


def _reserve_mutation(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    operation: str,
    idempotency_key: str,
    request_hash: str,
) -> IdempotencyReservation:
    return reserve_idempotency(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        dataset_id=dataset_id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )


def _enable_acl(
    session: Session,
    dataset: Dataset,
    *,
    actor_id: str,
) -> None:
    """Persist the one-way grant-side transition into Dataset ACL mode."""

    mode = str(getattr(dataset, "acl_mode", "tenant_role") or "").strip().casefold()
    if mode == "dataset_acl":
        return
    if mode != "tenant_role":
        raise EnterpriseAccessGrantUnavailable("dataset ACL mode is invalid")
    current_revision = int(getattr(dataset, "acl_revision", 1) or 0)
    dataset.acl_mode = "dataset_acl"
    dataset.acl_revision = current_revision + 1
    dataset.acl_enabled_at = datetime.utcnow()
    dataset.acl_enabled_by = actor_id
    # Flush explicitly before the grant INSERT.  Besides making the state
    # transition atomic, this gives operators and tests a visible Dataset-row
    # lock/update before any child grant write.
    session.flush()


def _dataset_control_snapshot(dataset: Dataset, *, reason: str | None = None) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "dataset_id": str(dataset.id),
        "acl_mode": str(dataset.acl_mode),
        "acl_revision": int(dataset.acl_revision),
    }
    if reason is not None:
        snapshot["reason"] = _safe_reason(reason)
    return snapshot


def _append_dataset_audit(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    actor_name: str,
    actor_email: str,
    dataset: Dataset,
    before_snapshot: dict[str, Any],
    after_snapshot: dict[str, Any],
    request_id: str,
    request_ip: str,
) -> TenantAuditEvent:
    event = TenantAuditEvent(
        id=f"tenant-audit-{uuid.uuid4().hex}",
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_name_snapshot=actor_name,
        actor_email_snapshot=actor_email,
        action="dataset_access_control.disabled",
        resource_type="dataset_access_control",
        resource_id=str(dataset.id),
        target_account_id=None,
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        request_id=request_id,
        request_ip=request_ip,
        occurred_at=datetime.utcnow(),
    )
    session.add(event)
    session.flush()
    return event


def create_dataset_access_grant(
    engine: Any,
    *,
    tenant_id: str,
    dataset_id: str,
    actor_id: str,
    actor_role: str,
    subject_type: str,
    subject_id: str,
    role: str,
    reason: str,
    request_id: str,
    request_ip: str = "",
    idempotency_key: str | None = None,
    request_payload: dict[str, Any] | None = None,
    lock_for_update: bool = True,
) -> dict[str, Any]:
    """Create one active dataset grant and its audit event atomically."""

    tenant_id = _clean(tenant_id, field="tenant_id", maximum=64)
    dataset_id = _clean(dataset_id, field="dataset_id", maximum=64)
    actor_id = _clean(actor_id, field="actor_id", maximum=64)
    normalized_subject_type = _subject_type(subject_type)
    subject_id = _clean(subject_id, field="subject_id", maximum=64)
    normalized_role = _role(role)
    reason = _clean(reason, field="reason", maximum=512)
    request_id = _clean(request_id, field="request_id", maximum=128)
    request_ip = str(request_ip or "").strip()[:64]
    _ = request_payload
    key, request_hash = _mutation_idempotency(
        idempotency_key=idempotency_key,
        request_id=request_id,
        operation="grant.create",
        path_identity={"dataset_id": dataset_id},
        body={
            "subject_type": normalized_subject_type,
            "subject_id": subject_id,
            "role": normalized_role,
            "reason": reason,
        },
    )
    lock_key = idempotency_key_digest(tenant_id, actor_id, key)

    explicit_idempotency = idempotency_key is not None

    try:
        with idempotency_key_lock(tenant_id, actor_id, lock_key):
            with engine_serialization_lock(engine):
                with Session(engine, expire_on_commit=False) as session:
                    with _mutation_transaction(session):
                        _ensure_mutation_schema(session.connection())
                        dataset = _lock_dataset(
                            session,
                            tenant_id=tenant_id,
                            dataset_id=dataset_id,
                            lock_for_update=lock_for_update,
                        )
                        _actor_context(
                            session,
                            tenant_id=tenant_id,
                            actor_id=actor_id,
                            lock_for_update=lock_for_update,
                        )
                        reservation: IdempotencyReservation | None = None
                        if explicit_idempotency:
                            reservation = _reserve_mutation(
                                session,
                                tenant_id=tenant_id,
                                actor_id=actor_id,
                                dataset_id=dataset_id,
                                operation="grant.create",
                                idempotency_key=key,
                                request_hash=request_hash,
                            )
                            if reservation.replay is not None:
                                return reservation.replay.response
                        decision, persisted_role, actor_name, actor_email = _authorize(
                            engine,
                            session,
                            tenant_id=tenant_id,
                            dataset_id=dataset_id,
                            actor_id=actor_id,
                            actor_role=actor_role,
                            lock_for_update=lock_for_update,
                        )
                        _enable_acl(session, dataset, actor_id=actor_id)
                        subject_name = _subject_name(
                            session,
                            tenant_id=tenant_id,
                            subject_type=normalized_subject_type,
                            subject_id=subject_id,
                            lock_for_update=lock_for_update,
                        )
                        existing = session.scalar(
                            _locked(
                                select(DatasetAccessGrant).where(
                                    DatasetAccessGrant.tenant_id == tenant_id,
                                    DatasetAccessGrant.dataset_id == dataset_id,
                                    DatasetAccessGrant.subject_type == normalized_subject_type,
                                    DatasetAccessGrant.subject_id == subject_id,
                                ),
                                lock_for_update=lock_for_update,
                            )
                        )
                        if existing is not None:
                            raise EnterpriseAccessGrantStateConflict(
                                "dataset_access_grant_already_exists",
                                "该主体已存在知识库授权，请修改或恢复现有授权",
                            )
                        grant = DatasetAccessGrant(
                            id=f"dataset-grant-{uuid.uuid4().hex}",
                            tenant_id=tenant_id,
                            dataset_id=dataset_id,
                            subject_type=normalized_subject_type,
                            subject_id=subject_id,
                            role=normalized_role,
                            status="active",
                            revision=1,
                        )
                        session.add(grant)
                        session.flush()
                        post_decision = evaluate_dataset_permissions(
                            engine,
                            tenant_id,
                            actor_id,
                            persisted_role,
                            dataset_id,
                            session=session,
                            lock_for_update=lock_for_update,
                        )
                        audit = _append_audit(
                            session,
                            tenant_id=tenant_id,
                            actor_id=actor_id,
                            actor_name=actor_name,
                            actor_email=actor_email,
                            action="dataset_access_grant.created",
                            resource_type="dataset_access_grant",
                            resource_id=str(grant.id),
                            target_account_id=(
                                str(grant.subject_id) if grant.subject_type == "account" else None
                            ),
                            before_snapshot=None,
                            after_snapshot=_grant_snapshot(grant, reason=reason),
                            request_id=request_id,
                            request_ip=request_ip,
                        )
                        response = _result(
                            grant,
                            subject_name=subject_name,
                            audit=audit,
                            decision=post_decision,
                            actor_role=persisted_role,
                        )
                        public_response = _public_mutation_response(
                            response, explicit_idempotency=explicit_idempotency
                        )
                        if reservation is None:
                            return public_response
                        return complete_idempotency(
                            session,
                            reservation,
                            response=public_response,
                            http_status=201,
                            resource_id=str(grant.id),
                        )
    except EnterpriseAccessGrantError:
        raise
    except (
        EnterpriseAccessGrantValidationError,
        EnterpriseAclIdempotencyValidationError,
        EnterpriseAclIdempotencyConflict,
        EnterpriseAclIdempotencyInProgress,
    ):
        raise
    except DatasetAccessControlUnavailable as exc:
        raise EnterpriseAccessGrantUnavailable("dataset ACL authorization unavailable") from exc
    except IntegrityError as exc:
        raise EnterpriseAccessGrantStateConflict(
            "dataset_access_grant_conflict",
            "知识库授权已发生冲突，请刷新后重试",
        ) from exc
    except SQLAlchemyError as exc:
        raise EnterpriseAccessGrantUnavailable("dataset ACL mutation failed") from exc


def _mutate_existing(
    engine: Any,
    *,
    tenant_id: str,
    dataset_id: str,
    actor_id: str,
    actor_role: str,
    grant_id: str,
    expected_revision: int,
    operation: Literal["role", "revoke", "resume"],
    role: str | None,
    reason: str,
    request_id: str,
    request_ip: str,
    idempotency_key: str | None,
    request_payload: dict[str, Any] | None,
    lock_for_update: bool,
) -> dict[str, Any]:
    tenant_id = _clean(tenant_id, field="tenant_id", maximum=64)
    dataset_id = _clean(dataset_id, field="dataset_id", maximum=64)
    actor_id = _clean(actor_id, field="actor_id", maximum=64)
    grant_id = _clean(grant_id, field="grant_id", maximum=64)
    expected_revision = _revision(expected_revision)
    normalized_role = _role(role) if role is not None else None
    reason = _clean(reason, field="reason", maximum=512)
    request_id = _clean(request_id, field="request_id", maximum=128)
    request_ip = str(request_ip or "").strip()[:64]
    operation_name = f"grant.{operation}"
    _ = request_payload
    key, request_hash = _mutation_idempotency(
        idempotency_key=idempotency_key,
        request_id=request_id,
        operation=operation_name,
        path_identity={"dataset_id": dataset_id, "grant_id": grant_id},
        body={
            "grant_id": grant_id,
            "revision": expected_revision,
            "role": normalized_role,
            "reason": reason,
        },
    )
    lock_key = idempotency_key_digest(tenant_id, actor_id, key)

    explicit_idempotency = idempotency_key is not None
    try:
        with idempotency_key_lock(tenant_id, actor_id, lock_key):
            with engine_serialization_lock(engine):
                with Session(engine, expire_on_commit=False) as session:
                    with _mutation_transaction(session):
                        _ensure_mutation_schema(session.connection())
                        dataset = _lock_dataset(
                            session,
                            tenant_id=tenant_id,
                            dataset_id=dataset_id,
                            lock_for_update=lock_for_update,
                        )
                        _actor_context(
                            session,
                            tenant_id=tenant_id,
                            actor_id=actor_id,
                            lock_for_update=lock_for_update,
                        )
                        reservation: IdempotencyReservation | None = None
                        if explicit_idempotency:
                            reservation = _reserve_mutation(
                                session,
                                tenant_id=tenant_id,
                                actor_id=actor_id,
                                dataset_id=dataset_id,
                                operation=operation_name,
                                idempotency_key=key,
                                request_hash=request_hash,
                            )
                            if reservation.replay is not None:
                                return reservation.replay.response
                        decision, persisted_role, actor_name, actor_email = _authorize(
                            engine,
                            session,
                            tenant_id=tenant_id,
                            dataset_id=dataset_id,
                            actor_id=actor_id,
                            actor_role=actor_role,
                            lock_for_update=lock_for_update,
                        )
                        _enable_acl(session, dataset, actor_id=actor_id)
                        grant = _lock_grant(
                            session,
                            tenant_id=tenant_id,
                            dataset_id=dataset_id,
                            grant_id=grant_id,
                            lock_for_update=lock_for_update,
                        )
                        current_revision = int(grant.revision)
                        if current_revision != expected_revision:
                            raise EnterpriseAccessGrantRevisionConflict(
                                expected_revision=expected_revision,
                                current_revision=current_revision,
                            )
                        current_status = str(grant.status).strip().casefold()
                        before = _grant_snapshot(grant)
                        if operation == "role":
                            if current_status != "active":
                                raise EnterpriseAccessGrantStateConflict(
                                    "dataset_access_grant_state_conflict",
                                    "只有 active 授权可以修改角色",
                                )
                            if normalized_role is None:
                                raise EnterpriseAccessGrantValidationError(
                                    "role is required for a role update"
                                )
                            if str(grant.role).casefold() == normalized_role:
                                raise EnterpriseAccessGrantStateConflict(
                                    "dataset_access_grant_role_unchanged",
                                    "授权角色没有变化",
                                )
                            values = {"role": normalized_role}
                            action = "dataset_access_grant.role_updated"
                        elif operation == "revoke":
                            if current_status != "active":
                                raise EnterpriseAccessGrantStateConflict(
                                    "dataset_access_grant_state_conflict",
                                    "只有 active 授权可以撤销",
                                )
                            # ACL mode is durable.  Revoking the last active grant
                            # is allowed and must not restore tenant-role fallback.
                            values = {"status": "revoked"}
                            action = "dataset_access_grant.revoked"
                        else:
                            if current_status != "revoked":
                                raise EnterpriseAccessGrantStateConflict(
                                    "dataset_access_grant_state_conflict",
                                    "只有 revoked 授权可以恢复",
                                )
                            # Revalidate the subject at resume time; a revoked grant may
                            # outlive the account/group/organization lifecycle.
                            values = {"status": "active"}
                            if normalized_role is not None:
                                values["role"] = normalized_role
                            action = "dataset_access_grant.resumed"

                        subject_name = _subject_name(
                            session,
                            tenant_id=tenant_id,
                            subject_type=_subject_type(grant.subject_type),
                            subject_id=str(grant.subject_id),
                            lock_for_update=lock_for_update,
                        )
                        values.update(
                            revision=current_revision + 1,
                            updated_at=datetime.utcnow(),
                        )
                        result = session.execute(
                            update(DatasetAccessGrant)
                            .where(
                                DatasetAccessGrant.tenant_id == tenant_id,
                                DatasetAccessGrant.dataset_id == dataset_id,
                                DatasetAccessGrant.id == grant_id,
                                DatasetAccessGrant.revision == expected_revision,
                            )
                            .values(**values)
                        )
                        if result.rowcount != 1:
                            current = session.scalar(
                                select(DatasetAccessGrant.revision).where(
                                    DatasetAccessGrant.tenant_id == tenant_id,
                                    DatasetAccessGrant.dataset_id == dataset_id,
                                    DatasetAccessGrant.id == grant_id,
                                )
                            )
                            if current is None:
                                raise EnterpriseAccessGrantNotFound(
                                    "dataset access grant disappeared during mutation"
                                )
                            raise EnterpriseAccessGrantRevisionConflict(
                                expected_revision=expected_revision,
                                current_revision=int(current),
                            )
                        session.expire(grant)
                        session.refresh(grant)
                        post_decision = evaluate_dataset_permissions(
                            engine,
                            tenant_id,
                            actor_id,
                            persisted_role,
                            dataset_id,
                            session=session,
                            lock_for_update=lock_for_update,
                        )
                        if (
                            operation in {"role", "revoke"}
                            and decision.bypass_reason is None
                            and not post_decision.allows(KNOWLEDGE_MANAGE)
                        ):
                            raise EnterpriseAccessGrantStateConflict(
                                "dataset_access_grant_actor_manage_protected",
                                "该变更会移除当前操作者唯一的知识库管理路径",
                            )
                        audit = _append_audit(
                            session,
                            tenant_id=tenant_id,
                            actor_id=actor_id,
                            actor_name=actor_name,
                            actor_email=actor_email,
                            action=action,
                            grant=grant,
                            before_snapshot=before,
                            after_snapshot=_grant_snapshot(grant, reason=reason),
                            request_id=request_id,
                            request_ip=request_ip,
                        )
                        response = _result(
                            grant,
                            subject_name=subject_name,
                            audit=audit,
                            decision=post_decision,
                            actor_role=persisted_role,
                        )
                        public_response = _public_mutation_response(
                            response, explicit_idempotency=explicit_idempotency
                        )
                        if reservation is None:
                            return public_response
                        return complete_idempotency(
                            session,
                            reservation,
                            response=public_response,
                            http_status=200,
                            resource_id=str(grant.id),
                        )
    except EnterpriseAccessGrantError:
        raise
    except (
        EnterpriseAccessGrantValidationError,
        EnterpriseAclIdempotencyValidationError,
        EnterpriseAclIdempotencyConflict,
        EnterpriseAclIdempotencyInProgress,
    ):
        raise
    except DatasetAccessControlUnavailable as exc:
        raise EnterpriseAccessGrantUnavailable("dataset ACL authorization unavailable") from exc
    except SQLAlchemyError as exc:
        raise EnterpriseAccessGrantUnavailable("dataset ACL mutation failed") from exc


def change_dataset_access_grant_role(
    engine: Any,
    *,
    tenant_id: str,
    dataset_id: str,
    actor_id: str,
    actor_role: str,
    grant_id: str,
    expected_revision: int,
    role: str,
    reason: str,
    request_id: str,
    request_ip: str = "",
    idempotency_key: str | None = None,
    request_payload: dict[str, Any] | None = None,
    lock_for_update: bool = True,
) -> dict[str, Any]:
    return _mutate_existing(
        engine,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        actor_id=actor_id,
        actor_role=actor_role,
        grant_id=grant_id,
        expected_revision=expected_revision,
        operation="role",
        role=role,
        reason=reason,
        request_id=request_id,
        request_ip=request_ip,
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        lock_for_update=lock_for_update,
    )


def revoke_dataset_access_grant(
    engine: Any,
    *,
    tenant_id: str,
    dataset_id: str,
    actor_id: str,
    actor_role: str,
    grant_id: str,
    expected_revision: int,
    reason: str,
    request_id: str,
    request_ip: str = "",
    idempotency_key: str | None = None,
    request_payload: dict[str, Any] | None = None,
    lock_for_update: bool = True,
) -> dict[str, Any]:
    return _mutate_existing(
        engine,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        actor_id=actor_id,
        actor_role=actor_role,
        grant_id=grant_id,
        expected_revision=expected_revision,
        operation="revoke",
        role=None,
        reason=reason,
        request_id=request_id,
        request_ip=request_ip,
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        lock_for_update=lock_for_update,
    )


def resume_dataset_access_grant(
    engine: Any,
    *,
    tenant_id: str,
    dataset_id: str,
    actor_id: str,
    actor_role: str,
    grant_id: str,
    expected_revision: int,
    reason: str,
    request_id: str,
    role: str | None = None,
    request_ip: str = "",
    idempotency_key: str | None = None,
    request_payload: dict[str, Any] | None = None,
    lock_for_update: bool = True,
) -> dict[str, Any]:
    return _mutate_existing(
        engine,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        actor_id=actor_id,
        actor_role=actor_role,
        grant_id=grant_id,
        expected_revision=expected_revision,
        operation="resume",
        role=role,
        reason=reason,
        request_id=request_id,
        request_ip=request_ip,
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        lock_for_update=lock_for_update,
    )


def disable_dataset_acl(
    engine: Any,
    *,
    tenant_id: str,
    dataset_id: str,
    actor_id: str,
    actor_role: str,
    expected_acl_revision: int,
    reason: str,
    request_id: str,
    request_ip: str = "",
    idempotency_key: str | None = None,
    request_payload: dict[str, Any] | None = None,
    lock_for_update: bool = True,
    approval_execution_id: str | None = None,
) -> dict[str, Any]:
    """Explicitly leave ACL mode; only an active tenant owner/admin may do so."""

    tenant_id = _clean(tenant_id, field="tenant_id", maximum=64)
    dataset_id = _clean(dataset_id, field="dataset_id", maximum=64)
    actor_id = _clean(actor_id, field="actor_id", maximum=64)
    expected_acl_revision = _revision(expected_acl_revision)
    reason = _clean(reason, field="reason", maximum=512)
    request_id = _clean(request_id, field="request_id", maximum=128)
    request_ip = str(request_ip or "").strip()[:64]
    approval_execution = str(approval_execution_id or "").strip()
    if approval_execution_id is not None and (
        not approval_execution or len(approval_execution) > 128
    ):
        raise EnterpriseAccessGrantValidationError("approval execution id is invalid")
    _ = request_payload
    key, request_hash = _mutation_idempotency(
        idempotency_key=idempotency_key,
        request_id=request_id,
        operation="access_control.disable",
        path_identity={"dataset_id": dataset_id},
        body={"expected_acl_revision": expected_acl_revision, "reason": reason},
    )
    lock_key = idempotency_key_digest(tenant_id, actor_id, key)

    explicit_idempotency = idempotency_key is not None
    try:
        with idempotency_key_lock(tenant_id, actor_id, lock_key):
            with engine_serialization_lock(engine):
                with Session(engine, expire_on_commit=False) as session:
                    with _mutation_transaction(session):
                        _ensure_mutation_schema(session.connection())
                        dataset = _lock_dataset(
                            session,
                            tenant_id=tenant_id,
                            dataset_id=dataset_id,
                            lock_for_update=lock_for_update,
                        )
                        persisted_role, actor_name, actor_email = _actor_context(
                            session,
                            tenant_id=tenant_id,
                            actor_id=actor_id,
                            lock_for_update=lock_for_update,
                        )
                        if persisted_role not in {"owner", "admin"}:
                            raise EnterpriseAccessGrantForbidden(
                                "only active tenant owner/admin may disable dataset ACL"
                            )
                        session.execute(
                            select(Tenant.id).where(Tenant.id == tenant_id).with_for_update()
                        ).scalar_one()
                        if not approval_execution:
                            policy = resolve_active_approval_policy_in_session(
                                session,
                                tenant_id=tenant_id,
                                action_type="dataset_acl_disable",
                                resource_type="knowledge_base",
                                resource_id=dataset_id,
                                lock_for_update=True,
                            )
                            if policy is not None:
                                raise EnterpriseAccessApprovalRequired(policy)
                        reservation: IdempotencyReservation | None = None
                        if explicit_idempotency:
                            reservation = _reserve_mutation(
                                session,
                                tenant_id=tenant_id,
                                actor_id=actor_id,
                                dataset_id=dataset_id,
                                operation="access_control.disable",
                                idempotency_key=key,
                                request_hash=request_hash,
                            )
                            if reservation.replay is not None:
                                return reservation.replay.response
                        current_mode = str(dataset.acl_mode or "").strip().casefold()
                        current_revision = int(dataset.acl_revision)
                        if current_revision != expected_acl_revision:
                            raise EnterpriseAccessControlRevisionConflict(
                                expected_revision=expected_acl_revision,
                                current_revision=current_revision,
                            )
                        if current_mode != "dataset_acl":
                            raise EnterpriseAccessGrantStateConflict(
                                "dataset_access_control_not_enabled",
                                "知识库当前未启用 ACL",
                            )
                        before = _dataset_control_snapshot(dataset)
                        dataset.acl_mode = "tenant_role"
                        dataset.acl_revision = current_revision + 1
                        dataset.acl_enabled_at = None
                        dataset.acl_enabled_by = None
                        session.flush()
                        after = _dataset_control_snapshot(dataset, reason=reason)
                        audit = _append_audit(
                            session,
                            tenant_id=tenant_id,
                            actor_id=actor_id,
                            actor_name=actor_name,
                            actor_email=actor_email,
                            action="dataset_access_control.disabled",
                            resource_type="dataset_access_control",
                            resource_id=str(dataset.id),
                            target_account_id=None,
                            before_snapshot=before,
                            after_snapshot=after,
                            request_id=request_id,
                            request_ip=request_ip,
                        )
                        response = {
                            "dataset": {
                                "id": str(dataset.id),
                                "tenant_id": str(dataset.tenant_id),
                                "acl_mode": str(dataset.acl_mode),
                                "acl_revision": int(dataset.acl_revision),
                            },
                            "audit": {
                                "id": str(audit.id),
                                "sequence": int(audit.sequence),
                            },
                            "authorization": {
                                "mode": "tenant_role_fallback",
                                "permission": KNOWLEDGE_MANAGE,
                                "actor_role": persisted_role,
                                "bypass_reason": f"tenant_{persisted_role}",
                            },
                        }
                        public_response = _public_mutation_response(
                            response, explicit_idempotency=explicit_idempotency
                        )
                        if reservation is None:
                            return public_response
                        return complete_idempotency(
                            session,
                            reservation,
                            response=public_response,
                            http_status=200,
                            resource_id=str(dataset.id),
                        )
    except EnterpriseAccessGrantError:
        raise
    except (
        EnterpriseAccessGrantValidationError,
        EnterpriseAclIdempotencyValidationError,
        EnterpriseAclIdempotencyConflict,
        EnterpriseAclIdempotencyInProgress,
    ):
        raise
    except SQLAlchemyError as exc:
        raise EnterpriseAccessGrantUnavailable("dataset ACL disable failed") from exc


__all__ = [
    "EnterpriseAccessControlRevisionConflict",
    "EnterpriseAccessApprovalRequired",
    "EnterpriseAccessGrantConflict",
    "EnterpriseAccessGrantError",
    "EnterpriseAccessGrantForbidden",
    "EnterpriseAccessGrantNotFound",
    "EnterpriseAccessGrantRevisionConflict",
    "EnterpriseAccessGrantStateConflict",
    "EnterpriseAccessGrantSubjectNotFound",
    "EnterpriseAccessGrantUnavailable",
    "EnterpriseAccessGrantValidationError",
    "change_dataset_access_grant_role",
    "create_dataset_access_grant",
    "resume_dataset_access_grant",
    "revoke_dataset_access_grant",
]
