from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.enterprise_release_quality_operations import canonical_operations_digest
from core.enterprise_release_quality_operation_mutations import (
    ReleaseQualityConflict,
    ReleaseQualityForbidden,
    ReleaseQualityInvalid,
    ReleaseQualityNotFound,
    ReleaseQualityUnavailable,
    create_quality_slo_policy,
    list_quality_slo_policies,
    update_quality_slo_policy,
)
from models.orm import (
    Account,
    Tenant,
    TenantAuditEvent,
    TenantControlMutationRequest,
    TenantMember,
    TenantReleaseChannel,
    TenantReleaseQualitySloPolicy,
)
from test_enterprise_knowledge_base_release_snapshot import _release_engine


NOW = datetime(2026, 8, 28, 12, 0, 0)


def _engine(tmp_path: Path):
    engine, _ = _release_engine(tmp_path)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-b", name="Tenant B", plan="enterprise", status="active"))
        session.add_all(
            [
                Account(id="admin-a", name="Admin A", email="admin-a@slo.test"),
                Account(id="editor-a", name="Editor A", email="editor-a@slo.test"),
                Account(id="member-a", name="Member A", email="member-a@slo.test"),
                Account(id="owner-b", name="Owner B", email="owner-b@slo.test"),
            ]
        )
        session.flush()
        session.add_all(
            [
                TenantReleaseChannel(
                    id=f"tenant-b-channel-{code}",
                    tenant_id="tenant-b",
                    code=code,
                    normalized_code=code,
                    name=f"Tenant B {name}",
                    status="active",
                    risk_tier=risk,
                    promotion_order=order,
                    is_default_serving=code == "production",
                    active_default_slot="default" if code == "production" else None,
                    revision=1,
                    created_at=NOW,
                    created_by="owner-b",
                    updated_at=NOW,
                    updated_by="owner-b",
                )
                for code, name, risk, order in (
                    ("development", "Development", "low", 10),
                    ("testing", "Testing", "medium", 20),
                    ("production", "Production", "high", 30),
                )
            ]
        )
        session.add_all(
            [
                TenantMember(
                    account_id="admin-a",
                    tenant_id="tenant-a",
                    role="admin",
                    status="active",
                    revision=1,
                    created_at=NOW,
                    updated_at=NOW,
                    updated_by="owner-a",
                ),
                TenantMember(
                    account_id="editor-a",
                    tenant_id="tenant-a",
                    role="editor",
                    status="active",
                    revision=1,
                    created_at=NOW,
                    updated_at=NOW,
                    updated_by="owner-a",
                ),
                TenantMember(
                    account_id="member-a",
                    tenant_id="tenant-a",
                    role="member",
                    status="active",
                    revision=1,
                    created_at=NOW,
                    updated_at=NOW,
                    updated_by="owner-a",
                ),
                TenantMember(
                    account_id="owner-b",
                    tenant_id="tenant-b",
                    role="owner",
                    status="active",
                    revision=1,
                    created_at=NOW,
                    updated_at=NOW,
                    updated_by="owner-b",
                ),
            ]
        )
        session.commit()
    return engine


def _create(
    engine,
    *,
    actor_id: str = "owner-a",
    key: str = "slo-create-key",
    name: str = "Enterprise release SLO",
    scope_type: str = "global",
    scope_value: str = "*",
    channel_id: str | None = None,
    certification_warning_minutes: int = 10080,
    certification_critical_minutes: int = 1440,
    waiver_warning_minutes: int = 720,
    max_open_alerts: int = 100,
    auto_queue_recertification: bool = True,
    require_passing_certification: bool = True,
    allow_active_waiver: bool = True,
    tenant_id: str = "tenant-a",
    reason: str = "establish release quality operations",
):
    return create_quality_slo_policy(
        engine,
        tenant_id=tenant_id,
        actor_id=actor_id,
        name=name,
        scope_type=scope_type,
        scope_value=scope_value,
        channel_id=channel_id,
        certification_warning_minutes=certification_warning_minutes,
        certification_critical_minutes=certification_critical_minutes,
        waiver_warning_minutes=waiver_warning_minutes,
        max_open_alerts=max_open_alerts,
        auto_queue_recertification=auto_queue_recertification,
        require_passing_certification=require_passing_certification,
        allow_active_waiver=allow_active_waiver,
        reason=reason,
        request_id=f"request-{key}",
        request_ip="127.0.0.1",
        idempotency_key=key,
    )


def _update(
    engine,
    *,
    policy_id: str,
    actor_id: str = "owner-a",
    tenant_id: str = "tenant-a",
    key: str = "slo-update-key",
    expected_revision: int = 1,
    name: str | None = None,
    certification_warning_minutes: int | None = None,
    certification_critical_minutes: int | None = None,
    waiver_warning_minutes: int | None = None,
    max_open_alerts: int | None = None,
    auto_queue_recertification: bool | None = None,
    require_passing_certification: bool | None = None,
    allow_active_waiver: bool | None = None,
    status: str | None = None,
    reason: str = "update release quality operations",
):
    return update_quality_slo_policy(
        engine,
        tenant_id=tenant_id,
        actor_id=actor_id,
        policy_id=policy_id,
        expected_revision=expected_revision,
        name=name,
        certification_warning_minutes=certification_warning_minutes,
        certification_critical_minutes=certification_critical_minutes,
        waiver_warning_minutes=waiver_warning_minutes,
        max_open_alerts=max_open_alerts,
        auto_queue_recertification=auto_queue_recertification,
        require_passing_certification=require_passing_certification,
        allow_active_waiver=allow_active_waiver,
        status=status,
        reason=reason,
        request_id=f"request-{key}",
        request_ip="127.0.0.1",
        idempotency_key=key,
    )


def _policy_digest(policy: dict[str, object]) -> str:
    return canonical_operations_digest(
        "slo_policy",
        {
            "scope_type": policy["scope_type"],
            "scope_value": policy["scope_value"],
            "channel_id": policy["channel_id"],
            "revision": policy["revision"],
            "certification_warning_minutes": policy["certification_warning_minutes"],
            "certification_critical_minutes": policy["certification_critical_minutes"],
            "waiver_warning_minutes": policy["waiver_warning_minutes"],
            "max_open_alerts": policy["max_open_alerts"],
            "auto_queue_recertification": policy["auto_queue_recertification"],
            "require_passing_certification": policy["require_passing_certification"],
            "allow_active_waiver": policy["allow_active_waiver"],
        },
    )


def test_create_policy_is_canonical_revisioned_audited_and_safely_replayed(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    first = _create(engine, key="slo-create-owner")
    replay = _create(engine, key="slo-create-owner")

    assert first.status == 201
    assert replay.status == first.status
    assert replay.body == first.body
    policy = first.body["policy"]
    assert policy["tenant_id"] == "tenant-a"
    assert policy["scope_type"] == "global"
    assert policy["scope_value"] == "*"
    assert policy["active_scope_key"] == "global:*"
    assert policy["status"] == "active"
    assert policy["revision"] == 1
    assert policy["policy_digest"] == _policy_digest(policy)
    assert "slo-create-owner" not in repr(first.body)

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(TenantReleaseQualitySloPolicy)) == 1
        ledger = session.scalars(select(TenantControlMutationRequest)).all()
        assert len(ledger) == 1
        assert ledger[0].idempotency_key != "slo-create-owner"
        assert ledger[0].status == "completed"
        audit_rows = session.scalars(select(TenantAuditEvent)).all()
        assert len(audit_rows) == 1
        assert audit_rows[0].tenant_id == "tenant-a"
        assert audit_rows[0].action == "knowledge_base.release_quality.slo_policy_created"
        assert "slo-create-owner" not in repr(audit_rows[0].after_snapshot)
    engine.dispose()


def test_admin_can_create_but_editor_cannot_mutate_and_member_can_read(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    created = _create(
        engine,
        actor_id="admin-a",
        key="slo-admin-risk",
        scope_type="risk_tier",
        scope_value="high",
    )
    assert created.status == 201

    with pytest.raises(ReleaseQualityForbidden, match="owner or admin"):
        _create(
            engine,
            actor_id="editor-a",
            key="slo-editor-create",
            scope_type="risk_tier",
            scope_value="medium",
        )

    listed = list_quality_slo_policies(engine, tenant_id="tenant-a", actor_id="member-a")
    assert listed.status == 200
    assert [item["id"] for item in listed.body["items"]] == [created.body["policy"]["id"]]
    engine.dispose()


def test_scope_threshold_and_exact_type_validation_is_fail_closed(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    cases = [
        {"scope_type": "global", "scope_value": "prod", "key": "bad-global"},
        {
            "scope_type": "risk_tier",
            "scope_value": "urgent",
            "key": "bad-risk",
        },
        {
            "scope_type": "channel",
            "scope_value": "channel-production",
            "channel_id": "channel-testing",
            "key": "bad-channel",
        },
        {
            "certification_warning_minutes": 1440,
            "certification_critical_minutes": 1440,
            "key": "bad-order",
        },
        {
            "certification_warning_minutes": True,
            "key": "bad-int",
        },
        {
            "auto_queue_recertification": 1,
            "key": "bad-bool",
        },
    ]
    for values in cases:
        with pytest.raises(ReleaseQualityInvalid):
            _create(engine, **values)

    with pytest.raises(ReleaseQualityInvalid):
        _create(
            engine,
            scope_type="channel",
            scope_value="channel-production",
            channel_id="channel-production",
            key="bad-channel-body",
            reason="body=private query=ticket-42 credential=secret",
        )
    engine.dispose()


def test_active_scope_is_unique_per_tenant_but_isolated_between_tenants(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    first = _create(engine, key="slo-tenant-a")
    with pytest.raises(ReleaseQualityConflict, match="active quality policy"):
        _create(engine, key="slo-tenant-a-duplicate")

    foreign = _create(engine, tenant_id="tenant-b", actor_id="owner-b", key="slo-tenant-b")
    assert foreign.status == 201
    assert foreign.body["policy"]["tenant_id"] == "tenant-b"

    tenant_a = list_quality_slo_policies(engine, tenant_id="tenant-a", actor_id="owner-a")
    tenant_b = list_quality_slo_policies(engine, tenant_id="tenant-b", actor_id="owner-b")
    assert [item["tenant_id"] for item in tenant_a.body["items"]] == ["tenant-a"]
    assert [item["tenant_id"] for item in tenant_b.body["items"]] == ["tenant-b"]
    with pytest.raises(ReleaseQualityNotFound):
        _update(
            engine,
            policy_id=first.body["policy"]["id"],
            tenant_id="tenant-b",
            actor_id="owner-b",
            key="slo-foreign-update",
            name="must not cross tenant",
        )
    engine.dispose()


def test_update_is_revision_fenced_supports_disabled_lifecycle_and_replay(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    created = _create(engine, key="slo-update-create")
    policy_id = created.body["policy"]["id"]

    disabled = _update(
        engine,
        policy_id=policy_id,
        key="slo-disable",
        expected_revision=1,
        name="Enterprise release SLO v2",
        certification_warning_minutes=20160,
        certification_critical_minutes=2880,
        waiver_warning_minutes=1440,
        max_open_alerts=200,
        auto_queue_recertification=False,
        allow_active_waiver=False,
        status="disabled",
        reason="retire old policy without deleting history",
    )
    replay = _update(
        engine,
        policy_id=policy_id,
        key="slo-disable",
        expected_revision=1,
        name="Enterprise release SLO v2",
        certification_warning_minutes=20160,
        certification_critical_minutes=2880,
        waiver_warning_minutes=1440,
        max_open_alerts=200,
        auto_queue_recertification=False,
        allow_active_waiver=False,
        status="disabled",
        reason="retire old policy without deleting history",
    )
    assert disabled.status == 200
    assert replay.status == disabled.status
    assert replay.body == disabled.body
    disabled_policy = disabled.body["policy"]
    assert disabled_policy["revision"] == 2
    assert disabled_policy["status"] == "disabled"
    assert disabled_policy["active_scope_key"] is None
    assert disabled_policy["disabled_by"] == "owner-a"
    assert disabled_policy["disabled_at"]
    assert disabled_policy["policy_digest"] == _policy_digest(disabled_policy)
    assert "retire old policy" not in repr(replay.body)

    with pytest.raises(ReleaseQualityConflict, match="revision"):
        _update(
            engine,
            policy_id=policy_id,
            key="slo-stale-update",
            expected_revision=1,
            name="stale write",
        )

    active = _update(
        engine,
        policy_id=policy_id,
        key="slo-reenable",
        expected_revision=2,
        status="active",
        name="Enterprise release SLO v3",
    )
    active_policy = active.body["policy"]
    assert active_policy["revision"] == 3
    assert active_policy["status"] == "active"
    assert active_policy["active_scope_key"] == "global:*"
    assert active_policy["disabled_at"] is None
    assert active_policy["disabled_by"] is None
    assert active_policy["policy_digest"] == _policy_digest(active_policy)

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(TenantReleaseQualitySloPolicy)) == 1
        assert session.scalar(select(func.count()).select_from(TenantAuditEvent)) == 3
        assert session.scalar(select(func.count()).select_from(TenantControlMutationRequest)) == 3
    engine.dispose()


def test_update_requires_a_change_and_preserves_canonical_scope(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    created = _create(
        engine,
        key="slo-channel-create",
        scope_type="channel",
        scope_value="channel-production",
        channel_id="channel-production",
    )
    policy_id = created.body["policy"]["id"]

    with pytest.raises(ReleaseQualityInvalid, match="changed field"):
        _update(engine, policy_id=policy_id, key="slo-noop")
    with pytest.raises(ReleaseQualityInvalid, match="exact integer"):
        _update(engine, policy_id=policy_id, key="slo-bad-update-int", max_open_alerts=True)
    with pytest.raises(ReleaseQualityInvalid, match="exact boolean"):
        _update(engine, policy_id=policy_id, key="slo-bad-update-bool", allow_active_waiver=1)

    updated = _update(
        engine,
        policy_id=policy_id,
        key="slo-channel-update",
        name="Production SLO",
        certification_warning_minutes=20000,
    )
    policy = updated.body["policy"]
    assert policy["scope_type"] == "channel"
    assert policy["scope_value"] == "channel-production"
    assert policy["channel_id"] == "channel-production"
    assert policy["active_scope_key"] == "channel:channel-production"
    engine.dispose()


def test_list_is_read_scoped_paginated_and_rejects_tampered_digest(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _create(engine, key="slo-list-global")
    risk = _create(engine, key="slo-list-risk", scope_type="risk_tier", scope_value="high")
    channel = _create(
        engine,
        key="slo-list-channel",
        scope_type="channel",
        scope_value="channel-testing",
        channel_id="channel-testing",
    )
    _update(
        engine,
        policy_id=risk.body["policy"]["id"],
        key="slo-list-disable",
        status="disabled",
    )

    first = list_quality_slo_policies(engine, tenant_id="tenant-a", actor_id="member-a", limit=2)
    assert first.status == 200
    assert len(first.body["items"]) == 2
    assert first.body["next_cursor"]
    second = list_quality_slo_policies(
        engine,
        tenant_id="tenant-a",
        actor_id="member-a",
        cursor=first.body["next_cursor"],
        limit=2,
    )
    assert second.status == 200
    assert not {item["id"] for item in first.body["items"]} & {
        item["id"] for item in second.body["items"]
    }
    assert {item["id"] for item in first.body["items"] + second.body["items"]} == {
        item["id"]
        for item in list_quality_slo_policies(
            engine, tenant_id="tenant-a", actor_id="member-a", limit=10
        ).body["items"]
    }
    disabled = list_quality_slo_policies(
        engine, tenant_id="tenant-a", actor_id="member-a", status="disabled"
    )
    assert [item["id"] for item in disabled.body["items"]] == [risk.body["policy"]["id"]]
    assert [
        item["id"]
        for item in list_quality_slo_policies(
            engine,
            tenant_id="tenant-a",
            actor_id="member-a",
            scope_type="channel",
            scope_value="channel-testing",
        ).body["items"]
    ] == [channel.body["policy"]["id"]]

    with pytest.raises(ReleaseQualityInvalid, match="cursor"):
        list_quality_slo_policies(
            engine,
            tenant_id="tenant-a",
            actor_id="member-a",
            cursor="not-a-valid-cursor",
        )

    with Session(engine) as session:
        row = session.scalar(
            select(TenantReleaseQualitySloPolicy).where(
                TenantReleaseQualitySloPolicy.tenant_id == "tenant-a",
                TenantReleaseQualitySloPolicy.scope_type == "global",
            )
        )
        assert row is not None
        row.policy_digest = "a" * 64
        session.commit()
    with pytest.raises(ReleaseQualityUnavailable, match="digest"):
        list_quality_slo_policies(engine, tenant_id="tenant-a", actor_id="member-a")
    engine.dispose()
