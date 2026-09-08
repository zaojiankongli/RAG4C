from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint

import models.orm as orm

TABLES = {
    "tenant_release_quality_slo_policies": "TenantReleaseQualitySloPolicy",
    "tenant_release_quality_scan_schedules": "TenantReleaseQualityScanSchedule",
    "tenant_release_quality_scan_runs": "TenantReleaseQualityScanRun",
    "dataset_release_quality_observations": "DatasetReleaseQualityObservation",
    "dataset_release_quality_alerts": "DatasetReleaseQualityAlert",
    "dataset_release_recertification_jobs": "DatasetReleaseRecertificationJob",
}


def test_quality_operations_orm_models_exist_with_enterprise_constraints() -> None:
    for table_name, class_name in TABLES.items():
        model = getattr(orm, class_name, None)
        assert model is not None, class_name
        table = orm.Base.metadata.tables[table_name]
        assert table is model.__table__
        assert any(isinstance(item, UniqueConstraint) for item in table.constraints)
        assert any(isinstance(item, CheckConstraint) for item in table.constraints)
        assert any(isinstance(item, Index) for item in table.indexes)
        for fk in (item for item in table.constraints if isinstance(item, ForeignKeyConstraint)):
            assert tuple(fk.column_keys)[0] == "tenant_id", (table_name, fk.name)
            target_columns = tuple(
                element.target_fullname.split(".")[-1] for element in fk.elements
            )
            if fk.referred_table.name == "tenants":
                assert target_columns == ("id",)
            else:
                assert target_columns[0] == "tenant_id"


def _check_sql(table_name: str, constraint_name: str) -> str:
    table = orm.Base.metadata.tables[table_name]
    constraint = next(
        item
        for item in table.constraints
        if isinstance(item, CheckConstraint) and item.name == constraint_name
    )
    return str(constraint.sqltext.compile(compile_kwargs={"literal_binds": True})).casefold()


def test_slo_policy_uses_exact_integer_thresholds_and_canonical_scope() -> None:
    table = orm.Base.metadata.tables["tenant_release_quality_slo_policies"]
    for name in (
        "certification_warning_minutes",
        "certification_critical_minutes",
        "waiver_warning_minutes",
        "max_open_alerts",
        "revision",
    ):
        assert name in table.c
        assert table.c[name].type.python_type is int
    thresholds = _check_sql(
        "tenant_release_quality_slo_policies",
        "ck_tenant_release_quality_slo_policies_thresholds",
    )
    assert "certification_warning_minutes > certification_critical_minutes" in thresholds
    canonical = _check_sql(
        "tenant_release_quality_slo_policies",
        "ck_tenant_release_quality_slo_policies_active_scope_key",
    )
    assert "global:*" in canonical
    assert "risk_tier:" in canonical
    assert "channel:" in canonical


def test_scan_run_and_job_declare_lease_fenced_terminal_states() -> None:
    scan_run = _check_sql(
        "tenant_release_quality_scan_runs",
        "ck_tenant_release_quality_scan_runs_execution_state",
    )
    assert "claim_owner" in scan_run
    assert "claim_lease_until" in scan_run
    assert "heartbeat_at" in scan_run
    terminal = _check_sql(
        "tenant_release_quality_scan_runs",
        "ck_tenant_release_quality_scan_runs_terminal_state",
    )
    assert "finished_at" in terminal
    assert "claim_owner is null" in terminal

    job = _check_sql(
        "dataset_release_recertification_jobs",
        "ck_dataset_release_recertification_jobs_execution_state",
    )
    assert "claim_owner" in job
    assert "claim_lease_until" in job
    assert "heartbeat_at" in job
    assert "ready_to_certify" in _check_sql(
        "dataset_release_recertification_jobs",
        "ck_dataset_release_recertification_jobs_status",
    )


def test_observation_is_body_free_and_alert_job_have_active_keys() -> None:
    observation = orm.Base.metadata.tables["dataset_release_quality_observations"]
    forbidden = {
        "query",
        "query_text",
        "result_body",
        "judgment_note",
        "ticket",
        "idempotency_key",
    }
    assert forbidden.isdisjoint(observation.c.keys())
    assert observation.c["minutes_to_certification_expiry"].type.python_type is int
    assert observation.c["minutes_to_waiver_expiry"].type.python_type is int

    alert = orm.Base.metadata.tables["dataset_release_quality_alerts"]
    assert "active_alert_key" in alert.c
    assert "resolved_by" in _check_sql(
        "dataset_release_quality_alerts", "ck_dataset_release_quality_alerts_lifecycle"
    )

    schedule = orm.Base.metadata.tables["tenant_release_quality_scan_schedules"]
    run = orm.Base.metadata.tables["tenant_release_quality_scan_runs"]
    assert "dataset_id" in schedule.c
    assert "dataset_id" in run.c

    job = orm.Base.metadata.tables["dataset_release_recertification_jobs"]
    assert "active_job_key" in job.c
    assert "cycle_key" in job.c
    forbidden_job = {"idempotency_key", "query", "result_body", "judgment_note", "ticket"}
    assert forbidden_job.isdisjoint(job.c.keys())
