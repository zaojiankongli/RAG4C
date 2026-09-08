from __future__ import annotations

from importlib import import_module
from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import exc as sa_exc
from sqlalchemy import inspect, text

from tests.test_enterprise_knowledge_base_release_migration import (
    alembic_config,
    engine_for,
    seed_0028,
    sqlite_url,
    upgrade_0029,
)
from tests.test_enterprise_release_quality_migration import upgrade_0030

REVISION = "0031_enterprise_release_quality_operations"
DOWN_REVISION = "0030_enterprise_release_quality_certification"
TABLES = {
    "tenant_release_quality_slo_policies",
    "tenant_release_quality_scan_schedules",
    "tenant_release_quality_scan_runs",
    "dataset_release_quality_observations",
    "dataset_release_quality_alerts",
    "dataset_release_recertification_jobs",
}
EXPECTED_COLUMNS = {
    "tenant_release_quality_slo_policies": (
        "id",
        "tenant_id",
        "name",
        "scope_type",
        "scope_value",
        "channel_id",
        "active_scope_key",
        "status",
        "revision",
        "certification_warning_minutes",
        "certification_critical_minutes",
        "waiver_warning_minutes",
        "max_open_alerts",
        "auto_queue_recertification",
        "require_passing_certification",
        "allow_active_waiver",
        "policy_digest",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "disabled_at",
        "disabled_by",
    ),
    "tenant_release_quality_scan_schedules": (
        "id",
        "tenant_id",
        "dataset_id",
        "slo_policy_id",
        "status",
        "active_policy_slot",
        "revision",
        "interval_seconds",
        "next_run_at",
        "last_enqueued_at",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "paused_at",
        "paused_by",
        "archived_at",
        "archived_by",
    ),
    "tenant_release_quality_scan_runs": (
        "id",
        "tenant_id",
        "dataset_id",
        "schedule_id",
        "slo_policy_id",
        "slo_policy_revision",
        "status",
        "planned_at",
        "claim_owner",
        "claim_lease_until",
        "heartbeat_at",
        "attempt_count",
        "max_attempts",
        "next_attempt_at",
        "started_at",
        "finished_at",
        "observation_count",
        "alert_count",
        "recertification_job_count",
        "idempotency_key_digest",
        "request_hash",
        "summary_digest",
        "safe_error_code",
        "safe_error",
        "created_at",
        "updated_at",
    ),
    "dataset_release_quality_observations": (
        "id",
        "tenant_id",
        "dataset_id",
        "release_id",
        "channel_id",
        "scan_run_id",
        "slo_policy_id",
        "slo_policy_revision",
        "release_role",
        "gate_state",
        "gate_reason",
        "certification_id",
        "certification_digest",
        "certification_valid_until",
        "waiver_id",
        "waiver_digest",
        "waiver_expires_at",
        "minutes_to_certification_expiry",
        "minutes_to_waiver_expiry",
        "severity",
        "observation_digest",
        "observed_at",
        "observed_by",
        "request_id",
    ),
    "dataset_release_quality_alerts": (
        "id",
        "tenant_id",
        "dataset_id",
        "release_id",
        "channel_id",
        "release_role",
        "alert_type",
        "severity",
        "status",
        "active_alert_key",
        "revision",
        "source_observation_id",
        "source_observation_digest",
        "occurrence_count",
        "opened_at",
        "last_observed_at",
        "acknowledged_at",
        "acknowledged_by",
        "acknowledged_comment",
        "resolved_at",
        "resolved_by",
        "resolved_comment",
        "suppressed_until",
        "suppressed_by",
        "suppressed_comment",
        "created_at",
        "updated_at",
    ),
    "dataset_release_recertification_jobs": (
        "id",
        "tenant_id",
        "dataset_id",
        "release_id",
        "channel_id",
        "release_role",
        "baseline_id",
        "policy_id",
        "policy_revision",
        "slo_policy_id",
        "slo_policy_revision",
        "trigger",
        "status",
        "active_job_key",
        "cycle_key",
        "expected_manifest_digest",
        "expected_evidence_digest",
        "expected_channel_revision",
        "claim_owner",
        "claim_lease_until",
        "heartbeat_at",
        "attempt_count",
        "max_attempts",
        "next_attempt_at",
        "result_certification_id",
        "idempotency_key_digest",
        "request_hash",
        "safe_error_code",
        "safe_error",
        "created_at",
        "created_by",
        "updated_at",
        "completed_at",
        "cancelled_at",
        "cancelled_by",
        "request_id",
        "reason",
    ),
}


def migration_module():
    try:
        return import_module(
            "catalog_migrations.versions.0031_enterprise_release_quality_operations"
        )
    except ModuleNotFoundError as exc:  # RED: implementation intentionally does not exist yet.
        pytest.fail(f"Stage 21 migration is missing: {exc}")


def upgrade_0031(url: str) -> None:
    command.upgrade(alembic_config(url), REVISION)


def _upgrade_fixture(url: str) -> None:
    seed_0028(url)
    upgrade_0029(url)
    upgrade_0030(url)
    upgrade_0031(url)


def _insert_slo_policy(connection, *, policy_id: str = "slo-policy-a") -> None:
    connection.execute(
        text(
            "INSERT INTO tenant_release_quality_slo_policies "
            "(id,tenant_id,name,scope_type,scope_value,channel_id,active_scope_key,status,revision,"
            "certification_warning_minutes,certification_critical_minutes,waiver_warning_minutes,"
            "max_open_alerts,auto_queue_recertification,require_passing_certification,"
            "allow_active_waiver,policy_digest,created_at,created_by,updated_at,updated_by) VALUES "
            "(:id,'tenant-a','Enterprise SLO','global','*',NULL,'global:*','active',1,"
            "10080,1440,720,100,1,1,1,:digest,CURRENT_TIMESTAMP,'owner-a',CURRENT_TIMESTAMP,'owner-a')"
        ),
        {"id": policy_id, "digest": "a" * 64},
    )


def _insert_schedule(connection, *, schedule_id: str = "quality-schedule-a") -> None:
    connection.execute(
        text(
            "INSERT INTO tenant_release_quality_scan_schedules "
            "(id,tenant_id,dataset_id,slo_policy_id,status,active_policy_slot,revision,interval_seconds,"
            "next_run_at,created_at,created_by,updated_at,updated_by) VALUES "
            "(:id,'tenant-a','dataset-a','slo-policy-a','active','slo-policy-a',1,3600,"
            "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'owner-a',CURRENT_TIMESTAMP,'owner-a')"
        ),
        {"id": schedule_id},
    )


def _insert_run(connection, *, run_id: str = "quality-run-a", status: str = "pending") -> None:
    connection.execute(
        text(
            "INSERT INTO tenant_release_quality_scan_runs "
            "(id,tenant_id,dataset_id,schedule_id,slo_policy_id,slo_policy_revision,status,planned_at,"
            "attempt_count,max_attempts,observation_count,alert_count,recertification_job_count,"
            "idempotency_key_digest,request_hash,created_at,updated_at) VALUES "
            "(:id,'tenant-a','dataset-a','quality-schedule-a','slo-policy-a',1,:status,"
            "'2026-08-28 12:00:00.000000',0,3,0,0,0,:digest,:digest,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        ),
        {"id": run_id, "status": status, "digest": "b" * 64},
    )


def _normalized_checks(inspector, table: str) -> dict[str, str]:
    return {
        str(item["name"]): " ".join(str(item.get("sqltext") or "").casefold().split())
        for item in inspector.get_check_constraints(table)
    }


def test_0031_is_head_and_follows_quality_certification() -> None:
    migration = migration_module()
    from alembic.script import ScriptDirectory

    scripts = ScriptDirectory.from_config(alembic_config("sqlite://"))
    assert scripts.get_current_head() == REVISION
    assert migration.revision == REVISION
    assert migration.down_revision == DOWN_REVISION


def test_upgrade_creates_exact_six_table_contract(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "quality-operations-schema.db")
    _upgrade_fixture(url)
    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        assert TABLES <= set(inspector.get_table_names())
        for table, expected in EXPECTED_COLUMNS.items():
            assert tuple(item["name"] for item in inspector.get_columns(table)) == expected
        assert (
            engine.connect().execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == REVISION
        )
    finally:
        engine.dispose()


def test_every_operations_fk_is_tenant_leading_and_targets_a_unique_identity(
    tmp_path: Path,
) -> None:
    url = sqlite_url(tmp_path / "quality-operations-fks.db")
    _upgrade_fixture(url)
    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        for table in TABLES:
            for fk in inspector.get_foreign_keys(table):
                constrained = tuple(fk.get("constrained_columns") or ())
                referred = tuple(fk.get("referred_columns") or ())
                assert constrained and constrained[0] == "tenant_id", (table, fk)
                target = str(fk["referred_table"])
                if target == "tenants":
                    assert referred == ("id",), (table, fk)
                else:
                    assert referred and referred[0] == "tenant_id", (table, fk)
                identities = {
                    tuple(item.get("column_names") or ())
                    for item in inspector.get_unique_constraints(target)
                }
                pk = tuple(
                    (inspector.get_pk_constraint(target) or {}).get("constrained_columns") or ()
                )
                if pk:
                    identities.add(pk)
                assert referred in identities, (table, fk, identities)
    finally:
        engine.dispose()


def test_policy_schedule_run_constraints_are_fail_closed(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "quality-operations-state.db")
    _upgrade_fixture(url)
    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        policy_checks = _normalized_checks(inspector, "tenant_release_quality_slo_policies")
        assert (
            "certification_warning_minutes > certification_critical_minutes"
            in policy_checks["ck_tenant_release_quality_slo_policies_thresholds"]
        )
        assert (
            "active_scope_key"
            in policy_checks["ck_tenant_release_quality_slo_policies_active_scope_key"]
        )
        run_checks = _normalized_checks(inspector, "tenant_release_quality_scan_runs")
        assert (
            "claim_lease_until" in run_checks["ck_tenant_release_quality_scan_runs_execution_state"]
        )
        assert "finished_at" in run_checks["ck_tenant_release_quality_scan_runs_terminal_state"]

        with engine.begin() as connection:
            _insert_slo_policy(connection)
            _insert_schedule(connection)
            _insert_run(connection)

        with pytest.raises(sa_exc.IntegrityError):
            with engine.begin() as connection:
                _insert_slo_policy(connection, policy_id="slo-policy-duplicate")
        with pytest.raises(sa_exc.IntegrityError):
            with engine.begin() as connection:
                _insert_schedule(connection, schedule_id="quality-schedule-duplicate")
        with pytest.raises(sa_exc.IntegrityError):
            with engine.begin() as connection:
                _insert_run(connection, run_id="quality-run-duplicate")
        with pytest.raises(sa_exc.IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE tenant_release_quality_scan_runs SET status='completed', "
                        "claim_owner='worker-a', claim_lease_until=CURRENT_TIMESTAMP "
                        "WHERE id='quality-run-a'"
                    )
                )
    finally:
        engine.dispose()


def test_observation_guards_and_alert_job_active_authority_are_declared(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "quality-operations-guards.db")
    _upgrade_fixture(url)
    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        trigger_names = {
            str(row[0])
            for row in engine.connect()
            .execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='trigger' "
                    "AND tbl_name='dataset_release_quality_observations'"
                )
            )
            .all()
        }
        assert {
            "trg_dataset_release_quality_observations_no_update",
            "trg_dataset_release_quality_observations_no_delete",
        } <= trigger_names

        alert_uniques = {
            item["name"]: tuple(item.get("column_names") or ())
            for item in inspector.get_unique_constraints("dataset_release_quality_alerts")
        }
        assert alert_uniques["uq_dataset_release_quality_alerts_active_key"] == (
            "tenant_id",
            "active_alert_key",
        )
        alert_checks = _normalized_checks(inspector, "dataset_release_quality_alerts")
        assert "resolved_by" in alert_checks["ck_dataset_release_quality_alerts_lifecycle"]
        assert "suppressed_until" in alert_checks["ck_dataset_release_quality_alerts_lifecycle"]

        job_uniques = {
            item["name"]: tuple(item.get("column_names") or ())
            for item in inspector.get_unique_constraints("dataset_release_recertification_jobs")
        }
        assert job_uniques["uq_dataset_release_recertification_jobs_active_key"] == (
            "tenant_id",
            "active_job_key",
        )
        job_checks = _normalized_checks(inspector, "dataset_release_recertification_jobs")
        assert "ready_to_certify" in job_checks["ck_dataset_release_recertification_jobs_status"]
        assert (
            "claim_lease_until"
            in job_checks["ck_dataset_release_recertification_jobs_execution_state"]
        )
    finally:
        engine.dispose()


def test_offline_mysql_postgresql_include_operations_ddl_and_sqlite_fails_closed() -> None:
    for url, marker in (
        ("mysql+pymysql://u:p@localhost/rag4c", "DATETIME(6)"),
        ("postgresql+psycopg://u:p@localhost/rag4c", "TIMESTAMP"),
    ):
        output = StringIO()
        command.upgrade(
            alembic_config(url, output_buffer=output), f"{DOWN_REVISION}:{REVISION}", sql=True
        )
        sql = output.getvalue().upper()
        for table in TABLES:
            assert f"CREATE TABLE {table.upper()}" in sql
        assert "ACTIVE_SCOPE_KEY" in sql
        assert "ACTIVE_ALERT_KEY" in sql
        assert "ACTIVE_JOB_KEY" in sql
        assert marker in sql
    with pytest.raises(Exception, match="online|SQLite|sqlite"):
        command.upgrade(
            alembic_config("sqlite:///quality-operations-offline.db", output_buffer=StringIO()),
            f"{DOWN_REVISION}:{REVISION}",
            sql=True,
        )


def test_clean_downgrade_restores_0030_and_nonempty_authority_blocks(tmp_path: Path) -> None:
    clean_url = sqlite_url(tmp_path / "quality-operations-clean-down.db")
    _upgrade_fixture(clean_url)
    command.downgrade(alembic_config(clean_url), DOWN_REVISION)
    clean_engine = engine_for(clean_url)
    try:
        assert TABLES.isdisjoint(inspect(clean_engine).get_table_names())
        assert (
            clean_engine.connect()
            .execute(text("SELECT version_num FROM alembic_version"))
            .scalar_one()
            == DOWN_REVISION
        )
    finally:
        clean_engine.dispose()

    blocked_url = sqlite_url(tmp_path / "quality-operations-blocked-down.db")
    _upgrade_fixture(blocked_url)
    blocked_engine = engine_for(blocked_url)
    with blocked_engine.begin() as connection:
        _insert_slo_policy(connection)
    blocked_engine.dispose()
    with pytest.raises(Exception, match="0031|quality|operations|SLO|slo"):
        command.downgrade(alembic_config(blocked_url), DOWN_REVISION)
