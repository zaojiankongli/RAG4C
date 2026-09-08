"""Tenant-isolated read projections for the enterprise access graph."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from sqlalchemy import func, inspect, or_, select
from sqlalchemy.orm import Session

from models.orm import (
    Account,
    DatasetAccessGrant,
    TenantGroup,
    TenantGroupMember,
    TenantInvitation,
    TenantMember,
    TenantOrganizationUnit,
    TenantOrganizationUnitMember,
)

OrganizationStatus = Literal["active", "archived"]
GroupStatus = Literal["active", "archived"]
InvitationStatus = Literal["pending", "accepted", "revoked", "expired"]
GrantSubjectType = Literal["account", "group", "organization_unit"]
GrantRole = Literal["viewer", "editor", "manager"]
GrantStatus = Literal["active", "revoked"]

_REQUIRED_TABLES = frozenset(
    {
        "tenant_organization_units",
        "tenant_organization_unit_members",
        "tenant_groups",
        "tenant_group_members",
        "dataset_access_grants",
        "tenant_invitations",
    }
)


class EnterpriseAccessGraphMigrationRequired(RuntimeError):
    """Raised when the catalog does not contain the complete 0018 access-graph contract."""

    def __init__(self, missing: Iterable[str] = ()) -> None:
        self.missing = tuple(sorted(str(item) for item in missing))
        super().__init__("enterprise access graph schema 0018 is required")


class EnterpriseAccessGraphResourceNotFound(LookupError):
    """Raised when a tenant-scoped graph resource must remain non-enumerable."""


class EnterpriseAccessGraphInconsistent(RuntimeError):
    """Raised when stored access-graph facts cannot be projected safely."""


def _ensure_schema(engine: Any) -> None:
    tables = set(inspect(engine).get_table_names())
    missing = _REQUIRED_TABLES - tables
    if missing:
        raise EnterpriseAccessGraphMigrationRequired(missing)


def _page(items: list[dict[str, Any]], *, count: int, limit: int) -> dict[str, Any]:
    has_more = len(items) > limit
    visible = items[:limit]
    return {
        "items": visible,
        "count": int(count),
        "next_before_id": str(visible[-1]["id"]) if has_more and visible else None,
    }


def list_organization_units(
    engine: Any,
    *,
    tenant_id: str,
    parent_id: str | None = None,
    status: OrganizationStatus | None = None,
    before_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    _ensure_schema(engine)
    filters = [TenantOrganizationUnit.tenant_id == tenant_id]
    if parent_id is not None:
        filters.append(TenantOrganizationUnit.parent_id == parent_id)
    if status is not None:
        filters.append(TenantOrganizationUnit.status == status)

    with Session(engine) as session:
        count = session.scalar(select(func.count(TenantOrganizationUnit.id)).where(*filters)) or 0
        page_filters = list(filters)
        if before_id is not None:
            page_filters.append(TenantOrganizationUnit.id < before_id)
        rows = list(
            session.scalars(
                select(TenantOrganizationUnit)
                .where(*page_filters)
                .order_by(TenantOrganizationUnit.id.desc())
                .limit(limit + 1)
            )
        )
        visible_rows = rows[:limit]
        visible_ids = [str(row.id) for row in visible_rows]
        child_counts: dict[str, int] = {}
        if visible_ids:
            child_counts = {
                str(parent): int(value)
                for parent, value in session.execute(
                    select(TenantOrganizationUnit.parent_id, func.count(TenantOrganizationUnit.id))
                    .where(
                        TenantOrganizationUnit.tenant_id == tenant_id,
                        TenantOrganizationUnit.parent_id.in_(visible_ids),
                    )
                    .group_by(TenantOrganizationUnit.parent_id)
                )
            }

        member_counts = {identifier: 0 for identifier in visible_ids}
        if visible_ids:
            member_counts.update(
                {
                    str(unit_id): int(value)
                    for unit_id, value in session.execute(
                        select(
                            TenantOrganizationUnitMember.organization_unit_id,
                            func.count(TenantOrganizationUnitMember.id),
                        )
                        .where(
                            TenantOrganizationUnitMember.tenant_id == tenant_id,
                            TenantOrganizationUnitMember.organization_unit_id.in_(visible_ids),
                            TenantOrganizationUnitMember.status == "active",
                        )
                        .group_by(TenantOrganizationUnitMember.organization_unit_id)
                    )
                }
            )

        items = [
            {
                "id": str(row.id),
                "parent_id": str(row.parent_id) if row.parent_id is not None else None,
                "name": row.name,
                "code": row.code,
                "status": row.status,
                "revision": int(row.revision),
                "member_count": member_counts.get(str(row.id), 0),
                "child_count": child_counts.get(str(row.id), 0),
            }
            for row in rows
        ]
    return _page(items, count=count, limit=limit)


def list_groups(
    engine: Any,
    *,
    tenant_id: str,
    query: str | None = None,
    status: GroupStatus | None = None,
    before_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    _ensure_schema(engine)
    filters = [TenantGroup.tenant_id == tenant_id]
    if query:
        pattern = f"%{query.strip().casefold()}%"
        filters.append(
            or_(
                func.lower(TenantGroup.name).like(pattern),
                func.lower(TenantGroup.description).like(pattern),
            )
        )
    if status is not None:
        filters.append(TenantGroup.status == status)

    with Session(engine) as session:
        count = session.scalar(select(func.count(TenantGroup.id)).where(*filters)) or 0
        page_filters = list(filters)
        if before_id is not None:
            page_filters.append(TenantGroup.id < before_id)
        rows = list(
            session.scalars(
                select(TenantGroup)
                .where(*page_filters)
                .order_by(TenantGroup.id.desc())
                .limit(limit + 1)
            )
        )
        visible_ids = [str(row.id) for row in rows[:limit]]
        member_counts: dict[str, int] = {}
        if visible_ids:
            member_counts = {
                str(group_id): int(value)
                for group_id, value in session.execute(
                    select(TenantGroupMember.group_id, func.count(TenantGroupMember.id))
                    .where(
                        TenantGroupMember.tenant_id == tenant_id,
                        TenantGroupMember.group_id.in_(visible_ids),
                        TenantGroupMember.status == "active",
                    )
                    .group_by(TenantGroupMember.group_id)
                )
            }
        items = [
            {
                "id": str(row.id),
                "name": row.name,
                "description": row.description or "",
                "status": row.status,
                "member_count": member_counts.get(str(row.id), 0),
                "revision": int(row.revision),
            }
            for row in rows
        ]
    return _page(items, count=count, limit=limit)


def list_group_members(
    engine: Any,
    *,
    tenant_id: str,
    group_id: str,
    before_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    _ensure_schema(engine)
    with Session(engine) as session:
        group_exists = session.scalar(
            select(func.count(TenantGroup.id)).where(
                TenantGroup.id == group_id,
                TenantGroup.tenant_id == tenant_id,
            )
        )
        if not group_exists:
            raise EnterpriseAccessGraphResourceNotFound("group does not exist")

        filters = [
            TenantGroupMember.tenant_id == tenant_id,
            TenantGroupMember.group_id == group_id,
        ]
        count = session.scalar(select(func.count(TenantGroupMember.id)).where(*filters)) or 0
        page_filters = list(filters)
        if before_id is not None:
            page_filters.append(TenantGroupMember.id < int(before_id))
        rows = list(
            session.execute(
                select(TenantGroupMember, Account, TenantMember.role)
                .join(Account, Account.id == TenantGroupMember.account_id)
                .join(
                    TenantMember,
                    (TenantMember.account_id == TenantGroupMember.account_id)
                    & (TenantMember.tenant_id == TenantGroupMember.tenant_id),
                )
                .where(*page_filters)
                .order_by(TenantGroupMember.id.desc())
                .limit(limit + 1)
            )
        )
        items = [
            {
                "id": str(group_member.id),
                "account_id": account.id,
                "name": account.name,
                "email": account.email,
                "role": role,
                "status": group_member.status,
            }
            for group_member, account, role in rows
        ]
    return _page(items, count=count, limit=limit)


def list_invitations(
    engine: Any,
    *,
    tenant_id: str,
    status: InvitationStatus | None = None,
    before_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    _ensure_schema(engine)
    filters = [TenantInvitation.tenant_id == tenant_id]
    if status is not None:
        filters.append(TenantInvitation.status == status)
    with Session(engine) as session:
        count = session.scalar(select(func.count(TenantInvitation.id)).where(*filters)) or 0
        page_filters = list(filters)
        if before_id is not None:
            page_filters.append(TenantInvitation.id < before_id)
        rows = list(
            session.scalars(
                select(TenantInvitation)
                .where(*page_filters)
                .order_by(TenantInvitation.id.desc())
                .limit(limit + 1)
            )
        )
        items = [
            {
                "id": str(row.id),
                "email": row.email,
                "role": row.role,
                "status": row.status,
                "invited_by": row.invited_by,
                "expires_at": row.expires_at,
                "revision": int(row.revision),
            }
            for row in rows
        ]
    return _page(items, count=count, limit=limit)


def _subject_names(
    session: Session,
    *,
    tenant_id: str,
    grants: list[Any],
) -> dict[tuple[str, str], str]:
    names: dict[tuple[str, str], str] = {}
    grouped_ids = {
        "account": {str(grant.subject_id) for grant in grants if grant.subject_type == "account"},
        "group": {str(grant.subject_id) for grant in grants if grant.subject_type == "group"},
        "organization_unit": {
            str(grant.subject_id) for grant in grants if grant.subject_type == "organization_unit"
        },
    }
    if grouped_ids["account"]:
        names.update(
            {
                ("account", str(identifier)): str(name)
                for identifier, name in session.execute(
                    select(Account.id, Account.name)
                    .join(TenantMember, TenantMember.account_id == Account.id)
                    .where(
                        TenantMember.tenant_id == tenant_id,
                        Account.id.in_(grouped_ids["account"]),
                    )
                )
            }
        )
    if grouped_ids["group"]:
        names.update(
            {
                ("group", str(identifier)): str(name)
                for identifier, name in session.execute(
                    select(TenantGroup.id, TenantGroup.name).where(
                        TenantGroup.tenant_id == tenant_id,
                        TenantGroup.id.in_(grouped_ids["group"]),
                    )
                )
            }
        )
    if grouped_ids["organization_unit"]:
        names.update(
            {
                ("organization_unit", str(identifier)): str(name)
                for identifier, name in session.execute(
                    select(TenantOrganizationUnit.id, TenantOrganizationUnit.name).where(
                        TenantOrganizationUnit.tenant_id == tenant_id,
                        TenantOrganizationUnit.id.in_(grouped_ids["organization_unit"]),
                    )
                )
            }
        )
    return names


def list_dataset_access_grants(
    engine: Any,
    *,
    tenant_id: str,
    dataset_id: str,
    subject_type: GrantSubjectType | None = None,
    role: GrantRole | None = None,
    status: GrantStatus | None = None,
    before_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    _ensure_schema(engine)
    filters = [
        DatasetAccessGrant.tenant_id == tenant_id,
        DatasetAccessGrant.dataset_id == dataset_id,
    ]
    if subject_type is not None:
        filters.append(DatasetAccessGrant.subject_type == subject_type)
    if role is not None:
        filters.append(DatasetAccessGrant.role == role)
    if status is not None:
        filters.append(DatasetAccessGrant.status == status)
    with Session(engine) as session:
        count = session.scalar(select(func.count(DatasetAccessGrant.id)).where(*filters)) or 0
        page_filters = list(filters)
        if before_id is not None:
            page_filters.append(DatasetAccessGrant.id < before_id)
        rows = list(
            session.scalars(
                select(DatasetAccessGrant)
                .where(*page_filters)
                .order_by(DatasetAccessGrant.id.desc())
                .limit(limit + 1)
            )
        )
        names = _subject_names(session, tenant_id=tenant_id, grants=rows[:limit])
        items = [
            {
                "id": str(row.id),
                "dataset_id": row.dataset_id,
                "subject_type": row.subject_type,
                "subject_id": row.subject_id,
                "subject_name": names.get((row.subject_type, str(row.subject_id)), row.subject_id),
                "role": row.role,
                "status": row.status,
                "revision": int(row.revision),
            }
            for row in rows
        ]
    return _page(items, count=count, limit=limit)


__all__ = [
    "EnterpriseAccessGraphInconsistent",
    "EnterpriseAccessGraphMigrationRequired",
    "EnterpriseAccessGraphResourceNotFound",
    "list_dataset_access_grants",
    "list_group_members",
    "list_groups",
    "list_invitations",
    "list_organization_units",
]
