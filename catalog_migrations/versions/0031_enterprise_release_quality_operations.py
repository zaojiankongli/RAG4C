"""Add Release quality SLO operations, alerts and recertification authority.

Revision ID: 0031_enterprise_release_quality_operations
Revises: 0030_enterprise_release_quality_certification
Create Date: 2026-08-28
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import mysql

revision: str = "0031_enterprise_release_quality_operations"
down_revision: str | None = "0030_enterprise_release_quality_certification"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "tenant_release_quality_slo_policies",
    "tenant_release_quality_scan_schedules",
    "tenant_release_quality_scan_runs",
    "dataset_release_quality_observations",
    "dataset_release_quality_alerts",
    "dataset_release_recertification_jobs",
)
OBSERVATION_TABLE = "dataset_release_quality_observations"


def _datetime6():
    return (
        sa.DateTime()
        .with_variant(mysql.DATETIME(fsp=6), "mysql")
        .with_variant(mysql.DATETIME(fsp=6), "mariadb")
    )


def _dialect_name() -> str:
    return str(op.get_context().dialect.name).casefold()


def _canonical_scope_key_check():
    status = sa.column("status", sa.String(length=16))
    scope_type = sa.column("scope_type", sa.String(length=16))
    scope_value = sa.column("scope_value", sa.String(length=128))
    active_scope_key = sa.column("active_scope_key", sa.String(length=192))
    return sa.or_(
        status != "active",
        sa.and_(scope_type == "global", active_scope_key == "global:*"),
        sa.and_(
            scope_type == "risk_tier", active_scope_key == sa.literal("risk_tier:") + scope_value
        ),
        sa.and_(scope_type == "channel", active_scope_key == sa.literal("channel:") + scope_value),
    )


def _canonical_alert_key_check():
    status = sa.column("status", sa.String(length=16))
    active_key = sa.column("active_alert_key", sa.String(length=384))
    expected = (
        sa.column("dataset_id", sa.String(length=64))
        + sa.literal(":")
        + sa.column("release_id", sa.String(length=64))
        + sa.literal(":")
        + sa.column("channel_id", sa.String(length=128))
        + sa.literal(":")
        + sa.column("release_role", sa.String(length=16))
        + sa.literal(":")
        + sa.column("alert_type", sa.String(length=40))
    )
    return sa.or_(
        sa.and_(status == "resolved", active_key.is_(None)),
        sa.and_(status != "resolved", active_key == expected),
    )


def _canonical_job_key_check():
    status = sa.column("status", sa.String(length=24))
    active_key = sa.column("active_job_key", sa.String(length=384))
    expected = (
        sa.column("dataset_id", sa.String(length=64))
        + sa.literal(":")
        + sa.column("release_id", sa.String(length=64))
        + sa.literal(":")
        + sa.column("channel_id", sa.String(length=128))
        + sa.literal(":")
        + sa.column("release_role", sa.String(length=16))
        + sa.literal(":")
        + sa.column("policy_id", sa.String(length=64))
    )
    return sa.or_(
        sa.and_(status.in_(("completed", "failed", "cancelled")), active_key.is_(None)),
        sa.and_(
            status.in_(("pending", "claimed", "awaiting_evidence", "ready_to_certify")),
            active_key == expected,
        ),
    )


def _create_observation_guards() -> None:
    dialect = _dialect_name()
    if dialect == "sqlite":
        for operation in ("UPDATE", "DELETE"):
            name = f"trg_{OBSERVATION_TABLE}_no_{operation.casefold()}"
            op.execute(
                f"CREATE TRIGGER {name} BEFORE {operation} ON {OBSERVATION_TABLE} "
                f"BEGIN SELECT RAISE(ABORT, '{OBSERVATION_TABLE} are immutable'); END"
            )
    elif dialect in {"mysql", "mariadb"}:
        for operation in ("UPDATE", "DELETE"):
            name = f"trg_{OBSERVATION_TABLE}_no_{operation.casefold()}"
            op.execute(
                f"CREATE TRIGGER {name} BEFORE {operation} ON {OBSERVATION_TABLE} FOR EACH ROW "
                f"SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{OBSERVATION_TABLE} are immutable'"
            )
    elif dialect == "postgresql":
        op.execute(
            "CREATE FUNCTION rag4c_quality_observation_immutable() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Release quality observations are immutable'; END; $$"
        )
        for operation in ("UPDATE", "DELETE"):
            name = f"trg_{OBSERVATION_TABLE}_no_{operation.casefold()}"
            op.execute(
                f"CREATE TRIGGER {name} BEFORE {operation} ON {OBSERVATION_TABLE} "
                "FOR EACH ROW EXECUTE FUNCTION rag4c_quality_observation_immutable()"
            )


def _drop_observation_guards() -> None:
    dialect = _dialect_name()
    for operation in ("update", "delete"):
        name = f"trg_{OBSERVATION_TABLE}_no_{operation}"
        if dialect == "postgresql":
            op.execute(f"DROP TRIGGER IF EXISTS {name} ON {OBSERVATION_TABLE}")
        else:
            op.execute(f"DROP TRIGGER IF EXISTS {name}")
    if dialect == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS rag4c_quality_observation_immutable()")


def _guard_downgrade() -> None:
    connection = op.get_bind()
    counts = {
        table: int(connection.scalar(text(f"SELECT COUNT(*) FROM {table}")) or 0)
        for table in TABLES
    }
    if any(counts.values()):
        detail = ", ".join(f"{table}={count}" for table, count in counts.items() if count)
        raise RuntimeError(
            f"0031 downgrade blocked by Release quality operations authority: {detail}"
        )


def upgrade() -> None:
    if context.is_offline_mode() and _dialect_name() == "sqlite":
        raise RuntimeError("0031 SQLite offline upgrade is unsupported; use online migration")

    # trigger 是 MySQL 保留字：列名用 quoted_name 反引号；CHECK 文本里的引用
    # 也需按方言处理（SQLite 不认反引号，测试库用 SQLite 在线升级）。
    _is_mysql = _dialect_name() == "mysql"
    _trigger_check = "`trigger` IN ('manual','certification_warning','certification_expired','stale_evidence','alert_escalation')" if _is_mysql else "trigger IN ('manual','certification_warning','certification_expired','stale_evidence','alert_escalation')"

    op.create_table(
        "tenant_release_quality_slo_policies",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("scope_type", sa.String(16), nullable=False),
        sa.Column("scope_value", sa.String(128), nullable=False),
        sa.Column("channel_id", sa.String(128), nullable=True),
        sa.Column("active_scope_key", sa.String(192), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("certification_warning_minutes", sa.Integer(), nullable=False),
        sa.Column("certification_critical_minutes", sa.Integer(), nullable=False),
        sa.Column("waiver_warning_minutes", sa.Integer(), nullable=False),
        sa.Column("max_open_alerts", sa.Integer(), nullable=False),
        sa.Column("auto_queue_recertification", sa.Boolean(), nullable=False),
        sa.Column("require_passing_certification", sa.Boolean(), nullable=False),
        sa.Column("allow_active_waiver", sa.Boolean(), nullable=False),
        sa.Column("policy_digest", sa.String(64), nullable=False),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("updated_by", sa.String(64), nullable=False),
        sa.Column("disabled_at", _datetime6(), nullable=True),
        sa.Column("disabled_by", sa.String(64), nullable=True),
        sa.CheckConstraint(
            "scope_type IN ('global','risk_tier','channel')",
            name="ck_tenant_release_quality_slo_policies_scope_type",
        ),
        sa.CheckConstraint(
            "status IN ('active','disabled')", name="ck_tenant_release_quality_slo_policies_status"
        ),
        sa.CheckConstraint("revision > 0", name="ck_tenant_release_quality_slo_policies_revision"),
        sa.CheckConstraint(
            "certification_warning_minutes > certification_critical_minutes AND certification_critical_minutes > 0 "
            "AND waiver_warning_minutes > 0 AND max_open_alerts > 0",
            name="ck_tenant_release_quality_slo_policies_thresholds",
        ),
        sa.CheckConstraint(
            "auto_queue_recertification IN (false,true) AND require_passing_certification IN (false,true) AND allow_active_waiver IN (false,true)",
            name="ck_tenant_release_quality_slo_policies_flags",
        ),
        sa.CheckConstraint(
            "(scope_type='global' AND scope_value='*' AND channel_id IS NULL) OR "
            "(scope_type='risk_tier' AND scope_value IN ('low','medium','high') AND channel_id IS NULL) OR "
            "(scope_type='channel' AND channel_id IS NOT NULL AND scope_value=channel_id)",
            name="ck_tenant_release_quality_slo_policies_scope_binding",
        ),
        sa.CheckConstraint(
            _canonical_scope_key_check(),
            name="ck_tenant_release_quality_slo_policies_active_scope_key",
        ),
        sa.CheckConstraint(
            "(status='active' AND active_scope_key IS NOT NULL AND disabled_at IS NULL AND disabled_by IS NULL) OR "
            "(status='disabled' AND active_scope_key IS NULL AND disabled_at IS NOT NULL AND disabled_by IS NOT NULL)",
            name="ck_tenant_release_quality_slo_policies_lifecycle",
        ),
        sa.CheckConstraint(
            "length(policy_digest)=64 AND lower(policy_digest)=policy_digest",
            name="ck_tenant_release_quality_slo_policies_digest",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_quality_slo_policies_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "channel_id"],
            ["tenant_release_channels.tenant_id", "tenant_release_channels.id"],
            name="fk_quality_slo_policies_channel",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_tenant_release_quality_slo_policies_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "active_scope_key",
            name="uq_tenant_release_quality_slo_policies_active_scope",
        ),
    )
    op.create_index(
        "ix_quality_slo_policies_scope",
        "tenant_release_quality_slo_policies",
        ["tenant_id", "scope_type", "scope_value", "status", "id"],
    )
    op.create_index(
        "ix_quality_slo_policies_updated",
        "tenant_release_quality_slo_policies",
        ["tenant_id", "status", "updated_at", "id"],
    )

    op.create_table(
        "tenant_release_quality_scan_schedules",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("dataset_id", sa.String(64), nullable=False),
        sa.Column("slo_policy_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("active_policy_slot", sa.String(64), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("next_run_at", _datetime6(), nullable=False),
        sa.Column("last_enqueued_at", _datetime6(), nullable=True),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("updated_by", sa.String(64), nullable=False),
        sa.Column("paused_at", _datetime6(), nullable=True),
        sa.Column("paused_by", sa.String(64), nullable=True),
        sa.Column("archived_at", _datetime6(), nullable=True),
        sa.Column("archived_by", sa.String(64), nullable=True),
        sa.CheckConstraint(
            "status IN ('active','paused','archived')", name="ck_quality_scan_schedules_status"
        ),
        sa.CheckConstraint("revision > 0", name="ck_quality_scan_schedules_revision"),
        sa.CheckConstraint(
            "interval_seconds BETWEEN 300 AND 604800", name="ck_quality_scan_schedules_interval"
        ),
        sa.CheckConstraint(
            "(status='active' AND active_policy_slot=slo_policy_id AND paused_at IS NULL AND paused_by IS NULL AND archived_at IS NULL AND archived_by IS NULL) OR "
            "(status='paused' AND active_policy_slot IS NULL AND paused_at IS NOT NULL AND paused_by IS NOT NULL AND archived_at IS NULL AND archived_by IS NULL) OR "
            "(status='archived' AND active_policy_slot IS NULL AND archived_at IS NOT NULL AND archived_by IS NOT NULL)",
            name="ck_quality_scan_schedules_lifecycle",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_quality_scan_schedules_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_quality_scan_schedules_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "slo_policy_id"],
            [
                "tenant_release_quality_slo_policies.tenant_id",
                "tenant_release_quality_slo_policies.id",
            ],
            name="fk_quality_scan_schedules_policy",
        ),
        sa.UniqueConstraint(
            "tenant_id", "dataset_id", "id", name="uq_quality_scan_schedules_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "id",
            "slo_policy_id",
            name="uq_quality_scan_schedules_scope_policy_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "active_policy_slot",
            name="uq_quality_scan_schedules_active_policy",
        ),
    )
    op.create_index(
        "ix_quality_scan_schedules_due",
        "tenant_release_quality_scan_schedules",
        ["status", "next_run_at", "id"],
    )
    op.create_index(
        "ix_quality_scan_schedules_policy",
        "tenant_release_quality_scan_schedules",
        ["tenant_id", "dataset_id", "slo_policy_id", "status", "id"],
    )

    op.create_table(
        "tenant_release_quality_scan_runs",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("dataset_id", sa.String(64), nullable=False),
        sa.Column("schedule_id", sa.String(64), nullable=False),
        sa.Column("slo_policy_id", sa.String(64), nullable=False),
        sa.Column("slo_policy_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("planned_at", _datetime6(), nullable=False),
        sa.Column("claim_owner", sa.String(128), nullable=True),
        sa.Column("claim_lease_until", _datetime6(), nullable=True),
        sa.Column("heartbeat_at", _datetime6(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("next_attempt_at", _datetime6(), nullable=True),
        sa.Column("started_at", _datetime6(), nullable=True),
        sa.Column("finished_at", _datetime6(), nullable=True),
        sa.Column("observation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("alert_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recertification_job_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("idempotency_key_digest", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("summary_digest", sa.String(64), nullable=True),
        sa.Column("safe_error_code", sa.String(64), nullable=True),
        sa.Column("safe_error", sa.String(512), nullable=True),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.CheckConstraint(
            "status IN ('pending','claimed','running','completed','failed','cancelled')",
            name="ck_tenant_release_quality_scan_runs_status",
        ),
        sa.CheckConstraint(
            "slo_policy_revision > 0 AND attempt_count >= 0 AND max_attempts > 0 AND attempt_count <= max_attempts",
            name="ck_tenant_release_quality_scan_runs_attempts",
        ),
        sa.CheckConstraint(
            "observation_count >= 0 AND alert_count >= 0 AND recertification_job_count >= 0",
            name="ck_tenant_release_quality_scan_runs_counts",
        ),
        sa.CheckConstraint(
            "(status IN ('pending','completed','failed','cancelled') AND claim_owner IS NULL AND claim_lease_until IS NULL) OR "
            "(status='claimed' AND claim_owner IS NOT NULL AND claim_lease_until IS NOT NULL AND heartbeat_at IS NULL) OR "
            "(status='running' AND claim_owner IS NOT NULL AND claim_lease_until IS NOT NULL AND heartbeat_at IS NOT NULL AND started_at IS NOT NULL)",
            name="ck_tenant_release_quality_scan_runs_execution_state",
        ),
        sa.CheckConstraint(
            "(status IN ('pending','claimed','running') AND finished_at IS NULL) OR "
            "(status IN ('completed','failed','cancelled') AND finished_at IS NOT NULL AND claim_owner IS NULL AND claim_lease_until IS NULL)",
            name="ck_tenant_release_quality_scan_runs_terminal_state",
        ),
        sa.CheckConstraint(
            "length(idempotency_key_digest)=64 AND lower(idempotency_key_digest)=idempotency_key_digest AND "
            "length(request_hash)=64 AND lower(request_hash)=request_hash AND "
            "(summary_digest IS NULL OR (length(summary_digest)=64 AND lower(summary_digest)=summary_digest))",
            name="ck_tenant_release_quality_scan_runs_digests",
        ),
        sa.CheckConstraint(
            "(safe_error_code IS NULL AND safe_error IS NULL) OR (safe_error_code IS NOT NULL AND safe_error IS NOT NULL)",
            name="ck_tenant_release_quality_scan_runs_safe_error",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_quality_scan_runs_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_quality_scan_runs_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "schedule_id", "slo_policy_id"],
            [
                "tenant_release_quality_scan_schedules.tenant_id",
                "tenant_release_quality_scan_schedules.dataset_id",
                "tenant_release_quality_scan_schedules.id",
                "tenant_release_quality_scan_schedules.slo_policy_id",
            ],
            name="fk_quality_scan_runs_schedule_policy",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "slo_policy_id"],
            [
                "tenant_release_quality_slo_policies.tenant_id",
                "tenant_release_quality_slo_policies.id",
            ],
            name="fk_quality_scan_runs_policy",
        ),
        sa.UniqueConstraint("tenant_id", "dataset_id", "id", name="uq_quality_scan_runs_scope_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "schedule_id",
            "planned_at",
            name="uq_quality_scan_runs_schedule_planned",
        ),
    )
    op.create_index(
        "ix_quality_scan_runs_claim",
        "tenant_release_quality_scan_runs",
        ["status", "next_attempt_at", "planned_at", "id"],
    )
    op.create_index(
        "ix_quality_scan_runs_schedule",
        "tenant_release_quality_scan_runs",
        ["tenant_id", "dataset_id", "schedule_id", "planned_at", "id"],
    )

    op.create_table(
        "dataset_release_quality_observations",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("dataset_id", sa.String(64), nullable=False),
        sa.Column("release_id", sa.String(64), nullable=False),
        sa.Column("channel_id", sa.String(128), nullable=False),
        sa.Column("scan_run_id", sa.String(64), nullable=False),
        sa.Column("slo_policy_id", sa.String(64), nullable=False),
        sa.Column("slo_policy_revision", sa.Integer(), nullable=False),
        sa.Column("release_role", sa.String(16), nullable=False),
        sa.Column("gate_state", sa.String(24), nullable=False),
        sa.Column("gate_reason", sa.String(512), nullable=False),
        sa.Column("certification_id", sa.String(64), nullable=True),
        sa.Column("certification_digest", sa.String(64), nullable=True),
        sa.Column("certification_valid_until", _datetime6(), nullable=True),
        sa.Column("waiver_id", sa.String(64), nullable=True),
        sa.Column("waiver_digest", sa.String(64), nullable=True),
        sa.Column("waiver_expires_at", _datetime6(), nullable=True),
        sa.Column("minutes_to_certification_expiry", sa.Integer(), nullable=True),
        sa.Column("minutes_to_waiver_expiry", sa.Integer(), nullable=True),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("observation_digest", sa.String(64), nullable=False),
        sa.Column("observed_at", _datetime6(), nullable=False),
        sa.Column("observed_by", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.CheckConstraint(
            "release_role IN ('active','pinned')", name="ck_quality_observations_release_role"
        ),
        sa.CheckConstraint(
            "severity IN ('healthy','warning','critical','unavailable')",
            name="ck_quality_observations_severity",
        ),
        sa.CheckConstraint(
            "gate_state IN ('passing','waived','not_required','blocked','unavailable')",
            name="ck_quality_observations_gate_state",
        ),
        sa.CheckConstraint("slo_policy_revision > 0", name="ck_quality_observations_policy_rev"),
        sa.CheckConstraint(
            "length(observation_digest)=64 AND lower(observation_digest)=observation_digest",
            name="ck_quality_observations_digest",
        ),
        sa.CheckConstraint(
            "(certification_id IS NULL AND certification_digest IS NULL AND certification_valid_until IS NULL AND minutes_to_certification_expiry IS NULL) OR "
            "(certification_id IS NOT NULL AND certification_digest IS NOT NULL AND certification_valid_until IS NOT NULL AND minutes_to_certification_expiry IS NOT NULL)",
            name="ck_quality_observations_certification",
        ),
        sa.CheckConstraint(
            "(waiver_id IS NULL AND waiver_digest IS NULL AND waiver_expires_at IS NULL AND minutes_to_waiver_expiry IS NULL) OR "
            "(waiver_id IS NOT NULL AND waiver_digest IS NOT NULL AND waiver_expires_at IS NOT NULL AND minutes_to_waiver_expiry IS NOT NULL)",
            name="ck_quality_observations_waiver",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_quality_observations_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_quality_observations_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "release_id"],
            [
                "dataset_release_manifests.tenant_id",
                "dataset_release_manifests.dataset_id",
                "dataset_release_manifests.id",
            ],
            name="fk_quality_observations_release",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "channel_id"],
            ["tenant_release_channels.tenant_id", "tenant_release_channels.id"],
            name="fk_quality_observations_channel",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "scan_run_id"],
            [
                "tenant_release_quality_scan_runs.tenant_id",
                "tenant_release_quality_scan_runs.dataset_id",
                "tenant_release_quality_scan_runs.id",
            ],
            name="fk_quality_observations_scan_run",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "slo_policy_id"],
            [
                "tenant_release_quality_slo_policies.tenant_id",
                "tenant_release_quality_slo_policies.id",
            ],
            name="fk_quality_observations_slo_policy",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "certification_id"],
            [
                "dataset_release_quality_certifications.tenant_id",
                "dataset_release_quality_certifications.dataset_id",
                "dataset_release_quality_certifications.id",
            ],
            name="fk_quality_observations_certification",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "waiver_id"],
            [
                "dataset_release_quality_waivers.tenant_id",
                "dataset_release_quality_waivers.dataset_id",
                "dataset_release_quality_waivers.id",
            ],
            name="fk_quality_observations_waiver",
        ),
        sa.UniqueConstraint(
            "tenant_id", "dataset_id", "id", name="uq_quality_observations_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "scan_run_id",
            "dataset_id",
            "release_id",
            "channel_id",
            "release_role",
            name="uq_quality_observations_run_authority",
        ),
    )
    op.create_index(
        "ix_quality_observations_scope_time",
        "dataset_release_quality_observations",
        ["tenant_id", "dataset_id", "observed_at", "id"],
    )
    op.create_index(
        "ix_quality_observations_severity",
        "dataset_release_quality_observations",
        ["tenant_id", "dataset_id", "severity", "observed_at", "id"],
    )

    op.create_table(
        "dataset_release_quality_alerts",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("dataset_id", sa.String(64), nullable=False),
        sa.Column("release_id", sa.String(64), nullable=False),
        sa.Column("channel_id", sa.String(128), nullable=False),
        sa.Column("release_role", sa.String(16), nullable=False),
        sa.Column("alert_type", sa.String(40), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("active_alert_key", sa.String(384), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source_observation_id", sa.String(64), nullable=False),
        sa.Column("source_observation_digest", sa.String(64), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("opened_at", _datetime6(), nullable=False),
        sa.Column("last_observed_at", _datetime6(), nullable=False),
        sa.Column("acknowledged_at", _datetime6(), nullable=True),
        sa.Column("acknowledged_by", sa.String(64), nullable=True),
        sa.Column("acknowledged_comment", sa.String(512), nullable=True),
        sa.Column("resolved_at", _datetime6(), nullable=True),
        sa.Column("resolved_by", sa.String(64), nullable=True),
        sa.Column("resolved_comment", sa.String(512), nullable=True),
        sa.Column("suppressed_until", _datetime6(), nullable=True),
        sa.Column("suppressed_by", sa.String(64), nullable=True),
        sa.Column("suppressed_comment", sa.String(512), nullable=True),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.CheckConstraint(
            "revision > 0 AND occurrence_count > 0", name="ck_quality_alerts_counts"
        ),
        sa.CheckConstraint(
            "release_role IN ('active','pinned')", name="ck_quality_alerts_release_role"
        ),
        sa.CheckConstraint(
            "alert_type IN ('certification_expiring','certification_expired','certification_stale','waiver_expiring','waiver_expired','quality_gate_blocked','quality_authority_unavailable')",
            name="ck_quality_alerts_type",
        ),
        sa.CheckConstraint("severity IN ('warning','critical')", name="ck_quality_alerts_severity"),
        sa.CheckConstraint(
            "status IN ('open','acknowledged','resolved','suppressed')",
            name="ck_quality_alerts_status",
        ),
        sa.CheckConstraint(_canonical_alert_key_check(), name="ck_quality_alerts_active_key"),
        sa.CheckConstraint(
            "(status='open' AND acknowledged_at IS NULL AND acknowledged_by IS NULL AND resolved_at IS NULL AND resolved_by IS NULL AND suppressed_until IS NULL AND suppressed_by IS NULL) OR "
            "(status='acknowledged' AND acknowledged_at IS NOT NULL AND acknowledged_by IS NOT NULL AND resolved_at IS NULL AND resolved_by IS NULL AND suppressed_until IS NULL AND suppressed_by IS NULL) OR "
            "(status='resolved' AND resolved_at IS NOT NULL AND resolved_by IS NOT NULL AND suppressed_until IS NULL AND suppressed_by IS NULL) OR "
            "(status='suppressed' AND resolved_at IS NULL AND resolved_by IS NULL AND suppressed_until IS NOT NULL AND suppressed_by IS NOT NULL)",
            name="ck_dataset_release_quality_alerts_lifecycle",
        ),
        sa.CheckConstraint(
            "length(source_observation_digest)=64 AND lower(source_observation_digest)=source_observation_digest",
            name="ck_quality_alerts_source_digest",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_quality_alerts_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_quality_alerts_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "release_id"],
            [
                "dataset_release_manifests.tenant_id",
                "dataset_release_manifests.dataset_id",
                "dataset_release_manifests.id",
            ],
            name="fk_quality_alerts_release",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "channel_id"],
            ["tenant_release_channels.tenant_id", "tenant_release_channels.id"],
            name="fk_quality_alerts_channel",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "source_observation_id"],
            [
                "dataset_release_quality_observations.tenant_id",
                "dataset_release_quality_observations.dataset_id",
                "dataset_release_quality_observations.id",
            ],
            name="fk_quality_alerts_observation",
        ),
        sa.UniqueConstraint("tenant_id", "dataset_id", "id", name="uq_quality_alerts_scope_id"),
        sa.UniqueConstraint(
            "tenant_id", "active_alert_key", name="uq_dataset_release_quality_alerts_active_key"
        ),
    )
    op.create_index(
        "ix_quality_alerts_inbox",
        "dataset_release_quality_alerts",
        ["tenant_id", "dataset_id", "status", "severity", "last_observed_at", "id"],
    )
    op.create_index(
        "ix_quality_alerts_authority",
        "dataset_release_quality_alerts",
        ["tenant_id", "dataset_id", "release_id", "channel_id", "release_role", "alert_type"],
    )

    op.create_table(
        "dataset_release_recertification_jobs",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("dataset_id", sa.String(64), nullable=False),
        sa.Column("release_id", sa.String(64), nullable=False),
        sa.Column("channel_id", sa.String(128), nullable=False),
        sa.Column("release_role", sa.String(16), nullable=False),
        sa.Column("baseline_id", sa.String(64), nullable=False),
        sa.Column("policy_id", sa.String(64), nullable=False),
        sa.Column("policy_revision", sa.Integer(), nullable=False),
        sa.Column("slo_policy_id", sa.String(64), nullable=False),
        sa.Column("slo_policy_revision", sa.Integer(), nullable=False),
        sa.Column(sa.quoted_name("trigger", True), sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("active_job_key", sa.String(384), nullable=True),
        sa.Column("cycle_key", sa.String(64), nullable=False),
        sa.Column("expected_manifest_digest", sa.String(64), nullable=False),
        sa.Column("expected_evidence_digest", sa.String(64), nullable=True),
        sa.Column("expected_channel_revision", sa.Integer(), nullable=False),
        sa.Column("claim_owner", sa.String(128), nullable=True),
        sa.Column("claim_lease_until", _datetime6(), nullable=True),
        sa.Column("heartbeat_at", _datetime6(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("next_attempt_at", _datetime6(), nullable=True),
        sa.Column("result_certification_id", sa.String(64), nullable=True),
        sa.Column("idempotency_key_digest", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("safe_error_code", sa.String(64), nullable=True),
        sa.Column("safe_error", sa.String(512), nullable=True),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("completed_at", _datetime6(), nullable=True),
        sa.Column("cancelled_at", _datetime6(), nullable=True),
        sa.Column("cancelled_by", sa.String(64), nullable=True),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("reason", sa.String(512), nullable=False),
        sa.CheckConstraint(
            "release_role IN ('active','pinned')",
            name="ck_dataset_release_recertification_jobs_release_role",
        ),
        sa.CheckConstraint(
            _trigger_check,
            name="ck_dataset_release_recertification_jobs_trigger",
        ),
        sa.CheckConstraint(
            "status IN ('pending','claimed','awaiting_evidence','ready_to_certify','completed','failed','cancelled')",
            name="ck_dataset_release_recertification_jobs_status",
        ),
        sa.CheckConstraint(
            "policy_revision > 0 AND slo_policy_revision > 0 AND expected_channel_revision > 0 AND "
            "attempt_count >= 0 AND max_attempts > 0 AND attempt_count <= max_attempts",
            name="ck_dataset_release_recertification_jobs_revisions",
        ),
        sa.CheckConstraint(
            _canonical_job_key_check(), name="ck_dataset_release_recertification_jobs_active_key"
        ),
        sa.CheckConstraint(
            "(status='claimed' AND claim_owner IS NOT NULL AND claim_lease_until IS NOT NULL) OR "
            "(status<>'claimed' AND claim_owner IS NULL AND claim_lease_until IS NULL AND heartbeat_at IS NULL)",
            name="ck_dataset_release_recertification_jobs_execution_state",
        ),
        sa.CheckConstraint(
            "(status='completed' AND completed_at IS NOT NULL AND result_certification_id IS NOT NULL AND cancelled_at IS NULL AND cancelled_by IS NULL) OR "
            "(status='cancelled' AND completed_at IS NULL AND result_certification_id IS NULL AND cancelled_at IS NOT NULL AND cancelled_by IS NOT NULL) OR "
            "(status NOT IN ('completed','cancelled') AND completed_at IS NULL AND result_certification_id IS NULL AND cancelled_at IS NULL AND cancelled_by IS NULL)",
            name="ck_dataset_release_recertification_jobs_terminal_state",
        ),
        sa.CheckConstraint(
            "length(cycle_key)=64 AND lower(cycle_key)=cycle_key AND "
            "length(expected_manifest_digest)=64 AND lower(expected_manifest_digest)=expected_manifest_digest AND "
            "(expected_evidence_digest IS NULL OR (length(expected_evidence_digest)=64 AND lower(expected_evidence_digest)=expected_evidence_digest)) AND "
            "length(idempotency_key_digest)=64 AND lower(idempotency_key_digest)=idempotency_key_digest AND "
            "length(request_hash)=64 AND lower(request_hash)=request_hash",
            name="ck_dataset_release_recertification_jobs_digests",
        ),
        sa.CheckConstraint(
            "(safe_error_code IS NULL AND safe_error IS NULL) OR (safe_error_code IS NOT NULL AND safe_error IS NOT NULL)",
            name="ck_dataset_release_recertification_jobs_safe_error",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_recertification_jobs_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_recertification_jobs_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "release_id"],
            [
                "dataset_release_manifests.tenant_id",
                "dataset_release_manifests.dataset_id",
                "dataset_release_manifests.id",
            ],
            name="fk_recertification_jobs_release",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "channel_id"],
            ["tenant_release_channels.tenant_id", "tenant_release_channels.id"],
            name="fk_recertification_jobs_channel",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "baseline_id"],
            [
                "dataset_quality_baselines.tenant_id",
                "dataset_quality_baselines.dataset_id",
                "dataset_quality_baselines.id",
            ],
            name="fk_recertification_jobs_baseline",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            [
                "tenant_release_quality_gate_policies.tenant_id",
                "tenant_release_quality_gate_policies.id",
            ],
            name="fk_recertification_jobs_gate_policy",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "slo_policy_id"],
            [
                "tenant_release_quality_slo_policies.tenant_id",
                "tenant_release_quality_slo_policies.id",
            ],
            name="fk_recertification_jobs_slo_policy",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "result_certification_id"],
            [
                "dataset_release_quality_certifications.tenant_id",
                "dataset_release_quality_certifications.dataset_id",
                "dataset_release_quality_certifications.id",
            ],
            name="fk_recertification_jobs_result_cert",
        ),
        sa.UniqueConstraint(
            "tenant_id", "dataset_id", "id", name="uq_recertification_jobs_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "active_job_key", name="uq_dataset_release_recertification_jobs_active_key"
        ),
        sa.UniqueConstraint("tenant_id", "cycle_key", name="uq_recertification_jobs_cycle_key"),
    )
    op.create_index(
        "ix_recertification_jobs_queue",
        "dataset_release_recertification_jobs",
        ["tenant_id", "status", "next_attempt_at", "created_at", "id"],
    )
    op.create_index(
        "ix_recertification_jobs_authority",
        "dataset_release_recertification_jobs",
        ["tenant_id", "dataset_id", "release_id", "channel_id", "release_role", "status"],
    )

    _create_observation_guards()


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("0031 downgrade requires online preflight")
    _guard_downgrade()
    _drop_observation_guards()

    op.drop_index(
        "ix_recertification_jobs_authority", table_name="dataset_release_recertification_jobs"
    )
    op.drop_index(
        "ix_recertification_jobs_queue", table_name="dataset_release_recertification_jobs"
    )
    op.drop_table("dataset_release_recertification_jobs")
    op.drop_index("ix_quality_alerts_authority", table_name="dataset_release_quality_alerts")
    op.drop_index("ix_quality_alerts_inbox", table_name="dataset_release_quality_alerts")
    op.drop_table("dataset_release_quality_alerts")
    op.drop_index(
        "ix_quality_observations_severity", table_name="dataset_release_quality_observations"
    )
    op.drop_index(
        "ix_quality_observations_scope_time", table_name="dataset_release_quality_observations"
    )
    op.drop_table("dataset_release_quality_observations")
    op.drop_index("ix_quality_scan_runs_schedule", table_name="tenant_release_quality_scan_runs")
    op.drop_index("ix_quality_scan_runs_claim", table_name="tenant_release_quality_scan_runs")
    op.drop_table("tenant_release_quality_scan_runs")
    op.drop_index(
        "ix_quality_scan_schedules_policy", table_name="tenant_release_quality_scan_schedules"
    )
    op.drop_index(
        "ix_quality_scan_schedules_due", table_name="tenant_release_quality_scan_schedules"
    )
    op.drop_table("tenant_release_quality_scan_schedules")
    op.drop_index(
        "ix_quality_slo_policies_updated", table_name="tenant_release_quality_slo_policies"
    )
    op.drop_index("ix_quality_slo_policies_scope", table_name="tenant_release_quality_slo_policies")
    op.drop_table("tenant_release_quality_slo_policies")
