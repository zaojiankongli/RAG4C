"""Reflection-backed enterprise workspace control-plane services.

Stage 16 deliberately keeps Workspace authority separate from Dataset ACL
authorization. The module owns tenant-scoped workspace facts and lifecycle
mutations only; callers must continue to use the existing Dataset ACL engine
until a later stage connects workspace membership to effective permissions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import base64
import json
import re
import threading
import weakref
from typing import Any, Literal
import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    and_,
    func,
    inspect,
    insert,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.orm import Session

from core.catalog_schema import (
    ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_CHECK_FRAGMENTS as _MANIFEST_CHECK_FRAGMENTS,
    ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_EXACT_CHECK_SQL as _MANIFEST_EXACT_CHECK_SQL,
    ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_COLUMNS as _MANIFEST_COLUMNS,
    ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_FOREIGN_KEYS as _MANIFEST_FOREIGN_KEYS,
    ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_INDEXES as _MANIFEST_INDEXES,
    ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_NOT_NULL as _MANIFEST_NOT_NULL,
    ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_UNIQUES as _MANIFEST_UNIQUES,
    ENTERPRISE_WORKSPACE_AUTHORIZATION_REVISION as _AUTHORIZATION_REVISION,
    ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REVISION as _REGISTRY_REVISION,
    inspect_workspace_authorization_capability,
    _known_catalog_revisions,
    _canonical_check_sql,
    _normalized_sql,
)
from core.enterprise_approval_control import sanitize_reason, tenant_authority_transaction
from core.enterprise_tenant_idempotency import (
    TenantMutationIdempotencyConflict,
    TenantMutationIdempotencyInProgress,
    TenantMutationIdempotencyValidationError,
    complete_tenant_mutation,
    engine_serialization_lock,
    idempotency_key_lock,
    reserve_tenant_mutation,
    tenant_idempotency_key_digest,
    tenant_request_hash,
)
from core.knowledge_governance import sanitize_audit_snapshot

_REVISION = "0026_enterprise_workspace_control"


def _revision_order(value: str) -> int:
    prefix, separator, _suffix = value.partition("_")
    return int(prefix) if separator and prefix.isdigit() else -1


_SUPPORTED_REVISIONS = frozenset(
    revision
    for revision in _known_catalog_revisions()
    if _revision_order(revision) >= _revision_order(_REVISION)
)
_REGISTRY_MANAGED_REVISIONS = frozenset(
    revision
    for revision in _SUPPORTED_REVISIONS
    if _revision_order(revision) >= _revision_order(_REGISTRY_REVISION)
)
_WORKSPACE_STATUSES = frozenset({"active", "archived"})
_WORKSPACE_ENVIRONMENTS = frozenset({"development", "testing", "production"})
_WORKSPACE_MEMBER_ROLES = frozenset({"owner", "admin", "editor", "viewer"})
_WORKSPACE_MEMBER_STATUSES = frozenset({"active", "removed"})
_BINDING_KINDS = frozenset({"primary", "shared"})
_BINDING_STATUSES = frozenset({"active", "removed"})
_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_AUTHORIZATION_BOUNDARY = {
    "state": "workspace_authorization_not_enforced",
    "enforced": False,
    "message": "Workspace 角色已记录，但尚未替代现有 Dataset ACL 授权引擎",
}
_REQUIRED_TABLES = frozenset(
    {
        "accounts",
        "tenants",
        "tenant_members",
        "datasets",
        "tenant_audit_events",
        "tenant_control_mutation_requests",
        "tenant_workspaces",
        "tenant_workspace_members",
        "tenant_workspace_datasets",
        "alembic_version",
    }
)
_REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "tenant_workspaces": frozenset(
        {
            "id",
            "tenant_id",
            "code",
            "name",
            "normalized_name",
            "description",
            "status",
            "environment",
            "is_default",
            "active_default_slot",
            "revision",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
            "archived_at",
            "archived_by",
        }
    ),
    "tenant_workspace_members": frozenset(
        {
            "id",
            "tenant_id",
            "workspace_id",
            "account_id",
            "role",
            "status",
            "revision",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
            "removed_at",
            "removed_by",
        }
    ),
    "tenant_workspace_datasets": frozenset(
        {
            "id",
            "tenant_id",
            "workspace_id",
            "dataset_id",
            "binding_kind",
            "active_primary_slot",
            "status",
            "revision",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
            "removed_at",
            "removed_by",
        }
    ),
}
_REQUIRED_NOT_NULL: dict[str, frozenset[str]] = {
    "tenant_workspaces": frozenset(
        {
            "id",
            "tenant_id",
            "code",
            "name",
            "normalized_name",
            "description",
            "status",
            "environment",
            "is_default",
            "revision",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
        }
    ),
    "tenant_workspace_members": frozenset(
        {
            "id",
            "tenant_id",
            "workspace_id",
            "account_id",
            "role",
            "status",
            "revision",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
        }
    ),
    "tenant_workspace_datasets": frozenset(
        {
            "id",
            "tenant_id",
            "workspace_id",
            "dataset_id",
            "binding_kind",
            "status",
            "revision",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
        }
    ),
}


# The catalog manifest is the source of truth for the 0026 structural contract.
# Local aliases keep the gate inspectable in focused tests while preventing this
# service from drifting from the migration/ORM contract.
_REQUIRED_COLUMNS = {table: frozenset(columns) for table, columns in _MANIFEST_COLUMNS.items()}
_REQUIRED_NOT_NULL = {table: frozenset(columns) for table, columns in _MANIFEST_NOT_NULL.items()}
_REQUIRED_UNIQUES = {table: dict(values) for table, values in _MANIFEST_UNIQUES.items()}
_REQUIRED_FOREIGN_KEYS = {table: dict(values) for table, values in _MANIFEST_FOREIGN_KEYS.items()}
_REQUIRED_CHECK_FRAGMENTS = {
    table: dict(values) for table, values in _MANIFEST_CHECK_FRAGMENTS.items()
}
_REQUIRED_INDEXES = {table: dict(values) for table, values in _MANIFEST_INDEXES.items()}
# Explicit reverse-nullability contract. These columns are intentionally NULL-able
# because they represent optional default/primary slots or terminal evidence.
_REQUIRED_NULLABLE: dict[str, frozenset[str]] = {
    "tenant_workspaces": frozenset({"active_default_slot", "archived_at", "archived_by"}),
    "tenant_workspace_members": frozenset({"removed_at", "removed_by"}),
    "tenant_workspace_datasets": frozenset({"active_primary_slot", "removed_at", "removed_by"}),
}
# SQLAlchemy Engine equality can be URL-based, so WeakKeyDictionary may alias
# separate in-memory engines. Cache by object identity and retain a weak reference.
_SCHEMA_GATE_CACHE: dict[int, tuple[weakref.ReferenceType[Any], tuple[str, int | None]]] = {}
_SCHEMA_GATE_CACHE_LOCK = threading.RLock()
_WORKSPACE_TYPE_SPECS: dict[str, dict[str, tuple[str, int | None]]] = {
    "tenant_workspaces": {
        "id": ("string", 128),
        "tenant_id": ("string", 64),
        "code": ("string", 64),
        "name": ("string", 128),
        "normalized_name": ("string", 256),
        "description": ("string", 512),
        "status": ("string", 16),
        "environment": ("string", 16),
        "is_default": ("boolean", None),
        "active_default_slot": ("string", 16),
        "revision": ("integer", None),
        "created_at": ("datetime", None),
        "created_by": ("string", 64),
        "updated_at": ("datetime", None),
        "updated_by": ("string", 64),
        "archived_at": ("datetime", None),
        "archived_by": ("string", 64),
    },
    "tenant_workspace_members": {
        "id": ("integer", None),
        "tenant_id": ("string", 64),
        "workspace_id": ("string", 128),
        "account_id": ("string", 64),
        "role": ("string", 16),
        "status": ("string", 16),
        "revision": ("integer", None),
        "created_at": ("datetime", None),
        "created_by": ("string", 64),
        "updated_at": ("datetime", None),
        "updated_by": ("string", 64),
        "removed_at": ("datetime", None),
        "removed_by": ("string", 64),
    },
    "tenant_workspace_datasets": {
        "id": ("integer", None),
        "tenant_id": ("string", 64),
        "workspace_id": ("string", 128),
        "dataset_id": ("string", 64),
        "binding_kind": ("string", 16),
        "active_primary_slot": ("string", 16),
        "status": ("string", 16),
        "revision": ("integer", None),
        "created_at": ("datetime", None),
        "created_by": ("string", 64),
        "updated_at": ("datetime", None),
        "updated_by": ("string", 64),
        "removed_at": ("datetime", None),
        "removed_by": ("string", 64),
    },
}


class WorkspaceError(RuntimeError):
    """Base class for stable workspace service errors."""

    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class WorkspaceMigrationRequired(WorkspaceError):
    def __init__(self, missing: Sequence[str] = ()) -> None:
        self.missing = tuple(sorted(str(item) for item in missing))
        super().__init__(
            "workspace_migration_required",
            "Workspace 服务需要先完成 0026_enterprise_workspace_control 数据库迁移",
            503,
        )


class WorkspaceForbidden(WorkspaceError):
    def __init__(self, message: str = "当前身份无权执行 Workspace 操作") -> None:
        super().__init__("workspace_forbidden", message, 403)


class WorkspaceNotFound(WorkspaceError):
    def __init__(self, message: str = "Workspace 资源不存在") -> None:
        super().__init__("workspace_not_found", message, 404)


class WorkspaceValidation(WorkspaceError):
    def __init__(self, message: str = "Workspace 请求参数无效") -> None:
        super().__init__("workspace_validation_error", message, 422)


class WorkspaceConflict(WorkspaceError):
    pass


class WorkspaceRevisionConflict(WorkspaceConflict):
    def __init__(self, *, expected_revision: int, current_revision: int) -> None:
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__(
            "workspace_revision_conflict", "Workspace revision 已变化，请刷新后重试", 409
        )


class WorkspaceIdempotencyConflict(WorkspaceConflict):
    def __init__(self) -> None:
        super().__init__(
            "workspace_idempotency_conflict", "Idempotency-Key 已用于不同的 Workspace 请求", 409
        )


class WorkspaceIdempotencyInProgress(WorkspaceConflict):
    def __init__(self) -> None:
        super().__init__(
            "workspace_idempotency_in_progress", "相同的 Workspace 操作正在处理中", 409
        )


@dataclass(frozen=True)
class ServiceResult:
    body: dict[str, Any]
    status: int = 200

    def __getitem__(self, key: str) -> Any:
        return self.body[key]


@dataclass(frozen=True)
class _Actor:
    tenant_id: str
    account_id: str
    role: str
    name: str
    email: str


@dataclass(frozen=True)
class _Cursor:
    created_at: datetime
    item_id: str


def _table(session: Session, name: str) -> Table:
    return Table(name, MetaData(), autoload_with=session.connection())


def _value(row: Mapping[str, Any], name: str, default: Any = None) -> Any:
    try:
        return row[name]
    except (KeyError, TypeError):
        return default


def _datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"
    return str(value)


def _now(value: datetime | None) -> datetime:
    current = value or datetime.utcnow()
    if not isinstance(current, datetime):
        raise WorkspaceValidation("now 必须是 datetime")
    return current.replace(tzinfo=None)


def _clean(value: Any, field: str, maximum: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise WorkspaceValidation(f"{field} 必须是字符串")
    result = value.strip()
    if (not result and not allow_empty) or len(result) > maximum:
        raise WorkspaceValidation(f"{field} 无效")
    if any(ord(char) < 32 and char not in "\t\n\r" for char in result):
        raise WorkspaceValidation(f"{field} 包含非法控制字符")
    if field == "reason":
        return sanitize_reason(result)
    return result


def _positive_int(value: Any, field: str) -> int:
    if type(value) is not int or value < 1:
        raise WorkspaceValidation(f"{field} 必须是正整数")
    return value


def _enum(value: Any, field: str, values: frozenset[str]) -> str:
    result = _clean(value, field, 32).casefold()
    if result not in values:
        raise WorkspaceValidation(f"{field} 不在允许范围内")
    return result


def _optional_enum(value: Any, field: str, values: frozenset[str]) -> str | None:
    return None if value is None else _enum(value, field, values)


def _code(value: Any) -> tuple[str, str]:
    display = _clean(value, "code", 64)
    if not _CODE_RE.fullmatch(display):
        raise WorkspaceValidation("code 只能包含字母、数字、点、下划线和短横线")
    return display, display.casefold()


def _normalized_name(value: Any) -> tuple[str, str]:
    display = _clean(value, "name", 128)
    normalized = display.casefold()
    if len(normalized) > 256:
        raise WorkspaceValidation("name 规范化结果不能超过 256 个字符")
    return display, normalized


def _request_id(value: Any) -> str:
    return _clean(value, "request_id", 128)


def _request_ip(value: Any) -> str:
    return _clean(value or "", "request_ip", 64, allow_empty=True)


def _encode_cursor(value: datetime, item_id: str) -> str:
    raw = json.dumps(
        {"created_at": _iso(value), "id": item_id}, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode_cursor(value: str | None) -> _Cursor | None:
    if value is None or not str(value).strip():
        return None
    token = str(value).strip()
    if len(token) > 512:
        raise WorkspaceValidation("cursor 无效")
    try:
        padded = token + "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkspaceValidation("cursor 无效") from exc
    if not isinstance(payload, Mapping):
        raise WorkspaceValidation("cursor 无效")
    timestamp = _datetime(payload.get("created_at"))
    item_id = payload.get("id")
    if timestamp is None or not isinstance(item_id, str) or not item_id.strip():
        raise WorkspaceValidation("cursor 无效")
    return _Cursor(timestamp, item_id.strip())


def _schema_type_matches(column_type: Any, kind: str, length: int | None, dialect: str) -> bool:
    if kind == "string":
        return isinstance(column_type, String) and getattr(column_type, "length", None) == length
    if kind == "integer":
        return isinstance(column_type, Integer)
    if kind == "boolean":
        if isinstance(column_type, Boolean):
            return True
        return dialect in {"mysql", "mariadb"} and type(column_type).__name__.casefold() in {
            "tinyint",
            "boolean",
        }
    if kind == "datetime":
        if not isinstance(column_type, DateTime):
            return False
        return dialect not in {"mysql", "mariadb"} or getattr(column_type, "fsp", None) == 6
    return False


def _schema_issue_strings(inspector: Any, connection: Any) -> list[str]:
    dialect = str(getattr(connection.dialect, "name", "")).casefold()
    issues: list[str] = []
    for table_name, type_specs in _WORKSPACE_TYPE_SPECS.items():
        columns = {str(item.get("name")): item for item in inspector.get_columns(table_name)}
        for name, (kind, length) in type_specs.items():
            column = columns.get(name)
            if column is None:
                continue
            if not _schema_type_matches(column.get("type"), kind, length, dialect):
                expected = kind if length is None else f"{kind}({length})"
                issues.append(f"invalid type {table_name}.{name}: expected {expected}")

    for table_name, required in _REQUIRED_NULLABLE.items():
        columns = {str(item.get("name")): item for item in inspector.get_columns(table_name)}
        issues.extend(
            f"non-nullable column {table_name}.{name}"
            for name in sorted(required)
            if name in columns and not bool(columns[name].get("nullable", True))
        )

    for table_name, required in _REQUIRED_UNIQUES.items():
        actual = {
            item.get("name"): tuple(item.get("column_names") or ())
            for item in inspector.get_unique_constraints(table_name)
        }
        for constraint_name, columns in required.items():
            if actual.get(constraint_name) != tuple(columns):
                issues.append(f"missing or invalid unique {table_name}.{constraint_name}")

    for table_name, required in _REQUIRED_FOREIGN_KEYS.items():
        actual = {
            item.get("name"): (
                tuple(item.get("constrained_columns") or ()),
                item.get("referred_table"),
                tuple(item.get("referred_columns") or ()),
            )
            for item in inspector.get_foreign_keys(table_name)
        }
        for constraint_name, contract in required.items():
            if actual.get(constraint_name) != contract:
                issues.append(f"missing or invalid foreign key {table_name}.{constraint_name}")

    for table_name, required in _REQUIRED_CHECK_FRAGMENTS.items():
        actual = {
            item.get("name"): item.get("sqltext")
            for item in inspector.get_check_constraints(table_name)
        }
        exact_required = _MANIFEST_EXACT_CHECK_SQL.get(table_name, {})
        for constraint_name, fragments in required.items():
            raw_sql = actual.get(constraint_name)
            sql = _normalized_sql(raw_sql)
            expected_sql = exact_required.get(constraint_name)
            if expected_sql is not None:
                if not raw_sql or _canonical_check_sql(raw_sql) != _canonical_check_sql(
                    expected_sql
                ):
                    issues.append(f"missing or invalid exact check {table_name}.{constraint_name}")
            elif not sql or any(_normalized_sql(fragment) not in sql for fragment in fragments):
                issues.append(f"missing or invalid check {table_name}.{constraint_name}")

    for table_name, required in _REQUIRED_INDEXES.items():
        actual = {
            item.get("name"): tuple(item.get("column_names") or ())
            for item in inspector.get_indexes(table_name)
        }
        for index_name, columns in required.items():
            if actual.get(index_name) != tuple(columns):
                issues.append(f"missing or invalid index {table_name}.{index_name}")
    return issues


def _schema_cache_marker(connection: Any) -> int | None:
    if str(getattr(connection.dialect, "name", "")).casefold() != "sqlite":
        return None
    return int(connection.exec_driver_sql("PRAGMA schema_version").scalar_one())


def _schema_cache_hit(engine: Any, marker: int | None) -> bool:
    if marker is None:
        return False
    with _SCHEMA_GATE_CACHE_LOCK:
        entry = _SCHEMA_GATE_CACHE.get(id(engine))
        return entry is not None and entry[0]() is engine and entry[1] == (_REVISION, marker)


def _schema_cache_store(engine: Any, marker: int | None) -> None:
    if marker is None:
        return
    with _SCHEMA_GATE_CACHE_LOCK:
        _SCHEMA_GATE_CACHE[id(engine)] = (weakref.ref(engine), (_REVISION, marker))


def _ensure_0026(connection: Any) -> str:
    try:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        missing_tables = _REQUIRED_TABLES - tables
        if missing_tables:
            raise WorkspaceMigrationRequired(sorted(missing_tables))
        revisions = tuple(
            str(value)
            for value in connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalars()
        )
        if len(revisions) != 1 or revisions[0] not in _SUPPORTED_REVISIONS:
            raise WorkspaceMigrationRequired(("alembic_version", _REVISION))
        current_revision = revisions[0]
        if _revision_order(current_revision) >= _revision_order(_AUTHORIZATION_REVISION):
            capability_state, capability_issues = inspect_workspace_authorization_capability(
                connection
            )
            if capability_state != "ready":
                raise WorkspaceMigrationRequired(capability_issues or ("0027 capability",))
        marker = _schema_cache_marker(connection)
        engine = connection.engine
        if _schema_cache_hit(engine, marker):
            return current_revision

        issues: list[str] = []
        for table_name, required in _REQUIRED_COLUMNS.items():
            columns = {str(item.get("name")): item for item in inspector.get_columns(table_name)}
            missing_columns = required - set(columns)
            issues.extend(f"missing column {table_name}.{name}" for name in sorted(missing_columns))
            required_not_null = _REQUIRED_NOT_NULL.get(table_name, frozenset())
            issues.extend(
                f"nullable column {table_name}.{name}"
                for name in sorted(required_not_null)
                if name in columns and bool(columns[name].get("nullable", True))
            )
        issues.extend(_schema_issue_strings(inspector, connection))
        if issues:
            raise WorkspaceMigrationRequired(sorted(set(issues)))
        _schema_cache_store(engine, marker)
        return current_revision
    except WorkspaceMigrationRequired:
        raise
    except Exception as exc:
        raise WorkspaceMigrationRequired() from exc


def _actor(session: Session, tenant_id: str, actor_id: str) -> _Actor:
    tenants = _table(session, "tenants")
    members = _table(session, "tenant_members")
    accounts = _table(session, "accounts")
    row = (
        session.execute(
            select(
                members.c.role,
                members.c.status,
                accounts.c.name,
                accounts.c.email,
                tenants.c.status.label("tenant_status"),
            )
            .select_from(
                members.join(accounts, accounts.c.id == members.c.account_id).join(
                    tenants, tenants.c.id == members.c.tenant_id
                )
            )
            .where(members.c.tenant_id == tenant_id, members.c.account_id == actor_id)
        )
        .mappings()
        .one_or_none()
    )
    if (
        row is None
        or str(_value(row, "status", "")).casefold() != "active"
        or str(_value(row, "tenant_status", "")).casefold() != "active"
    ):
        raise WorkspaceForbidden("当前身份不是该租户的 active member")
    role = str(_value(row, "role", "")).casefold()
    if role not in {"owner", "admin", "editor", "member"}:
        raise WorkspaceForbidden("当前租户角色无效")
    return _Actor(
        tenant_id=tenant_id,
        account_id=actor_id,
        role=role,
        name=str(_value(row, "name", "")),
        email=str(_value(row, "email", "")),
    )


def _require_tenant_manager(actor: _Actor) -> None:
    if actor.role not in {"owner", "admin"}:
        raise WorkspaceForbidden("只有 tenant owner/admin 可以执行该 Workspace 操作")


def _workspace_row(
    session: Session, tenant_id: str, workspace_id: str, *, lock_for_update: bool = False
) -> Mapping[str, Any]:
    table = _table(session, "tenant_workspaces")
    statement = select(table).where(table.c.tenant_id == tenant_id, table.c.id == workspace_id)
    if lock_for_update:
        statement = statement.with_for_update()
    row = session.execute(statement).mappings().one_or_none()
    if row is None:
        raise WorkspaceNotFound()
    return row


def _lock_tenant(session: Session, tenant_id: str) -> None:
    tenants = _table(session, "tenants")
    row = (
        session.execute(
            select(tenants.c.id, tenants.c.status)
            .where(tenants.c.id == tenant_id)
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise WorkspaceNotFound("租户不存在")
    if str(row["status"]).casefold() != "active":
        raise WorkspaceForbidden("租户当前不可执行 Workspace 操作")


def _require_workspace_manager(
    session: Session, actor: _Actor, workspace_id: str, *, lock_for_update: bool = True
) -> Mapping[str, Any]:
    workspace = _workspace_row(
        session, actor.tenant_id, workspace_id, lock_for_update=lock_for_update
    )
    if actor.role == "owner":
        return workspace
    members = _table(session, "tenant_workspace_members")
    row = (
        session.execute(
            select(members.c.role, members.c.status).where(
                members.c.tenant_id == actor.tenant_id,
                members.c.workspace_id == workspace_id,
                members.c.account_id == actor.account_id,
            )
        )
        .mappings()
        .one_or_none()
    )
    if (
        row is None
        or str(row["status"]).casefold() != "active"
        or str(row["role"]).casefold() not in {"owner", "admin"}
    ):
        raise WorkspaceForbidden("当前身份不是该 Workspace 的 owner/admin")
    return workspace


def _workspace_payload(
    row: Mapping[str, Any], *, member_count: int | None = None, dataset_count: int | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(_value(row, "id")),
        "tenant_id": str(_value(row, "tenant_id")),
        "code": str(_value(row, "code")),
        "name": str(_value(row, "name")),
        "description": str(_value(row, "description", "")),
        "status": str(_value(row, "status")),
        "environment": str(_value(row, "environment")),
        "is_default": bool(_value(row, "is_default", False)),
        "active_default_slot": _value(row, "active_default_slot"),
        "revision": int(_value(row, "revision", 1)),
        "created_at": _iso(_value(row, "created_at")),
        "created_by": str(_value(row, "created_by")),
        "updated_at": _iso(_value(row, "updated_at")),
        "updated_by": str(_value(row, "updated_by")),
        "archived_at": _iso(_value(row, "archived_at")),
        "archived_by": _value(row, "archived_by"),
    }
    if member_count is not None:
        payload["member_count"] = int(member_count)
    if dataset_count is not None:
        payload["dataset_count"] = int(dataset_count)
    return payload


def _member_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(_value(row, "id")),
        "tenant_id": str(_value(row, "tenant_id")),
        "workspace_id": str(_value(row, "workspace_id")),
        "account_id": str(_value(row, "account_id")),
        "account_name": str(_value(row, "account_name", "")),
        "account_email": str(_value(row, "account_email", "")),
        "role": str(_value(row, "role")),
        "status": str(_value(row, "status")),
        "revision": int(_value(row, "revision", 1)),
        "created_at": _iso(_value(row, "created_at")),
        "created_by": str(_value(row, "created_by")),
        "updated_at": _iso(_value(row, "updated_at")),
        "updated_by": str(_value(row, "updated_by")),
        "removed_at": _iso(_value(row, "removed_at")),
        "removed_by": _value(row, "removed_by"),
    }


def _is_active_primary_binding(row: Mapping[str, Any]) -> bool:
    return (
        str(_value(row, "status", "")).casefold() == "active"
        and str(_value(row, "binding_kind", "")).casefold() == "primary"
        and str(_value(row, "active_primary_slot", "")).casefold() == "primary"
    )


def _primary_binding_managed_by_registry(
    current_revision: str,
    row: Mapping[str, Any] | None = None,
    *,
    requested_kind: str | None = None,
) -> bool:
    if current_revision not in _REGISTRY_MANAGED_REVISIONS:
        return False
    return requested_kind == "primary" or (row is not None and _is_active_primary_binding(row))


def _binding_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(_value(row, "id")),
        "tenant_id": str(_value(row, "tenant_id")),
        "workspace_id": str(_value(row, "workspace_id")),
        "dataset_id": str(_value(row, "dataset_id")),
        "dataset_name": str(_value(row, "dataset_name", "")),
        "dataset_status": _value(row, "dataset_status"),
        "binding_kind": str(_value(row, "binding_kind")),
        "active_primary_slot": _value(row, "active_primary_slot"),
        "status": str(_value(row, "status")),
        "revision": int(_value(row, "revision", 1)),
        "created_at": _iso(_value(row, "created_at")),
        "created_by": str(_value(row, "created_by")),
        "updated_at": _iso(_value(row, "updated_at")),
        "updated_by": str(_value(row, "updated_by")),
        "removed_at": _iso(_value(row, "removed_at")),
        "removed_by": _value(row, "removed_by"),
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
    audits = _table(session, "tenant_audit_events")
    session.execute(
        insert(audits).values(
            id=f"tenant-audit-{uuid.uuid4().hex}",
            tenant_id=actor.tenant_id,
            actor_id=actor.account_id,
            actor_name_snapshot=actor.name[:128],
            actor_email_snapshot=actor.email[:256],
            action=action,
            resource_type=resource_type,
            resource_id=resource_id[:512],
            target_account_id=(
                str(after.get("account_id"))[:64]
                if after is not None and after.get("account_id") is not None
                else None
            ),
            before_snapshot=sanitize_audit_snapshot(dict(before)) if before is not None else None,
            after_snapshot=sanitize_audit_snapshot(dict(after)) if after is not None else None,
            request_id=request_id[:128],
            request_ip=request_ip[:64],
            occurred_at=now,
        )
    )
    session.flush()


def _mutation_context(engine: Any, tenant_id: str, actor_id: str, key: str):
    digest = tenant_idempotency_key_digest(tenant_id, actor_id, key)
    return idempotency_key_lock(tenant_id, actor_id, digest), engine_serialization_lock(engine)


def _reserve(
    session: Session,
    *,
    actor: _Actor,
    key: str,
    request_hash: str,
    operation: str,
    resource_type: str,
):
    try:
        reservation = reserve_tenant_mutation(
            session,
            tenant_id=actor.tenant_id,
            actor_id=actor.account_id,
            raw_idempotency_key=key,
            request_hash=request_hash,
            operation=operation,
            resource_type=resource_type,
        )
    except TenantMutationIdempotencyConflict as exc:
        raise WorkspaceIdempotencyConflict() from exc
    except TenantMutationIdempotencyInProgress as exc:
        raise WorkspaceIdempotencyInProgress() from exc
    except TenantMutationIdempotencyValidationError as exc:
        raise WorkspaceValidation("Idempotency-Key 无效") from exc
    if reservation.replay is not None:
        return reservation, ServiceResult(
            reservation.replay.response, reservation.replay.http_status
        )
    return reservation, None


def _complete(
    session: Session,
    reservation: Any,
    payload: Mapping[str, Any],
    *,
    status: int,
    resource_id: str,
) -> ServiceResult:
    complete_tenant_mutation(
        session,
        reservation,
        response_for_replay=payload,
        http_status=status,
        resource_id=resource_id,
    )
    return ServiceResult(dict(payload), status)


def _keyset_filter(table: Table, cursor: _Cursor | None):
    if cursor is None:
        return None
    item_id: Any = cursor.item_id
    if isinstance(table.c.id.type, Integer):
        try:
            item_id = int(cursor.item_id)
        except (TypeError, ValueError) as exc:
            raise WorkspaceValidation("cursor 无效") from exc
    return or_(
        table.c.created_at < cursor.created_at,
        and_(table.c.created_at == cursor.created_at, table.c.id < item_id),
    )


def _page_result(
    rows: list[Mapping[str, Any]], *, limit: int, count: int, item_builder: Any
) -> dict[str, Any]:
    has_more = len(rows) > limit
    visible = rows[:limit]
    next_cursor = None
    if has_more and visible:
        timestamp = _datetime(_value(visible[-1], "created_at"))
        if timestamp is not None:
            next_cursor = _encode_cursor(timestamp, str(_value(visible[-1], "id")))
    return {
        "items": [item_builder(row) for row in visible],
        "count": int(count),
        "next_cursor": next_cursor,
    }


def _workspace_member_rows(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    status: str | None = "active",
    role: str | None = None,
    cursor: _Cursor | None = None,
    limit: int = 100,
) -> tuple[list[Mapping[str, Any]], int]:
    members = _table(session, "tenant_workspace_members")
    accounts = _table(session, "accounts")
    conditions = [members.c.tenant_id == tenant_id, members.c.workspace_id == workspace_id]
    if status is not None:
        conditions.append(members.c.status == status)
    if role is not None:
        conditions.append(members.c.role == role)
    total = int(session.scalar(select(func.count()).select_from(members).where(*conditions)) or 0)
    statement = (
        select(
            members,
            accounts.c.name.label("account_name"),
            accounts.c.email.label("account_email"),
        )
        .select_from(members.outerjoin(accounts, accounts.c.id == members.c.account_id))
        .where(*conditions)
    )
    cursor_filter = _keyset_filter(members, cursor)
    if cursor_filter is not None:
        statement = statement.where(cursor_filter)
    statement = statement.order_by(members.c.created_at.desc(), members.c.id.desc()).limit(
        limit + 1
    )
    return list(session.execute(statement).mappings()), total


def _workspace_dataset_rows(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    status: str | None = "active",
    binding_kind: str | None = None,
    cursor: _Cursor | None = None,
    limit: int = 100,
) -> tuple[list[Mapping[str, Any]], int]:
    bindings = _table(session, "tenant_workspace_datasets")
    datasets = _table(session, "datasets")
    conditions = [bindings.c.tenant_id == tenant_id, bindings.c.workspace_id == workspace_id]
    if status is not None:
        conditions.append(bindings.c.status == status)
    if binding_kind is not None:
        conditions.append(bindings.c.binding_kind == binding_kind)
    total = int(session.scalar(select(func.count()).select_from(bindings).where(*conditions)) or 0)
    statement = (
        select(
            bindings,
            datasets.c.name.label("dataset_name"),
            datasets.c.status.label("dataset_status"),
        )
        .select_from(
            bindings.outerjoin(
                datasets,
                and_(
                    datasets.c.tenant_id == bindings.c.tenant_id,
                    datasets.c.id == bindings.c.dataset_id,
                ),
            )
        )
        .where(*conditions)
    )
    cursor_filter = _keyset_filter(bindings, cursor)
    if cursor_filter is not None:
        statement = statement.where(cursor_filter)
    statement = statement.order_by(bindings.c.created_at.desc(), bindings.c.id.desc()).limit(
        limit + 1
    )
    return list(session.execute(statement).mappings()), total


def list_workspaces(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    status: str | None = None,
    environment: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
) -> ServiceResult:
    clean_tenant = _clean(tenant_id, "tenant_id", 64)
    clean_actor = _clean(actor_id, "actor_id", 64)
    clean_status = _optional_enum(status, "status", _WORKSPACE_STATUSES)
    clean_environment = _optional_enum(environment, "environment", _WORKSPACE_ENVIRONMENTS)
    limit = _positive_int(limit, "limit")
    if limit > 200:
        raise WorkspaceValidation("limit 不能超过 200")
    decoded = _decode_cursor(cursor)
    with Session(engine) as session:
        _ensure_0026(session.connection())
        _actor(session, clean_tenant, clean_actor)
        table = _table(session, "tenant_workspaces")
        conditions = [table.c.tenant_id == clean_tenant]
        if clean_status is not None:
            conditions.append(table.c.status == clean_status)
        if clean_environment is not None:
            conditions.append(table.c.environment == clean_environment)
        count = int(session.scalar(select(func.count()).select_from(table).where(*conditions)) or 0)
        statement = select(table).where(*conditions)
        cursor_filter = _keyset_filter(table, decoded)
        if cursor_filter is not None:
            statement = statement.where(cursor_filter)
        rows = list(
            session.execute(
                statement.order_by(table.c.created_at.desc(), table.c.id.desc()).limit(limit + 1)
            ).mappings()
        )
        visible_ids = [str(_value(row, "id")) for row in rows[:limit]]
        member_counts: dict[str, int] = {}
        dataset_counts: dict[str, int] = {}
        if visible_ids:
            members = _table(session, "tenant_workspace_members")
            datasets = _table(session, "tenant_workspace_datasets")
            member_counts = {
                str(workspace_id): int(total)
                for workspace_id, total in session.execute(
                    select(members.c.workspace_id, func.count())
                    .where(
                        members.c.tenant_id == clean_tenant,
                        members.c.workspace_id.in_(visible_ids),
                        members.c.status == "active",
                    )
                    .group_by(members.c.workspace_id)
                )
            }
            dataset_counts = {
                str(workspace_id): int(total)
                for workspace_id, total in session.execute(
                    select(datasets.c.workspace_id, func.count())
                    .where(
                        datasets.c.tenant_id == clean_tenant,
                        datasets.c.workspace_id.in_(visible_ids),
                        datasets.c.status == "active",
                    )
                    .group_by(datasets.c.workspace_id)
                )
            }
        payload = _page_result(
            rows,
            limit=limit,
            count=count,
            item_builder=lambda row: _workspace_payload(
                row,
                member_count=member_counts.get(str(_value(row, "id")), 0),
                dataset_count=dataset_counts.get(str(_value(row, "id")), 0),
            ),
        )
        payload["workspace_authorization_not_enforced"] = True
    return ServiceResult(payload)


def list_workspace_members(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    workspace_id: str,
    status: str | None = "active",
    role: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
) -> ServiceResult:
    clean_tenant = _clean(tenant_id, "tenant_id", 64)
    clean_actor = _clean(actor_id, "actor_id", 64)
    clean_workspace = _clean(workspace_id, "workspace_id", 128)
    clean_status = _optional_enum(status, "status", _WORKSPACE_MEMBER_STATUSES)
    clean_role = _optional_enum(role, "role", _WORKSPACE_MEMBER_ROLES)
    limit = _positive_int(limit, "limit")
    if limit > 200:
        raise WorkspaceValidation("limit 不能超过 200")
    decoded = _decode_cursor(cursor)
    with Session(engine) as session:
        _ensure_0026(session.connection())
        _actor(session, clean_tenant, clean_actor)
        _workspace_row(session, clean_tenant, clean_workspace)
        rows, count = _workspace_member_rows(
            session,
            tenant_id=clean_tenant,
            workspace_id=clean_workspace,
            status=clean_status,
            role=clean_role,
            cursor=decoded,
            limit=limit,
        )
        payload = _page_result(rows, limit=limit, count=count, item_builder=_member_payload)
        payload["workspace_authorization_not_enforced"] = True
    return ServiceResult(payload)


def list_workspace_datasets(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    workspace_id: str,
    status: str | None = "active",
    binding_kind: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
) -> ServiceResult:
    clean_tenant = _clean(tenant_id, "tenant_id", 64)
    clean_actor = _clean(actor_id, "actor_id", 64)
    clean_workspace = _clean(workspace_id, "workspace_id", 128)
    clean_status = _optional_enum(status, "status", _BINDING_STATUSES)
    clean_binding_kind = _optional_enum(binding_kind, "binding_kind", _BINDING_KINDS)
    limit = _positive_int(limit, "limit")
    if limit > 200:
        raise WorkspaceValidation("limit 不能超过 200")
    decoded = _decode_cursor(cursor)
    with Session(engine) as session:
        _ensure_0026(session.connection())
        _actor(session, clean_tenant, clean_actor)
        _workspace_row(session, clean_tenant, clean_workspace)
        rows, count = _workspace_dataset_rows(
            session,
            tenant_id=clean_tenant,
            workspace_id=clean_workspace,
            status=clean_status,
            binding_kind=clean_binding_kind,
            cursor=decoded,
            limit=limit,
        )
        payload = _page_result(rows, limit=limit, count=count, item_builder=_binding_payload)
        payload["workspace_authorization_not_enforced"] = True
    return ServiceResult(payload)


def get_workspace(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    workspace_id: str,
) -> ServiceResult:
    clean_tenant = _clean(tenant_id, "tenant_id", 64)
    clean_actor = _clean(actor_id, "actor_id", 64)
    clean_workspace = _clean(workspace_id, "workspace_id", 128)
    with Session(engine) as session:
        _ensure_0026(session.connection())
        _actor(session, clean_tenant, clean_actor)
        workspace = _workspace_row(session, clean_tenant, clean_workspace)
        member_rows, member_count = _workspace_member_rows(
            session, tenant_id=clean_tenant, workspace_id=clean_workspace, limit=200
        )
        dataset_rows, dataset_count = _workspace_dataset_rows(
            session, tenant_id=clean_tenant, workspace_id=clean_workspace, limit=200
        )
        payload = {
            "workspace": _workspace_payload(
                workspace, member_count=member_count, dataset_count=dataset_count
            ),
            "members": [_member_payload(row) for row in member_rows[:200]],
            "datasets": [_binding_payload(row) for row in dataset_rows[:200]],
            "workspace_authorization_not_enforced": True,
            "evidence": {
                "authorization": dict(_AUTHORIZATION_BOUNDARY),
                "members_truncated": len(member_rows) > 200,
                "datasets_truncated": len(dataset_rows) > 200,
            },
        }
    return ServiceResult(payload)


def create_workspace(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    code: str,
    name: str,
    description: str = "",
    environment: str = "production",
    is_default: bool = False,
    reason: str,
    idempotency_key: str,
    request_id: str,
    request_ip: str = "",
    now: datetime | None = None,
) -> ServiceResult:
    clean_tenant = _clean(tenant_id, "tenant_id", 64)
    clean_actor = _clean(actor_id, "actor_id", 64)
    display_code, normalized_code = _code(code)
    display_name, normalized_name = _normalized_name(name)
    clean_description = _clean(description, "description", 512, allow_empty=True)
    clean_environment = _enum(environment, "environment", _WORKSPACE_ENVIRONMENTS)
    if type(is_default) is not bool:
        raise WorkspaceValidation("is_default 必须是布尔值")
    clean_reason = _clean(reason, "reason", 512)
    clean_key = _clean(idempotency_key, "idempotency_key", 128)
    clean_request_id = _request_id(request_id)
    clean_ip = _request_ip(request_ip)
    current = _now(now)
    request_hash = tenant_request_hash(
        operation="workspace.create",
        path_identity={"tenant_id": clean_tenant},
        body={
            "code": display_code,
            "name": display_name,
            "description": clean_description,
            "environment": clean_environment,
            "is_default": is_default,
            "reason": clean_reason,
        },
    )
    key_lock, engine_lock = _mutation_context(engine, clean_tenant, clean_actor, clean_key)
    with key_lock, engine_lock, Session(engine, expire_on_commit=False) as session:
        with tenant_authority_transaction(session):
            _ensure_0026(session.connection())
            actor = _actor(session, clean_tenant, clean_actor)
            _require_tenant_manager(actor)
            _lock_tenant(session, clean_tenant)
            table = _table(session, "tenant_workspaces")
            reservation, replay = _reserve(
                session,
                actor=actor,
                key=clean_key,
                request_hash=request_hash,
                operation="workspace.create",
                resource_type="tenant_workspace",
            )
            if replay is not None:
                return replay
            duplicate_code = session.scalar(
                select(table.c.id).where(
                    table.c.tenant_id == clean_tenant,
                    func.lower(table.c.code) == normalized_code,
                )
            )
            if duplicate_code is not None:
                raise WorkspaceConflict("workspace_code_conflict", "同一租户中 code 已存在", 409)
            duplicate_name = session.scalar(
                select(table.c.id).where(
                    table.c.tenant_id == clean_tenant,
                    table.c.normalized_name == normalized_name,
                )
            )
            if duplicate_name is not None:
                raise WorkspaceConflict("workspace_name_conflict", "同一租户中 name 已存在", 409)
            if is_default:
                default_id = session.scalar(
                    select(table.c.id).where(
                        table.c.tenant_id == clean_tenant,
                        table.c.status == "active",
                        table.c.is_default.is_(True),
                    )
                )
                if default_id is not None:
                    raise WorkspaceConflict(
                        "workspace_default_conflict",
                        "同一租户只能有一个 active default Workspace",
                        409,
                    )
            workspace_id = f"workspace-{uuid.uuid4().hex}"
            session.execute(
                table.insert().values(
                    id=workspace_id,
                    tenant_id=clean_tenant,
                    code=display_code,
                    name=display_name,
                    normalized_name=normalized_name,
                    description=clean_description,
                    status="active",
                    environment=clean_environment,
                    is_default=is_default,
                    active_default_slot="default" if is_default else None,
                    revision=1,
                    created_at=current,
                    created_by=clean_actor,
                    updated_at=current,
                    updated_by=clean_actor,
                    archived_at=None,
                    archived_by=None,
                )
            )
            workspace = dict(
                session.execute(select(table).where(table.c.id == workspace_id)).mappings().one()
            )
            member_table = _table(session, "tenant_workspace_members")
            member_values = {
                "tenant_id": clean_tenant,
                "workspace_id": workspace_id,
                "account_id": clean_actor,
                "role": "owner",
                "status": "active",
                "revision": 1,
                "created_at": current,
                "created_by": clean_actor,
                "updated_at": current,
                "updated_by": clean_actor,
                "removed_at": None,
                "removed_by": None,
            }
            session.execute(member_table.insert().values(**member_values))
            catalog_revision = str(
                session.scalar(text("SELECT version_num FROM alembic_version")) or ""
            )
            if catalog_revision == _AUTHORIZATION_REVISION:
                from core.enterprise_workspace_authorization import create_shadow_policy_in_session

                create_shadow_policy_in_session(
                    session,
                    tenant_id=clean_tenant,
                    workspace_id=workspace_id,
                    actor_id=clean_actor,
                    now=current,
                )
            session.flush()
            member = dict(
                session.execute(
                    select(member_table).where(
                        member_table.c.tenant_id == clean_tenant,
                        member_table.c.workspace_id == workspace_id,
                        member_table.c.account_id == clean_actor,
                    )
                )
                .mappings()
                .one()
            )
            member_id = str(member["id"])
            workspace_payload = _workspace_payload(workspace, member_count=1, dataset_count=0)
            member_payload = _member_payload(
                {**member, "account_name": actor.name, "account_email": actor.email}
            )
            _audit(
                session,
                actor=actor,
                action="workspace.created",
                resource_type="tenant_workspace",
                resource_id=workspace_id,
                before=None,
                after={**workspace_payload, "reason": clean_reason},
                request_id=clean_request_id,
                request_ip=clean_ip,
                now=current,
            )
            _audit(
                session,
                actor=actor,
                action="workspace.member.added",
                resource_type="tenant_workspace_member",
                resource_id=member_id,
                before=None,
                after={**member_payload, "reason": clean_reason},
                request_id=clean_request_id,
                request_ip=clean_ip,
                now=current,
            )
            payload = {"workspace": workspace_payload, "member": member_payload}
            return _complete(session, reservation, payload, status=201, resource_id=workspace_id)


def update_workspace(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    workspace_id: str,
    expected_revision: int,
    name: str | None = None,
    code: str | None = None,
    description: str | None = None,
    environment: str | None = None,
    is_default: bool | None = None,
    reason: str,
    idempotency_key: str,
    request_id: str,
    request_ip: str = "",
    now: datetime | None = None,
) -> ServiceResult:
    clean_tenant = _clean(tenant_id, "tenant_id", 64)
    clean_actor = _clean(actor_id, "actor_id", 64)
    clean_workspace = _clean(workspace_id, "workspace_id", 128)
    revision = _positive_int(expected_revision, "expected_revision")
    clean_reason = _clean(reason, "reason", 512)
    clean_key = _clean(idempotency_key, "idempotency_key", 128)
    clean_request_id = _request_id(request_id)
    clean_ip = _request_ip(request_ip)
    current = _now(now)
    display_name, normalized_name = (None, None)
    if name is not None:
        display_name, normalized_name = _normalized_name(name)
    display_code, normalized_code = (None, None)
    if code is not None:
        display_code, normalized_code = _code(code)
    clean_description = (
        _clean(description, "description", 512, allow_empty=True)
        if description is not None
        else None
    )
    clean_environment = (
        _enum(environment, "environment", _WORKSPACE_ENVIRONMENTS)
        if environment is not None
        else None
    )
    if is_default is not None and type(is_default) is not bool:
        raise WorkspaceValidation("is_default 必须是布尔值")
    if all(
        value is None
        for value in (display_name, display_code, clean_description, clean_environment, is_default)
    ):
        raise WorkspaceValidation("至少提供一个 Workspace 更新字段")
    request_hash = tenant_request_hash(
        operation="workspace.update",
        path_identity={"tenant_id": clean_tenant, "workspace_id": clean_workspace},
        body={
            "revision": revision,
            "name": display_name,
            "code": display_code,
            "description": clean_description,
            "environment": clean_environment,
            "is_default": is_default,
            "reason": clean_reason,
        },
    )
    key_lock, engine_lock = _mutation_context(engine, clean_tenant, clean_actor, clean_key)
    with key_lock, engine_lock, Session(engine, expire_on_commit=False) as session:
        with tenant_authority_transaction(session):
            _ensure_0026(session.connection())
            actor = _actor(session, clean_tenant, clean_actor)
            _require_tenant_manager(actor)
            _lock_tenant(session, clean_tenant)
            table = _table(session, "tenant_workspaces")
            reservation, replay = _reserve(
                session,
                actor=actor,
                key=clean_key,
                request_hash=request_hash,
                operation="workspace.update",
                resource_type="tenant_workspace",
            )
            if replay is not None:
                return replay
            row = dict(_workspace_row(session, clean_tenant, clean_workspace, lock_for_update=True))
            if str(row["status"]) != "active":
                raise WorkspaceConflict("workspace_archived", "archived Workspace 不能更新", 409)
            if int(row["revision"]) != revision:
                raise WorkspaceRevisionConflict(
                    expected_revision=revision, current_revision=int(row["revision"])
                )
            next_code = display_code if display_code is not None else str(row["code"])
            next_normalized_code = normalized_code or str(row["code"]).casefold()
            next_name = display_name if display_name is not None else str(row["name"])
            next_normalized_name = normalized_name or str(row["normalized_name"])
            if next_normalized_code != str(row["code"]).casefold():
                duplicate = session.scalar(
                    select(table.c.id).where(
                        table.c.tenant_id == clean_tenant,
                        table.c.id != clean_workspace,
                        func.lower(table.c.code) == next_normalized_code,
                    )
                )
                if duplicate is not None:
                    raise WorkspaceConflict(
                        "workspace_code_conflict", "同一租户中 code 已存在", 409
                    )
            if next_normalized_name != str(row["normalized_name"]):
                duplicate = session.scalar(
                    select(table.c.id).where(
                        table.c.tenant_id == clean_tenant,
                        table.c.id != clean_workspace,
                        table.c.normalized_name == next_normalized_name,
                    )
                )
                if duplicate is not None:
                    raise WorkspaceConflict(
                        "workspace_name_conflict", "同一租户中 name 已存在", 409
                    )
            next_default = bool(row["is_default"]) if is_default is None else is_default
            if next_default and not bool(row["is_default"]):
                duplicate = session.scalar(
                    select(table.c.id).where(
                        table.c.tenant_id == clean_tenant,
                        table.c.status == "active",
                        table.c.is_default.is_(True),
                        table.c.id != clean_workspace,
                    )
                )
                if duplicate is not None:
                    raise WorkspaceConflict(
                        "workspace_default_conflict",
                        "同一租户只能有一个 active default Workspace",
                        409,
                    )
            before = _workspace_payload(row)
            values = {
                "code": next_code,
                "name": next_name,
                "normalized_name": next_normalized_name,
                "description": (
                    clean_description if clean_description is not None else str(row["description"])
                ),
                "environment": clean_environment or str(row["environment"]),
                "is_default": next_default,
                "active_default_slot": "default" if next_default else None,
                "revision": revision + 1,
                "updated_at": current,
                "updated_by": clean_actor,
            }
            session.execute(
                update(table)
                .where(
                    table.c.tenant_id == clean_tenant,
                    table.c.id == clean_workspace,
                    table.c.revision == revision,
                )
                .values(**values)
            )
            updated = dict(
                session.execute(select(table).where(table.c.id == clean_workspace)).mappings().one()
            )
            after = _workspace_payload(updated)
            _audit(
                session,
                actor=actor,
                action="workspace.updated",
                resource_type="tenant_workspace",
                resource_id=clean_workspace,
                before=before,
                after={**after, "reason": clean_reason},
                request_id=clean_request_id,
                request_ip=clean_ip,
                now=current,
            )
            return _complete(
                session,
                reservation,
                {"workspace": after},
                status=200,
                resource_id=clean_workspace,
            )


def archive_workspace(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    workspace_id: str,
    expected_revision: int,
    reason: str,
    idempotency_key: str,
    request_id: str,
    request_ip: str = "",
    now: datetime | None = None,
) -> ServiceResult:
    clean_tenant = _clean(tenant_id, "tenant_id", 64)
    clean_actor = _clean(actor_id, "actor_id", 64)
    clean_workspace = _clean(workspace_id, "workspace_id", 128)
    revision = _positive_int(expected_revision, "expected_revision")
    clean_reason = _clean(reason, "reason", 512)
    clean_key = _clean(idempotency_key, "idempotency_key", 128)
    clean_request_id = _request_id(request_id)
    clean_ip = _request_ip(request_ip)
    current = _now(now)
    request_hash = tenant_request_hash(
        operation="workspace.archive",
        path_identity={"tenant_id": clean_tenant, "workspace_id": clean_workspace},
        body={"revision": revision, "reason": clean_reason},
    )
    key_lock, engine_lock = _mutation_context(engine, clean_tenant, clean_actor, clean_key)
    with key_lock, engine_lock, Session(engine, expire_on_commit=False) as session:
        with tenant_authority_transaction(session):
            _ensure_0026(session.connection())
            actor = _actor(session, clean_tenant, clean_actor)
            _require_tenant_manager(actor)
            _lock_tenant(session, clean_tenant)
            table = _table(session, "tenant_workspaces")
            reservation, replay = _reserve(
                session,
                actor=actor,
                key=clean_key,
                request_hash=request_hash,
                operation="workspace.archive",
                resource_type="tenant_workspace",
            )
            if replay is not None:
                return replay
            row = dict(_workspace_row(session, clean_tenant, clean_workspace, lock_for_update=True))
            if int(row["revision"]) != revision:
                raise WorkspaceRevisionConflict(
                    expected_revision=revision, current_revision=int(row["revision"])
                )
            if str(row["status"]) != "active":
                raise WorkspaceConflict("workspace_terminal", "Workspace 已经归档", 409)
            bindings = _table(session, "tenant_workspace_datasets")
            primary_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(bindings)
                    .where(
                        bindings.c.tenant_id == clean_tenant,
                        bindings.c.workspace_id == clean_workspace,
                        bindings.c.status == "active",
                        bindings.c.binding_kind == "primary",
                    )
                )
                or 0
            )
            if primary_count:
                code = (
                    "workspace_default_dataset_protected"
                    if bool(row["is_default"])
                    else "workspace_primary_dataset_protected"
                )
                raise WorkspaceConflict(code, "Workspace 仍拥有 active primary Dataset 绑定", 409)
            before = _workspace_payload(row)
            session.execute(
                update(table)
                .where(
                    table.c.tenant_id == clean_tenant,
                    table.c.id == clean_workspace,
                    table.c.revision == revision,
                    table.c.status == "active",
                )
                .values(
                    status="archived",
                    is_default=False,
                    active_default_slot=None,
                    revision=revision + 1,
                    archived_at=current,
                    archived_by=clean_actor,
                    updated_at=current,
                    updated_by=clean_actor,
                )
            )
            updated = dict(
                session.execute(select(table).where(table.c.id == clean_workspace)).mappings().one()
            )
            after = _workspace_payload(updated)
            after["reason"] = clean_reason
            _audit(
                session,
                actor=actor,
                action="workspace.archived",
                resource_type="tenant_workspace",
                resource_id=clean_workspace,
                before=before,
                after=after,
                request_id=clean_request_id,
                request_ip=clean_ip,
                now=current,
            )
            return _complete(
                session,
                reservation,
                {"workspace": _workspace_payload(updated)},
                status=200,
                resource_id=clean_workspace,
            )


def add_workspace_member(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    workspace_id: str,
    account_id: str,
    role: str = "viewer",
    expected_revision: int | None = None,
    reason: str,
    idempotency_key: str,
    request_id: str,
    request_ip: str = "",
    now: datetime | None = None,
) -> ServiceResult:
    clean_tenant = _clean(tenant_id, "tenant_id", 64)
    clean_actor = _clean(actor_id, "actor_id", 64)
    clean_workspace = _clean(workspace_id, "workspace_id", 128)
    clean_account = _clean(account_id, "account_id", 64)
    clean_role = _enum(role, "role", _WORKSPACE_MEMBER_ROLES)
    if expected_revision is not None:
        expected_revision = _positive_int(expected_revision, "expected_revision")
    clean_reason = _clean(reason, "reason", 512)
    clean_key = _clean(idempotency_key, "idempotency_key", 128)
    clean_request_id = _request_id(request_id)
    clean_ip = _request_ip(request_ip)
    current = _now(now)
    request_hash = tenant_request_hash(
        operation="workspace.member.add",
        path_identity={
            "tenant_id": clean_tenant,
            "workspace_id": clean_workspace,
            "account_id": clean_account,
        },
        body={"role": clean_role, "revision": expected_revision, "reason": clean_reason},
    )
    key_lock, engine_lock = _mutation_context(engine, clean_tenant, clean_actor, clean_key)
    with key_lock, engine_lock, Session(engine, expire_on_commit=False) as session:
        with tenant_authority_transaction(session):
            _ensure_0026(session.connection())
            actor = _actor(session, clean_tenant, clean_actor)
            _lock_tenant(session, clean_tenant)
            workspace = _require_workspace_manager(session, actor, clean_workspace)
            reservation, replay = _reserve(
                session,
                actor=actor,
                key=clean_key,
                request_hash=request_hash,
                operation="workspace.member.add",
                resource_type="tenant_workspace_member",
            )
            if replay is not None:
                return replay
            if str(workspace["status"]) != "active":
                raise WorkspaceConflict(
                    "workspace_archived", "archived Workspace 不能新增成员", 409
                )
            tenant_members = _table(session, "tenant_members")
            accounts = _table(session, "accounts")
            target = (
                session.execute(
                    select(
                        tenant_members.c.account_id,
                        tenant_members.c.status,
                        accounts.c.name.label("account_name"),
                        accounts.c.email.label("account_email"),
                    )
                    .select_from(
                        tenant_members.join(accounts, accounts.c.id == tenant_members.c.account_id)
                    )
                    .where(
                        tenant_members.c.tenant_id == clean_tenant,
                        tenant_members.c.account_id == clean_account,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if target is None or str(target["status"]).casefold() != "active":
                raise WorkspaceConflict(
                    "workspace_member_target_invalid", "目标必须是当前租户 active member", 409
                )
            table = _table(session, "tenant_workspace_members")
            existing = (
                session.execute(
                    select(table)
                    .where(
                        table.c.tenant_id == clean_tenant,
                        table.c.workspace_id == clean_workspace,
                        table.c.account_id == clean_account,
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None and str(existing["status"]) == "active":
                raise WorkspaceConflict("workspace_member_exists", "成员已经在 Workspace 中", 409)
            if existing is None:
                session.execute(
                    table.insert().values(
                        tenant_id=clean_tenant,
                        workspace_id=clean_workspace,
                        account_id=clean_account,
                        role=clean_role,
                        status="active",
                        revision=1,
                        created_at=current,
                        created_by=clean_actor,
                        updated_at=current,
                        updated_by=clean_actor,
                        removed_at=None,
                        removed_by=None,
                    )
                )
                session.flush()
                member_id = str(
                    session.execute(
                        select(table.c.id).where(
                            table.c.tenant_id == clean_tenant,
                            table.c.workspace_id == clean_workspace,
                            table.c.account_id == clean_account,
                        )
                    ).scalar_one()
                )
            else:
                if expected_revision is not None and int(existing["revision"]) != expected_revision:
                    raise WorkspaceRevisionConflict(
                        expected_revision=expected_revision,
                        current_revision=int(existing["revision"]),
                    )
                member_id = str(existing["id"])
                session.execute(
                    update(table)
                    .where(table.c.id == member_id, table.c.revision == int(existing["revision"]))
                    .values(
                        role=clean_role,
                        status="active",
                        revision=int(existing["revision"]) + 1,
                        updated_at=current,
                        updated_by=clean_actor,
                        removed_at=None,
                        removed_by=None,
                    )
                )
            member_row = dict(
                session.execute(select(table).where(table.c.id == member_id)).mappings().one()
            )
            member_payload = _member_payload(
                {
                    **member_row,
                    "account_name": target["account_name"],
                    "account_email": target["account_email"],
                }
            )
            old_payload = None
            if existing is not None:
                old_payload = _member_payload(
                    {
                        **existing,
                        "account_name": target["account_name"],
                        "account_email": target["account_email"],
                    }
                )
            _audit(
                session,
                actor=actor,
                action="workspace.member.added",
                resource_type="tenant_workspace_member",
                resource_id=member_id,
                before=old_payload,
                after={**member_payload, "reason": clean_reason},
                request_id=clean_request_id,
                request_ip=clean_ip,
                now=current,
            )
            return _complete(
                session,
                reservation,
                {"member": member_payload},
                status=201 if existing is None else 200,
                resource_id=member_id,
            )


def _change_workspace_member(
    engine: Any,
    *,
    operation: Literal["update", "remove"],
    tenant_id: str,
    actor_id: str,
    workspace_id: str,
    account_id: str,
    expected_revision: int,
    role: str | None,
    status: str | None,
    reason: str,
    idempotency_key: str,
    request_id: str,
    request_ip: str,
    now: datetime | None,
) -> ServiceResult:
    clean_tenant = _clean(tenant_id, "tenant_id", 64)
    clean_actor = _clean(actor_id, "actor_id", 64)
    clean_workspace = _clean(workspace_id, "workspace_id", 128)
    clean_account = _clean(account_id, "account_id", 64)
    revision = _positive_int(expected_revision, "expected_revision")
    clean_role = _optional_enum(role, "role", _WORKSPACE_MEMBER_ROLES)
    clean_status = _optional_enum(status, "status", _WORKSPACE_MEMBER_STATUSES)
    clean_reason = _clean(reason, "reason", 512)
    if operation == "remove":
        clean_status = "removed"
    if operation == "update" and clean_role is None and clean_status is None:
        raise WorkspaceValidation("至少提供 role 或 status")
    clean_key = _clean(idempotency_key, "idempotency_key", 128)
    clean_request_id = _request_id(request_id)
    clean_ip = _request_ip(request_ip)
    current = _now(now)
    request_hash = tenant_request_hash(
        operation=f"workspace.member.{operation}",
        path_identity={
            "tenant_id": clean_tenant,
            "workspace_id": clean_workspace,
            "account_id": clean_account,
        },
        body={
            "revision": revision,
            "role": clean_role,
            "status": clean_status,
            "reason": clean_reason,
        },
    )
    key_lock, engine_lock = _mutation_context(engine, clean_tenant, clean_actor, clean_key)
    with key_lock, engine_lock, Session(engine, expire_on_commit=False) as session:
        with tenant_authority_transaction(session):
            _ensure_0026(session.connection())
            actor = _actor(session, clean_tenant, clean_actor)
            _lock_tenant(session, clean_tenant)
            workspace = _require_workspace_manager(session, actor, clean_workspace)
            reservation, replay = _reserve(
                session,
                actor=actor,
                key=clean_key,
                request_hash=request_hash,
                operation=f"workspace.member.{operation}",
                resource_type="tenant_workspace_member",
            )
            if replay is not None:
                return replay
            if operation == "update" and str(workspace["status"]) != "active":
                raise WorkspaceConflict(
                    "workspace_archived", "archived Workspace 不能更新成员", 409
                )
            table = _table(session, "tenant_workspace_members")
            accounts = _table(session, "accounts")
            row = (
                session.execute(
                    select(
                        table,
                        accounts.c.name.label("account_name"),
                        accounts.c.email.label("account_email"),
                    )
                    .select_from(table.outerjoin(accounts, accounts.c.id == table.c.account_id))
                    .where(
                        table.c.tenant_id == clean_tenant,
                        table.c.workspace_id == clean_workspace,
                        table.c.account_id == clean_account,
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise WorkspaceNotFound("Workspace 成员不存在")
            if int(row["revision"]) != revision:
                raise WorkspaceRevisionConflict(
                    expected_revision=revision, current_revision=int(row["revision"])
                )
            current_status = str(row["status"]).casefold()
            current_role = str(row["role"]).casefold()
            if operation == "remove" and current_status != "active":
                raise WorkspaceConflict("workspace_member_terminal", "Workspace 成员已经移除", 409)
            if current_status == "removed" and operation == "update":
                raise WorkspaceConflict("workspace_member_terminal", "已移除成员不能更新", 409)
            next_role = clean_role or current_role
            next_status = clean_status or current_status
            if current_role == "owner" and (next_status == "removed" or next_role != "owner"):
                owners = int(
                    session.scalar(
                        select(func.count()).where(
                            table.c.tenant_id == clean_tenant,
                            table.c.workspace_id == clean_workspace,
                            table.c.status == "active",
                            table.c.role == "owner",
                        )
                    )
                    or 0
                )
                if owners <= 1:
                    raise WorkspaceConflict(
                        "workspace_last_owner_protected",
                        "最后一个 active workspace owner 不可移除或降级",
                        409,
                    )
            before = _member_payload(row)
            values: dict[str, Any] = {
                "role": next_role,
                "status": next_status,
                "revision": revision + 1,
                "updated_at": current,
                "updated_by": clean_actor,
            }
            if next_status == "removed":
                values.update({"removed_at": current, "removed_by": clean_actor})
            session.execute(
                update(table)
                .where(
                    table.c.tenant_id == clean_tenant,
                    table.c.workspace_id == clean_workspace,
                    table.c.account_id == clean_account,
                    table.c.revision == revision,
                )
                .values(**values)
            )
            updated_row = dict(
                session.execute(select(table).where(table.c.id == row["id"])).mappings().one()
            )
            after = _member_payload(
                {
                    **updated_row,
                    "account_name": row["account_name"],
                    "account_email": row["account_email"],
                }
            )
            after_for_audit = {**after, "reason": clean_reason}
            _audit(
                session,
                actor=actor,
                action=(
                    "workspace.member.removed"
                    if next_status == "removed"
                    else "workspace.member.updated"
                ),
                resource_type="tenant_workspace_member",
                resource_id=str(row["id"]),
                before=before,
                after=after_for_audit,
                request_id=clean_request_id,
                request_ip=clean_ip,
                now=current,
            )
            return _complete(
                session,
                reservation,
                {"member": after},
                status=200,
                resource_id=str(row["id"]),
            )


def update_workspace_member(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    workspace_id: str,
    account_id: str,
    expected_revision: int,
    role: str | None = None,
    status: str | None = None,
    reason: str = "",
    idempotency_key: str,
    request_id: str,
    request_ip: str = "",
    now: datetime | None = None,
) -> ServiceResult:
    return _change_workspace_member(
        engine,
        operation="update",
        tenant_id=tenant_id,
        actor_id=actor_id,
        workspace_id=workspace_id,
        account_id=account_id,
        expected_revision=expected_revision,
        role=role,
        status=status,
        reason=reason or "更新 Workspace 成员",
        idempotency_key=idempotency_key,
        request_id=request_id,
        request_ip=request_ip,
        now=now,
    )


def remove_workspace_member(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    workspace_id: str,
    account_id: str,
    expected_revision: int,
    reason: str,
    idempotency_key: str,
    request_id: str,
    request_ip: str = "",
    now: datetime | None = None,
) -> ServiceResult:
    return _change_workspace_member(
        engine,
        operation="remove",
        tenant_id=tenant_id,
        actor_id=actor_id,
        workspace_id=workspace_id,
        account_id=account_id,
        expected_revision=expected_revision,
        role=None,
        status="removed",
        reason=reason,
        idempotency_key=idempotency_key,
        request_id=request_id,
        request_ip=request_ip,
        now=now,
    )


def bind_workspace_dataset(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    workspace_id: str,
    dataset_id: str,
    binding_kind: str = "shared",
    expected_revision: int | None = None,
    reason: str,
    idempotency_key: str,
    request_id: str,
    request_ip: str = "",
    now: datetime | None = None,
) -> ServiceResult:
    clean_tenant = _clean(tenant_id, "tenant_id", 64)
    clean_actor = _clean(actor_id, "actor_id", 64)
    clean_workspace = _clean(workspace_id, "workspace_id", 128)
    clean_dataset = _clean(dataset_id, "dataset_id", 64)
    clean_kind = _enum(binding_kind, "binding_kind", _BINDING_KINDS)
    if expected_revision is not None:
        expected_revision = _positive_int(expected_revision, "expected_revision")
    clean_reason = _clean(reason, "reason", 512)
    clean_key = _clean(idempotency_key, "idempotency_key", 128)
    clean_request_id = _request_id(request_id)
    clean_ip = _request_ip(request_ip)
    current = _now(now)
    request_hash = tenant_request_hash(
        operation="workspace.dataset.bind",
        path_identity={
            "tenant_id": clean_tenant,
            "workspace_id": clean_workspace,
            "dataset_id": clean_dataset,
        },
        body={
            "binding_kind": clean_kind,
            "revision": expected_revision,
            "reason": clean_reason,
        },
    )
    key_lock, engine_lock = _mutation_context(engine, clean_tenant, clean_actor, clean_key)
    with key_lock, engine_lock, Session(engine, expire_on_commit=False) as session:
        with tenant_authority_transaction(session):
            current_revision = _ensure_0026(session.connection())
            actor = _actor(session, clean_tenant, clean_actor)
            _require_tenant_manager(actor)
            _lock_tenant(session, clean_tenant)
            workspace = _workspace_row(session, clean_tenant, clean_workspace, lock_for_update=True)
            reservation, replay = _reserve(
                session,
                actor=actor,
                key=clean_key,
                request_hash=request_hash,
                operation="workspace.dataset.bind",
                resource_type="tenant_workspace_dataset",
            )
            if replay is not None:
                return replay
            if str(workspace["status"]) != "active":
                raise WorkspaceConflict(
                    "workspace_archived", "archived Workspace 不能绑定 Dataset", 409
                )
            datasets = _table(session, "datasets")
            dataset = (
                session.execute(
                    select(datasets.c.id, datasets.c.status, datasets.c.name).where(
                        datasets.c.tenant_id == clean_tenant,
                        datasets.c.id == clean_dataset,
                    )
                )
                .mappings()
                .one_or_none()
            )
            if dataset is None:
                raise WorkspaceNotFound("Dataset 不属于当前租户")
            if str(dataset["status"]).casefold() == "disabled":
                raise WorkspaceConflict(
                    "workspace_dataset_invalid", "disabled Dataset 不能绑定", 409
                )
            table = _table(session, "tenant_workspace_datasets")
            existing = (
                session.execute(
                    select(table)
                    .where(
                        table.c.tenant_id == clean_tenant,
                        table.c.workspace_id == clean_workspace,
                        table.c.dataset_id == clean_dataset,
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if _primary_binding_managed_by_registry(
                current_revision, existing, requested_kind=clean_kind
            ):
                raise WorkspaceConflict(
                    "workspace_dataset_primary_managed_by_registry",
                    "0028 后 primary Dataset 绑定只能由 Knowledge Base Registry ownership transfer 管理",
                    409,
                )
            if clean_kind == "primary":
                other_primary = session.scalar(
                    select(table.c.id).where(
                        table.c.tenant_id == clean_tenant,
                        table.c.dataset_id == clean_dataset,
                        table.c.binding_kind == "primary",
                        table.c.status == "active",
                        table.c.id != (existing["id"] if existing is not None else "__none__"),
                    )
                )
                if other_primary is not None:
                    raise WorkspaceConflict(
                        "workspace_dataset_primary_conflict",
                        "同一 Dataset 只能拥有一个 active primary Workspace 绑定",
                        409,
                    )
            if existing is not None and str(existing["status"]) == "active":
                if expected_revision is not None and int(existing["revision"]) != expected_revision:
                    raise WorkspaceRevisionConflict(
                        expected_revision=expected_revision,
                        current_revision=int(existing["revision"]),
                    )
                if str(existing["binding_kind"]) == clean_kind:
                    raise WorkspaceConflict(
                        "workspace_dataset_exists", "Dataset 已绑定到该 Workspace", 409
                    )
                expected = int(existing["revision"])
                binding_id = str(existing["id"])
                session.execute(
                    update(table)
                    .where(table.c.id == binding_id, table.c.revision == expected)
                    .values(
                        binding_kind=clean_kind,
                        active_primary_slot="primary" if clean_kind == "primary" else None,
                        revision=expected + 1,
                        updated_at=current,
                        updated_by=clean_actor,
                    )
                )
                http_status = 200
            elif existing is not None:
                expected = int(existing["revision"])
                if expected_revision is not None and expected != expected_revision:
                    raise WorkspaceRevisionConflict(
                        expected_revision=expected_revision, current_revision=expected
                    )
                binding_id = str(existing["id"])
                session.execute(
                    update(table)
                    .where(table.c.id == binding_id, table.c.revision == expected)
                    .values(
                        binding_kind=clean_kind,
                        active_primary_slot="primary" if clean_kind == "primary" else None,
                        status="active",
                        revision=expected + 1,
                        updated_at=current,
                        updated_by=clean_actor,
                        removed_at=None,
                        removed_by=None,
                    )
                )
                http_status = 200
            else:
                session.execute(
                    table.insert().values(
                        tenant_id=clean_tenant,
                        workspace_id=clean_workspace,
                        dataset_id=clean_dataset,
                        binding_kind=clean_kind,
                        active_primary_slot="primary" if clean_kind == "primary" else None,
                        status="active",
                        revision=1,
                        created_at=current,
                        created_by=clean_actor,
                        updated_at=current,
                        updated_by=clean_actor,
                        removed_at=None,
                        removed_by=None,
                    )
                )
                session.flush()
                binding_id = str(
                    session.execute(
                        select(table.c.id).where(
                            table.c.tenant_id == clean_tenant,
                            table.c.workspace_id == clean_workspace,
                            table.c.dataset_id == clean_dataset,
                        )
                    ).scalar_one()
                )
                http_status = 201
            binding_row = dict(
                session.execute(select(table).where(table.c.id == binding_id)).mappings().one()
            )
            binding_payload = _binding_payload(
                {
                    **binding_row,
                    "dataset_name": dataset["name"],
                    "dataset_status": dataset["status"],
                }
            )
            old_payload = None
            if existing is not None:
                old_payload = _binding_payload(
                    {
                        **existing,
                        "dataset_name": dataset["name"],
                        "dataset_status": dataset["status"],
                    }
                )
            _audit(
                session,
                actor=actor,
                action="workspace.dataset.bound",
                resource_type="tenant_workspace_dataset",
                resource_id=binding_id,
                before=old_payload,
                after={**binding_payload, "reason": clean_reason},
                request_id=clean_request_id,
                request_ip=clean_ip,
                now=current,
            )
            return _complete(
                session,
                reservation,
                {"binding": binding_payload},
                status=http_status,
                resource_id=binding_id,
            )


def remove_workspace_dataset(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    workspace_id: str,
    dataset_id: str,
    expected_revision: int,
    reason: str,
    idempotency_key: str,
    request_id: str,
    request_ip: str = "",
    now: datetime | None = None,
) -> ServiceResult:
    clean_tenant = _clean(tenant_id, "tenant_id", 64)
    clean_actor = _clean(actor_id, "actor_id", 64)
    clean_workspace = _clean(workspace_id, "workspace_id", 128)
    clean_dataset = _clean(dataset_id, "dataset_id", 64)
    revision = _positive_int(expected_revision, "expected_revision")
    clean_reason = _clean(reason, "reason", 512)
    clean_key = _clean(idempotency_key, "idempotency_key", 128)
    clean_request_id = _request_id(request_id)
    clean_ip = _request_ip(request_ip)
    current = _now(now)
    request_hash = tenant_request_hash(
        operation="workspace.dataset.remove",
        path_identity={
            "tenant_id": clean_tenant,
            "workspace_id": clean_workspace,
            "dataset_id": clean_dataset,
        },
        body={"revision": revision, "reason": clean_reason},
    )
    key_lock, engine_lock = _mutation_context(engine, clean_tenant, clean_actor, clean_key)
    with key_lock, engine_lock, Session(engine, expire_on_commit=False) as session:
        with tenant_authority_transaction(session):
            current_revision = _ensure_0026(session.connection())
            actor = _actor(session, clean_tenant, clean_actor)
            _require_tenant_manager(actor)
            _lock_tenant(session, clean_tenant)
            _workspace_row(session, clean_tenant, clean_workspace, lock_for_update=True)
            reservation, replay = _reserve(
                session,
                actor=actor,
                key=clean_key,
                request_hash=request_hash,
                operation="workspace.dataset.remove",
                resource_type="tenant_workspace_dataset",
            )
            if replay is not None:
                return replay
            table = _table(session, "tenant_workspace_datasets")
            datasets = _table(session, "datasets")
            row = (
                session.execute(
                    select(
                        table,
                        datasets.c.name.label("dataset_name"),
                        datasets.c.status.label("dataset_status"),
                    )
                    .select_from(
                        table.outerjoin(
                            datasets,
                            and_(
                                datasets.c.tenant_id == table.c.tenant_id,
                                datasets.c.id == table.c.dataset_id,
                            ),
                        )
                    )
                    .where(
                        table.c.tenant_id == clean_tenant,
                        table.c.workspace_id == clean_workspace,
                        table.c.dataset_id == clean_dataset,
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise WorkspaceNotFound("Workspace Dataset 绑定不存在")
            if int(row["revision"]) != revision:
                raise WorkspaceRevisionConflict(
                    expected_revision=revision, current_revision=int(row["revision"])
                )
            if str(row["status"]) != "active":
                raise WorkspaceConflict("workspace_dataset_terminal", "Dataset 绑定已经移除", 409)
            if _primary_binding_managed_by_registry(current_revision, row):
                raise WorkspaceConflict(
                    "workspace_dataset_primary_managed_by_registry",
                    "0028 后 primary Dataset 绑定只能由 Knowledge Base Registry ownership transfer 管理",
                    409,
                )
            before = _binding_payload(row)
            session.execute(
                update(table)
                .where(
                    table.c.id == row["id"],
                    table.c.revision == revision,
                    table.c.status == "active",
                )
                .values(
                    status="removed",
                    active_primary_slot=None,
                    revision=revision + 1,
                    removed_at=current,
                    removed_by=clean_actor,
                    updated_at=current,
                    updated_by=clean_actor,
                )
            )
            updated_row = dict(
                session.execute(select(table).where(table.c.id == row["id"])).mappings().one()
            )
            after = _binding_payload(
                {
                    **updated_row,
                    "dataset_name": row["dataset_name"],
                    "dataset_status": row["dataset_status"],
                }
            )
            after_for_audit = {**after, "reason": clean_reason}
            _audit(
                session,
                actor=actor,
                action="workspace.dataset.removed",
                resource_type="tenant_workspace_dataset",
                resource_id=str(row["id"]),
                before=before,
                after=after_for_audit,
                request_id=clean_request_id,
                request_ip=clean_ip,
                now=current,
            )
            return _complete(
                session,
                reservation,
                {
                    "binding": _binding_payload(
                        {
                            **updated_row,
                            "dataset_name": row["dataset_name"],
                            "dataset_status": row["dataset_status"],
                        }
                    )
                },
                status=200,
                resource_id=str(row["id"]),
            )


__all__ = [
    "ServiceResult",
    "WorkspaceConflict",
    "WorkspaceError",
    "WorkspaceForbidden",
    "WorkspaceIdempotencyConflict",
    "WorkspaceIdempotencyInProgress",
    "WorkspaceMigrationRequired",
    "WorkspaceNotFound",
    "WorkspaceRevisionConflict",
    "WorkspaceValidation",
    "add_workspace_member",
    "archive_workspace",
    "bind_workspace_dataset",
    "create_workspace",
    "get_workspace",
    "list_workspace_datasets",
    "list_workspace_members",
    "list_workspaces",
    "remove_workspace_dataset",
    "remove_workspace_member",
    "update_workspace",
    "update_workspace_member",
]
