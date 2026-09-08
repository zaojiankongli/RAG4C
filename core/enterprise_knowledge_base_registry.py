"""Tenant-scoped enterprise Knowledge Base registry authority.

The registry is deliberately separate from Dataset ACL. It projects Dataset,
Workspace association, ownership and Application dependency facts without
turning any of those facts into a permission grant. The module resolves the
Stage 18 ORM models lazily so the application can import while the 0028
migration worker is still being integrated; every registry operation fails
closed until the 0028 catalog capability is provable.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime
import importlib
import json
import re
import uuid
from typing import Any

from sqlalchemy import MetaData, Table, and_, func, inspect, or_, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from core.enterprise_acl_idempotency import engine_serialization_lock, idempotency_key_lock
from core.enterprise_approval_control import (
    ApprovalError,
    ApprovalExecutionFact,
    resolve_active_approval_policy_in_session,
    sanitize_reason,
)
from core.enterprise_tenant_idempotency import (
    TenantMutationIdempotencyConflict,
    TenantMutationIdempotencyInProgress,
    TenantMutationIdempotencyValidationError,
    complete_tenant_mutation,
    reserve_tenant_mutation,
    tenant_idempotency_key_digest,
    tenant_request_hash,
)
from core.knowledge_datasets import DatasetArchiveBlocked
from core.knowledge_governance import sanitize_audit_snapshot
from core.knowledge_permissions import KNOWLEDGE_MANAGE


REVISION = "0028_enterprise_knowledge_base_registry"
REGISTRY_REVISION = REVISION
RESOURCE_KNOWLEDGE_BASE = "knowledge_base"
REFERENCE_KIND_KNOWLEDGE = "knowledge"
ACTIVE_REFERENCE_STATUS = "active"
REMOVED_REFERENCE_STATUS = "removed"
ACTIVE_REFERENCE_SLOT = "active"

_SAFE_ID = re.compile(r"^[^\x00-\x1f\x7f]+$")
_CURSOR_KIND = "knowledge_base"
_DATASET_STATUSES = frozenset({"active", "archived", "disabled"})
_REFERENCE_STATUSES = frozenset({"active", "removed"})
_MANAGER_ROLES = frozenset({"owner", "admin"})
_REGISTRY_TABLES = frozenset(
    {
        "alembic_version",
        "accounts",
        "tenants",
        "tenant_members",
        "datasets",
        "tenant_workspaces",
        "tenant_workspace_datasets",
        "apps",
        "tenant_approval_policies",
        "tenant_approval_policy_approvers",
        "tenant_approval_requests",
        "tenant_approval_decisions",
        "tenant_control_mutation_requests",
        "tenant_audit_events",
        "dataset_workspace_ownerships",
        "app_dataset_references",
    }
)


class KnowledgeBaseRegistryError(RuntimeError):
    """Base error for Registry authority failures."""

    def __init__(self, code: str, message: str, status: int = 503) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class KnowledgeBaseRegistryUnavailable(KnowledgeBaseRegistryError):
    def __init__(self, message: str = "Knowledge Base Registry 服务暂不可用") -> None:
        super().__init__("knowledge_base_registry_unavailable", message, 503)


class KnowledgeBaseRegistryForbidden(KnowledgeBaseRegistryError):
    def __init__(self, message: str = "当前身份无权访问 Knowledge Base Registry") -> None:
        super().__init__("knowledge_base_registry_forbidden", message, 403)


class KnowledgeBaseRegistryNotFound(KnowledgeBaseRegistryError):
    def __init__(self, message: str = "Knowledge Base 资源不存在") -> None:
        super().__init__("knowledge_base_registry_not_found", message, 404)


class KnowledgeBaseRegistryValidation(KnowledgeBaseRegistryError):
    def __init__(self, message: str = "Knowledge Base Registry 请求无效") -> None:
        super().__init__("knowledge_base_registry_invalid", message, 422)


class KnowledgeBaseRegistryConflict(KnowledgeBaseRegistryError):
    def __init__(self, message: str = "Knowledge Base Registry 请求冲突") -> None:
        super().__init__("knowledge_base_registry_conflict", message, 409)


class KnowledgeBaseRegistryIdempotencyConflict(KnowledgeBaseRegistryConflict):
    def __init__(self, message: str = "Idempotency-Key 已用于不同的 Registry 请求") -> None:
        super().__init__(message)
        self.code = "knowledge_base_registry_idempotency_conflict"


class KnowledgeBaseRegistryIdempotencyInProgress(KnowledgeBaseRegistryConflict):
    def __init__(self, message: str = "相同的 Registry 操作正在处理中") -> None:
        super().__init__(message)
        self.code = "knowledge_base_registry_idempotency_in_progress"


class KnowledgeBaseRegistryApprovalRequired(KnowledgeBaseRegistryConflict):
    def __init__(self, policy: Mapping[str, Any]) -> None:
        self.policy = dict(policy)
        super().__init__("Dataset Workspace ownership transfer 需要企业审批")
        self.code = "dataset_workspace_transfer_approval_required"


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
    updated_at: datetime
    item_id: str


# The public alias keeps the archive blocker discoverable from the Registry
# module while the Dataset repository owns the DatasetProfileConflict hierarchy.
DatasetArchiveBlocker = DatasetArchiveBlocked


def _clean(value: Any, field: str, maximum: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise KnowledgeBaseRegistryValidation(f"{field} 必须是字符串")
    result = value.strip()
    if (not result and not allow_empty) or len(result) > maximum:
        raise KnowledgeBaseRegistryValidation(f"{field} 无效")
    if any(ord(character) < 32 and character not in "\t\n\r" for character in result):
        raise KnowledgeBaseRegistryValidation(f"{field} 包含非法控制字符")
    if field == "reason":
        return sanitize_reason(result)
    return result


def _positive(value: Any, field: str) -> int:
    if type(value) is not int or value < 1:
        raise KnowledgeBaseRegistryValidation(f"{field} 必须是正整数")
    return value


def _optional_enum(value: Any, field: str, choices: frozenset[str]) -> str | None:
    if value is None:
        return None
    result = _clean(value, field, 32).casefold()
    if result not in choices:
        raise KnowledgeBaseRegistryValidation(f"{field} 不在允许范围内")
    return result


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.utcnow()
    if not isinstance(current, datetime):
        raise KnowledgeBaseRegistryValidation("now 必须是 datetime")
    return current.replace(tzinfo=None)


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
    current = _datetime(value)
    return current.isoformat(timespec="microseconds") + "Z" if current is not None else None


def _value(row: Mapping[str, Any], name: str, default: Any = None) -> Any:
    try:
        return row[name]
    except (KeyError, TypeError):
        return default


def _model_table(session: Session, table_name: str, model_name: str | None = None) -> Table:
    """Use the Stage 18 ORM when present, otherwise reflect the same table."""

    if model_name:
        try:
            orm = importlib.import_module("models.orm")
            model = getattr(orm, model_name, None)
            table = getattr(model, "__table__", None)
            if table is not None and getattr(table, "name", None) == table_name:
                return table
        except (ImportError, AttributeError):
            pass
    try:
        return Table(table_name, MetaData(), autoload_with=session.connection())
    except SQLAlchemyError as exc:
        raise KnowledgeBaseRegistryUnavailable(
            f"Registry Schema 缺少 {table_name} 结构证据"
        ) from exc


def _connection_for(bind: Any) -> tuple[Any, bool]:
    if hasattr(bind, "connect") and not hasattr(bind, "get_table_names"):
        return bind.connect(), True
    return bind, False


def _fallback_capability(bind: Any) -> tuple[str, tuple[str, ...]]:
    connection, owned = _connection_for(bind)
    try:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        missing = tuple(sorted(_REGISTRY_TABLES - tables))
        if missing:
            return "unavailable", tuple(f"missing table {name}" for name in missing)
        revisions = tuple(
            str(value)
            for value in connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalars()
        )
        if len(revisions) != 1 or revisions[0] != REVISION:
            return "unavailable", (f"catalog revision is not {REVISION}",)
        return "ready", ()
    except Exception as exc:  # pragma: no cover - defensive schema boundary
        return "unavailable", (f"Registry Schema inspection failed: {exc.__class__.__name__}",)
    finally:
        if owned:
            connection.close()


def _external_capability(bind: Any) -> tuple[str, tuple[str, ...]] | None:
    try:
        catalog_schema = importlib.import_module("core.catalog_schema")
    except ImportError:
        return None
    for name in (
        "inspect_enterprise_knowledge_base_registry_capability",
        "inspect_knowledge_base_registry_capability",
        "inspect_enterprise_knowledge_base_capability",
    ):
        inspector = getattr(catalog_schema, name, None)
        if not callable(inspector):
            continue
        try:
            result = inspector(bind)
            if isinstance(result, tuple) and len(result) == 2:
                state, issues = result
            elif isinstance(result, Mapping):
                state = result.get("state", result.get("capability_state"))
                issues = result.get("issues", ())
            else:
                state = getattr(result, "state", None)
                issues = getattr(result, "issues", ())
            if not isinstance(state, str):
                return "unavailable", ("0028 capability state is invalid",)
            if isinstance(issues, str):
                issues = (issues,)
            if not isinstance(issues, (tuple, list)):
                issues = ()
            return state, tuple(str(issue) for issue in issues)
        except Exception as exc:  # pragma: no cover - supplied worker boundary
            return "unavailable", (f"0028 capability inspection failed: {exc.__class__.__name__}",)
    return None


def inspect_registry_capability(bind: Any) -> tuple[str, tuple[str, ...]]:
    """Return the Stage 18 capability without treating an older catalog as ready."""

    external = _external_capability(bind)
    return external if external is not None else _fallback_capability(bind)


def _ensure_capability(bind: Any) -> None:
    state, issues = inspect_registry_capability(bind)
    if state != "ready":
        detail = "; ".join(issues) if issues else "0028 capability is not ready"
        raise KnowledgeBaseRegistryUnavailable(detail)


def _require_actor(session: Session, tenant_id: str, actor_id: str) -> _Actor:
    tenants = _model_table(session, "tenants", "Tenant")
    members = _model_table(session, "tenant_members", "TenantMember")
    accounts = _model_table(session, "accounts", "Account")
    statement = (
        select(
            members.c.role,
            members.c.status,
            tenants.c.status.label("tenant_status"),
            accounts.c.name,
            accounts.c.email,
        )
        .select_from(
            members.join(tenants, tenants.c.id == members.c.tenant_id).join(
                accounts, accounts.c.id == members.c.account_id
            )
        )
        .where(members.c.tenant_id == tenant_id, members.c.account_id == actor_id)
    )
    row = session.execute(statement).mappings().one_or_none()
    if (
        row is None
        or str(_value(row, "status", "")).casefold() != "active"
        or str(_value(row, "tenant_status", "")).casefold() != "active"
    ):
        raise KnowledgeBaseRegistryForbidden("当前身份不是该租户的 active member")
    role = str(_value(row, "role", "")).casefold()
    if not role:
        raise KnowledgeBaseRegistryUnavailable("Tenant member role 证据缺失")
    return _Actor(
        tenant_id=tenant_id,
        account_id=actor_id,
        role=role,
        name=str(_value(row, "name", ""))[:128],
        email=str(_value(row, "email", ""))[:256],
    )


def _lock_tenant(session: Session, tenant_id: str) -> None:
    tenants = _model_table(session, "tenants", "Tenant")
    row = session.execute(
        select(tenants.c.id).where(tenants.c.id == tenant_id).with_for_update()
    ).first()
    if row is None:
        raise KnowledgeBaseRegistryNotFound("租户不存在")


def _dataset(
    session: Session,
    tenant_id: str,
    dataset_id: str,
    *,
    lock: bool = False,
) -> Mapping[str, Any]:
    datasets = _model_table(session, "datasets", "Dataset")
    statement = select(datasets).where(
        datasets.c.tenant_id == tenant_id,
        datasets.c.id == dataset_id,
    )
    if lock:
        statement = statement.with_for_update()
    rows = list(session.execute(statement).mappings())
    if not rows:
        raise KnowledgeBaseRegistryNotFound()
    if len(rows) != 1:
        raise KnowledgeBaseRegistryUnavailable("Dataset ownership scope is ambiguous")
    return rows[0]


def _app(session: Session, tenant_id: str, app_id: str) -> Mapping[str, Any]:
    apps = _model_table(session, "apps", "App")
    row = (
        session.execute(select(apps).where(apps.c.tenant_id == tenant_id, apps.c.id == app_id))
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise KnowledgeBaseRegistryNotFound()
    return row


def _workspace(
    session: Session,
    tenant_id: str,
    workspace_id: str,
    *,
    lock: bool = False,
) -> Mapping[str, Any]:
    workspaces = _model_table(session, "tenant_workspaces", "TenantWorkspace")
    statement = select(workspaces).where(
        workspaces.c.tenant_id == tenant_id,
        workspaces.c.id == workspace_id,
    )
    if lock:
        statement = statement.with_for_update()
    row = session.execute(statement).mappings().one_or_none()
    if row is None:
        raise KnowledgeBaseRegistryNotFound("Workspace 不存在")
    return row


def _ownership_table(session: Session) -> Table:
    return _model_table(session, "dataset_workspace_ownerships", "DatasetWorkspaceOwnership")


def _reference_table(session: Session) -> Table:
    return _model_table(session, "app_dataset_references", "AppDatasetReference")


def _ownership_rows(
    session: Session,
    tenant_id: str,
    dataset_ids: list[str],
    *,
    lock: bool = False,
) -> dict[str, dict[str, Any]]:
    if not dataset_ids:
        return {}
    ownerships = _ownership_table(session)
    workspaces = _model_table(session, "tenant_workspaces", "TenantWorkspace")
    statement = (
        select(
            ownerships,
            workspaces.c.id.label("workspace_name_id"),
            workspaces.c.name.label("workspace_name"),
            workspaces.c.status.label("workspace_status"),
            workspaces.c.revision.label("workspace_revision"),
        )
        .select_from(
            ownerships.outerjoin(
                workspaces,
                and_(
                    workspaces.c.tenant_id == ownerships.c.tenant_id,
                    workspaces.c.id == ownerships.c.workspace_id,
                ),
            )
        )
        .where(
            ownerships.c.tenant_id == tenant_id,
            ownerships.c.dataset_id.in_(dataset_ids),
        )
        .order_by(ownerships.c.dataset_id.asc(), ownerships.c.id.asc())
    )
    if lock:
        statement = statement.with_for_update()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in session.execute(statement).mappings():
        grouped.setdefault(str(row["dataset_id"]), []).append(dict(row))
    result: dict[str, dict[str, Any]] = {}
    for dataset_id, rows in grouped.items():
        if len(rows) != 1:
            raise KnowledgeBaseRegistryUnavailable("每个 Dataset 必须有且只有一条 ownership")
        row = rows[0]
        if row.get("workspace_name") is None:
            raise KnowledgeBaseRegistryUnavailable("ownership 的 Workspace 证据缺失")
        result[dataset_id] = row
    return result


def _group_count(
    session: Session,
    table: Table,
    *,
    tenant_id: str,
    dataset_ids: list[str],
    conditions: tuple[Any, ...] = (),
) -> dict[str, int]:
    if not dataset_ids:
        return {}
    rows = session.execute(
        select(table.c.dataset_id, func.count().label("total"))
        .where(
            table.c.tenant_id == tenant_id,
            table.c.dataset_id.in_(dataset_ids),
            *conditions,
        )
        .group_by(table.c.dataset_id)
    )
    return {str(dataset_id): int(total) for dataset_id, total in rows}


def _reference_rows(
    session: Session,
    *,
    tenant_id: str,
    dataset_id: str | None = None,
    app_id: str | None = None,
    status: str | None = None,
    lock: bool = False,
) -> list[dict[str, Any]]:
    references = _reference_table(session)
    apps = _model_table(session, "apps", "App")
    conditions = [references.c.tenant_id == tenant_id]
    if dataset_id is not None:
        conditions.append(references.c.dataset_id == dataset_id)
    if app_id is not None:
        conditions.append(references.c.app_id == app_id)
    if status is not None:
        conditions.append(references.c.status == status)
    statement = (
        select(references, apps.c.name.label("app_name"), apps.c.kind.label("app_kind"))
        .select_from(
            references.outerjoin(
                apps,
                and_(apps.c.tenant_id == references.c.tenant_id, apps.c.id == references.c.app_id),
            )
        )
        .where(*conditions)
        .order_by(references.c.updated_at.desc(), references.c.id.desc())
    )
    if lock:
        statement = statement.with_for_update()
    return [dict(row) for row in session.execute(statement).mappings()]


def _binding_rows(
    session: Session,
    *,
    tenant_id: str,
    dataset_id: str,
    lock: bool = False,
) -> list[dict[str, Any]]:
    bindings = _model_table(session, "tenant_workspace_datasets", "TenantWorkspaceDataset")
    workspaces = _model_table(session, "tenant_workspaces", "TenantWorkspace")
    statement = (
        select(
            bindings,
            workspaces.c.name.label("workspace_name"),
            workspaces.c.status.label("workspace_status"),
        )
        .select_from(
            bindings.outerjoin(
                workspaces,
                and_(
                    workspaces.c.tenant_id == bindings.c.tenant_id,
                    workspaces.c.id == bindings.c.workspace_id,
                ),
            )
        )
        .where(bindings.c.tenant_id == tenant_id, bindings.c.dataset_id == dataset_id)
        .order_by(bindings.c.workspace_id.asc(), bindings.c.id.asc())
    )
    if lock:
        statement = statement.with_for_update()
    return [dict(row) for row in session.execute(statement).mappings()]


def _workspace_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(_value(row, "workspace_id")),
        "name": str(_value(row, "workspace_name", "")),
        "status": str(_value(row, "workspace_status", "")),
        "revision": int(_value(row, "workspace_revision", 1) or 1),
        "ownership_revision": int(_value(row, "revision", 1) or 1),
    }


def _ownership_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(_value(row, "id")),
        "tenant_id": str(_value(row, "tenant_id")),
        "dataset_id": str(_value(row, "dataset_id")),
        "workspace_id": str(_value(row, "workspace_id")),
        "revision": int(_value(row, "revision", 1) or 1),
        "created_at": _iso(_value(row, "created_at")),
        "created_by": str(_value(row, "created_by", "")),
        "updated_at": _iso(_value(row, "updated_at")),
        "updated_by": str(_value(row, "updated_by", "")),
        "last_transfer_at": _iso(_value(row, "last_transfer_at")),
    }


def _reference_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(_value(row, "id")),
        "tenant_id": str(_value(row, "tenant_id")),
        "app_id": str(_value(row, "app_id")),
        "app_name": _value(row, "app_name"),
        "app_kind": _value(row, "app_kind"),
        "dataset_id": str(_value(row, "dataset_id")),
        "reference_kind": str(_value(row, "reference_kind", REFERENCE_KIND_KNOWLEDGE)),
        "status": str(_value(row, "status")),
        "active_slot": _value(row, "active_slot"),
        "revision": int(_value(row, "revision", 1) or 1),
        "created_at": _iso(_value(row, "created_at")),
        "created_by": str(_value(row, "created_by", "")),
        "updated_at": _iso(_value(row, "updated_at")),
        "updated_by": str(_value(row, "updated_by", "")),
        "removed_at": _iso(_value(row, "removed_at")),
        "removed_by": _value(row, "removed_by"),
        "request_id": str(_value(row, "request_id", "")),
        "release_mode": _value(row, "release_mode"),
        "release_channel_id": _value(row, "release_channel_id"),
        "pinned_release_id": _value(row, "pinned_release_id"),
    }


def _binding_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": int(_value(row, "id")),
        "tenant_id": str(_value(row, "tenant_id")),
        "workspace_id": str(_value(row, "workspace_id")),
        "workspace_name": _value(row, "workspace_name"),
        "workspace_status": _value(row, "workspace_status"),
        "dataset_id": str(_value(row, "dataset_id")),
        "binding_kind": str(_value(row, "binding_kind")),
        "active_primary_slot": _value(row, "active_primary_slot"),
        "status": str(_value(row, "status")),
        "revision": int(_value(row, "revision", 1) or 1),
        "created_at": _iso(_value(row, "created_at")),
        "created_by": str(_value(row, "created_by", "")),
        "updated_at": _iso(_value(row, "updated_at")),
        "updated_by": str(_value(row, "updated_by", "")),
        "removed_at": _iso(_value(row, "removed_at")),
        "removed_by": _value(row, "removed_by"),
    }


def _archive_readiness(reference_rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    active = [
        row
        for row in reference_rows
        if str(_value(row, "status", "")).casefold() == ACTIVE_REFERENCE_STATUS
        and str(_value(row, "active_slot", "")).casefold() == ACTIVE_REFERENCE_SLOT
    ]
    references = [str(_value(row, "id")) for row in active]
    return {
        "ready": not active,
        "blocker_count": len(active),
        "blockers": (
            []
            if not active
            else [
                {
                    "code": "active_application_references",
                    "label": "存在活跃 Application 引用",
                    "count": len(active),
                    "status": "active",
                    "reference_ids": references,
                }
            ]
        ),
    }


def _dataset_projection(
    row: Mapping[str, Any],
    *,
    ownership: Mapping[str, Any] | None,
    shared_count: int,
    reference_count: int,
    source_count: int | None,
    catalog_revision: str,
    archive_readiness: Mapping[str, Any],
) -> dict[str, Any]:
    workspace = _workspace_projection(ownership) if ownership is not None else None
    ownership_revision = (
        int(_value(ownership, "revision", 1) or 1) if ownership is not None else None
    )
    return {
        "id": str(_value(row, "id")),
        "tenant_id": str(_value(row, "tenant_id")),
        "name": str(_value(row, "name", "")),
        "description": str(_value(row, "description", "")),
        "status": str(_value(row, "status", "")),
        "visibility": str(_value(row, "visibility", "")),
        "profile_revision": int(_value(row, "profile_revision", 1) or 1),
        "owning_workspace": workspace,
        "workspace": workspace,
        "ownership_revision": ownership_revision,
        "active_shared_association_count": shared_count,
        "shared_association_count": shared_count,
        "active_application_reference_count": reference_count,
        "document_count": _value(row, "doc_count"),
        "chunk_count": _value(row, "chunk_count"),
        "source_count": source_count,
        "updated_at": _iso(_value(row, "updated_at")),
        "archive_readiness": dict(archive_readiness),
        "archive_ready": archive_readiness.get("ready"),
        "archive_blocker_count": archive_readiness.get("blocker_count"),
        "catalog_revision": catalog_revision,
        "catalog_capability_state": "ready",
        "capability_state": "ready",
        "catalog": {"revision": catalog_revision, "capability_state": "ready"},
    }


def _cursor_encode(value: datetime, item_id: str) -> str:
    raw = json.dumps(
        {"v": 1, "kind": _CURSOR_KIND, "updated_at": _iso(value), "id": item_id},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _cursor_decode(value: str | None) -> _Cursor | None:
    if value is None or not value.strip():
        return None
    token = value.strip()
    if len(token) > 512:
        raise KnowledgeBaseRegistryValidation("cursor 无效")
    try:
        padded = token + "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KnowledgeBaseRegistryValidation("cursor 无效") from exc
    if (
        not isinstance(payload, Mapping)
        or set(payload) != {"v", "kind", "updated_at", "id"}
        or payload.get("v") != 1
        or payload.get("kind") != _CURSOR_KIND
        or not isinstance(payload.get("id"), str)
        or not _SAFE_ID.fullmatch(payload["id"])
    ):
        raise KnowledgeBaseRegistryValidation("cursor 无效")
    timestamp = _datetime(payload.get("updated_at"))
    if timestamp is None:
        raise KnowledgeBaseRegistryValidation("cursor 无效")
    return _Cursor(timestamp, payload["id"])


def _page_dataset_rows(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str | None,
    status: str | None,
    keyword: str | None,
    cursor: _Cursor | None,
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    datasets = _model_table(session, "datasets", "Dataset")
    ownerships = _ownership_table(session)
    conditions: list[Any] = [datasets.c.tenant_id == tenant_id]
    if status is not None:
        conditions.append(datasets.c.status == status)
    if keyword:
        pattern = f"%{keyword}%"
        conditions.append(
            or_(
                datasets.c.id.ilike(pattern),
                datasets.c.name.ilike(pattern),
                datasets.c.description.ilike(pattern),
            )
        )
    if workspace_id is not None:
        conditions.append(
            select(ownerships.c.dataset_id)
            .where(
                ownerships.c.tenant_id == tenant_id,
                ownerships.c.dataset_id == datasets.c.id,
                ownerships.c.workspace_id == workspace_id,
            )
            .exists()
        )
    total = int(session.scalar(select(func.count()).select_from(datasets).where(*conditions)) or 0)
    statement = select(datasets).where(*conditions)
    if cursor is not None:
        statement = statement.where(
            or_(
                datasets.c.updated_at < cursor.updated_at,
                and_(datasets.c.updated_at == cursor.updated_at, datasets.c.id < cursor.item_id),
            )
        )
    rows = list(
        session.execute(
            statement.order_by(datasets.c.updated_at.desc(), datasets.c.id.desc()).limit(limit + 1)
        ).mappings()
    )
    return [dict(row) for row in rows], total


def _catalog_revision(session: Session) -> str:
    version = _model_table(session, "alembic_version")
    row = session.execute(select(version.c.version_num)).first()
    if row is None or not row[0]:
        raise KnowledgeBaseRegistryUnavailable("Catalog revision 证据缺失")
    return str(row[0])


def _read_projection(
    session: Session,
    *,
    tenant_id: str,
    dataset_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    dataset_ids = [str(row["id"]) for row in dataset_rows]
    ownerships = _ownership_rows(session, tenant_id, dataset_ids)
    bindings = _model_table(session, "tenant_workspace_datasets", "TenantWorkspaceDataset")
    references = _reference_table(session)
    shared_counts = _group_count(
        session,
        bindings,
        tenant_id=tenant_id,
        dataset_ids=dataset_ids,
        conditions=(
            bindings.c.status == ACTIVE_REFERENCE_STATUS,
            bindings.c.binding_kind == "shared",
        ),
    )
    active_reference_counts = _group_count(
        session,
        references,
        tenant_id=tenant_id,
        dataset_ids=dataset_ids,
        conditions=(
            references.c.status == ACTIVE_REFERENCE_STATUS,
            references.c.active_slot == ACTIVE_REFERENCE_SLOT,
        ),
    )
    source_counts: dict[str, int] = {}
    source_count_available = True
    try:
        sources = _model_table(session, "data_sources", "DataSourceRecord")
        source_counts = _group_count(session, sources, tenant_id=tenant_id, dataset_ids=dataset_ids)
    except KnowledgeBaseRegistryUnavailable:
        source_count_available = False
    reference_rows = _reference_rows(session, tenant_id=tenant_id, status=ACTIVE_REFERENCE_STATUS)
    references_by_dataset: dict[str, list[Mapping[str, Any]]] = {}
    for reference in reference_rows:
        references_by_dataset.setdefault(str(reference["dataset_id"]), []).append(reference)
    revision = _catalog_revision(session)
    projections: list[dict[str, Any]] = []
    for row in dataset_rows:
        dataset_id = str(row["id"])
        projections.append(
            _dataset_projection(
                row,
                ownership=ownerships.get(dataset_id),
                shared_count=shared_counts.get(dataset_id, 0),
                reference_count=active_reference_counts.get(dataset_id, 0),
                source_count=source_counts.get(dataset_id, 0) if source_count_available else None,
                catalog_revision=revision,
                archive_readiness=_archive_readiness(references_by_dataset.get(dataset_id, [])),
            )
        )
    return projections


def _registry_evidence(session: Session, tenant_id: str) -> dict[str, Any]:
    datasets = _model_table(session, "datasets", "Dataset")
    ownerships = _ownership_table(session)
    references = _reference_table(session)
    total = int(
        session.scalar(
            select(func.count()).select_from(datasets).where(datasets.c.tenant_id == tenant_id)
        )
        or 0
    )
    active = int(
        session.scalar(
            select(func.count())
            .select_from(datasets)
            .where(datasets.c.tenant_id == tenant_id, datasets.c.status == "active")
        )
        or 0
    )
    owned = int(
        session.scalar(
            select(func.count()).select_from(ownerships).where(ownerships.c.tenant_id == tenant_id)
        )
        or 0
    )
    active_references = int(
        session.scalar(
            select(func.count())
            .select_from(references)
            .where(
                references.c.tenant_id == tenant_id,
                references.c.status == ACTIVE_REFERENCE_STATUS,
                references.c.active_slot == ACTIVE_REFERENCE_SLOT,
            )
        )
        or 0
    )
    blocked_datasets = int(
        session.scalar(
            select(func.count(func.distinct(references.c.dataset_id))).where(
                references.c.tenant_id == tenant_id,
                references.c.status == ACTIVE_REFERENCE_STATUS,
                references.c.active_slot == ACTIVE_REFERENCE_SLOT,
            )
        )
        or 0
    )
    revision = _catalog_revision(session)
    return {
        "knowledge_base_count": total,
        "active_count": active,
        "owned_count": owned,
        "active_application_reference_count": active_references,
        "archive_ready_count": max(0, total - blocked_datasets),
        "catalog_revision": revision,
        "capability_state": "ready",
    }


def _require_manage(session: Session, actor: _Actor, dataset_id: str) -> None:
    if actor.role in _MANAGER_ROLES:
        return
    try:
        access = importlib.import_module("core.enterprise_access_control")
        decision = access.evaluate_dataset_permissions(
            session.bind,
            actor.tenant_id,
            actor.account_id,
            actor.role,
            dataset_id,
            session=session,
        )
        if decision.allows(KNOWLEDGE_MANAGE):
            return
    except Exception:
        pass
    raise KnowledgeBaseRegistryForbidden("当前身份没有该 Dataset 的 manage 权限")


def _audit_json(value: Any) -> Any:
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, Mapping):
        return {str(key): _audit_json(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_audit_json(child) for child in value]
    if isinstance(value, tuple):
        return [_audit_json(child) for child in value]
    return value


def _safe_audit_snapshot(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return sanitize_audit_snapshot(_audit_json(dict(value)))


def _audit(
    session: Session,
    *,
    actor: _Actor,
    action: str,
    resource_id: str,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    request_id: str,
    request_ip: str,
    occurred_at: datetime,
) -> None:
    audits = _model_table(session, "tenant_audit_events", "TenantAuditEvent")
    values: dict[str, Any] = {
        "id": f"tenant-audit-{uuid.uuid4().hex}",
        "tenant_id": actor.tenant_id,
        "actor_id": actor.account_id,
        "actor_name_snapshot": actor.name[:128],
        "actor_email_snapshot": actor.email[:256],
        "action": action,
        "resource_type": RESOURCE_KNOWLEDGE_BASE,
        "resource_id": resource_id,
        "target_account_id": None,
        "before_snapshot": _safe_audit_snapshot(before),
        "after_snapshot": _safe_audit_snapshot(after),
        "request_id": _clean(request_id, "request_id", 128, allow_empty=True),
        "request_ip": _clean(request_ip, "request_ip", 64, allow_empty=True),
        "occurred_at": occurred_at,
    }
    available = {str(column.name) for column in audits.columns}
    session.execute(
        audits.insert().values({key: value for key, value in values.items() if key in available})
    )


@contextmanager
def _mutation_scope(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    idempotency_key: str,
    request_hash: str,
    operation: str,
    resource_type: str = RESOURCE_KNOWLEDGE_BASE,
) -> Iterator[tuple[Session, _Actor, Any]]:
    digest = tenant_idempotency_key_digest(tenant_id, actor_id, idempotency_key)
    with ExitStack() as stack:
        stack.enter_context(idempotency_key_lock(tenant_id, actor_id, digest))
        stack.enter_context(engine_serialization_lock(engine))
        session = stack.enter_context(Session(engine, expire_on_commit=False))
        stack.enter_context(session.begin())
        _ensure_capability(session.connection())
        _lock_tenant(session, tenant_id)
        actor = _require_actor(session, tenant_id, actor_id)
        try:
            reservation = reserve_tenant_mutation(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                raw_idempotency_key=idempotency_key,
                request_hash=request_hash,
                operation=operation,
                resource_type=resource_type,
            )
        except TenantMutationIdempotencyConflict as exc:
            raise KnowledgeBaseRegistryIdempotencyConflict() from exc
        except TenantMutationIdempotencyInProgress as exc:
            raise KnowledgeBaseRegistryIdempotencyInProgress() from exc
        except TenantMutationIdempotencyValidationError as exc:
            raise KnowledgeBaseRegistryValidation(str(exc)) from exc
        try:
            yield session, actor, reservation
        except IntegrityError as exc:
            raise KnowledgeBaseRegistryConflict(
                "Registry mutation violates an authority constraint"
            ) from exc


def _complete(
    session: Session,
    reservation: Any,
    *,
    payload: Mapping[str, Any],
    status: int,
    resource_id: str,
) -> ServiceResult:
    try:
        replay = complete_tenant_mutation(
            session,
            reservation,
            response_for_replay=payload,
            http_status=status,
            resource_id=resource_id,
        )
    except TenantMutationIdempotencyValidationError as exc:
        raise KnowledgeBaseRegistryValidation(str(exc)) from exc
    return ServiceResult(dict(replay), status)


def _mutation_fields(
    *,
    request_id: str,
    request_ip: str,
    idempotency_key: str,
    reason: str,
) -> tuple[str, str, str, str]:
    return (
        _clean(request_id, "request_id", 128, allow_empty=True),
        _clean(request_ip, "request_ip", 64, allow_empty=True),
        _clean(idempotency_key, "idempotency_key", 128),
        _clean(reason, "reason", 512),
    )


def list_knowledge_bases(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    workspace_id: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
) -> ServiceResult:
    tenant = _clean(tenant_id, "tenant_id", 64)
    actor = _clean(actor_id, "actor_id", 64)
    workspace = None if workspace_id is None else _clean(workspace_id, "workspace_id", 128)
    dataset_status = _optional_enum(status, "status", _DATASET_STATUSES)
    query = None if keyword is None else _clean(keyword, "keyword", 128, allow_empty=True)
    page_limit = _positive(limit, "limit")
    if page_limit > 100:
        raise KnowledgeBaseRegistryValidation("limit 不能超过 100")
    decoded = _cursor_decode(cursor)
    try:
        with Session(engine) as session:
            _ensure_capability(session.connection())
            _require_actor(session, tenant, actor)
            rows, total = _page_dataset_rows(
                session,
                tenant_id=tenant,
                workspace_id=workspace,
                status=dataset_status,
                keyword=query,
                cursor=decoded,
                limit=page_limit,
            )
            has_more = len(rows) > page_limit
            visible = rows[:page_limit]
            items = _read_projection(session, tenant_id=tenant, dataset_rows=visible)
            next_cursor = None
            if has_more and visible:
                updated_at = _datetime(visible[-1].get("updated_at"))
                if updated_at is None:
                    raise KnowledgeBaseRegistryUnavailable("Dataset updated_at 证据缺失")
                next_cursor = _cursor_encode(updated_at, str(visible[-1]["id"]))
            return ServiceResult(
                {
                    "items": items,
                    "count": total,
                    "next_cursor": next_cursor,
                    "catalog_revision": _catalog_revision(session),
                    "evidence": _registry_evidence(session, tenant),
                }
            )
    except KnowledgeBaseRegistryError:
        raise
    except SQLAlchemyError as exc:
        raise KnowledgeBaseRegistryUnavailable() from exc


def get_knowledge_base(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
) -> ServiceResult:
    tenant = _clean(tenant_id, "tenant_id", 64)
    actor = _clean(actor_id, "actor_id", 64)
    dataset = _clean(dataset_id, "dataset_id", 64)
    try:
        with Session(engine) as session:
            _ensure_capability(session.connection())
            _require_actor(session, tenant, actor)
            row = dict(_dataset(session, tenant, dataset))
            projection = _read_projection(session, tenant_id=tenant, dataset_rows=[row])[0]
            binding_rows = _binding_rows(session, tenant_id=tenant, dataset_id=dataset)
            reference_rows = _reference_rows(
                session, tenant_id=tenant, dataset_id=dataset, status=ACTIVE_REFERENCE_STATUS
            )
            projected_bindings = [_binding_projection(item) for item in binding_rows]
            projected_references = [_reference_projection(item) for item in reference_rows]
            payload = {
                "knowledge_base": projection,
                "workspace_associations": projected_bindings,
                "references": projected_references,
                "application_references": {
                    "items": projected_references,
                    "count": len(projected_references),
                    "next_cursor": None,
                },
                "dependencies": {
                    "state": "ready",
                    "reason": None,
                    "owning_workspace": projection["owning_workspace"],
                    "shared_associations": [
                        item for item in projected_bindings if item.get("binding_kind") == "shared"
                    ],
                    "associations": projected_bindings,
                    "application_references": projected_references,
                    "archive": projection["archive_readiness"],
                    "archive_readiness": projection["archive_readiness"],
                    "capability_state": "ready",
                },
                "catalog": projection["catalog"],
            }
            payload.update(projection)
            return ServiceResult(payload)
    except KnowledgeBaseRegistryError:
        raise
    except SQLAlchemyError as exc:
        raise KnowledgeBaseRegistryUnavailable() from exc


def get_knowledge_base_dependencies(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
) -> ServiceResult:
    detail = get_knowledge_base(
        engine, tenant_id=tenant_id, actor_id=actor_id, dataset_id=dataset_id
    )
    projection = detail.body["knowledge_base"]
    return ServiceResult(
        {
            "dataset": projection,
            "knowledge_base": projection,
            "state": "ready",
            "reason": None,
            "owning_workspace": projection["owning_workspace"],
            "shared_associations": [
                item
                for item in detail.body["workspace_associations"]
                if item.get("binding_kind") == "shared"
            ],
            "associations": detail.body["workspace_associations"],
            "application_references": detail.body["references"],
            "archive": projection["archive_readiness"],
            "archive_readiness": projection["archive_readiness"],
            "capability_state": "ready",
            "catalog": detail.body["catalog"],
        }
    )


def list_app_references(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    app_id: str,
    status: str | None = ACTIVE_REFERENCE_STATUS,
) -> ServiceResult:
    tenant = _clean(tenant_id, "tenant_id", 64)
    actor = _clean(actor_id, "actor_id", 64)
    app = _clean(app_id, "app_id", 64)
    reference_status = _optional_enum(status, "status", _REFERENCE_STATUSES)
    try:
        with Session(engine) as session:
            _ensure_capability(session.connection())
            _require_actor(session, tenant, actor)
            _app(session, tenant, app)
            rows = _reference_rows(session, tenant_id=tenant, app_id=app, status=reference_status)
            return ServiceResult(
                {
                    "items": [_reference_projection(row) for row in rows],
                    "count": len(rows),
                    "catalog_revision": _catalog_revision(session),
                }
            )
    except KnowledgeBaseRegistryError:
        raise
    except SQLAlchemyError as exc:
        raise KnowledgeBaseRegistryUnavailable() from exc


def _active_transfer_policy(
    session: Session, tenant_id: str, dataset_id: str
) -> dict[str, Any] | None:
    """Resolve transfer approval through the canonical approval scope authority."""

    try:
        return resolve_active_approval_policy_in_session(
            session,
            tenant_id=tenant_id,
            action_type="dataset_workspace_transfer",
            resource_type=RESOURCE_KNOWLEDGE_BASE,
            resource_id=dataset_id,
            lock_for_update=True,
            tenant_already_locked=True,
        )
    except ApprovalError as exc:
        raise KnowledgeBaseRegistryUnavailable(exc.message) from exc


def _validate_approval_fact(
    fact: Mapping[str, Any] | None,
    *,
    tenant_id: str,
    dataset_id: str,
    target_workspace_id: str,
    expected_dataset_profile_revision: int,
    expected_ownership_revision: int,
    source_workspace_id: str | None = None,
) -> None:
    if not isinstance(fact, ApprovalExecutionFact):
        raise KnowledgeBaseRegistryConflict("ApprovalExecutionFact 类型或字段不完整")
    expected = {
        "tenant_id": tenant_id,
        "action_type": "dataset_workspace_transfer",
        "resource_type": RESOURCE_KNOWLEDGE_BASE,
        "resource_id": dataset_id,
        "target_workspace_id": target_workspace_id,
        "expected_dataset_profile_revision": expected_dataset_profile_revision,
        "expected_ownership_revision": expected_ownership_revision,
    }
    if source_workspace_id is not None:
        expected["source_workspace_id"] = source_workspace_id
    for key, value in expected.items():
        if getattr(fact, key, None) != value:
            raise KnowledgeBaseRegistryConflict(
                "审批执行事实与当前 ownership transfer scope 不匹配"
            )
    if (
        fact.profile_revision != expected_dataset_profile_revision
        or fact.dataset_profile_revision != expected_dataset_profile_revision
        or fact.expected_dataset_profile_revision != expected_dataset_profile_revision
        or fact.ownership_revision != expected_ownership_revision
        or fact.expected_ownership_revision != expected_ownership_revision
        or fact.expected_source_workspace_revision != fact.source_workspace_revision
        or fact.expected_target_workspace_revision != fact.target_workspace_revision
        or not fact.source_workspace_id
        or not fact.target_workspace_id
        or type(fact.source_workspace_revision) is not int
        or type(fact.target_workspace_revision) is not int
        or fact.source_workspace_revision < 1
        or fact.target_workspace_revision < 1
    ):
        raise KnowledgeBaseRegistryConflict("审批执行事实缺少完整 Workspace revision")


def create_app_reference(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    actor_role: str | None = None,
    app_id: str,
    dataset_id: str,
    reference_kind: str = REFERENCE_KIND_KNOWLEDGE,
    reason: str,
    request_id: str,
    request_ip: str = "",
    idempotency_key: str,
    now: datetime | None = None,
) -> ServiceResult:
    tenant = _clean(tenant_id, "tenant_id", 64)
    actor = _clean(actor_id, "actor_id", 64)
    app = _clean(app_id, "app_id", 64)
    dataset = _clean(dataset_id, "dataset_id", 64)
    kind = _clean(reference_kind, "reference_kind", 24).casefold()
    if kind != REFERENCE_KIND_KNOWLEDGE:
        raise KnowledgeBaseRegistryValidation("reference_kind 只支持 knowledge")
    clean_request, clean_ip, clean_key, clean_reason = _mutation_fields(
        request_id=request_id, request_ip=request_ip, idempotency_key=idempotency_key, reason=reason
    )
    request_hash = tenant_request_hash(
        operation="create_app_reference",
        path_identity={"app_id": app, "dataset_id": dataset},
        body={"reference_kind": kind, "reason": clean_reason},
    )
    try:
        with _mutation_scope(
            engine,
            tenant_id=tenant,
            actor_id=actor,
            idempotency_key=clean_key,
            request_hash=request_hash,
            operation="create_app_reference",
        ) as (session, current_actor, reservation):
            if reservation.replay is not None:
                return ServiceResult(reservation.replay.response, reservation.replay.http_status)
            if actor_role is not None and str(actor_role).casefold() != current_actor.role:
                raise KnowledgeBaseRegistryForbidden(
                    "actor role evidence does not match Tenant membership"
                )
            _app(session, tenant, app)
            dataset_row = _dataset(session, tenant, dataset)
            if str(_value(dataset_row, "status", "")).casefold() != "active":
                raise KnowledgeBaseRegistryConflict(
                    "Dataset 必须是 active 才能创建 Application reference"
                )
            _require_manage(session, current_actor, dataset)
            active = _reference_rows(
                session,
                tenant_id=tenant,
                app_id=app,
                dataset_id=dataset,
                status=ACTIVE_REFERENCE_STATUS,
                lock=True,
            )
            if any(
                str(_value(row, "reference_kind", "")).casefold() == kind
                and str(_value(row, "active_slot", "")).casefold() == ACTIVE_REFERENCE_SLOT
                for row in active
            ):
                raise KnowledgeBaseRegistryConflict(
                    "Application 已存在 active Knowledge Base reference"
                )
            references = _reference_table(session)
            current = _now(now)
            reference_id = f"app-ref-{uuid.uuid4().hex[:16]}"
            values = {
                "id": reference_id,
                "tenant_id": tenant,
                "app_id": app,
                "dataset_id": dataset,
                "reference_kind": kind,
                "status": ACTIVE_REFERENCE_STATUS,
                "active_slot": ACTIVE_REFERENCE_SLOT,
                "revision": 1,
                "created_at": current,
                "created_by": actor,
                "updated_at": current,
                "updated_by": actor,
                "removed_at": None,
                "removed_by": None,
                "request_id": clean_request,
            }
            available = {str(column.name) for column in references.columns}
            if {"release_mode", "release_channel_id", "pinned_release_id"} <= available:
                from core.enterprise_release_channels import (
                    ensure_default_release_channels_in_session,
                )

                ensure_default_release_channels_in_session(
                    session, tenant_id=tenant, actor_id=actor, now=current
                )
                release_channels = _model_table(
                    session, "tenant_release_channels", "TenantReleaseChannel"
                )
                default_channel_id = session.scalar(
                    select(release_channels.c.id).where(
                        release_channels.c.tenant_id == tenant,
                        release_channels.c.status == "active",
                        release_channels.c.is_default_serving.is_(True),
                    )
                )
                if default_channel_id is None:
                    raise KnowledgeBaseRegistryUnavailable(
                        "Tenant default-serving Release Channel 缺失"
                    )
                values.update(
                    {
                        "release_mode": "follow_channel",
                        "release_channel_id": str(default_channel_id),
                        "pinned_release_id": None,
                    }
                )
            session.execute(
                references.insert().values(
                    {key: value for key, value in values.items() if key in available}
                )
            )
            after = _reference_rows(
                session,
                tenant_id=tenant,
                app_id=app,
                dataset_id=dataset,
                status=ACTIVE_REFERENCE_STATUS,
            )
            created = next(row for row in after if str(row["id"]) == reference_id)
            projected = _reference_projection(created)
            _audit(
                session,
                actor=current_actor,
                action="knowledge_base.app_reference.create",
                resource_id=dataset,
                before=None,
                after={"reference": projected, "reason": clean_reason},
                request_id=clean_request,
                request_ip=clean_ip,
                occurred_at=current,
            )
            return _complete(
                session,
                reservation,
                payload={
                    "state": "applied",
                    "message": "Application 引用已添加",
                    "reference": projected,
                    "mutation": {"result": "created", "resource_id": reference_id},
                },
                status=201,
                resource_id=reference_id,
            )
    except KnowledgeBaseRegistryError:
        raise
    except IntegrityError as exc:
        raise KnowledgeBaseRegistryConflict(
            "Application reference violates an authority constraint"
        ) from exc
    except SQLAlchemyError as exc:
        raise KnowledgeBaseRegistryUnavailable() from exc


def remove_app_reference(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    actor_role: str | None = None,
    app_id: str,
    dataset_id: str,
    expected_revision: int,
    reason: str,
    request_id: str,
    request_ip: str = "",
    idempotency_key: str,
    now: datetime | None = None,
) -> ServiceResult:
    tenant = _clean(tenant_id, "tenant_id", 64)
    actor = _clean(actor_id, "actor_id", 64)
    app = _clean(app_id, "app_id", 64)
    dataset = _clean(dataset_id, "dataset_id", 64)
    expected = _positive(expected_revision, "expected_revision")
    clean_request, clean_ip, clean_key, clean_reason = _mutation_fields(
        request_id=request_id, request_ip=request_ip, idempotency_key=idempotency_key, reason=reason
    )
    request_hash = tenant_request_hash(
        operation="remove_app_reference",
        path_identity={"app_id": app, "dataset_id": dataset},
        body={"expected_revision": expected, "reason": clean_reason},
    )
    try:
        with _mutation_scope(
            engine,
            tenant_id=tenant,
            actor_id=actor,
            idempotency_key=clean_key,
            request_hash=request_hash,
            operation="remove_app_reference",
        ) as (session, current_actor, reservation):
            if reservation.replay is not None:
                return ServiceResult(reservation.replay.response, reservation.replay.http_status)
            if actor_role is not None and str(actor_role).casefold() != current_actor.role:
                raise KnowledgeBaseRegistryForbidden(
                    "actor role evidence does not match Tenant membership"
                )
            _app(session, tenant, app)
            _dataset(session, tenant, dataset)
            _require_manage(session, current_actor, dataset)
            references = _reference_table(session)
            rows = _reference_rows(
                session,
                tenant_id=tenant,
                app_id=app,
                dataset_id=dataset,
                status=ACTIVE_REFERENCE_STATUS,
                lock=True,
            )
            matching = [
                row
                for row in rows
                if str(_value(row, "reference_kind", "")).casefold() == REFERENCE_KIND_KNOWLEDGE
                and str(_value(row, "active_slot", "")).casefold() == ACTIVE_REFERENCE_SLOT
            ]
            if not matching:
                raise KnowledgeBaseRegistryNotFound("active Application reference 不存在")
            if len(matching) != 1:
                raise KnowledgeBaseRegistryUnavailable("active Application reference 不唯一")
            before = _reference_projection(matching[0])
            if int(_value(matching[0], "revision", 0)) != expected:
                raise KnowledgeBaseRegistryConflict("Application reference revision conflict")
            current = _now(now)
            session.execute(
                update(references)
                .where(
                    references.c.tenant_id == tenant,
                    references.c.id == matching[0]["id"],
                    references.c.status == ACTIVE_REFERENCE_STATUS,
                    references.c.revision == expected,
                )
                .values(
                    status=REMOVED_REFERENCE_STATUS,
                    active_slot=None,
                    revision=references.c.revision + 1,
                    updated_at=current,
                    updated_by=actor,
                    removed_at=current,
                    removed_by=actor,
                )
            )
            after_rows = _reference_rows(session, tenant_id=tenant, app_id=app, dataset_id=dataset)
            after = next(row for row in after_rows if str(row["id"]) == str(matching[0]["id"]))
            projected = _reference_projection(after)
            _audit(
                session,
                actor=current_actor,
                action="knowledge_base.app_reference.remove",
                resource_id=dataset,
                before={"reference": before, "reason": clean_reason},
                after={"reference": projected, "reason": clean_reason},
                request_id=clean_request,
                request_ip=clean_ip,
                occurred_at=current,
            )
            return _complete(
                session,
                reservation,
                payload={
                    "state": "applied",
                    "message": "Application 引用已移除",
                    "reference": projected,
                    "mutation": {"result": "removed", "resource_id": str(matching[0]["id"])},
                },
                status=200,
                resource_id=str(matching[0]["id"]),
            )
    except KnowledgeBaseRegistryError:
        raise
    except IntegrityError as exc:
        raise KnowledgeBaseRegistryConflict(
            "Application reference removal violates an authority constraint"
        ) from exc
    except SQLAlchemyError as exc:
        raise KnowledgeBaseRegistryUnavailable() from exc


def transfer_dataset_ownership(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    actor_role: str | None = None,
    dataset_id: str,
    target_workspace_id: str,
    expected_dataset_profile_revision: int,
    expected_ownership_revision: int,
    expected_source_workspace_revision: int,
    expected_target_workspace_revision: int,
    reason: str,
    request_id: str,
    request_ip: str = "",
    idempotency_key: str,
    now: datetime | None = None,
    approval_execution_fact: Mapping[str, Any] | None = None,
) -> ServiceResult:
    tenant = _clean(tenant_id, "tenant_id", 64)
    actor = _clean(actor_id, "actor_id", 64)
    dataset_id = _clean(dataset_id, "dataset_id", 64)
    target = _clean(target_workspace_id, "target_workspace_id", 128)
    expected_profile = _positive(
        expected_dataset_profile_revision, "expected_dataset_profile_revision"
    )
    expected_owner = _positive(expected_ownership_revision, "expected_ownership_revision")
    expected_source_workspace = _positive(
        expected_source_workspace_revision, "expected_source_workspace_revision"
    )
    expected_target_workspace = _positive(
        expected_target_workspace_revision, "expected_target_workspace_revision"
    )
    clean_request, clean_ip, clean_key, clean_reason = _mutation_fields(
        request_id=request_id, request_ip=request_ip, idempotency_key=idempotency_key, reason=reason
    )
    request_hash = tenant_request_hash(
        operation="transfer_dataset_ownership",
        path_identity={"dataset_id": dataset_id},
        body={
            "target_workspace_id": target,
            "expected_dataset_profile_revision": expected_profile,
            "expected_ownership_revision": expected_owner,
            "expected_source_workspace_revision": expected_source_workspace,
            "expected_target_workspace_revision": expected_target_workspace,
            "reason": clean_reason,
        },
    )
    try:
        with _mutation_scope(
            engine,
            tenant_id=tenant,
            actor_id=actor,
            idempotency_key=clean_key,
            request_hash=request_hash,
            operation="transfer_dataset_ownership",
        ) as (session, current_actor, reservation):
            if reservation.replay is not None:
                return ServiceResult(reservation.replay.response, reservation.replay.http_status)
            if actor_role is not None and str(actor_role).casefold() != current_actor.role:
                raise KnowledgeBaseRegistryForbidden(
                    "actor role evidence does not match Tenant membership"
                )
            if current_actor.role not in _MANAGER_ROLES:
                raise KnowledgeBaseRegistryForbidden(
                    "只有 Tenant owner/admin 可以转移 Dataset ownership"
                )
            dataset_row = dict(_dataset(session, tenant, dataset_id, lock=True))
            if int(_value(dataset_row, "profile_revision", 0)) != expected_profile:
                raise KnowledgeBaseRegistryConflict("Dataset profile revision conflict")
            ownerships = _ownership_table(session)
            ownership_rows = list(
                session.execute(
                    select(ownerships)
                    .where(
                        ownerships.c.tenant_id == tenant,
                        ownerships.c.dataset_id == dataset_id,
                    )
                    .with_for_update()
                ).mappings()
            )
            if len(ownership_rows) != 1:
                raise KnowledgeBaseRegistryUnavailable("Dataset ownership 必须存在且唯一")
            ownership_before_row = dict(ownership_rows[0])
            if int(_value(ownership_before_row, "revision", 0)) != expected_owner:
                raise KnowledgeBaseRegistryConflict("Dataset ownership revision conflict")
            source_workspace_id = str(ownership_before_row["workspace_id"])
            if source_workspace_id == target:
                raise KnowledgeBaseRegistryConflict("target Workspace 已经是当前 owner")
            workspace_rows: dict[str, Mapping[str, Any]] = {}
            for workspace_id in sorted({source_workspace_id, target}):
                workspace_rows[workspace_id] = _workspace(session, tenant, workspace_id, lock=True)
            source_workspace = workspace_rows[source_workspace_id]
            target_workspace = workspace_rows[target]
            if int(_value(source_workspace, "revision", 0)) != expected_source_workspace:
                raise KnowledgeBaseRegistryConflict("source Workspace revision conflict")
            if int(_value(target_workspace, "revision", 0)) != expected_target_workspace:
                raise KnowledgeBaseRegistryConflict("target Workspace revision conflict")
            if str(_value(source_workspace, "status", "")).casefold() != "active":
                raise KnowledgeBaseRegistryConflict("source Workspace 必须是 active")
            if str(_value(target_workspace, "status", "")).casefold() != "active":
                raise KnowledgeBaseRegistryConflict("target Workspace 必须是 active")
            binding_rows = _binding_rows(
                session, tenant_id=tenant, dataset_id=dataset_id, lock=True
            )
            active_bindings = [
                row for row in binding_rows if str(_value(row, "status", "")).casefold() == "active"
            ]
            primary_rows = [
                row
                for row in active_bindings
                if str(_value(row, "binding_kind", "")).casefold() == "primary"
                and str(_value(row, "active_primary_slot", "")).casefold() == "primary"
            ]
            if (
                len(primary_rows) != 1
                or str(primary_rows[0]["workspace_id"]) != source_workspace_id
            ):
                raise KnowledgeBaseRegistryUnavailable(
                    "active primary Workspace binding 与 ownership 不一致"
                )
            source_binding = primary_rows[0]
            target_shared = [
                row
                for row in active_bindings
                if str(row["workspace_id"]) == target
                and str(_value(row, "binding_kind", "")).casefold() == "shared"
            ]
            target_primary = [
                row
                for row in active_bindings
                if str(row["workspace_id"]) == target
                and str(_value(row, "binding_kind", "")).casefold() == "primary"
            ]
            target_removed = [
                row
                for row in binding_rows
                if str(row["workspace_id"]) == target
                and str(_value(row, "status", "")).casefold() == "removed"
            ]
            if len(target_removed) > 1:
                raise KnowledgeBaseRegistryUnavailable("target Workspace 存在重复 removed binding")
            if target_primary:
                raise KnowledgeBaseRegistryConflict(
                    "target Workspace 已存在 active primary binding"
                )
            current = _now(now)
            approval = _active_transfer_policy(session, tenant, dataset_id)
            if approval is not None:
                if approval_execution_fact is None:
                    raise KnowledgeBaseRegistryApprovalRequired(approval)
                _validate_approval_fact(
                    approval_execution_fact,
                    tenant_id=tenant,
                    dataset_id=dataset_id,
                    target_workspace_id=target,
                    expected_dataset_profile_revision=expected_profile,
                    expected_ownership_revision=expected_owner,
                    source_workspace_id=source_workspace_id,
                )
            ownership_before = _ownership_projection(ownership_before_row)
            before_bindings = [_binding_projection(row) for row in binding_rows]
            bindings = _model_table(session, "tenant_workspace_datasets", "TenantWorkspaceDataset")
            # Release the old primary slot before promoting or creating the target
            # primary row; the database uniqueness fence must hold at every flush.
            session.execute(
                update(bindings)
                .where(bindings.c.id == source_binding["id"])
                .values(
                    binding_kind="shared",
                    active_primary_slot=None,
                    revision=bindings.c.revision + 1,
                    updated_at=current,
                    updated_by=actor,
                )
            )
            if target_shared:
                target_binding = target_shared[0]
                session.execute(
                    update(bindings)
                    .where(bindings.c.id == target_binding["id"])
                    .values(
                        binding_kind="primary",
                        active_primary_slot="primary",
                        revision=bindings.c.revision + 1,
                        updated_at=current,
                        updated_by=actor,
                    )
                )
            elif target_removed:
                target_binding = target_removed[0]
                session.execute(
                    update(bindings)
                    .where(bindings.c.id == target_binding["id"])
                    .values(
                        binding_kind="primary",
                        active_primary_slot="primary",
                        status="active",
                        revision=bindings.c.revision + 1,
                        updated_at=current,
                        updated_by=actor,
                        removed_at=None,
                        removed_by=None,
                    )
                )
            else:
                available_binding_columns = {str(column.name) for column in bindings.columns}
                values = {
                    "tenant_id": tenant,
                    "workspace_id": target,
                    "dataset_id": dataset_id,
                    "binding_kind": "primary",
                    "active_primary_slot": "primary",
                    "status": "active",
                    "revision": 1,
                    "created_at": current,
                    "created_by": actor,
                    "updated_at": current,
                    "updated_by": actor,
                    "removed_at": None,
                    "removed_by": None,
                }
                session.execute(
                    bindings.insert().values(
                        {
                            key: value
                            for key, value in values.items()
                            if key in available_binding_columns
                        }
                    )
                )
            session.execute(
                update(ownerships)
                .where(
                    ownerships.c.tenant_id == tenant,
                    ownerships.c.dataset_id == dataset_id,
                    ownerships.c.revision == expected_owner,
                )
                .values(
                    workspace_id=target,
                    revision=ownerships.c.revision + 1,
                    updated_at=current,
                    updated_by=actor,
                    last_transfer_at=current,
                )
            )
            datasets = _model_table(session, "datasets", "Dataset")
            session.execute(
                update(datasets)
                .where(
                    datasets.c.tenant_id == tenant,
                    datasets.c.id == dataset_id,
                    datasets.c.profile_revision == expected_profile,
                )
                .values(profile_revision=datasets.c.profile_revision + 1, updated_at=current)
            )
            updated_dataset = dict(_dataset(session, tenant, dataset_id))
            updated_ownership_rows = list(
                session.execute(
                    select(ownerships).where(
                        ownerships.c.tenant_id == tenant,
                        ownerships.c.dataset_id == dataset_id,
                    )
                ).mappings()
            )
            if len(updated_ownership_rows) != 1:
                raise KnowledgeBaseRegistryUnavailable("更新后的 ownership 证据缺失")
            updated_bindings = _binding_rows(session, tenant_id=tenant, dataset_id=dataset_id)
            ownership_after = _ownership_projection(updated_ownership_rows[0])
            after_bindings = [_binding_projection(row) for row in updated_bindings]
            _audit(
                session,
                actor=current_actor,
                action="knowledge_base.ownership.transfer",
                resource_id=dataset_id,
                before={
                    "dataset": dataset_row,
                    "ownership": ownership_before,
                    "bindings": before_bindings,
                    "reason": clean_reason,
                },
                after={
                    "dataset": updated_dataset,
                    "ownership": ownership_after,
                    "bindings": after_bindings,
                    "reason": clean_reason,
                },
                request_id=clean_request,
                request_ip=clean_ip,
                occurred_at=current,
            )
            return _complete(
                session,
                reservation,
                payload={
                    "state": "applied",
                    "message": "知识库所有权已转移",
                    "dataset": {
                        "id": str(updated_dataset["id"]),
                        "tenant_id": str(updated_dataset["tenant_id"]),
                        "profile_revision": int(updated_dataset["profile_revision"]),
                    },
                    "ownership": ownership_after,
                    "source_workspace": {
                        "id": str(source_workspace["id"]),
                        "tenant_id": tenant,
                        "name": str(source_workspace.get("name", "")),
                        "status": str(source_workspace["status"]),
                        "revision": int(source_workspace["revision"]),
                    },
                    "target_workspace": {
                        "id": str(target_workspace["id"]),
                        "tenant_id": tenant,
                        "name": str(target_workspace.get("name", "")),
                        "status": str(target_workspace["status"]),
                        "revision": int(target_workspace["revision"]),
                    },
                    "bindings": after_bindings,
                    "mutation": {"result": "transferred", "resource_id": dataset_id},
                },
                status=200,
                resource_id=dataset_id,
            )
    except KnowledgeBaseRegistryError:
        raise
    except IntegrityError as exc:
        raise KnowledgeBaseRegistryConflict(
            "ownership transfer violates an authority constraint"
        ) from exc
    except SQLAlchemyError as exc:
        raise KnowledgeBaseRegistryUnavailable() from exc


def assert_dataset_archive_allowed(session: Session, tenant_id: str, dataset_id: str) -> None:
    """Raise before Dataset archive when active App references are provable.

    A partial 0028 deployment cannot prove that no active reference exists.  The
    pre-0028 catalog remains compatible only when neither Stage 18 table is
    present, while any partial/head Stage 18 evidence is fail-closed.
    """

    try:
        connection = session.connection()
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        stage18_tables = {"dataset_workspace_ownerships", "app_dataset_references"} & tables
        if not stage18_tables and "alembic_version" not in tables:
            return
        revision = None
        if "alembic_version" in tables:
            revisions = tuple(
                str(value)
                for value in connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalars()
            )
            if len(revisions) != 1:
                raise KnowledgeBaseRegistryUnavailable("Catalog revision 证据不完整")
            revision = revisions[0]
        if not stage18_tables and revision != REVISION:
            return
        _lock_tenant(session, tenant_id)
        _ensure_capability(connection)
        references = _reference_table(session)
        rows = list(
            session.execute(
                select(references.c.id)
                .where(
                    references.c.tenant_id == tenant_id,
                    references.c.dataset_id == dataset_id,
                    references.c.status == ACTIVE_REFERENCE_STATUS,
                    references.c.active_slot == ACTIVE_REFERENCE_SLOT,
                )
                .order_by(references.c.id.asc())
            ).scalars()
        )
    except KnowledgeBaseRegistryError:
        raise
    except SQLAlchemyError as exc:
        raise KnowledgeBaseRegistryUnavailable("无法验证 Dataset archive 依赖") from exc
    if rows:
        raise DatasetArchiveBlocked(
            "dataset archive is blocked by active application references: "
            + ", ".join(str(value) for value in rows)
        )


__all__ = [
    "ACTIVE_REFERENCE_SLOT",
    "ACTIVE_REFERENCE_STATUS",
    "DatasetArchiveBlocked",
    "DatasetArchiveBlocker",
    "KnowledgeBaseRegistryApprovalRequired",
    "KnowledgeBaseRegistryConflict",
    "KnowledgeBaseRegistryError",
    "KnowledgeBaseRegistryForbidden",
    "KnowledgeBaseRegistryIdempotencyConflict",
    "KnowledgeBaseRegistryIdempotencyInProgress",
    "KnowledgeBaseRegistryNotFound",
    "KnowledgeBaseRegistryUnavailable",
    "KnowledgeBaseRegistryValidation",
    "REGISTRY_REVISION",
    "REFERENCE_KIND_KNOWLEDGE",
    "REVISION",
    "RESOURCE_KNOWLEDGE_BASE",
    "ServiceResult",
    "assert_dataset_archive_allowed",
    "create_app_reference",
    "get_knowledge_base",
    "get_knowledge_base_dependencies",
    "inspect_registry_capability",
    "list_app_references",
    "list_knowledge_bases",
    "remove_app_reference",
    "transfer_dataset_ownership",
]
