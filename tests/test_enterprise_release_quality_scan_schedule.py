from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    event,
    text,
)
from sqlalchemy.pool import StaticPool

from core.enterprise_release_quality_scheduler import (
    QualityScheduleConflict,
    QualityScheduleForbidden,
    QualityScheduleRevisionConflict,
    QualityScheduleValidation,
    archive_quality_scan_schedule,
    create_quality_scan_schedule,
    next_quality_scan_slot,
    pause_quality_scan_schedule,
    resume_quality_scan_schedule,
    update_quality_scan_schedule,
)

NOW = datetime(2026, 8, 28, 12, 0, 0)


@pytest.fixture
def operations_db() -> Any:
    metadata = MetaData()
    tenants = Table(
        "tenants",
        metadata,
        *[
            # Keep this focused fixture independent of the not-yet-landed ORM
            # refinement while exercising the same 0031 table contract.
            Column("id", String(64), primary_key=True),
            Column("name", String(128), nullable=False),
            Column("status", String(16), nullable=False),
        ],
    )
    accounts = Table(
        "accounts",
        metadata,
        Column("id", String(64), primary_key=True),
        Column("name", String(128), nullable=False),
        Column("email", String(256), nullable=False),
    )
    tenant_members = Table(
        "tenant_members",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("tenant_id", String(64), nullable=False),
        Column("account_id", String(64), nullable=False),
        Column("role", String(16), nullable=False),
        Column("status", String(16), nullable=False),
    )
    datasets = Table(
        "datasets",
        metadata,
        Column("id", String(64), primary_key=True),
        Column("tenant_id", String(64), nullable=False),
        Column("owner_id", String(64), nullable=False),
        Column("name", String(128), nullable=False),
        Column("status", String(16), nullable=False),
        Column("acl_mode", String(16), nullable=False),
        Column("acl_revision", Integer, nullable=False),
    )
    policies = Table(
        "tenant_release_quality_slo_policies",
        metadata,
        Column("id", String(64), primary_key=True),
        Column("tenant_id", String(64), nullable=False),
        Column("status", String(16), nullable=False),
        Column("active_scope_key", String(192), nullable=True),
        Column("revision", Integer, nullable=False),
    )
    _schedules = Table(
        "tenant_release_quality_scan_schedules",
        metadata,
        Column("id", String(64), primary_key=True),
        Column("tenant_id", String(64), nullable=False),
        Column("dataset_id", String(64), nullable=False),
        Column("slo_policy_id", String(64), nullable=False),
        Column("status", String(16), nullable=False),
        Column("active_policy_slot", String(64), nullable=True),
        Column("revision", Integer, nullable=False),
        Column("interval_seconds", Integer, nullable=False),
        Column("next_run_at", DateTime, nullable=False),
        Column("last_enqueued_at", DateTime, nullable=True),
        Column("created_at", DateTime, nullable=False),
        Column("created_by", String(64), nullable=False),
        Column("updated_at", DateTime, nullable=False),
        Column("updated_by", String(64), nullable=False),
        Column("paused_at", DateTime, nullable=True),
        Column("paused_by", String(64), nullable=True),
        Column("archived_at", DateTime, nullable=True),
        Column("archived_by", String(64), nullable=True),
        CheckConstraint(
            "status IN ('active','paused','archived')",
            name="ck_quality_scan_schedules_status",
        ),
        CheckConstraint(
            "interval_seconds BETWEEN 300 AND 604800",
            name="ck_quality_scan_schedules_interval",
        ),
    )
    _mutation_requests = Table(
        "tenant_control_mutation_requests",
        metadata,
        Column("id", String(64), primary_key=True),
        Column("tenant_id", String(64), nullable=False),
        Column("actor_id", String(64), nullable=False),
        Column("idempotency_key", String(64), nullable=False),
        Column("request_hash", String(64), nullable=False),
        Column("operation", String(128), nullable=False),
        Column("resource_type", String(64), nullable=False),
        Column("resource_id", String(64), nullable=True),
        Column("status", String(16), nullable=False),
        Column("response_json", JSON, nullable=True),
        Column("http_status", Integer, nullable=True),
        Column("created_at", DateTime, nullable=False),
        Column("completed_at", DateTime, nullable=True),
    )
    _audit_events = Table(
        "tenant_audit_events",
        metadata,
        Column("sequence", Integer, primary_key=True, autoincrement=True),
        Column("id", String(64), nullable=False),
        Column("tenant_id", String(64), nullable=False),
        Column("actor_id", String(64), nullable=False),
        Column("actor_name_snapshot", String(128), nullable=False),
        Column("actor_email_snapshot", String(256), nullable=False),
        Column("action", String(128), nullable=False),
        Column("resource_type", String(64), nullable=False),
        Column("resource_id", String(512), nullable=False),
        Column("target_account_id", String(64), nullable=True),
        Column("before_snapshot", JSON, nullable=True),
        Column("after_snapshot", JSON, nullable=True),
        Column("request_id", String(128), nullable=False),
        Column("request_ip", String(64), nullable=False),
        Column("occurred_at", DateTime, nullable=False),
    )
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _configure(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            tenants.insert(),
            [
                {"id": "tenant-a", "name": "Tenant A", "status": "active"},
                {"id": "tenant-b", "name": "Tenant B", "status": "active"},
            ],
        )
        connection.execute(
            accounts.insert(),
            [
                {"id": "owner-a", "name": "Owner A", "email": "owner-a@example.test"},
                {"id": "admin-a", "name": "Admin A", "email": "admin-a@example.test"},
                {"id": "editor-a", "name": "Editor A", "email": "editor-a@example.test"},
                {"id": "owner-b", "name": "Owner B", "email": "owner-b@example.test"},
            ],
        )
        connection.execute(
            tenant_members.insert(),
            [
                {
                    "tenant_id": "tenant-a",
                    "account_id": "owner-a",
                    "role": "owner",
                    "status": "active",
                },
                {
                    "tenant_id": "tenant-a",
                    "account_id": "admin-a",
                    "role": "admin",
                    "status": "active",
                },
                {
                    "tenant_id": "tenant-a",
                    "account_id": "editor-a",
                    "role": "editor",
                    "status": "active",
                },
                {
                    "tenant_id": "tenant-b",
                    "account_id": "owner-b",
                    "role": "owner",
                    "status": "active",
                },
            ],
        )
        connection.execute(
            datasets.insert(),
            [
                {
                    "id": "dataset-a",
                    "tenant_id": "tenant-a",
                    "owner_id": "owner-a",
                    "name": "Dataset A",
                    "status": "active",
                    "acl_mode": "tenant_role",
                    "acl_revision": 1,
                },
                {
                    "id": "dataset-a2",
                    "tenant_id": "tenant-a",
                    "owner_id": "owner-a",
                    "name": "Dataset A2",
                    "status": "active",
                    "acl_mode": "tenant_role",
                    "acl_revision": 1,
                },
                {
                    "id": "dataset-b",
                    "tenant_id": "tenant-b",
                    "owner_id": "owner-b",
                    "name": "Dataset B",
                    "status": "active",
                    "acl_mode": "tenant_role",
                    "acl_revision": 1,
                },
            ],
        )
        connection.execute(
            policies.insert(),
            [
                {
                    "id": "policy-a",
                    "tenant_id": "tenant-a",
                    "status": "active",
                    "active_scope_key": "global:*",
                    "revision": 2,
                },
                {
                    "id": "policy-disabled",
                    "tenant_id": "tenant-a",
                    "status": "disabled",
                    "active_scope_key": None,
                    "revision": 3,
                },
                {
                    "id": "policy-b",
                    "tenant_id": "tenant-b",
                    "status": "active",
                    "active_scope_key": "global:*",
                    "revision": 1,
                },
            ],
        )
    try:
        yield engine
    finally:
        engine.dispose()


def _create(engine: Any, *, key: str = "create-key", dataset_id: str = "dataset-a"):
    return create_quality_scan_schedule(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id=dataset_id,
        slo_policy_id="policy-a",
        interval_seconds=3600,
        reason="establish quality SLO scan",
        idempotency_key=key,
        request_id=f"request-{dataset_id}",
        request_ip="127.0.0.1",
        now=NOW,
    )


def test_next_quality_scan_slot_is_utc_exact_and_fixed_interval() -> None:
    planned = datetime(2026, 8, 28, 0, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 8, 28, 9, 40, 0, 999999, tzinfo=timezone.utc)
    assert next_quality_scan_slot(planned, now, 300) == datetime(2026, 8, 28, 9, 45, 0)
    assert next_quality_scan_slot(
        datetime(2026, 8, 28, 8, 0, tzinfo=timezone(timedelta(hours=8))),
        datetime(2026, 8, 28, 17, 0, tzinfo=timezone(timedelta(hours=8))),
        604800,
    ) == datetime(2026, 9, 4, 0, 0)
    with pytest.raises(QualityScheduleValidation):
        next_quality_scan_slot(planned, now, True)
    with pytest.raises(QualityScheduleValidation):
        next_quality_scan_slot(planned, now, 299)
    with pytest.raises(QualityScheduleValidation):
        next_quality_scan_slot(planned, now, 604801)


def test_schedule_mutations_require_dataset_scope_and_disabled_policy_cannot_activate(
    operations_db: Any,
) -> None:
    with pytest.raises(QualityScheduleValidation, match="dataset_id"):
        create_quality_scan_schedule(
            operations_db,
            tenant_id="tenant-a",
            actor_id="owner-a",
            dataset_id="",
            slo_policy_id="policy-a",
            interval_seconds=3600,
            reason="missing dataset scope",
            idempotency_key="missing-dataset-key",
            request_id="missing-dataset-request",
            now=NOW,
        )
    with pytest.raises(QualityScheduleConflict, match="disabled"):
        create_quality_scan_schedule(
            operations_db,
            tenant_id="tenant-a",
            actor_id="owner-a",
            dataset_id="dataset-a",
            slo_policy_id="policy-disabled",
            interval_seconds=3600,
            reason="disabled policy must not serve",
            idempotency_key="disabled-policy-key",
            request_id="disabled-policy-request",
            now=NOW,
        )
    with operations_db.connect() as connection:
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM tenant_release_quality_scan_schedules")
            ).scalar_one()
            == 0
        )


def test_create_is_dataset_scoped_audited_and_safely_replayed(operations_db: Any) -> None:
    created = _create(operations_db)
    assert created.status == 201
    schedule = created.body["schedule"]
    assert schedule["tenant_id"] == "tenant-a"
    assert schedule["dataset_id"] == "dataset-a"
    assert schedule["slo_policy_id"] == "policy-a"
    assert schedule["status"] == "active"
    assert schedule["active_policy_slot"] == "policy-a"
    assert schedule["revision"] == 1
    assert schedule["next_run_at"] == "2026-08-28T13:00:00.000000Z"

    replay = _create(operations_db)
    assert replay.status == 201
    assert replay.body == created.body

    with operations_db.connect() as connection:
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM tenant_release_quality_scan_schedules")
            ).scalar_one()
            == 1
        )
        ledger = (
            connection.execute(
                text(
                    "SELECT idempotency_key, request_hash, resource_id, response_json "
                    "FROM tenant_control_mutation_requests"
                )
            )
            .mappings()
            .one()
        )
        assert "create-key" not in str(dict(ledger))
        assert ledger["resource_id"] == schedule["id"]
        actions = list(
            connection.execute(
                text(
                    "SELECT action FROM tenant_audit_events "
                    "WHERE tenant_id='tenant-a' ORDER BY sequence"
                )
            ).scalars()
        )
    assert actions == ["quality_scan_schedule.created"]

    with pytest.raises(QualityScheduleConflict, match="idempotency"):
        create_quality_scan_schedule(
            operations_db,
            tenant_id="tenant-a",
            actor_id="owner-a",
            dataset_id="dataset-a",
            slo_policy_id="policy-a",
            interval_seconds=7200,
            reason="same key different semantic request",
            idempotency_key="create-key",
            request_id="request-conflict",
            now=NOW,
        )
    with pytest.raises(QualityScheduleConflict, match="active schedule"):
        _create(operations_db, key="second-key")

    second_dataset = _create(operations_db, key="second-dataset-key", dataset_id="dataset-a2")
    assert second_dataset.body["schedule"]["dataset_id"] == "dataset-a2"


def test_update_pause_resume_archive_are_revision_fenced_and_lifecycle_safe(
    operations_db: Any,
) -> None:
    created = _create(operations_db)
    schedule_id = created.body["schedule"]["id"]

    with pytest.raises(QualityScheduleForbidden):
        update_quality_scan_schedule(
            operations_db,
            tenant_id="tenant-a",
            actor_id="editor-a",
            dataset_id="dataset-a",
            schedule_id=schedule_id,
            expected_revision=1,
            interval_seconds=1800,
            reason="editor cannot mutate schedule",
            idempotency_key="editor-update-key",
            request_id="editor-update-request",
            now=NOW,
        )
    with pytest.raises(QualityScheduleRevisionConflict):
        update_quality_scan_schedule(
            operations_db,
            tenant_id="tenant-a",
            actor_id="admin-a",
            dataset_id="dataset-a",
            schedule_id=schedule_id,
            expected_revision=2,
            interval_seconds=1800,
            reason="stale revision",
            idempotency_key="stale-update-key",
            request_id="stale-update-request",
            now=NOW,
        )

    updated = update_quality_scan_schedule(
        operations_db,
        tenant_id="tenant-a",
        actor_id="admin-a",
        dataset_id="dataset-a",
        schedule_id=schedule_id,
        expected_revision=1,
        interval_seconds=1800,
        reason="tighten quality scan interval",
        idempotency_key="update-key",
        request_id="update-request",
        now=NOW + timedelta(minutes=1),
    )
    assert updated.body["schedule"]["revision"] == 2
    assert updated.body["schedule"]["interval_seconds"] == 1800
    assert updated.body["schedule"]["next_run_at"] == "2026-08-28T12:31:00.000000Z"

    paused = pause_quality_scan_schedule(
        operations_db,
        tenant_id="tenant-a",
        actor_id="admin-a",
        dataset_id="dataset-a",
        schedule_id=schedule_id,
        expected_revision=2,
        reason="pause for maintenance",
        idempotency_key="pause-key",
        request_id="pause-request",
        now=NOW + timedelta(minutes=2),
    )
    assert paused.body["schedule"]["status"] == "paused"
    assert paused.body["schedule"]["active_policy_slot"] is None
    assert paused.body["schedule"]["paused_by"] == "admin-a"
    assert paused.body["schedule"]["revision"] == 3

    pause_replay = pause_quality_scan_schedule(
        operations_db,
        tenant_id="tenant-a",
        actor_id="admin-a",
        dataset_id="dataset-a",
        schedule_id=schedule_id,
        expected_revision=2,
        reason="pause for maintenance",
        idempotency_key="pause-key",
        request_id="pause-retry-request",
        now=NOW + timedelta(minutes=99),
    )
    assert pause_replay.body == paused.body

    resumed = resume_quality_scan_schedule(
        operations_db,
        tenant_id="tenant-a",
        actor_id="admin-a",
        dataset_id="dataset-a",
        schedule_id=schedule_id,
        expected_revision=3,
        reason="resume after maintenance",
        idempotency_key="resume-key",
        request_id="resume-request",
        now=NOW + timedelta(minutes=3),
    )
    assert resumed.body["schedule"]["status"] == "active"
    assert resumed.body["schedule"]["active_policy_slot"] == "policy-a"
    assert resumed.body["schedule"]["paused_at"] is None
    assert resumed.body["schedule"]["next_run_at"] == "2026-08-28T12:33:00.000000Z"
    assert resumed.body["schedule"]["revision"] == 4

    archived = archive_quality_scan_schedule(
        operations_db,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        schedule_id=schedule_id,
        expected_revision=4,
        reason="retire quality schedule",
        idempotency_key="archive-key",
        request_id="archive-request",
        now=NOW + timedelta(minutes=4),
    )
    assert archived.body["schedule"]["status"] == "archived"
    assert archived.body["schedule"]["active_policy_slot"] is None
    assert archived.body["schedule"]["archived_by"] == "owner-a"
    assert archived.body["schedule"]["revision"] == 5

    with pytest.raises(QualityScheduleConflict, match="archived"):
        update_quality_scan_schedule(
            operations_db,
            tenant_id="tenant-a",
            actor_id="admin-a",
            dataset_id="dataset-a",
            schedule_id=schedule_id,
            expected_revision=5,
            interval_seconds=3600,
            reason="archived schedule cannot update",
            idempotency_key="archived-update-key",
            request_id="archived-update-request",
            now=NOW,
        )


def test_cross_tenant_dataset_scope_cannot_read_or_mutate_schedule(operations_db: Any) -> None:
    created = _create(operations_db)
    schedule_id = created.body["schedule"]["id"]
    with pytest.raises(QualityScheduleForbidden):
        pause_quality_scan_schedule(
            operations_db,
            tenant_id="tenant-b",
            actor_id="owner-b",
            dataset_id="dataset-a",
            schedule_id=schedule_id,
            expected_revision=1,
            reason="cross tenant attempt",
            idempotency_key="cross-tenant-key",
            request_id="cross-tenant-request",
            now=NOW,
        )
    with operations_db.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT tenant_id, dataset_id, status, revision "
                    "FROM tenant_release_quality_scan_schedules"
                )
            )
            .mappings()
            .one()
        )
    assert dict(row) == {
        "tenant_id": "tenant-a",
        "dataset_id": "dataset-a",
        "status": "active",
        "revision": 1,
    }


def test_active_schedule_uniqueness_is_dataset_and_policy_scoped(operations_db: Any) -> None:
    first = _create(operations_db, key="dataset-a-key", dataset_id="dataset-a")
    second = _create(operations_db, key="dataset-a2-key", dataset_id="dataset-a2")
    assert first.body["schedule"]["id"] != second.body["schedule"]["id"]
    with operations_db.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT tenant_id, dataset_id, slo_policy_id, active_policy_slot "
                    "FROM tenant_release_quality_scan_schedules ORDER BY dataset_id"
                )
            )
            .mappings()
            .all()
        )
    assert [dict(row) for row in rows] == [
        {
            "tenant_id": "tenant-a",
            "dataset_id": "dataset-a",
            "slo_policy_id": "policy-a",
            "active_policy_slot": "policy-a",
        },
        {
            "tenant_id": "tenant-a",
            "dataset_id": "dataset-a2",
            "slo_policy_id": "policy-a",
            "active_policy_slot": "policy-a",
        },
    ]
