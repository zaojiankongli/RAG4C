"""Add Release-linked quality baseline, certification, gate and waiver authority.

Revision ID: 0030_enterprise_release_quality_certification
Revises: 0029_enterprise_knowledge_base_releases
Create Date: 2026-08-29
"""

from __future__ import annotations
from collections.abc import Sequence
from alembic import context, op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import mysql

revision: str = "0030_enterprise_release_quality_certification"
down_revision: str | None = "0029_enterprise_knowledge_base_releases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WAIVER_ACTION = "knowledge_base_release_quality_waiver"
TABLES = (
    "tenant_release_quality_gate_policies",
    "dataset_quality_baselines",
    "dataset_quality_baseline_items",
    "dataset_release_quality_certifications",
    "dataset_release_quality_certification_evidence",
    "dataset_release_quality_waivers",
    "dataset_release_quality_events",
)
IMMUTABLE_TABLES = (
    "dataset_quality_baselines",
    "dataset_quality_baseline_items",
    "dataset_release_quality_certifications",
    "dataset_release_quality_certification_evidence",
    "dataset_release_quality_waivers",
    "dataset_release_quality_events",
)
APPROVAL_ACTION_TYPES_0030 = (
    "catalog_upgrade",
    "membership_bootstrap",
    "dataset_acl_disable",
    "member_role_change",
    "identity_provider_disable",
    "audit_retention_execute",
    "workspace_authorization_mode_change",
    "dataset_workspace_transfer",
    "knowledge_base_release_publish",
    "knowledge_base_release_rollback",
    WAIVER_ACTION,
)


def _datetime6():
    return (
        sa.DateTime()
        .with_variant(mysql.DATETIME(fsp=6), "mysql")
        .with_variant(mysql.DATETIME(fsp=6), "mariadb")
    )


def _dialect_name() -> str:
    return str(op.get_context().dialect.name).casefold()


def _approval_check() -> str:
    return "action_type IN (" + ",".join(f"'{value}'" for value in APPROVAL_ACTION_TYPES_0030) + ")"


def _quality_policy_active_scope_key_check():
    """Return the cross-dialect canonical active policy scope predicate."""
    status = sa.column("status", sa.String(length=16))
    scope_type = sa.column("scope_type", sa.String(length=16))
    scope_value = sa.column("scope_value", sa.String(length=128))
    active_scope_key = sa.column("active_scope_key", sa.String(length=192))
    return sa.or_(
        status != "active",
        sa.and_(scope_type == "global", active_scope_key == "global:*"),
        sa.and_(
            scope_type == "risk_tier",
            active_scope_key == sa.literal("risk_tier:") + scope_value,
        ),
        sa.and_(
            scope_type == "channel",
            active_scope_key == sa.literal("channel:") + scope_value,
        ),
    )


def _replace_approval_checks(actions: tuple[str, ...]) -> None:
    sql = "action_type IN (" + ",".join(f"'{value}'" for value in actions) + ")"
    for table in ("tenant_approval_policies", "tenant_approval_requests"):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"ck_{table}_action_type", type_="check")
            batch.create_check_constraint(f"ck_{table}_action_type", sql)


def _create_guards() -> None:
    dialect = _dialect_name()
    if dialect == "sqlite":
        for table in IMMUTABLE_TABLES:
            for operation in ("UPDATE", "DELETE"):
                name = f"trg_{table}_no_{operation.casefold()}"
                op.execute(
                    f"CREATE TRIGGER {name} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, '{table} are immutable'); END"
                )
    elif dialect in {"mysql", "mariadb"}:
        for table in IMMUTABLE_TABLES:
            for operation in ("UPDATE", "DELETE"):
                name = f"trg_{table}_no_{operation.casefold()}"
                op.execute(
                    f"CREATE TRIGGER {name} BEFORE {operation} ON {table} FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{table} are immutable'"
                )
    elif dialect == "postgresql":
        op.execute(
            "CREATE FUNCTION rag4c_release_quality_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'release quality authority is immutable'; END; $$"
        )
        for table in IMMUTABLE_TABLES:
            for operation in ("UPDATE", "DELETE"):
                name = f"trg_{table}_no_{operation.casefold()}"
                op.execute(
                    f"CREATE TRIGGER {name} BEFORE {operation} ON {table} FOR EACH ROW EXECUTE FUNCTION rag4c_release_quality_immutable()"
                )


def _drop_guards() -> None:
    dialect = _dialect_name()
    for table in IMMUTABLE_TABLES:
        for operation in ("update", "delete"):
            name = f"trg_{table}_no_{operation}"
            if dialect == "postgresql":
                op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
            else:
                op.execute(f"DROP TRIGGER IF EXISTS {name}")
    if dialect == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS rag4c_release_quality_immutable()")


def _guard_downgrade() -> None:
    connection = op.get_bind()
    counts = {
        table: int(connection.scalar(text(f"SELECT COUNT(*) FROM {table}")) or 0)
        for table in TABLES
    }
    for table in ("tenant_approval_policies", "tenant_approval_requests"):
        counts[table] = int(
            connection.scalar(
                text(f"SELECT COUNT(*) FROM {table} WHERE action_type=:action"),
                {"action": WAIVER_ACTION},
            )
            or 0
        )
    if any(counts.values()):
        detail = ", ".join(f"{key}={value}" for key, value in counts.items() if value)
        raise RuntimeError(f"0030 downgrade blocked by Release quality authority: {detail}")


def upgrade() -> None:
    if context.is_offline_mode() and _dialect_name() == "sqlite":
        raise RuntimeError("0030 SQLite offline upgrade is unsupported; use online migration")
    op.create_table(
        "tenant_release_quality_gate_policies",
        sa.Column("id", sa.String(length=64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("scope_value", sa.String(length=128), nullable=False),
        sa.Column("channel_id", sa.String(length=128), nullable=True),
        sa.Column("active_scope_key", sa.String(length=192), nullable=True),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'active'")
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("min_experiment_count", sa.Integer(), nullable=False),
        sa.Column("min_judged_result_count", sa.Integer(), nullable=False),
        sa.Column("min_judgment_coverage_bps", sa.Integer(), nullable=False),
        sa.Column("min_exact_agreement_bps", sa.Integer(), nullable=False),
        sa.Column("min_mean_score_milli", sa.Integer(), nullable=False),
        sa.Column("max_conflicting_results", sa.Integer(), nullable=False),
        sa.Column("require_all_experiments_completed", sa.Boolean(), nullable=False),
        sa.Column("require_no_degraded_results", sa.Boolean(), nullable=False),
        sa.Column("max_certification_age_minutes", sa.Integer(), nullable=False),
        sa.Column("policy_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
        sa.Column("disabled_at", _datetime6(), nullable=True),
        sa.Column("disabled_by", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "length(policy_digest)=64 AND lower(policy_digest)=policy_digest",
            name="ck_tenant_release_quality_gate_policies_digest",
        ),
        sa.CheckConstraint(
            "(status='active' AND active_scope_key IS NOT NULL AND disabled_at IS NULL AND disabled_by IS NULL) OR (status='disabled' AND active_scope_key IS NULL AND disabled_at IS NOT NULL AND disabled_by IS NOT NULL)",
            name="ck_tenant_release_quality_gate_policies_lifecycle",
        ),
        sa.CheckConstraint("revision > 0", name="ck_tenant_release_quality_gate_policies_revision"),
        sa.CheckConstraint(
            "(scope_type='global' AND scope_value='*' AND channel_id IS NULL) OR (scope_type='risk_tier' AND scope_value IN ('low','medium','high') AND channel_id IS NULL) OR (scope_type='channel' AND channel_id IS NOT NULL AND scope_value=channel_id)",
            name="ck_tenant_release_quality_gate_policies_scope_binding",
        ),
        sa.CheckConstraint(
            _quality_policy_active_scope_key_check(),
            name="ck_tenant_release_quality_gate_policies_active_scope_key",
        ),
        sa.CheckConstraint(
            "scope_type IN ('global','risk_tier','channel')",
            name="ck_tenant_release_quality_gate_policies_scope_type",
        ),
        sa.CheckConstraint(
            "status IN ('active','disabled')", name="ck_tenant_release_quality_gate_policies_status"
        ),
        sa.CheckConstraint(
            "min_experiment_count > 0 AND min_judged_result_count >= 0 AND min_judgment_coverage_bps BETWEEN 0 AND 10000 AND min_exact_agreement_bps BETWEEN 0 AND 10000 AND min_mean_score_milli BETWEEN 0 AND 3000 AND max_conflicting_results >= 0 AND max_certification_age_minutes > 0",
            name="ck_tenant_release_quality_gate_policies_thresholds",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "channel_id"],
            ["tenant_release_channels.tenant_id", "tenant_release_channels.id"],
            name="fk_tenant_release_quality_gate_policies_scope_channel",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_release_quality_gate_policies_tenant"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "active_scope_key",
            name="uq_tenant_release_quality_gate_policies_active_scope",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_tenant_release_quality_gate_policies_scope_id"
        ),
    )
    op.create_index(
        "ix_tenant_release_quality_gate_policies_scope_status",
        "tenant_release_quality_gate_policies",
        ["tenant_id", "scope_type", "scope_value", "status", "id"],
        unique=False,
    )
    op.create_index(
        "ix_tenant_release_quality_gate_policies_tenant_updated",
        "tenant_release_quality_gate_policies",
        ["tenant_id", "updated_at", "id"],
        unique=False,
    )
    op.create_table(
        "dataset_quality_baselines",
        sa.Column("id", sa.String(length=64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("normalized_name", sa.String(length=256), nullable=False),
        sa.Column("baseline_revision", sa.Integer(), nullable=False),
        sa.Column("parent_baseline_id", sa.String(length=64), nullable=True),
        sa.Column("experiment_count", sa.Integer(), nullable=False),
        sa.Column("query_count", sa.Integer(), nullable=False),
        sa.Column("baseline_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.CheckConstraint(
            "experiment_count > 0 AND query_count > 0", name="ck_dataset_quality_baselines_counts"
        ),
        sa.CheckConstraint(
            "length(baseline_digest)=64 AND lower(baseline_digest)=baseline_digest",
            name="ck_dataset_quality_baselines_digest",
        ),
        sa.CheckConstraint("baseline_revision > 0", name="ck_dataset_quality_baselines_revision"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_dataset_quality_baselines_scope_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "parent_baseline_id"],
            [
                "dataset_quality_baselines.tenant_id",
                "dataset_quality_baselines.dataset_id",
                "dataset_quality_baselines.id",
            ],
            name="fk_dataset_quality_baselines_scope_parent",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_dataset_quality_baselines_tenant"
        ),
        sa.UniqueConstraint(
            "tenant_id", "dataset_id", "baseline_digest", name="uq_dataset_quality_baselines_digest"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "normalized_name",
            "baseline_revision",
            name="uq_dataset_quality_baselines_name_revision",
        ),
        sa.UniqueConstraint(
            "tenant_id", "dataset_id", "id", name="uq_dataset_quality_baselines_scope_dataset_id"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_dataset_quality_baselines_scope_id"),
    )
    op.create_index(
        "ix_dataset_quality_baselines_scope_name_revision",
        "dataset_quality_baselines",
        ["tenant_id", "dataset_id", "normalized_name", "baseline_revision", "id"],
        unique=False,
    )
    op.create_table(
        "dataset_quality_baseline_items",
        sa.Column("id", sa.String(length=64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("baseline_id", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("experiment_id", sa.String(length=64), nullable=False),
        sa.Column("experiment_sequence", sa.BigInteger(), nullable=False),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("experiment_serving_generation", sa.BigInteger(), nullable=False),
        sa.Column("strategy_digest", sa.String(length=64), nullable=False),
        sa.Column("result_digest", sa.String(length=64), nullable=False),
        sa.Column("evidence_digest", sa.String(length=64), nullable=False),
        sa.Column("judgment_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.CheckConstraint(
            "experiment_sequence > 0 AND experiment_serving_generation >= 0",
            name="ck_dataset_quality_baseline_items_authority",
        ),
        sa.CheckConstraint("ordinal > 0", name="ck_dataset_quality_baseline_items_ordinal"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "baseline_id"],
            [
                "dataset_quality_baselines.tenant_id",
                "dataset_quality_baselines.dataset_id",
                "dataset_quality_baselines.id",
            ],
            name="fk_dataset_quality_baseline_items_scope_baseline",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "experiment_id"],
            [
                "retrieval_experiments.tenant_id",
                "retrieval_experiments.dataset_id",
                "retrieval_experiments.id",
            ],
            name="fk_dataset_quality_baseline_items_scope_experiment",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_dataset_quality_baseline_items_tenant"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "baseline_id",
            "experiment_id",
            name="uq_dataset_quality_baseline_items_experiment",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "baseline_id",
            "ordinal",
            name="uq_dataset_quality_baseline_items_ordinal",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_dataset_quality_baseline_items_scope_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "id",
            name="uq_dataset_quality_baseline_items_scope_dataset_id",
        ),
    )
    op.create_index(
        "ix_dataset_quality_baseline_items_scope_ordinal",
        "dataset_quality_baseline_items",
        ["tenant_id", "dataset_id", "baseline_id", "ordinal", "id"],
        unique=False,
    )
    op.create_table(
        "dataset_release_quality_certifications",
        sa.Column("id", sa.String(length=64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("release_id", sa.String(length=64), nullable=False),
        sa.Column("baseline_id", sa.String(length=64), nullable=False),
        sa.Column("policy_id", sa.String(length=64), nullable=False),
        sa.Column("policy_revision", sa.Integer(), nullable=False),
        sa.Column("release_manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("release_mutation_generation", sa.BigInteger(), nullable=False),
        sa.Column("release_serving_generation", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("experiment_count", sa.Integer(), nullable=False),
        sa.Column("completed_experiment_count", sa.Integer(), nullable=False),
        sa.Column("degraded_experiment_count", sa.Integer(), nullable=False),
        sa.Column("query_count", sa.Integer(), nullable=False),
        sa.Column("judged_result_count", sa.Integer(), nullable=False),
        sa.Column("total_result_count", sa.Integer(), nullable=False),
        sa.Column("judgment_count", sa.Integer(), nullable=False),
        sa.Column("judgment_coverage_bps", sa.Integer(), nullable=True),
        sa.Column("multi_judged_results", sa.Integer(), nullable=False),
        sa.Column("unanimous_results", sa.Integer(), nullable=False),
        sa.Column("conflicting_results", sa.Integer(), nullable=False),
        sa.Column("exact_agreement_bps", sa.Integer(), nullable=True),
        sa.Column("mean_score_milli", sa.Integer(), nullable=True),
        sa.Column("failed_rule_count", sa.Integer(), nullable=False),
        sa.Column("policy_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("evidence_digest", sa.String(length=64), nullable=False),
        sa.Column("certification_digest", sa.String(length=64), nullable=False),
        sa.Column("valid_until", _datetime6(), nullable=False),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.CheckConstraint(
            "exact_agreement_bps IS NULL OR exact_agreement_bps BETWEEN 0 AND 10000",
            name="ck_dataset_release_quality_certifications_agreement",
        ),
        sa.CheckConstraint(
            "experiment_count > 0 AND completed_experiment_count >= 0 AND degraded_experiment_count >= 0 AND query_count > 0 AND judged_result_count >= 0 AND total_result_count >= 0 AND judgment_count >= 0 AND multi_judged_results >= 0 AND unanimous_results >= 0 AND conflicting_results >= 0 AND failed_rule_count >= 0",
            name="ck_dataset_release_quality_certifications_counts",
        ),
        sa.CheckConstraint(
            "judgment_coverage_bps IS NULL OR judgment_coverage_bps BETWEEN 0 AND 10000",
            name="ck_dataset_release_quality_certifications_coverage",
        ),
        sa.CheckConstraint(
            "policy_revision > 0 AND release_mutation_generation >= 0 AND release_serving_generation >= 0",
            name="ck_dataset_release_quality_certifications_revisions",
        ),
        sa.CheckConstraint(
            "mean_score_milli IS NULL OR mean_score_milli BETWEEN 0 AND 3000",
            name="ck_dataset_release_quality_certifications_score",
        ),
        sa.CheckConstraint(
            "status IN ('passed','failed')", name="ck_dataset_release_quality_certifications_status"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "baseline_id"],
            [
                "dataset_quality_baselines.tenant_id",
                "dataset_quality_baselines.dataset_id",
                "dataset_quality_baselines.id",
            ],
            name="fk_dataset_release_quality_certifications_scope_baseline",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            [
                "tenant_release_quality_gate_policies.tenant_id",
                "tenant_release_quality_gate_policies.id",
            ],
            name="fk_dataset_release_quality_certifications_scope_policy",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "release_id"],
            [
                "dataset_release_manifests.tenant_id",
                "dataset_release_manifests.dataset_id",
                "dataset_release_manifests.id",
            ],
            name="fk_dataset_release_quality_certifications_scope_release",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_dataset_release_quality_certifications_tenant"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "release_id",
            "baseline_id",
            "policy_id",
            "policy_revision",
            "evidence_digest",
            name="uq_dataset_release_quality_certifications_authority",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "id",
            name="uq_dataset_release_quality_certifications_dataset_id",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_dataset_release_quality_certifications_scope_id"
        ),
    )
    op.create_index(
        "ix_dataset_release_quality_certifications_scope_release_time",
        "dataset_release_quality_certifications",
        ["tenant_id", "dataset_id", "release_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_dataset_release_quality_certifications_scope_status_valid",
        "dataset_release_quality_certifications",
        ["tenant_id", "dataset_id", "status", "valid_until", "id"],
        unique=False,
    )
    op.create_table(
        "dataset_release_quality_certification_evidence",
        sa.Column("id", sa.String(length=64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("certification_id", sa.String(length=64), nullable=False),
        sa.Column("baseline_item_id", sa.String(length=64), nullable=False),
        sa.Column("experiment_id", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("judged_result_count", sa.Integer(), nullable=False),
        sa.Column("judgment_count", sa.Integer(), nullable=False),
        sa.Column("multi_judged_results", sa.Integer(), nullable=False),
        sa.Column("unanimous_results", sa.Integer(), nullable=False),
        sa.Column("conflicting_results", sa.Integer(), nullable=False),
        sa.Column("exact_agreement_bps", sa.Integer(), nullable=True),
        sa.Column("mean_score_milli", sa.Integer(), nullable=True),
        sa.Column("experiment_digest", sa.String(length=64), nullable=False),
        sa.Column("judgment_digest", sa.String(length=64), nullable=False),
        sa.Column("safe_facts_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.CheckConstraint(
            "exact_agreement_bps IS NULL OR exact_agreement_bps BETWEEN 0 AND 10000",
            name="ck_dataset_release_quality_certification_evidence_agreement",
        ),
        sa.CheckConstraint(
            "result_count >= 0 AND judged_result_count >= 0 AND judgment_count >= 0 AND multi_judged_results >= 0 AND unanimous_results >= 0 AND conflicting_results >= 0",
            name="ck_dataset_release_quality_certification_evidence_counts",
        ),
        sa.CheckConstraint(
            "ordinal > 0", name="ck_dataset_release_quality_certification_evidence_ordinal"
        ),
        sa.CheckConstraint(
            "mean_score_milli IS NULL OR mean_score_milli BETWEEN 0 AND 3000",
            name="ck_dataset_release_quality_certification_evidence_score",
        ),
        sa.CheckConstraint(
            "status IN ('completed','failed')",
            name="ck_dataset_release_quality_certification_evidence_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "certification_id"],
            [
                "dataset_release_quality_certifications.tenant_id",
                "dataset_release_quality_certifications.dataset_id",
                "dataset_release_quality_certifications.id",
            ],
            name="fk_quality_cert_evidence_scope_certification",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "experiment_id"],
            [
                "retrieval_experiments.tenant_id",
                "retrieval_experiments.dataset_id",
                "retrieval_experiments.id",
            ],
            name="fk_quality_cert_evidence_scope_experiment",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "baseline_item_id"],
            [
                "dataset_quality_baseline_items.tenant_id",
                "dataset_quality_baseline_items.dataset_id",
                "dataset_quality_baseline_items.id",
            ],
            name="fk_dataset_release_quality_certification_evidence_scope_item",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_dataset_release_quality_certification_evidence_tenant",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "certification_id",
            "baseline_item_id",
            name="uq_dataset_release_quality_certification_evidence_item",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "certification_id",
            "ordinal",
            name="uq_dataset_release_quality_certification_evidence_ordinal",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_dataset_release_quality_certification_evidence_scope_id"
        ),
    )
    op.create_index(
        "ix_dataset_release_quality_certification_evidence_scope_ordinal",
        "dataset_release_quality_certification_evidence",
        ["tenant_id", "dataset_id", "certification_id", "ordinal", "id"],
        unique=False,
    )
    op.create_table(
        "dataset_release_quality_waivers",
        sa.Column("id", sa.String(length=64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("release_id", sa.String(length=64), nullable=False),
        sa.Column("channel_id", sa.String(length=128), nullable=False),
        sa.Column("policy_id", sa.String(length=64), nullable=False),
        sa.Column("policy_revision", sa.Integer(), nullable=False),
        sa.Column("release_manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("approval_request_id", sa.String(length=64), nullable=False),
        sa.Column("approval_execution_id", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=False),
        sa.Column("valid_from", _datetime6(), nullable=False),
        sa.Column("expires_at", _datetime6(), nullable=False),
        sa.Column("waiver_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.CheckConstraint(
            "length(waiver_digest)=64 AND lower(waiver_digest)=waiver_digest",
            name="ck_dataset_release_quality_waivers_digest",
        ),
        sa.CheckConstraint(
            "policy_revision > 0", name="ck_dataset_release_quality_waivers_policy_revision"
        ),
        sa.CheckConstraint(
            "expires_at > valid_from", name="ck_dataset_release_quality_waivers_window"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "approval_request_id"],
            ["tenant_approval_requests.tenant_id", "tenant_approval_requests.id"],
            name="fk_dataset_release_quality_waivers_scope_approval",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "channel_id"],
            ["tenant_release_channels.tenant_id", "tenant_release_channels.id"],
            name="fk_dataset_release_quality_waivers_scope_channel",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            [
                "tenant_release_quality_gate_policies.tenant_id",
                "tenant_release_quality_gate_policies.id",
            ],
            name="fk_dataset_release_quality_waivers_scope_policy",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "release_id"],
            [
                "dataset_release_manifests.tenant_id",
                "dataset_release_manifests.dataset_id",
                "dataset_release_manifests.id",
            ],
            name="fk_dataset_release_quality_waivers_scope_release",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_dataset_release_quality_waivers_tenant"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "approval_execution_id",
            name="uq_dataset_release_quality_waivers_approval_execution",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "approval_request_id",
            name="uq_dataset_release_quality_waivers_approval_request",
        ),
        sa.UniqueConstraint(
            "tenant_id", "dataset_id", "id", name="uq_dataset_release_quality_waivers_dataset_id"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_dataset_release_quality_waivers_scope_id"),
    )
    op.create_index(
        "ix_dataset_release_quality_waivers_scope_release_channel",
        "dataset_release_quality_waivers",
        ["tenant_id", "dataset_id", "release_id", "channel_id", "expires_at", "id"],
        unique=False,
    )
    op.create_table(
        "dataset_release_quality_events",
        sa.Column("id", sa.String(length=64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("release_id", sa.String(length=64), nullable=False),
        sa.Column("channel_id", sa.String(length=128), nullable=False),
        sa.Column("certification_id", sa.String(length=64), nullable=True),
        sa.Column("waiver_id", sa.String(length=64), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("event_sequence", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=True),
        sa.Column("previous_event_digest", sa.String(length=64), nullable=True),
        sa.Column("event_digest", sa.String(length=64), nullable=False),
        sa.Column("approval_request_id", sa.String(length=64), nullable=True),
        sa.Column("approval_execution_id", sa.String(length=128), nullable=True),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=False),
        sa.Column("safe_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column(
            "occurred_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.CheckConstraint(
            "length(event_digest)=64 AND lower(event_digest)=event_digest",
            name="ck_dataset_release_quality_events_digest",
        ),
        sa.CheckConstraint(
            "previous_event_digest IS NULL OR (length(previous_event_digest)=64 AND lower(previous_event_digest)=previous_event_digest)",
            name="ck_dataset_release_quality_events_previous_digest",
        ),
        sa.CheckConstraint("event_sequence > 0", name="ck_dataset_release_quality_events_sequence"),
        sa.CheckConstraint(
            "state IS NULL OR state IN ('passed','failed','blocked','unavailable','active','expired','revoked')",
            name="ck_dataset_release_quality_events_state",
        ),
        sa.CheckConstraint(
            "event_type IN ('certification_created','gate_evaluated','gate_passed','gate_failed','gate_blocked','gate_unavailable','gate_expired','gate_revoked','waiver_requested','waiver_approved','waiver_rejected','waiver_applied','waiver_expired','waiver_revoked','release_promoted_with_quality_gate','release_rolled_back_with_quality_gate')",
            name="ck_dataset_release_quality_events_type",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "certification_id"],
            [
                "dataset_release_quality_certifications.tenant_id",
                "dataset_release_quality_certifications.dataset_id",
                "dataset_release_quality_certifications.id",
            ],
            name="fk_dataset_release_quality_events_scope_certification",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "channel_id"],
            ["tenant_release_channels.tenant_id", "tenant_release_channels.id"],
            name="fk_dataset_release_quality_events_scope_channel",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "approval_request_id"],
            ["tenant_approval_requests.tenant_id", "tenant_approval_requests.id"],
            name="fk_dataset_release_quality_events_scope_approval",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "release_id"],
            [
                "dataset_release_manifests.tenant_id",
                "dataset_release_manifests.dataset_id",
                "dataset_release_manifests.id",
            ],
            name="fk_dataset_release_quality_events_scope_release",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "waiver_id"],
            [
                "dataset_release_quality_waivers.tenant_id",
                "dataset_release_quality_waivers.dataset_id",
                "dataset_release_quality_waivers.id",
            ],
            name="fk_dataset_release_quality_events_scope_waiver",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_dataset_release_quality_events_tenant"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_dataset_release_quality_events_scope_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "release_id",
            "channel_id",
            "event_sequence",
            name="uq_dataset_release_quality_events_stream_sequence",
        ),
    )
    op.create_index(
        "ix_dataset_release_quality_events_scope_stream",
        "dataset_release_quality_events",
        ["tenant_id", "dataset_id", "release_id", "channel_id", "event_sequence", "id"],
        unique=False,
    )
    op.create_index(
        "ix_dataset_release_quality_events_scope_time",
        "dataset_release_quality_events",
        ["tenant_id", "dataset_id", "occurred_at", "id"],
        unique=False,
    )
    _replace_approval_checks(APPROVAL_ACTION_TYPES_0030)
    _create_guards()


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("0030 downgrade requires online preflight")
    _guard_downgrade()
    _drop_guards()
    _replace_approval_checks(APPROVAL_ACTION_TYPES_0030[:-1])
    op.drop_index(
        "ix_dataset_release_quality_events_scope_time", table_name="dataset_release_quality_events"
    )
    op.drop_index(
        "ix_dataset_release_quality_events_scope_stream",
        table_name="dataset_release_quality_events",
    )
    op.drop_table("dataset_release_quality_events")
    op.drop_index(
        "ix_dataset_release_quality_waivers_scope_release_channel",
        table_name="dataset_release_quality_waivers",
    )
    op.drop_table("dataset_release_quality_waivers")
    op.drop_index(
        "ix_dataset_release_quality_certification_evidence_scope_ordinal",
        table_name="dataset_release_quality_certification_evidence",
    )
    op.drop_table("dataset_release_quality_certification_evidence")
    op.drop_index(
        "ix_dataset_release_quality_certifications_scope_status_valid",
        table_name="dataset_release_quality_certifications",
    )
    op.drop_index(
        "ix_dataset_release_quality_certifications_scope_release_time",
        table_name="dataset_release_quality_certifications",
    )
    op.drop_table("dataset_release_quality_certifications")
    op.drop_index(
        "ix_dataset_quality_baseline_items_scope_ordinal",
        table_name="dataset_quality_baseline_items",
    )
    op.drop_table("dataset_quality_baseline_items")
    op.drop_index(
        "ix_dataset_quality_baselines_scope_name_revision", table_name="dataset_quality_baselines"
    )
    op.drop_table("dataset_quality_baselines")
    op.drop_index(
        "ix_tenant_release_quality_gate_policies_tenant_updated",
        table_name="tenant_release_quality_gate_policies",
    )
    op.drop_index(
        "ix_tenant_release_quality_gate_policies_scope_status",
        table_name="tenant_release_quality_gate_policies",
    )
    op.drop_table("tenant_release_quality_gate_policies")
