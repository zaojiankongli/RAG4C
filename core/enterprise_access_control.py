"""Authoritative dataset-scoped ACL evaluation for KnowledgeOps requests."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, replace
from collections.abc import Iterator, Mapping
from typing import Any, Literal

from sqlalchemy import inspect, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from core.knowledge_permissions import (
    KNOWLEDGE_AUDIT,
    KNOWLEDGE_DELETE,
    KNOWLEDGE_MANAGE,
    KNOWLEDGE_READ,
    KNOWLEDGE_WRITE,
    permissions_for_role,
)
from models.orm import (
    Dataset,
    DatasetAccessGrant,
    TenantGroup,
    TenantGroupMember,
    TenantMember,
    TenantOrganizationUnit,
    TenantOrganizationUnitMember,
)

EnforcementMode = Literal["tenant_role_fallback", "dataset_acl"]

_DATASET_ROLE_PERMISSIONS = {
    "viewer": frozenset({KNOWLEDGE_READ}),
    "editor": frozenset({KNOWLEDGE_READ, KNOWLEDGE_WRITE, KNOWLEDGE_DELETE}),
    "manager": frozenset(
        {
            KNOWLEDGE_READ,
            KNOWLEDGE_WRITE,
            KNOWLEDGE_DELETE,
            KNOWLEDGE_MANAGE,
            KNOWLEDGE_AUDIT,
        }
    ),
}
_ACL_TABLES = frozenset(
    {
        "tenant_organization_units",
        "tenant_groups",
        "tenant_group_members",
        "dataset_access_grants",
    }
)
_ORGANIZATION_MEMBERSHIP_TABLE = "tenant_organization_unit_members"
_CONTROL_COLUMNS = frozenset({"acl_mode", "acl_revision", "acl_enabled_at", "acl_enabled_by"})
_REQUIRED_AUTH_COLUMNS = {
    "dataset_access_grants": {
        "id",
        "tenant_id",
        "dataset_id",
        "subject_type",
        "subject_id",
        "role",
        "status",
        "revision",
    },
    "tenant_groups": {"id", "tenant_id", "status"},
    "tenant_group_members": {"id", "tenant_id", "group_id", "account_id", "status"},
    "tenant_organization_units": {"id", "tenant_id", "status"},
}


class DatasetAccessControlUnavailable(RuntimeError):
    """Raised when ACL state cannot be proven safely."""


@dataclass(frozen=True)
class DatasetAccessDecision(Mapping[str, Any]):
    enforcement_mode: EnforcementMode
    effective_permissions: frozenset[str]
    matched_grants: tuple[dict[str, Any], ...] = ()
    bypass_reason: str | None = None
    dataset_role: str | None = None
    dataset_acl_supported: bool = False
    group_grants_supported: bool = False
    organization_inheritance_supported: bool = False
    warnings: tuple[str, ...] = ()
    workspace_authorization: Mapping[str, Any] | None = None

    def allows(self, permission: str) -> bool:
        return permission in self.effective_permissions

    def as_payload(self) -> dict[str, Any]:
        return {
            "enforcement_mode": self.enforcement_mode,
            "matched_grants": [str(item["id"]) for item in self.matched_grants],
            "effective_permissions": sorted(self.effective_permissions),
            "warnings": list(self.warnings),
            "bypass_reason": self.bypass_reason,
            "dataset_role": self.dataset_role,
            "dataset_acl_supported": self.dataset_acl_supported,
            "group_grants_supported": self.group_grants_supported,
            "organization_inheritance_supported": self.organization_inheritance_supported,
            "workspace_authorization": self.workspace_authorization
            or {
                "state": "workspace_authorization_not_available",
                "mode": None,
                "permission_model_version": None,
                "workspace_ids": [],
                "workspace_roles": [],
                "contributing_workspaces": [],
                "candidate_permissions": [],
                "would_grant_permissions": [],
                "granted_permissions": [],
                "policy_revisions": [],
                "warnings": ["Workspace 授权迁移尚未完成"],
            },
        }

    def __getitem__(self, key: str) -> Any:
        return self.as_payload()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.as_payload())

    def __len__(self) -> int:
        return len(self.as_payload())


def _fallback(
    tenant_role: str,
    *,
    dataset_acl_supported: bool,
    group_grants_supported: bool,
    organization_inheritance_supported: bool,
    warnings: tuple[str, ...],
) -> DatasetAccessDecision:
    normalized_role = str(tenant_role or "").strip().casefold()
    try:
        permissions = permissions_for_role(normalized_role)
    except (KeyError, ValueError) as exc:
        raise DatasetAccessControlUnavailable("tenant role is unknown") from exc
    return DatasetAccessDecision(
        enforcement_mode="tenant_role_fallback",
        effective_permissions=permissions,
        dataset_acl_supported=dataset_acl_supported,
        group_grants_supported=group_grants_supported,
        organization_inheritance_supported=organization_inheritance_supported,
        warnings=warnings,
    )


def _schema_state(engine: Any) -> tuple[set[str], bool]:
    """Validate the 0017 graph without treating a partial install as legacy."""

    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        present = tables & _ACL_TABLES
        if not present:
            return tables, False
        missing_tables = _ACL_TABLES - tables
        if missing_tables:
            raise DatasetAccessControlUnavailable(
                "enterprise access graph schema is partially installed"
            )
        for table_name, required_columns in _REQUIRED_AUTH_COLUMNS.items():
            columns = {str(column.get("name")) for column in inspector.get_columns(table_name)}
            if required_columns - columns:
                raise DatasetAccessControlUnavailable(
                    f"enterprise access graph schema is incomplete for {table_name}"
                )
        return tables, True
    except DatasetAccessControlUnavailable:
        raise
    except Exception as exc:
        raise DatasetAccessControlUnavailable(
            "enterprise access graph schema inspection failed"
        ) from exc


def _control_schema_state(engine: Any) -> bool:
    """Return whether the complete 0019 Dataset control projection is present."""

    try:
        columns = {str(column.get("name")) for column in inspect(engine).get_columns("datasets")}
    except Exception as exc:
        raise DatasetAccessControlUnavailable(
            "dataset ACL control schema inspection failed"
        ) from exc
    present = columns & _CONTROL_COLUMNS
    if present and present != _CONTROL_COLUMNS:
        raise DatasetAccessControlUnavailable("dataset ACL control schema is partially installed")
    return present == _CONTROL_COLUMNS


def _safe_grant(grant: DatasetAccessGrant) -> dict[str, Any]:
    return {
        "id": str(grant.id),
        "subject_type": str(grant.subject_type),
        "subject_id": str(grant.subject_id),
        "role": str(grant.role),
        "revision": int(grant.revision),
    }


def _locked(statement: Any, *, lock_for_update: bool) -> Any:
    return statement.with_for_update() if lock_for_update else statement


def _empty_decision(
    *,
    mode: EnforcementMode,
    supported: bool,
    group_supported: bool,
    organization_supported: bool,
    warnings: tuple[str, ...],
) -> DatasetAccessDecision:
    return DatasetAccessDecision(
        enforcement_mode=mode,
        effective_permissions=frozenset(),
        dataset_acl_supported=supported,
        group_grants_supported=group_supported,
        organization_inheritance_supported=organization_supported,
        warnings=warnings,
    )


def _dataset_role_max(current: str | None, workspace: str | None) -> str | None:
    rank = {None: 0, "viewer": 1, "editor": 2, "manager": 3}
    return workspace if rank.get(workspace, 0) > rank.get(current, 0) else current


def _apply_workspace_authorization(
    engine: Any,
    *,
    tenant_id: str,
    account_id: str,
    dataset_id: str,
    decision: DatasetAccessDecision,
    session: Session,
) -> DatasetAccessDecision:
    try:
        from core.catalog_schema import inspect_workspace_authorization_capability
        from core.catalog_capability import require_safe_capability_state
        from core.enterprise_workspace_authorization import (
            WorkspaceAuthorizationError,
            evaluate_workspace_authorization,
        )

        capability_state, capability_issues = inspect_workspace_authorization_capability(
            session.connection()
        )
        require_safe_capability_state(
            capability_state,
            capability_issues,
            allow_not_available=True,
            error=DatasetAccessControlUnavailable,
        )
        workspace = evaluate_workspace_authorization(
            engine,
            tenant_id=tenant_id,
            account_id=account_id,
            dataset_id=dataset_id,
            existing_permissions=decision.effective_permissions,
            session=session,
        )
    except WorkspaceAuthorizationError as exc:
        raise DatasetAccessControlUnavailable(str(exc)) from exc
    return replace(
        decision,
        effective_permissions=frozenset(
            set(decision.effective_permissions) | set(workspace.enforced_permissions)
        ),
        dataset_role=_dataset_role_max(decision.dataset_role, workspace.highest_dataset_role),
        workspace_authorization=workspace.as_payload(),
        warnings=tuple(decision.warnings) + tuple(workspace.warnings),
    )


def evaluate_dataset_permissions(
    engine: Any,
    tenant_id: str,
    account_id: str,
    tenant_role: str,
    dataset_id: str,
    *,
    session: Session | None = None,
    lock_for_update: bool = False,
) -> DatasetAccessDecision:
    """Evaluate one actor against one dataset using durable ACL mode when present.

    With a complete 0019 control projection, ``Dataset.acl_mode`` is the sole
    mode source of truth.  ``tenant_role`` therefore only applies when the
    persisted mode is ``tenant_role``.  In ``dataset_acl`` mode, zero matching
    active grants is a deny for ordinary members and never falls back.
    """

    schema_bind = session.connection() if session is not None else engine
    tables, graph_ready = _schema_state(schema_bind)
    control_ready = _control_schema_state(schema_bind)
    organization_membership_supported = _ORGANIZATION_MEMBERSHIP_TABLE in tables

    manager = nullcontext(session) if session is not None else Session(engine)
    try:
        with manager as active_session:
            assert active_session is not None
            dataset_columns = [Dataset.id, Dataset.owner_id, Dataset.status]
            if control_ready:
                dataset_columns.extend([Dataset.acl_mode, Dataset.acl_revision])
            dataset_statement = select(*dataset_columns).where(
                Dataset.id == dataset_id,
                Dataset.tenant_id == tenant_id,
            )
            if lock_for_update:
                dataset_statement = dataset_statement.with_for_update()
            dataset = active_session.execute(dataset_statement).one_or_none()
            if dataset is None:
                raise DatasetAccessControlUnavailable(
                    "dataset disappeared while evaluating access control"
                )

            member_columns = {
                str(column.get("name"))
                for column in inspect(schema_bind).get_columns("tenant_members")
            }
            membership_columns = [TenantMember.role]
            if "status" in member_columns:
                membership_columns.append(TenantMember.status)
            membership = active_session.execute(
                select(*membership_columns).where(
                    TenantMember.tenant_id == tenant_id,
                    TenantMember.account_id == account_id,
                )
            ).one_or_none()
            if membership is None:
                mode = (
                    "dataset_acl"
                    if control_ready and str(getattr(dataset, "acl_mode", "")) == "dataset_acl"
                    else "tenant_role_fallback"
                )
                return _empty_decision(
                    mode=mode,  # type: ignore[arg-type]
                    supported=graph_ready,
                    group_supported=graph_ready,
                    organization_supported=organization_membership_supported,
                    warnings=("当前账号不是活跃租户成员",),
                )
            persisted_role = str(membership[0] or "").strip().casefold()
            member_status = (
                str(membership[1] or "").strip().casefold() if len(membership) > 1 else "active"
            )
            if member_status != "active":
                mode = (
                    "dataset_acl"
                    if control_ready and str(getattr(dataset, "acl_mode", "")) == "dataset_acl"
                    else "tenant_role_fallback"
                )
                return _empty_decision(
                    mode=mode,  # type: ignore[arg-type]
                    supported=graph_ready,
                    group_supported=graph_ready,
                    organization_supported=organization_membership_supported,
                    warnings=("当前账号不是活跃租户成员",),
                )
            if persisted_role not in {"owner", "admin", "editor", "member"}:
                raise DatasetAccessControlUnavailable("tenant role is unknown")

            def finalize(decision: DatasetAccessDecision) -> DatasetAccessDecision:
                return _apply_workspace_authorization(
                    engine,
                    tenant_id=tenant_id,
                    account_id=account_id,
                    dataset_id=dataset_id,
                    decision=decision,
                    session=active_session,
                )

            if control_ready:
                mode = str(dataset.acl_mode or "").strip().casefold()
                if mode not in {"tenant_role", "dataset_acl"}:
                    raise DatasetAccessControlUnavailable("dataset ACL mode is unknown")
            else:
                mode = "tenant_role" if not graph_ready else ""

            dataset_owner_id = str(dataset.owner_id or "")
            if mode == "tenant_role":
                return finalize(
                    _fallback(
                        persisted_role,
                        dataset_acl_supported=graph_ready,
                        group_grants_supported=graph_ready,
                        organization_inheritance_supported=organization_membership_supported,
                        warnings=(
                            "当前知识库按持久化租户角色模式执行"
                            if control_ready
                            else "企业访问图谱尚未迁移，当前按租户角色执行",
                        ),
                    )
                )

            if not graph_ready:
                if control_ready and mode == "dataset_acl":
                    raise DatasetAccessControlUnavailable(
                        "dataset ACL mode requires the complete access graph"
                    )
                return finalize(
                    _fallback(
                        persisted_role,
                        dataset_acl_supported=False,
                        group_grants_supported=False,
                        organization_inheritance_supported=False,
                        warnings=("企业访问图谱尚未迁移，当前按租户角色执行",),
                    )
                )

            grants_statement = (
                select(DatasetAccessGrant)
                .where(
                    DatasetAccessGrant.tenant_id == tenant_id,
                    DatasetAccessGrant.dataset_id == dataset_id,
                    DatasetAccessGrant.status == "active",
                )
                .order_by(DatasetAccessGrant.id.asc())
            )
            if lock_for_update:
                grants_statement = grants_statement.with_for_update()
            active_grants = list(active_session.scalars(grants_statement))

            if not control_ready and not active_grants:
                return finalize(
                    _fallback(
                        persisted_role,
                        dataset_acl_supported=True,
                        group_grants_supported=True,
                        organization_inheritance_supported=organization_membership_supported,
                        warnings=("当前知识库尚无生效授权，继续按租户角色执行",),
                    )
                )

            for grant in active_grants:
                if grant.subject_type not in {"account", "group", "organization_unit"}:
                    raise DatasetAccessControlUnavailable(
                        "dataset grant has an unknown subject type"
                    )
                if grant.role not in _DATASET_ROLE_PERMISSIONS:
                    raise DatasetAccessControlUnavailable("dataset grant has an unknown role")

            if dataset_owner_id and dataset_owner_id == account_id:
                return finalize(
                    DatasetAccessDecision(
                        enforcement_mode="dataset_acl",
                        effective_permissions=_DATASET_ROLE_PERMISSIONS["manager"],
                        bypass_reason="dataset_owner",
                        dataset_role="manager",
                        dataset_acl_supported=True,
                        group_grants_supported=True,
                        organization_inheritance_supported=organization_membership_supported,
                        warnings=("知识库负责人在当前知识库范围内按管理员权限执行",),
                    )
                )
            if persisted_role in {"owner", "admin"}:
                return finalize(
                    DatasetAccessDecision(
                        enforcement_mode="dataset_acl",
                        effective_permissions=_DATASET_ROLE_PERMISSIONS["manager"],
                        bypass_reason=f"tenant_{persisted_role}",
                        dataset_role="manager",
                        dataset_acl_supported=True,
                        group_grants_supported=True,
                        organization_inheritance_supported=organization_membership_supported,
                        warnings=("租户管理角色在当前知识库范围内保留管理员权限",),
                    )
                )

            # Validate account subjects as real same-tenant members.  A
            # suspended account is valid but does not match; a dangling account
            # is integrity damage and must not trigger fallback.
            account_subject_ids = {
                str(grant.subject_id) for grant in active_grants if grant.subject_type == "account"
            }
            account_rows = {
                str(row[0]): str(row[1] or "").strip().casefold()
                for row in active_session.execute(
                    select(TenantMember.account_id, TenantMember.status).where(
                        TenantMember.tenant_id == tenant_id,
                        TenantMember.account_id.in_(account_subject_ids or {"__none__"}),
                    )
                )
            }
            if account_subject_ids - set(account_rows):
                raise DatasetAccessControlUnavailable("dataset grant references an unknown account")

            group_ids = {
                str(value)
                for value in active_session.scalars(
                    select(TenantGroupMember.group_id)
                    .join(
                        TenantGroup,
                        (TenantGroup.id == TenantGroupMember.group_id)
                        & (TenantGroup.tenant_id == TenantGroupMember.tenant_id),
                    )
                    .join(
                        TenantMember,
                        (TenantMember.account_id == TenantGroupMember.account_id)
                        & (TenantMember.tenant_id == TenantGroupMember.tenant_id),
                    )
                    .where(
                        TenantGroupMember.tenant_id == tenant_id,
                        TenantGroupMember.account_id == account_id,
                        TenantGroupMember.status == "active",
                        TenantGroup.status == "active",
                        TenantMember.status == "active",
                    )
                )
            }
            active_group_ids = {
                str(value)
                for value in active_session.scalars(
                    select(TenantGroup.id).where(
                        TenantGroup.tenant_id == tenant_id,
                        TenantGroup.id.in_(
                            {
                                str(grant.subject_id)
                                for grant in active_grants
                                if grant.subject_type == "group"
                            }
                            or {"__none__"}
                        ),
                        TenantGroup.status == "active",
                    )
                )
            }
            if any(
                grant.subject_type == "group" and str(grant.subject_id) not in active_group_ids
                for grant in active_grants
            ):
                # Archived groups simply no longer match; only an entirely
                # unknown group is treated as damaged state.
                all_group_ids = {
                    str(value)
                    for value in active_session.scalars(
                        select(TenantGroup.id).where(
                            TenantGroup.tenant_id == tenant_id,
                            TenantGroup.id.in_(
                                {
                                    str(grant.subject_id)
                                    for grant in active_grants
                                    if grant.subject_type == "group"
                                }
                                or {"__none__"}
                            ),
                        )
                    )
                }
                if active_group_ids | all_group_ids != {
                    str(grant.subject_id)
                    for grant in active_grants
                    if grant.subject_type == "group"
                }:
                    raise DatasetAccessControlUnavailable(
                        "dataset grant references an unknown group"
                    )

            organization_unit_ids: set[str] = set()
            warnings: list[str] = []
            grant_org_ids = {
                str(grant.subject_id)
                for grant in active_grants
                if grant.subject_type == "organization_unit"
            }
            if organization_membership_supported:
                organization_unit_ids = {
                    str(value)
                    for value in active_session.scalars(
                        select(TenantOrganizationUnitMember.organization_unit_id)
                        .join(
                            TenantOrganizationUnit,
                            (
                                TenantOrganizationUnit.id
                                == TenantOrganizationUnitMember.organization_unit_id
                            )
                            & (
                                TenantOrganizationUnit.tenant_id
                                == TenantOrganizationUnitMember.tenant_id
                            ),
                        )
                        .join(
                            TenantMember,
                            (TenantMember.account_id == TenantOrganizationUnitMember.account_id)
                            & (TenantMember.tenant_id == TenantOrganizationUnitMember.tenant_id),
                        )
                        .where(
                            TenantOrganizationUnitMember.tenant_id == tenant_id,
                            TenantOrganizationUnitMember.account_id == account_id,
                            TenantOrganizationUnitMember.status == "active",
                            TenantOrganizationUnit.status == "active",
                            TenantMember.status == "active",
                        )
                    )
                }
            elif grant_org_ids:
                warnings.append("组织成员关系迁移尚未完成，组织授权按未匹配处理")

            matched: list[DatasetAccessGrant] = []
            permissions: set[str] = set()
            matched_roles: set[str] = set()
            for grant in active_grants:
                matches = (
                    (
                        grant.subject_type == "account"
                        and str(grant.subject_id) == account_id
                        and account_rows.get(str(grant.subject_id)) == "active"
                    )
                    or (grant.subject_type == "group" and grant.subject_id in group_ids)
                    or (
                        grant.subject_type == "organization_unit"
                        and grant.subject_id in organization_unit_ids
                    )
                )
                if not matches:
                    continue
                matched.append(grant)
                matched_roles.add(str(grant.role))
                permissions.update(_DATASET_ROLE_PERMISSIONS[str(grant.role)])

            dataset_role = (
                "manager"
                if "manager" in matched_roles
                else "editor"
                if "editor" in matched_roles
                else "viewer"
                if "viewer" in matched_roles
                else None
            )
            if not matched:
                warnings.append("知识库已启用 ACL，但当前账号没有匹配的生效授权")
            return finalize(
                DatasetAccessDecision(
                    enforcement_mode="dataset_acl",
                    effective_permissions=frozenset(permissions),
                    matched_grants=tuple(_safe_grant(grant) for grant in matched),
                    dataset_role=dataset_role,
                    dataset_acl_supported=True,
                    group_grants_supported=True,
                    organization_inheritance_supported=organization_membership_supported,
                    warnings=tuple(warnings),
                )
            )
    except DatasetAccessControlUnavailable:
        raise
    except SQLAlchemyError as exc:
        raise DatasetAccessControlUnavailable("dataset ACL query failed") from exc


__all__ = [
    "DatasetAccessControlUnavailable",
    "DatasetAccessDecision",
    "evaluate_dataset_permissions",
]
