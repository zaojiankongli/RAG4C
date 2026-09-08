from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from core.knowledge_permissions import (
    KNOWLEDGE_AUDIT,
    KNOWLEDGE_DELETE,
    KNOWLEDGE_MANAGE,
    KNOWLEDGE_READ,
    KNOWLEDGE_WRITE,
)
from models.orm import (
    Account,
    Base,
    Dataset,
    DatasetAccessGrant,
    Tenant,
    TenantGroup,
    TenantGroupMember,
    TenantMember,
    TenantOrganizationUnit,
    TenantOrganizationUnitMember,
)

NOW = datetime(2026, 8, 26, 12, 0, 0)
ALL_PERMISSIONS = {
    KNOWLEDGE_READ,
    KNOWLEDGE_WRITE,
    KNOWLEDGE_DELETE,
    KNOWLEDGE_MANAGE,
    KNOWLEDGE_AUDIT,
}


def _engine(*, access_graph: bool = True, organization_membership: bool = True):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [Tenant.__table__, Account.__table__, TenantMember.__table__, Dataset.__table__]
    if access_graph:
        tables.extend(
            [
                TenantOrganizationUnit.__table__,
                TenantGroup.__table__,
                TenantGroupMember.__table__,
                DatasetAccessGrant.__table__,
            ]
        )
        if organization_membership:
            tables.insert(5, TenantOrganizationUnitMember.__table__)
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="tenant-a", name="Tenant A", status="active"),
                Tenant(id="tenant-b", name="Tenant B", status="active"),
                Account(id="owner-a", name="Owner A", email="owner-a@example.test", created_at=NOW),
                Account(id="admin-a", name="Admin A", email="admin-a@example.test", created_at=NOW),
                Account(
                    id="editor-a", name="Editor A", email="editor-a@example.test", created_at=NOW
                ),
                Account(
                    id="member-a", name="Member A", email="member-a@example.test", created_at=NOW
                ),
                Account(
                    id="member-b", name="Member B", email="member-b@example.test", created_at=NOW
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                TenantMember(account_id="owner-a", tenant_id="tenant-a", role="owner"),
                TenantMember(account_id="admin-a", tenant_id="tenant-a", role="admin"),
                TenantMember(account_id="editor-a", tenant_id="tenant-a", role="editor"),
                TenantMember(account_id="member-a", tenant_id="tenant-a", role="member"),
                TenantMember(account_id="member-b", tenant_id="tenant-b", role="member"),
            ]
        )
        session.flush()
        session.add_all(
            [
                Dataset(
                    id="dataset-a",
                    tenant_id="tenant-a",
                    name="Dataset A",
                    status="active",
                    owner_id="owner-a",
                ),
                Dataset(
                    id="dataset-b",
                    tenant_id="tenant-b",
                    name="Dataset B",
                    status="active",
                    owner_id="member-b",
                ),
            ]
        )
        if access_graph:
            session.add_all(
                [
                    TenantOrganizationUnit(
                        id="ou-a",
                        tenant_id="tenant-a",
                        name="研发",
                        code="RD",
                        status="active",
                        created_at=NOW,
                    ),
                    TenantGroup(
                        id="group-a",
                        tenant_id="tenant-a",
                        name="研发组",
                        normalized_name="研发组",
                        status="active",
                        created_at=NOW,
                    ),
                ]
            )
            session.flush()
            session.add_all(
                [
                    TenantGroupMember(
                        id=1,
                        tenant_id="tenant-a",
                        group_id="group-a",
                        account_id="member-a",
                        status="active",
                        created_at=NOW,
                    ),
                    TenantGroupMember(
                        id=2,
                        tenant_id="tenant-a",
                        group_id="group-a",
                        account_id="editor-a",
                        status="removed",
                        created_at=NOW,
                    ),
                ]
            )
            if organization_membership:
                session.add_all(
                    [
                        TenantOrganizationUnitMember(
                            id=1,
                            tenant_id="tenant-a",
                            organization_unit_id="ou-a",
                            account_id="editor-a",
                            status="active",
                            revision=1,
                            created_at=NOW,
                        ),
                        TenantOrganizationUnitMember(
                            id=2,
                            tenant_id="tenant-a",
                            organization_unit_id="ou-a",
                            account_id="member-a",
                            status="removed",
                            revision=2,
                            created_at=NOW,
                        ),
                    ]
                )
        session.commit()
    return engine


def _evaluate(engine, *, account_id="member-a", role="member", dataset_id="dataset-a"):
    from core.enterprise_access_control import evaluate_dataset_permissions

    return evaluate_dataset_permissions(
        engine,
        tenant_id="tenant-a",
        account_id=account_id,
        tenant_role=role,
        dataset_id=dataset_id,
    )


def _grant(
    engine, *, grant_id: str, subject_type: str, subject_id: str, role: str, status: str = "active"
):
    with Session(engine) as session:
        session.add(
            DatasetAccessGrant(
                id=grant_id,
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                subject_type=subject_type,
                subject_id=subject_id,
                role=role,
                status=status,
                revision=1,
                created_at=NOW,
            )
        )
        if status == "active":
            dataset = session.get(Dataset, "dataset-a")
            assert dataset is not None
            dataset.acl_mode = "dataset_acl"
            dataset.acl_revision = max(int(dataset.acl_revision or 1), 1)
        session.commit()


def test_legacy_schema_and_dataset_without_active_grants_use_tenant_role_fallback() -> None:
    legacy = _evaluate(_engine(access_graph=False), account_id="editor-a", role="editor")
    assert legacy.enforcement_mode == "tenant_role_fallback"
    assert legacy.effective_permissions == {KNOWLEDGE_READ, KNOWLEDGE_WRITE, KNOWLEDGE_DELETE}

    no_grants = _evaluate(_engine(), account_id="editor-a", role="editor")
    assert no_grants.enforcement_mode == "tenant_role_fallback"
    assert no_grants.matched_grants == ()


def test_any_active_dataset_grant_switches_to_acl_and_unmatched_actor_is_denied() -> None:
    engine = _engine()
    _grant(
        engine, grant_id="grant-other", subject_type="account", subject_id="editor-a", role="viewer"
    )

    decision = _evaluate(engine)

    assert decision.enforcement_mode == "dataset_acl"
    assert decision.effective_permissions == set()
    assert decision.matched_grants == ()


def test_direct_and_group_grants_union_permissions_and_revoked_grants_do_not_apply() -> None:
    engine = _engine()
    _grant(
        engine, grant_id="grant-view", subject_type="account", subject_id="member-a", role="viewer"
    )
    _grant(
        engine, grant_id="grant-editor", subject_type="group", subject_id="group-a", role="editor"
    )
    _grant(
        engine,
        grant_id="grant-revoked",
        subject_type="organization_unit",
        subject_id="ou-a",
        role="manager",
        status="revoked",
    )

    decision = _evaluate(engine)

    assert decision.effective_permissions == {KNOWLEDGE_READ, KNOWLEDGE_WRITE, KNOWLEDGE_DELETE}
    assert {item["id"] for item in decision.matched_grants} == {"grant-view", "grant-editor"}


def test_active_group_and_organization_memberships_apply_but_removed_memberships_do_not() -> None:
    engine = _engine()
    _grant(
        engine, grant_id="grant-group", subject_type="group", subject_id="group-a", role="viewer"
    )
    _grant(
        engine,
        grant_id="grant-org",
        subject_type="organization_unit",
        subject_id="ou-a",
        role="manager",
    )

    member = _evaluate(engine, account_id="member-a", role="member")
    editor = _evaluate(engine, account_id="editor-a", role="editor")

    assert member.effective_permissions == {KNOWLEDGE_READ}
    assert {item["id"] for item in member.matched_grants} == {"grant-group"}
    assert editor.effective_permissions == ALL_PERMISSIONS
    assert {item["id"] for item in editor.matched_grants} == {"grant-org"}


def test_tenant_owner_admin_and_persisted_dataset_owner_bypass_acl() -> None:
    engine = _engine()
    _grant(
        engine,
        grant_id="grant-activation",
        subject_type="account",
        subject_id="member-a",
        role="viewer",
    )

    owner = _evaluate(engine, account_id="owner-a", role="owner")
    admin = _evaluate(engine, account_id="admin-a", role="admin")

    assert owner.enforcement_mode == "dataset_acl"
    assert owner.bypass_reason == "dataset_owner"
    assert owner.effective_permissions == ALL_PERMISSIONS
    assert admin.bypass_reason == "tenant_admin"
    assert admin.effective_permissions == ALL_PERMISSIONS


def test_cross_tenant_rows_never_match_current_actor() -> None:
    engine = _engine()
    with Session(engine) as session:
        session.add(
            DatasetAccessGrant(
                id="grant-cross",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                subject_type="account",
                subject_id="member-b",
                role="manager",
                status="active",
                revision=1,
                created_at=NOW,
            )
        )
        dataset = session.get(Dataset, "dataset-a")
        assert dataset is not None
        dataset.acl_mode = "dataset_acl"
        dataset.acl_revision = 1
        session.commit()

    from core.enterprise_access_control import DatasetAccessControlUnavailable

    with pytest.raises(DatasetAccessControlUnavailable):
        _evaluate(engine, account_id="member-a", role="member")


def test_organization_grant_is_fail_closed_when_0018_membership_table_is_absent() -> None:
    engine = _engine(organization_membership=False)
    _grant(
        engine,
        grant_id="grant-org",
        subject_type="organization_unit",
        subject_id="ou-a",
        role="manager",
    )

    decision = _evaluate(engine, account_id="editor-a", role="editor")

    assert decision.enforcement_mode == "dataset_acl"
    assert decision.effective_permissions == set()
    assert any("组织成员关系迁移尚未完成" in warning for warning in decision.warnings)


def test_unknown_dataset_role_fails_closed() -> None:
    engine = _engine()
    with Session(engine) as session:
        session.add(
            DatasetAccessGrant(
                id="grant-invalid",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                subject_type="account",
                subject_id="member-a",
                role="unknown",
                status="active",
                revision=1,
                created_at=NOW,
            )
        )
        with pytest.raises(Exception):
            session.commit()
