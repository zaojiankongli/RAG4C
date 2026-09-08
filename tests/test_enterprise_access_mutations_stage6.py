from __future__ import annotations

import time

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from models.orm import (
    Account,
    Base,
    Dataset,
    DatasetAccessGrant,
    Tenant,
    TenantAuditEvent,
    TenantGroup,
    TenantMember,
    TenantOrganizationUnit,
)


def _engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)"))
        connection.execute(
            text("INSERT INTO alembic_version(version_num) VALUES ('0019_dataset_acl_control')")
        )
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="tenant-a", name="A", status="active"),
                Tenant(id="tenant-b", name="B", status="active"),
                Account(id="owner-a", name="Owner", email="owner-a@example.com"),
                Account(id="member-a", name="Member", email="member-a@example.com"),
                Account(id="member-b", name="Other", email="member-b@example.com"),
                TenantMember(
                    tenant_id="tenant-a",
                    account_id="owner-a",
                    role="owner",
                    status="active",
                    revision=1,
                ),
                TenantMember(
                    tenant_id="tenant-a",
                    account_id="member-a",
                    role="member",
                    status="active",
                    revision=1,
                ),
                TenantMember(
                    tenant_id="tenant-b",
                    account_id="member-b",
                    role="member",
                    status="active",
                    revision=1,
                ),
                Dataset(
                    id="dataset-a",
                    tenant_id="tenant-a",
                    owner_id="owner-a",
                    name="Knowledge",
                    status="active",
                ),
                TenantGroup(
                    id="group-a",
                    tenant_id="tenant-a",
                    name="Editors",
                    normalized_name="editors",
                    status="active",
                    revision=1,
                ),
                TenantOrganizationUnit(
                    id="org-a",
                    tenant_id="tenant-a",
                    parent_id=None,
                    code="org-a",
                    name="Platform",
                    status="active",
                    revision=1,
                ),
            ]
        )
        session.commit()
    return engine


def _kwargs() -> dict[str, object]:
    return {
        "tenant_id": "tenant-a",
        "dataset_id": "dataset-a",
        "actor_id": "owner-a",
        "actor_role": "owner",
        "reason": "stage 6 contract",
        "request_id": "request-stage-6",
        "request_ip": "127.0.0.1",
        "idempotency_key": f"stage7-direct-{time.time_ns()}",
    }


def test_create_grant_is_authorized_and_audited_atomically() -> None:
    from core.enterprise_access_mutations import create_dataset_access_grant

    engine = _engine()
    payload = create_dataset_access_grant(
        engine,
        **_kwargs(),
        subject_type="account",
        subject_id="member-a",
        role="editor",
    )

    assert payload["grant"] == {
        "id": payload["grant"]["id"],
        "dataset_id": "dataset-a",
        "subject_type": "account",
        "subject_id": "member-a",
        "subject_name": "Member",
        "role": "editor",
        "status": "active",
        "revision": 1,
    }
    assert "authorization" not in payload
    assert "audit" not in payload
    with Session(engine) as session:
        assert session.scalar(select(DatasetAccessGrant)) is not None
        event = session.scalar(select(TenantAuditEvent))
        assert event is not None
        assert event.action == "dataset_access_grant.created"
        assert event.after_snapshot["reason"] == "stage 6 contract"


@pytest.mark.parametrize(
    ("subject_type", "subject_id"),
    [("account", "member-b"), ("group", "missing-group"), ("organization_unit", "missing-org")],
)
def test_create_rejects_cross_tenant_or_missing_subjects(
    subject_type: str, subject_id: str
) -> None:
    from core.enterprise_access_mutations import (
        EnterpriseAccessGrantSubjectNotFound,
        create_dataset_access_grant,
    )

    engine = _engine()
    with pytest.raises(EnterpriseAccessGrantSubjectNotFound):
        create_dataset_access_grant(
            engine,
            **_kwargs(),
            subject_type=subject_type,
            subject_id=subject_id,
            role="viewer",
        )
    with Session(engine) as session:
        assert session.scalar(select(DatasetAccessGrant)) is None
        assert session.scalar(select(TenantAuditEvent)) is None


def test_revision_fencing_and_state_transitions() -> None:
    from core.enterprise_access_mutations import (
        EnterpriseAccessGrantRevisionConflict,
        change_dataset_access_grant_role,
        create_dataset_access_grant,
        resume_dataset_access_grant,
        revoke_dataset_access_grant,
    )

    engine = _engine()
    created = create_dataset_access_grant(
        engine,
        **_kwargs(),
        subject_type="group",
        subject_id="group-a",
        role="viewer",
    )["grant"]
    create_dataset_access_grant(
        engine,
        **_kwargs(),
        subject_type="account",
        subject_id="member-a",
        role="viewer",
    )
    changed = change_dataset_access_grant_role(
        engine,
        **_kwargs(),
        grant_id=created["id"],
        expected_revision=1,
        role="manager",
    )["grant"]
    assert (changed["role"], changed["revision"]) == ("manager", 2)
    with pytest.raises(EnterpriseAccessGrantRevisionConflict):
        revoke_dataset_access_grant(
            engine,
            **_kwargs(),
            grant_id=created["id"],
            expected_revision=1,
        )
    revoked = revoke_dataset_access_grant(
        engine,
        **_kwargs(),
        grant_id=created["id"],
        expected_revision=2,
    )["grant"]
    assert (revoked["status"], revoked["revision"]) == ("revoked", 3)
    resumed = resume_dataset_access_grant(
        engine,
        **_kwargs(),
        grant_id=created["id"],
        expected_revision=3,
    )["grant"]
    assert (resumed["status"], resumed["revision"]) == ("active", 4)


def test_permission_failure_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.enterprise_access_control import DatasetAccessDecision
    from core import enterprise_access_mutations as mutations

    engine = _engine()
    monkeypatch.setattr(
        mutations,
        "evaluate_dataset_permissions",
        lambda *args, **kwargs: DatasetAccessDecision(
            enforcement_mode="dataset_acl",
            effective_permissions=frozenset(),
            dataset_acl_supported=True,
        ),
    )
    with pytest.raises(mutations.EnterpriseAccessGrantForbidden):
        mutations.create_dataset_access_grant(
            engine,
            **_kwargs(),
            subject_type="account",
            subject_id="member-a",
            role="viewer",
        )
    with Session(engine) as session:
        assert session.scalar(select(DatasetAccessGrant)) is None
        assert session.scalar(select(TenantAuditEvent)) is None


def test_revoke_last_active_grant_keeps_persistent_acl_mode_and_denies_unmatched_actor() -> None:
    from core.enterprise_access_mutations import (
        create_dataset_access_grant,
        revoke_dataset_access_grant,
    )
    from core.enterprise_access_control import evaluate_dataset_permissions
    from core.knowledge_permissions import KNOWLEDGE_READ

    engine = _engine()
    created = create_dataset_access_grant(
        engine,
        **_kwargs(),
        subject_type="account",
        subject_id="member-a",
        role="viewer",
    )["grant"]
    revoked = revoke_dataset_access_grant(
        engine,
        **_kwargs(),
        grant_id=created["id"],
        expected_revision=1,
    )["grant"]

    assert revoked["status"] == "revoked"
    assert revoked["revision"] == 2
    with Session(engine) as session:
        grant = session.get(DatasetAccessGrant, created["id"])
        dataset = session.get(Dataset, "dataset-a")
        assert grant is not None and grant.status == "revoked" and grant.revision == 2
        assert dataset is not None and dataset.acl_mode == "dataset_acl"
        assert (
            session.scalar(
                select(TenantAuditEvent).where(
                    TenantAuditEvent.action == "dataset_access_grant.revoked"
                )
            )
            is not None
        )
    decision = evaluate_dataset_permissions(engine, "tenant-a", "member-a", "member", "dataset-a")
    assert decision.enforcement_mode == "dataset_acl"
    assert not decision.allows(KNOWLEDGE_READ)


def test_self_revoke_that_removes_the_only_manage_path_is_rejected() -> None:
    from core.enterprise_access_mutations import (
        EnterpriseAccessGrantStateConflict,
        create_dataset_access_grant,
        revoke_dataset_access_grant,
    )

    engine = _engine()
    manager = create_dataset_access_grant(
        engine,
        **_kwargs(),
        subject_type="account",
        subject_id="member-a",
        role="manager",
    )["grant"]
    create_dataset_access_grant(
        engine,
        **_kwargs(),
        subject_type="group",
        subject_id="group-a",
        role="viewer",
    )
    before_audits = 2

    with pytest.raises(EnterpriseAccessGrantStateConflict) as exc_info:
        revoke_dataset_access_grant(
            engine,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            actor_id="member-a",
            actor_role="member",
            grant_id=manager["id"],
            expected_revision=1,
            reason="self revoke",
            request_id="request-self-revoke",
            request_ip="127.0.0.1",
            idempotency_key="stage7-self-revoke-0001",
        )

    assert exc_info.value.code == "dataset_access_grant_actor_manage_protected"
    with Session(engine) as session:
        grant = session.get(DatasetAccessGrant, manager["id"])
        assert grant is not None and grant.status == "active"
        assert session.scalar(select(func.count(TenantAuditEvent.sequence))) == before_audits


def test_audit_reason_redacts_bearer_credentials() -> None:
    from core.enterprise_access_mutations import create_dataset_access_grant

    engine = _engine()
    create_dataset_access_grant(
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        actor_id="owner-a",
        actor_role="owner",
        subject_type="account",
        subject_id="member-a",
        role="viewer",
        reason="Authorization: Bearer SUPERSECRET",
        request_id="request-sensitive-reason",
        request_ip="127.0.0.1",
        idempotency_key="stage7-sensitive-reason-0001",
    )

    with Session(engine) as session:
        event = session.scalar(select(TenantAuditEvent))
        assert event is not None
        serialized = str(event.after_snapshot)
        assert "SUPERSECRET" not in serialized
        assert "[REDACTED]" in serialized
