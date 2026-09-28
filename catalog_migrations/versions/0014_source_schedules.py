"""Add authoritative fixed-interval source schedules.

Revision ID: 0014_source_schedules
Revises: 0013_source_control
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql
from core.dialects import boolean_check_sql

revision: str = "0014_source_schedules"
down_revision: str | None = "0013_source_control"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _datetime6():
    return (
        sa.DateTime()
        .with_variant(mysql.DATETIME(fsp=6), "mysql")
        .with_variant(mysql.DATETIME(fsp=6), "mariadb")
    )


def upgrade() -> None:
    with op.batch_alter_table("data_sources") as batch:
        batch.create_unique_constraint(
            "uq_data_sources_scope_id", ["tenant_id", "dataset_id", "id"]
        )

    op.create_table(
        "source_schedules",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("force_full", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", _datetime6(), nullable=False),
        sa.Column("last_enqueued_at", _datetime6(), nullable=True),
        sa.Column("last_run_id", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", _datetime6(), nullable=False),
        sa.Column("updated_at", _datetime6(), nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_source_schedules_revision_positive"),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'archived')",
            name="ck_source_schedules_status",
        ),
        sa.CheckConstraint(
            "interval_seconds >= 300 AND interval_seconds <= 604800",
            name="ck_source_schedules_interval",
        ),
        sa.CheckConstraint(
            # 布尔字面量按方言取：PG 的 boolean 不能与整数比较，写成 (0, 1) 会
            # 直接报 "operator does not exist: boolean = integer"。
            boolean_check_sql("force_full", op.get_bind().dialect.name),
            name="ck_source_schedules_force_full",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_source_schedules_scope_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "source_id"],
            ["data_sources.tenant_id", "data_sources.dataset_id", "data_sources.id"],
            name="fk_source_schedules_scope_source",
        ),
        sa.ForeignKeyConstraint(
            ["last_run_id"],
            ["source_sync_runs.id"],
            name="fk_source_schedules_last_run",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", name="uq_source_schedules_source"),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "source_id",
            "id",
            name="uq_source_schedules_scope_id",
        ),
    )
    op.create_index(
        "ix_source_schedules_due",
        "source_schedules",
        ["status", "next_run_at", "id"],
    )
    op.create_index(
        "ix_source_schedules_status",
        "source_schedules",
        ["tenant_id", "dataset_id", "status", "id"],
    )
    op.create_index(
        "ix_source_schedules_scope",
        "source_schedules",
        ["tenant_id", "dataset_id", "source_id"],
    )

    with op.batch_alter_table("source_sync_runs") as batch:
        batch.add_column(sa.Column("schedule_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("schedule_revision", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("planned_at", _datetime6(), nullable=True))

    # Pre-0014 scheduled runs have no schedule authority to bind to. Preserve
    # their durable history while preventing them from masquerading as governed
    # schedule intent under the new all-or-none metadata constraint.
    source_sync_runs = sa.table(
        "source_sync_runs",
        sa.column(sa.quoted_name("trigger", True), sa.String(length=24)),
    )
    op.execute(
        source_sync_runs.update()
        .where(source_sync_runs.c.trigger == "scheduled")
        .values(trigger="manual")
    )

    trigger_column = sa.column(sa.quoted_name("trigger", True), sa.String(length=24))
    schedule_id_column = sa.column("schedule_id", sa.String(length=64))
    schedule_revision_column = sa.column("schedule_revision", sa.Integer())
    planned_at_column = sa.column("planned_at", _datetime6())
    schedule_metadata_check = sa.or_(
        sa.and_(
            trigger_column == "scheduled",
            schedule_id_column.is_not(None),
            schedule_revision_column.is_not(None),
            planned_at_column.is_not(None),
        ),
        sa.and_(
            trigger_column.op("<>")("scheduled"),
            schedule_id_column.is_(None),
            schedule_revision_column.is_(None),
            planned_at_column.is_(None),
        ),
    )

    with op.batch_alter_table("source_sync_runs") as batch:
        batch.create_foreign_key(
            "fk_source_sync_runs_schedule",
            "source_schedules",
            ["tenant_id", "dataset_id", "source_id", "schedule_id"],
            ["tenant_id", "dataset_id", "source_id", "id"],
        )
        batch.create_check_constraint(
            "ck_source_sync_runs_schedule_revision",
            "schedule_revision IS NULL OR schedule_revision > 0",
        )
        batch.create_check_constraint(
            "ck_source_sync_runs_schedule_metadata",
            schedule_metadata_check,
        )
        batch.create_index(
            "ix_source_sync_runs_schedule_planned",
            ["schedule_id", "schedule_revision", "planned_at"],
        )


def downgrade() -> None:
    with op.batch_alter_table("source_sync_runs") as batch:
        batch.drop_index("ix_source_sync_runs_schedule_planned")
        batch.drop_constraint("ck_source_sync_runs_schedule_metadata", type_="check")
        batch.drop_constraint("ck_source_sync_runs_schedule_revision", type_="check")
        batch.drop_constraint("fk_source_sync_runs_schedule", type_="foreignkey")
        batch.drop_column("planned_at")
        batch.drop_column("schedule_revision")
        batch.drop_column("schedule_id")

    op.drop_index("ix_source_schedules_scope", table_name="source_schedules")
    op.drop_index("ix_source_schedules_status", table_name="source_schedules")
    op.drop_index("ix_source_schedules_due", table_name="source_schedules")
    op.drop_table("source_schedules")
    with op.batch_alter_table("data_sources") as batch:
        batch.drop_constraint("uq_data_sources_scope_id", type_="unique")
