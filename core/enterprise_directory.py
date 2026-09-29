"""Enterprise directory projections and tenant membership operations.

This module deliberately exposes only facts already represented by Tenant, Account,
TenantMember, and Dataset.  Missing enterprise capabilities remain explicit instead
of being synthesized from demo data.  Membership mutations use SQLAlchemy Core and
the 0016 schema contract so this module remains usable while the legacy ORM mapping
is still deployed without the new membership columns.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import JSON, MetaData, Table, func, inspect, insert, or_, select, update
from sqlalchemy.orm import Session

from core.enterprise_access_control import evaluate_dataset_permissions
from core.enterprise_approval_control import (
    resolve_active_approval_policy_in_session,
    tenant_authority_transaction,
)
from core.knowledge_governance import sanitize_audit_snapshot
from core.knowledge_permissions import permissions_for_role
from models.orm import Account, Dataset, Tenant, TenantMember

TenantRole = Literal["owner", "admin", "editor", "member"]

_ROLE_VALUES = frozenset({"owner", "admin", "editor", "member"})
_CAPABILITY_DEFINITIONS = {
    "member_directory": ("成员目录", "ready", None),
    "fixed_role_permissions": ("固定角色权限", "ready", None),
    "knowledge_bases": ("知识库目录", "ready", None),
    "knowledge_audit": ("知识库审计", "ready", None),
    "organization_units": ("组织架构", "unavailable", "尚未接入企业组织目录"),
    "user_groups": ("用户组", "unavailable", "尚未接入企业用户组"),
    "dataset_acl": ("知识库 ACL", "unavailable", "尚未接入知识库级 ACL"),
    "member_mutations": ("成员变更", "unavailable", "尚未接入成员邀请、角色变更与移除"),
    "sso": ("企业 SSO", "unavailable", "尚未接入企业 SSO"),
    "invitations": ("成员邀请", "unavailable", "尚未接入成员邀请流程"),
    "tenant_audit": ("企业管理日志", "unavailable", "尚未接入企业级管理审计"),
    "enterprise_notification_center": ("企业通知中心", "unavailable", "通知权威尚未通过数据库验证"),
    "enterprise_content_recovery": (
        "企业内容恢复",
        "unavailable",
        "内容恢复权威尚未通过数据库验证",
    ),
    "enterprise_task_operations": ("企业任务运营", "unavailable", "任务运营权威尚未通过数据库验证"),
    "enterprise_automation_workflows": (
        "企业自动化",
        "unavailable",
        "自动化权威尚未通过数据库验证",
    ),
    "enterprise_knowledge_serving_reliability": (
        "知识服务可靠性",
        "unavailable",
        "知识服务可靠性权威尚未通过数据库验证",
    ),
    "enterprise_knowledge_operations_feedback": (
        "企业知识运营与反馈",
        "unavailable",
        "企业知识运营与反馈权威尚未通过数据库验证",
    ),
}


class EnterpriseDirectoryInconsistent(RuntimeError):
    """Raised when authenticated catalog facts cannot be read consistently."""


class EnterpriseCapabilityUnavailable(RuntimeError):
    """Raised when the deployed schema cannot support one enterprise projection."""


class EnterpriseMembershipMigrationRequired(EnterpriseCapabilityUnavailable):
    """Raised when the deployed catalog does not contain the 0016 contract."""

    def __init__(self, missing: set[str] | list[str] | tuple[str, ...] | None = None):
        self.missing = tuple(sorted(str(item) for item in (missing or ())))
        super().__init__("enterprise membership schema 0016 is required")


class EnterpriseMemberForbidden(RuntimeError):
    """Raised when a tenant member operation is outside the actor's scope."""


class EnterpriseMemberConflict(RuntimeError):
    """Raised when a member mutation cannot be applied to the current state."""


class EnterpriseMemberApprovalRequired(EnterpriseMemberConflict):
    """Raised when an active approval policy gates a direct member role change."""

    def __init__(self, policy: dict[str, Any]) -> None:
        self.policy = dict(policy)
        super().__init__("member role change requires approval")


class EnterpriseMemberRevisionConflict(EnterpriseMemberConflict):
    """Raised when optimistic revision fencing rejects a stale request."""

    def __init__(self, *, expected_revision: int, current_revision: int):
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__("tenant member revision is stale")


class EnterpriseMemberStateConflict(EnterpriseMemberConflict):
    """Raised when a requested state transition is not valid."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class EnterpriseMemberValidationError(ValueError):
    """Raised when a direct service caller provides an invalid mutation value."""


_MUTABLE_MEMBER_COLUMNS = frozenset(
    {
        "status",
        "revision",
        "updated_at",
        "updated_by",
        "suspended_at",
        "suspended_by",
    }
)
_AUDIT_COLUMNS = frozenset(
    {
        "sequence",
        "id",
        "tenant_id",
        "actor_id",
        "actor_name_snapshot",
        "actor_email_snapshot",
        "action",
        "resource_type",
        "resource_id",
        "target_account_id",
        "before_snapshot",
        "after_snapshot",
        "request_id",
        "request_ip",
        "occurred_at",
    }
)
_BASE_TABLE_COLUMNS = {
    "tenants": frozenset({"id", "status"}),
    "accounts": frozenset({"id", "name", "email"}),
}


def _reflect_membership_tables(engine: Any) -> dict[str, Table]:
    """Reflect the 0016 tables without invoking ORM DDL or schema creation."""

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    missing: set[str] = set()

    if "tenant_members" not in table_names:
        missing.add("tenant_members")
    else:
        member_columns = {
            str(column.get("name")) for column in inspector.get_columns("tenant_members")
        }
        missing.update(
            f"tenant_members.{name}" for name in _MUTABLE_MEMBER_COLUMNS - member_columns
        )

    if "tenant_audit_events" not in table_names:
        missing.add("tenant_audit_events")
    else:
        audit_columns = {
            str(column.get("name")) for column in inspector.get_columns("tenant_audit_events")
        }
        missing.update(f"tenant_audit_events.{name}" for name in _AUDIT_COLUMNS - audit_columns)

    if missing:
        raise EnterpriseMembershipMigrationRequired(missing)

    base_missing: set[str] = set()
    for table_name, required_columns in _BASE_TABLE_COLUMNS.items():
        if table_name not in table_names:
            base_missing.add(table_name)
            continue
        columns = {str(column.get("name")) for column in inspector.get_columns(table_name)}
        base_missing.update(f"{table_name}.{name}" for name in required_columns - columns)
    if base_missing:
        raise EnterpriseCapabilityUnavailable("enterprise catalog base tables are incomplete")

    metadata = MetaData()
    return {
        table_name: Table(table_name, metadata, autoload_with=engine)
        for table_name in (
            "tenant_members",
            "tenant_audit_events",
            "tenants",
            "accounts",
        )
    }


def _clean_mutation_text(value: Any, *, field: str, maximum: int) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise EnterpriseMemberValidationError(f"{field} must not be empty")
    if len(normalized) > maximum:
        raise EnterpriseMemberValidationError(f"{field} must be at most {maximum} characters")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in normalized):
        raise EnterpriseMemberValidationError(f"{field} contains a control character")
    return normalized


def _validate_expected_revision(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise EnterpriseMemberValidationError("expected_revision must be a positive integer")
    return value


def _requested_role(value: Any) -> str:
    normalized = str(value or "").strip().casefold()
    if normalized not in _ROLE_VALUES:
        raise EnterpriseMemberValidationError("role is not a supported tenant role")
    return normalized


def _member_status(value: Any) -> str:
    normalized = str(value or "").strip().casefold()
    if normalized not in {"active", "suspended"}:
        raise EnterpriseDirectoryInconsistent("tenant member has an unsupported status")
    return normalized


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_for_column(column: Any, value: Any) -> Any:
    sanitized = sanitize_audit_snapshot(value)
    if value is None or isinstance(column.type, JSON):
        return sanitized
    return json.dumps(sanitized, ensure_ascii=False, separators=(",", ":"), default=str)


def _snapshot_from_column(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    return sanitize_audit_snapshot(value)


def _member_snapshot(member: dict[str, Any], *, reason: str | None = None) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "account_id": member.get("account_id"),
        "role": _normalized_role(member.get("role")),
        "status": _member_status(member.get("status")),
        "revision": int(member.get("revision")),
        "updated_by": member.get("updated_by"),
        "suspended_by": member.get("suspended_by"),
    }
    if member.get("suspended_at") is not None:
        suspended_at = member["suspended_at"]
        snapshot["suspended_at"] = (
            suspended_at.isoformat() if isinstance(suspended_at, datetime) else str(suspended_at)
        )
    if reason is not None:
        snapshot["reason"] = reason
    return snapshot


def _member_payload(member: dict[str, Any], account: dict[str, Any]) -> dict[str, Any]:
    return {
        "membership_id": member["id"],
        "account_id": member["account_id"],
        "name": account.get("name") or "",
        "email": account.get("email") or "",
        "tenant_id": member["tenant_id"],
        "role": _normalized_role(member["role"]),
        "status": _member_status(member["status"]),
        "revision": int(member["revision"]),
        "joined_at": member.get("created_at"),
        "updated_at": member.get("updated_at"),
        "updated_by": member.get("updated_by"),
        "suspended_at": member.get("suspended_at"),
        "suspended_by": member.get("suspended_by"),
    }


def _tenant_member_mutation(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    actor_role: str,
    target_account_id: str,
    expected_revision: int,
    operation: Literal["role", "suspend", "resume"],
    requested_role: str | None,
    reason: str,
    request_id: str,
    request_ip: str,
    approval_execution_id: str | None = None,
) -> dict[str, Any]:
    tables = _reflect_membership_tables(engine)
    members = tables["tenant_members"]
    audits = tables["tenant_audit_events"]
    tenants = tables["tenants"]
    accounts = tables["accounts"]

    tenant_id = _clean_mutation_text(tenant_id, field="tenant_id", maximum=64)
    actor_id = _clean_mutation_text(actor_id, field="actor_id", maximum=64)
    target_account_id = _clean_mutation_text(
        target_account_id, field="target_account_id", maximum=64
    )
    request_id = _clean_mutation_text(request_id, field="request_id", maximum=128)
    request_ip = str(request_ip or "").strip()[:64]
    reason = _clean_mutation_text(reason, field="reason", maximum=512)
    expected_revision = _validate_expected_revision(expected_revision)
    approval_execution = str(approval_execution_id or "").strip()
    if approval_execution_id is not None and (
        not approval_execution or len(approval_execution) > 128
    ):
        raise EnterpriseMemberValidationError("approval execution id is invalid")

    with Session(engine, expire_on_commit=False) as session:
        with tenant_authority_transaction(session):
            # Lock the tenant before the member row.  This serializes owner-count
            # decisions for all membership mutations within one tenant.
            tenant = (
                session.execute(select(tenants).where(tenants.c.id == tenant_id).with_for_update())
                .mappings()
                .one_or_none()
            )
            if tenant is None or str(tenant.get("status", "")).strip().casefold() != "active":
                raise EnterpriseMemberForbidden("tenant is outside the active actor scope")

            actor_member = (
                session.execute(
                    select(members)
                    .where(
                        members.c.tenant_id == tenant_id,
                        members.c.account_id == actor_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if actor_member is None or _member_status(actor_member.get("status")) != "active":
                raise EnterpriseMemberForbidden("actor is not an active tenant administrator")
            actual_actor_role = _normalized_role(actor_member.get("role"))
            if actual_actor_role not in {"owner", "admin"}:
                raise EnterpriseMemberForbidden("tenant role cannot manage members")
            # The dependency uses knowledge.manage for compatibility; the service
            # still derives the authoritative role from the locked membership row.
            _ = _normalized_role(actor_role)

            actor_account = (
                session.execute(
                    select(accounts.c.id, accounts.c.name, accounts.c.email).where(
                        accounts.c.id == actor_id
                    )
                )
                .mappings()
                .one_or_none()
            )
            if actor_account is None:
                raise EnterpriseMemberForbidden("actor account is outside tenant scope")

            target = (
                session.execute(
                    select(members)
                    .where(
                        members.c.tenant_id == tenant_id,
                        members.c.account_id == target_account_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if target is None:
                raise EnterpriseMemberForbidden("target member is outside tenant scope")
            target = dict(target)
            target_account = (
                session.execute(
                    select(accounts.c.id, accounts.c.name, accounts.c.email).where(
                        accounts.c.id == target_account_id
                    )
                )
                .mappings()
                .one_or_none()
            )
            if target_account is None:
                raise EnterpriseMemberForbidden("target account is outside tenant scope")
            target_account = dict(target_account)

            current_role = _normalized_role(target["role"])
            current_status = _member_status(target["status"])
            current_revision = int(target["revision"])
            if actual_actor_role == "admin" and current_role == "owner":
                raise EnterpriseMemberForbidden("tenant admin cannot manage an owner")
            if current_revision != expected_revision:
                raise EnterpriseMemberRevisionConflict(
                    expected_revision=expected_revision,
                    current_revision=current_revision,
                )

            now = _utc_now()
            if operation == "role":
                new_role = _requested_role(requested_role)
                if not approval_execution:
                    policy = resolve_active_approval_policy_in_session(
                        session,
                        tenant_id=tenant_id,
                        action_type="member_role_change",
                        resource_type="tenant_member",
                        resource_id=target_account_id,
                        lock_for_update=True,
                    )
                    if policy is not None:
                        raise EnterpriseMemberApprovalRequired(policy)
                if current_status == "active" and current_role == "owner" and new_role != "owner":
                    active_owners = session.execute(
                        select(func.count())
                        .select_from(members)
                        .where(
                            members.c.tenant_id == tenant_id,
                            func.lower(func.trim(members.c.status)) == "active",
                            func.lower(func.trim(members.c.role)) == "owner",
                        )
                    ).scalar_one()
                    if int(active_owners) <= 1:
                        raise EnterpriseMemberStateConflict(
                            "last_active_owner_protected",
                            "最后一个 active owner 不可降级",
                        )
                values: dict[str, Any] = {
                    "role": new_role,
                    "revision": current_revision + 1,
                    "updated_at": now,
                    "updated_by": actor_id,
                }
                action = "member.role.update"
            elif operation == "suspend":
                if current_status != "active":
                    raise EnterpriseMemberStateConflict(
                        "tenant_member_state_conflict",
                        "只有 active 成员可以暂停",
                    )
                if current_role == "owner":
                    active_owners = session.execute(
                        select(func.count())
                        .select_from(members)
                        .where(
                            members.c.tenant_id == tenant_id,
                            func.lower(func.trim(members.c.status)) == "active",
                            func.lower(func.trim(members.c.role)) == "owner",
                        )
                    ).scalar_one()
                    if int(active_owners) <= 1:
                        raise EnterpriseMemberStateConflict(
                            "last_active_owner_protected",
                            "最后一个 active owner 不可暂停",
                        )
                values = {
                    "status": "suspended",
                    "revision": current_revision + 1,
                    "updated_at": now,
                    "updated_by": actor_id,
                    "suspended_at": now,
                    "suspended_by": actor_id,
                }
                action = "member.suspend"
            else:
                if current_status != "suspended":
                    raise EnterpriseMemberStateConflict(
                        "tenant_member_state_conflict",
                        "只有 suspended 成员可以恢复",
                    )
                values = {
                    "status": "active",
                    "revision": current_revision + 1,
                    "updated_at": now,
                    "updated_by": actor_id,
                    "suspended_at": None,
                    "suspended_by": None,
                }
                action = "member.resume"

            result = session.execute(
                update(members)
                .where(
                    members.c.id == target["id"],
                    members.c.tenant_id == tenant_id,
                    members.c.revision == expected_revision,
                )
                .values(**values)
            )
            if result.rowcount != 1:
                current = session.execute(
                    select(members.c.revision).where(
                        members.c.id == target["id"],
                        members.c.tenant_id == tenant_id,
                    )
                ).scalar_one_or_none()
                raise EnterpriseMemberRevisionConflict(
                    expected_revision=expected_revision,
                    current_revision=int(current or expected_revision),
                )

            after = dict(target)
            after.update(values)
            event_id = f"tenant-audit-{uuid.uuid4().hex}"
            session.execute(
                insert(audits).values(
                    id=event_id,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    actor_name_snapshot=str(actor_account.get("name") or "")[:128],
                    actor_email_snapshot=str(actor_account.get("email") or "")[:256],
                    action=action,
                    resource_type="tenant_member",
                    resource_id=str(target["id"]),
                    target_account_id=target_account_id,
                    before_snapshot=_json_for_column(
                        audits.c.before_snapshot, _member_snapshot(target)
                    ),
                    after_snapshot=_json_for_column(
                        audits.c.after_snapshot, _member_snapshot(after, reason=reason)
                    ),
                    request_id=request_id,
                    request_ip=request_ip,
                    occurred_at=now,
                )
            )
            session.flush()
            audit = (
                session.execute(
                    select(audits.c.id, audits.c.sequence).where(audits.c.id == event_id)
                )
                .mappings()
                .one()
            )

            return {
                "membership": _member_payload(after, target_account),
                "audit": {"id": audit["id"], "sequence": audit["sequence"]},
                "authorization": {
                    "mode": "tenant_role_admin",
                    "permission": "knowledge.manage",
                    "actor_role": actual_actor_role,
                },
            }


def change_tenant_member_role(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    actor_role: str,
    target_account_id: str,
    expected_revision: int,
    role: str,
    reason: str,
    request_id: str,
    request_ip: str = "",
    approval_execution_id: str | None = None,
) -> dict[str, Any]:
    """Change one tenant member role and append the audit event atomically."""

    return _tenant_member_mutation(
        engine,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_role=actor_role,
        target_account_id=target_account_id,
        expected_revision=expected_revision,
        operation="role",
        requested_role=role,
        reason=reason,
        request_id=request_id,
        request_ip=request_ip,
        approval_execution_id=approval_execution_id,
    )


def suspend_tenant_member(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    actor_role: str,
    target_account_id: str,
    expected_revision: int,
    reason: str,
    request_id: str,
    request_ip: str = "",
) -> dict[str, Any]:
    """Suspend one active tenant member and append the audit atomically."""

    return _tenant_member_mutation(
        engine,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_role=actor_role,
        target_account_id=target_account_id,
        expected_revision=expected_revision,
        operation="suspend",
        requested_role=None,
        reason=reason,
        request_id=request_id,
        request_ip=request_ip,
    )


def resume_tenant_member(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    actor_role: str,
    target_account_id: str,
    expected_revision: int,
    reason: str,
    request_id: str,
    request_ip: str = "",
) -> dict[str, Any]:
    """Resume one suspended tenant member and append the audit atomically."""

    return _tenant_member_mutation(
        engine,
        tenant_id=tenant_id,
        actor_id=actor_id,
        actor_role=actor_role,
        target_account_id=target_account_id,
        expected_revision=expected_revision,
        operation="resume",
        requested_role=None,
        reason=reason,
        request_id=request_id,
        request_ip=request_ip,
    )


def list_tenant_audit_events(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    target_account_id: str | None = None,
    request_id: str | None = None,
    before_sequence: int | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List tenant audit events with descending sequence keyset pagination."""

    if type(limit) is not int or not 1 <= limit <= 200:
        raise EnterpriseMemberValidationError("limit must be between 1 and 200")
    if before_sequence is not None and (type(before_sequence) is not int or before_sequence < 1):
        raise EnterpriseMemberValidationError("before_sequence must be positive")

    tables = _reflect_membership_tables(engine)
    audits = tables["tenant_audit_events"]
    # 过滤条件逐列显式收集（列身份静态可见，值仍由 SQLAlchemy 绑定参数化），
    # 查询链整体内联进 execute 调用，不经过预构建的语句变量。
    conditions: list[Any] = [audits.c.tenant_id == tenant_id]
    if actor_id is not None and str(actor_id).strip():
        conditions.append(audits.c.actor_id == str(actor_id).strip())
    if action is not None and str(action).strip():
        conditions.append(audits.c.action == str(action).strip())
    if resource_type is not None and str(resource_type).strip():
        conditions.append(audits.c.resource_type == str(resource_type).strip())
    if target_account_id is not None and str(target_account_id).strip():
        conditions.append(audits.c.target_account_id == str(target_account_id).strip())
    if request_id is not None and str(request_id).strip():
        conditions.append(audits.c.request_id == str(request_id).strip())
    if before_sequence is not None:
        conditions.append(audits.c.sequence < before_sequence)

    with Session(engine) as session:
        rows = [
            dict(row)
            for row in session.execute(
                select(audits)
                .where(*conditions)
                .order_by(audits.c.sequence.desc())
                .limit(limit + 1)
            ).mappings().all()
        ]
    has_more = len(rows) > limit
    page = rows[:limit]
    items = []
    for row in page:
        items.append(
            {
                "sequence": row.get("sequence"),
                "id": row.get("id"),
                "tenant_id": row.get("tenant_id"),
                "actor_id": row.get("actor_id"),
                "actor_name_snapshot": row.get("actor_name_snapshot"),
                "actor_email_snapshot": row.get("actor_email_snapshot"),
                "action": row.get("action"),
                "resource_type": row.get("resource_type"),
                "resource_id": row.get("resource_id"),
                "target_account_id": row.get("target_account_id"),
                "before_snapshot": _snapshot_from_column(row.get("before_snapshot")),
                "after_snapshot": _snapshot_from_column(row.get("after_snapshot")),
                "request_id": row.get("request_id"),
                "request_ip": row.get("request_ip"),
                "occurred_at": row.get("occurred_at"),
            }
        )
    return {
        "items": items,
        "count": len(items),
        "next_before_sequence": page[-1]["sequence"] if has_more and page else None,
    }


def _normalized_role(role: Any) -> str:
    value = str(role or "").strip().casefold()
    if value not in _ROLE_VALUES:
        raise EnterpriseDirectoryInconsistent("tenant member has an unsupported role")
    return value


def _effective_permissions(role: Any) -> list[str]:
    return sorted(permissions_for_role(_normalized_role(role)))


def enterprise_capabilities(engine: Any) -> dict[str, dict[str, str | None]]:
    """Return a fresh, honest capability map for the current schema."""

    capabilities = {
        key: {"label": label, "state": state, "reason": reason}
        for key, (label, state, reason) in _CAPABILITY_DEFINITIONS.items()
    }
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "datasets" not in tables:
        capabilities["knowledge_bases"] = {
            "label": "知识库目录",
            "state": "unavailable",
            "reason": "当前数据库尚未包含知识库目录表",
        }
    else:
        dataset_columns = {str(column.get("name")) for column in inspector.get_columns("datasets")}
        required_profile_columns = {"owner_id", "visibility", "profile_revision", "status"}
        missing_profile_columns = required_profile_columns - dataset_columns
        if missing_profile_columns:
            capabilities["knowledge_bases"] = {
                "label": "知识库目录",
                "state": "limited",
                "reason": "基础目录可读；访问策略摘要需要数据库升级",
            }
    if "knowledge_audit_events" not in tables:
        capabilities["knowledge_audit"] = {
            "label": "知识库审计",
            "state": "unavailable",
            "reason": "当前数据库迁移尚未包含知识库审计表",
        }
    membership_columns = (
        {str(column.get("name")) for column in inspector.get_columns("tenant_members")}
        if "tenant_members" in tables
        else set()
    )
    required_membership_columns = {
        "status",
        "revision",
        "updated_at",
        "updated_by",
        "suspended_at",
        "suspended_by",
    }
    if required_membership_columns <= membership_columns and "tenant_audit_events" in tables:
        capabilities["member_mutations"] = {
            "label": "成员变更",
            "state": "ready",
            "reason": None,
        }
        capabilities["tenant_audit"] = {
            "label": "企业管理日志",
            "state": "ready",
            "reason": None,
        }
    elif (required_membership_columns & membership_columns) or "tenant_audit_events" in tables:
        reason = "0016 企业成员数据库升级不完整，仅保留不可用状态"
        capabilities["member_mutations"] = {
            "label": "成员变更",
            "state": "unavailable",
            "reason": reason,
        }
        capabilities["tenant_audit"] = {
            "label": "企业管理日志",
            "state": "unavailable",
            "reason": reason,
        }

    access_graph_tables = {
        "tenant_organization_units",
        "tenant_groups",
        "tenant_group_members",
        "dataset_access_grants",
        "tenant_invitations",
    }
    present_access_graph_tables = access_graph_tables & tables
    if access_graph_tables <= tables:
        capabilities["organization_units"] = {
            "label": "组织架构",
            "state": "limited",
            "reason": "企业组织目录已支持权威只读，组织变更尚未开放",
        }
        capabilities["user_groups"] = {
            "label": "用户组",
            "state": "limited",
            "reason": "企业用户组已支持权威只读，组成员变更尚未开放",
        }
        capabilities["dataset_acl"] = {
            "label": "知识库 ACL",
            "state": "limited",
            "reason": "知识库授权关系已支持权威只读，授权变更尚未开放",
        }
        capabilities["invitations"] = {
            "label": "成员邀请",
            "state": "limited",
            "reason": "成员邀请目录已支持权威只读，邀请签发尚未开放",
        }
    elif present_access_graph_tables:
        reason = "0017 企业访问图谱数据库升级不完整，仅保留不可用状态"
        for key in ("organization_units", "user_groups", "dataset_acl", "invitations"):
            capabilities[key] = {
                "label": capabilities[key]["label"],
                "state": "unavailable",
                "reason": reason,
            }

    from core.catalog_schema import (
        inspect_enterprise_automation_workflows_capability,
        inspect_enterprise_content_recovery_capability,
        inspect_enterprise_knowledge_operations_feedback_capability,
        inspect_enterprise_knowledge_serving_reliability_capability,
        inspect_enterprise_notification_center_capability,
        inspect_enterprise_task_operations_capability,
    )

    schema_capabilities = (
        (
            "enterprise_notification_center",
            inspect_enterprise_notification_center_capability,
            "0032 企业通知中心数据库升级尚未就绪",
        ),
        (
            "enterprise_content_recovery",
            inspect_enterprise_content_recovery_capability,
            "0033 企业内容恢复数据库升级尚未就绪",
        ),
        (
            "enterprise_task_operations",
            inspect_enterprise_task_operations_capability,
            "0034 企业任务运营数据库升级尚未就绪",
        ),
        (
            "enterprise_automation_workflows",
            inspect_enterprise_automation_workflows_capability,
            "0035 企业自动化数据库升级尚未就绪",
        ),
        (
            "enterprise_knowledge_serving_reliability",
            inspect_enterprise_knowledge_serving_reliability_capability,
            "0036 知识服务可靠性数据库升级尚未就绪",
        ),
        (
            "enterprise_knowledge_operations_feedback",
            inspect_enterprise_knowledge_operations_feedback_capability,
            "0037 企业知识运营与反馈数据库升级尚未就绪",
        ),
    )
    for key, checker, unavailable_reason in schema_capabilities:
        state, issues = checker(engine)
        if state == "ready":
            capabilities[key] = {
                "label": capabilities[key]["label"],
                "state": "ready",
                "reason": None,
            }
        else:
            capabilities[key] = {
                "label": capabilities[key]["label"],
                "state": "unavailable",
                "reason": unavailable_reason
                if not issues
                else f"{unavailable_reason}：{issues[0]}",
            }
    return capabilities


def role_permission_matrix() -> dict[str, list[str]]:
    """Return the server-owned fixed role policy for enterprise clients."""

    return {
        role: sorted(permissions_for_role(role)) for role in ("owner", "admin", "editor", "member")
    }


def get_enterprise_context(
    engine: Any,
    *,
    tenant_id: str,
    account_id: str,
    actor_role: str,
) -> dict[str, Any]:
    """Project the authenticated tenant and actor with real catalog counts."""

    normalized_role = _normalized_role(actor_role)
    with Session(engine) as session:
        identity = session.execute(
            select(Tenant, Account, TenantMember.role)
            .join(TenantMember, TenantMember.tenant_id == Tenant.id)
            .join(Account, Account.id == TenantMember.account_id)
            .where(
                Tenant.id == tenant_id,
                TenantMember.account_id == account_id,
            )
        ).one_or_none()
        if identity is None:
            raise EnterpriseDirectoryInconsistent(
                "authenticated enterprise identity no longer exists"
            )

        tenant, account, persisted_role = identity
        persisted_role = _normalized_role(persisted_role)
        if persisted_role != normalized_role:
            raise EnterpriseDirectoryInconsistent(
                "authenticated role no longer matches the enterprise directory"
            )

        member_count = session.scalar(
            select(func.count(TenantMember.id)).where(TenantMember.tenant_id == tenant_id)
        )
        dataset_count = session.scalar(
            select(func.count(Dataset.id)).where(Dataset.tenant_id == tenant_id)
        )

        return {
            "tenant": {
                "id": tenant.id,
                "name": tenant.name,
                "plan": tenant.plan,
                "status": tenant.status,
                "quota_documents": tenant.quota_documents,
                "quota_chunks": tenant.quota_chunks,
                "doc_count": tenant.doc_count,
                "chunk_count": tenant.chunk_count,
            },
            "actor": {
                "id": account.id,
                "name": account.name,
                "email": account.email,
                "role": persisted_role,
            },
            "role_permissions": role_permission_matrix(),
            "effective_permissions": _effective_permissions(persisted_role),
            "member_count": int(member_count or 0),
            "dataset_count": int(dataset_count or 0),
            "capabilities": enterprise_capabilities(engine),
        }


def list_enterprise_members(
    engine: Any,
    *,
    tenant_id: str,
    query: str | None = None,
    role: str | None = None,
    before_id: int | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List real tenant members using descending membership-id keyset pagination."""

    if limit < 1 or limit > 200:
        raise ValueError("limit must be between 1 and 200")
    if before_id is not None and before_id < 1:
        raise ValueError("before_id must be positive")

    normalized_filter_role: str | None = None
    if role is not None:
        normalized_filter_role = _normalized_role(role)

    # 过滤条件逐项显式收集（值由 SQLAlchemy 绑定参数化；LIKE 通配由 autoescape
    # 在 SQL 侧包裹与转义，与旧的 _escape_like + f-string 拼 %..% 语义等价），
    # 查询链整体内联进 execute 调用，不经过预构建的语句变量。
    conditions: list[Any] = [TenantMember.tenant_id == tenant_id]
    normalized_query = str(query or "").strip()
    if normalized_query:
        conditions.append(
            or_(
                Account.name.ilike(normalized_query, autoescape="\\"),
                Account.email.ilike(normalized_query, autoescape="\\"),
                Account.id.ilike(normalized_query, autoescape="\\"),
            )
        )
    if normalized_filter_role is not None:
        conditions.append(func.lower(func.trim(TenantMember.role)) == normalized_filter_role)
    if before_id is not None:
        conditions.append(TenantMember.id < before_id)

    with Session(engine) as session:
        rows = session.execute(
            select(
                TenantMember.id,
                TenantMember.account_id,
                Account.name,
                Account.email,
                TenantMember.role,
                TenantMember.created_at,
            )
            .join(Account, Account.id == TenantMember.account_id)
            .where(*conditions)
            .order_by(TenantMember.id.desc())
            .limit(limit + 1)
        ).all()

    has_more = len(rows) > limit
    page = rows[:limit]
    items = []
    for membership_id, account_id, name, email, persisted_role, joined_at in page:
        items.append(
            {
                "membership_id": membership_id,
                "account_id": account_id,
                "name": name,
                "email": email,
                "role": _normalized_role(persisted_role),
                "joined_at": joined_at,
            }
        )
    return {
        "items": items,
        "count": len(items),
        "next_before_id": items[-1]["membership_id"] if has_more and items else None,
    }


def get_dataset_access_summary(
    engine: Any,
    *,
    tenant_id: str,
    dataset_id: str,
    actor_role: str,
    account_id: str | None = None,
) -> dict[str, Any]:
    """Describe current dataset access facts without overstating ACL support."""

    normalized_role = _normalized_role(actor_role)
    inspector = inspect(engine)
    if "datasets" not in set(inspector.get_table_names()):
        raise EnterpriseCapabilityUnavailable("dataset access summary table is unavailable")
    dataset_columns = {str(column.get("name")) for column in inspector.get_columns("datasets")}
    required_columns = {"id", "tenant_id", "owner_id", "visibility", "status", "profile_revision"}
    missing_columns = required_columns - dataset_columns
    if missing_columns:
        raise EnterpriseCapabilityUnavailable(
            "dataset access summary requires a newer catalog schema"
        )
    with Session(engine) as session:
        row = session.execute(
            select(
                Dataset.id,
                Dataset.owner_id,
                Dataset.visibility,
                Dataset.status,
                Dataset.profile_revision,
            ).where(
                Dataset.id == dataset_id,
                Dataset.tenant_id == tenant_id,
            )
        ).one_or_none()
    if row is None:
        raise EnterpriseDirectoryInconsistent(
            "authorized dataset disappeared while reading access summary"
        )

    resolved_id, owner_id, visibility, status, profile_revision = row
    if account_id:
        decision = evaluate_dataset_permissions(
            engine,
            tenant_id,
            account_id,
            normalized_role,
            dataset_id,
        )
        matched_grants = [str(item["id"]) for item in decision.matched_grants]
        effective_permissions = sorted(decision.effective_permissions)
        warnings = list(decision.warnings)
        dataset_acl_supported = decision.dataset_acl_supported
        group_grants_supported = decision.group_grants_supported
        enforcement_mode = decision.enforcement_mode
        dataset_role = decision.dataset_role
        bypass_reason = decision.bypass_reason
        workspace_authorization = decision.workspace_authorization
    else:
        matched_grants = []
        effective_permissions = _effective_permissions(normalized_role)
        warnings = ["未提供账号上下文，访问摘要仅展示租户角色策略"]
        dataset_acl_supported = False
        group_grants_supported = False
        enforcement_mode = "tenant_role_fallback"
        dataset_role = None
        bypass_reason = None
        workspace_authorization = {
            "state": "workspace_authorization_not_available",
            "mode": None,
            "candidate_permissions": [],
            "would_grant_permissions": [],
            "granted_permissions": [],
            "workspace_roles": [],
            "contributing_workspaces": [],
            "policy_revisions": [],
            "warnings": ["未提供账号上下文，未计算 Workspace 授权"],
        }
    if visibility == "private":
        warnings.append("private 可见性由服务端授权策略执行，不应由前端字段自行推断")
    if dataset_acl_supported:
        warnings.append("组织单元授权当前只匹配直接归属，不执行父级继承")
    return {
        "dataset_id": resolved_id,
        "owner_id": owner_id,
        "visibility": visibility,
        "status": status,
        "profile_revision": profile_revision,
        "enforcement_mode": enforcement_mode,
        "actor_role": normalized_role,
        "dataset_role": dataset_role,
        "bypass_reason": bypass_reason,
        "role_permissions": role_permission_matrix(),
        "matched_grants": matched_grants,
        "effective_permissions": effective_permissions,
        "workspace_authorization": workspace_authorization,
        "dataset_acl_supported": dataset_acl_supported,
        "group_grants_supported": group_grants_supported,
        "organization_inheritance_supported": False,
        "warnings": warnings,
    }


__all__ = [
    "EnterpriseCapabilityUnavailable",
    "EnterpriseDirectoryInconsistent",
    "EnterpriseMemberConflict",
    "EnterpriseMemberApprovalRequired",
    "EnterpriseMemberForbidden",
    "EnterpriseMemberRevisionConflict",
    "EnterpriseMemberStateConflict",
    "EnterpriseMemberValidationError",
    "EnterpriseMembershipMigrationRequired",
    "change_tenant_member_role",
    "enterprise_capabilities",
    "get_dataset_access_summary",
    "get_enterprise_context",
    "list_tenant_audit_events",
    "list_enterprise_members",
    "role_permission_matrix",
    "resume_tenant_member",
    "suspend_tenant_member",
]
