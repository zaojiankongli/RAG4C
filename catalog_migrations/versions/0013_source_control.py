"""Add source control-plane idempotency and retry authority.

Revision ID: 0013_source_control
Revises: 0012_retrieval_experiments
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "0013_source_control"
down_revision: str | None = "0012_retrieval_experiments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _datetime6():
    return (
        sa.DateTime()
        .with_variant(mysql.DATETIME(fsp=6), "mysql")
        .with_variant(mysql.DATETIME(fsp=6), "mariadb")
    )


def upgrade() -> None:
    dialect_name = op.get_bind().dialect.name
    mysql_family = dialect_name in {"mysql", "mariadb"}
    with op.batch_alter_table("source_sync_runs") as batch:
        batch.add_column(
            sa.Column(
                "idempotency_key",
                sa.String(length=128).with_variant(
                    mysql.VARCHAR(length=128, collation="utf8mb4_bin"), "mysql"
                ),
                nullable=True,
            )
        )
        batch.add_column(sa.Column("request_hash", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("retry_of_run_id", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column(
                "execution_state",
                sa.String(length=16),
                nullable=False,
                server_default="completed",
            )
        )
        batch.add_column(
            sa.Column("execution_owner", sa.String(length=128), nullable=False, server_default="")
        )
        batch.add_column(sa.Column("execution_lease_until", _datetime6(), nullable=True))
        batch.add_column(sa.Column("execution_heartbeat_at", _datetime6(), nullable=True))
        batch.add_column(
            sa.Column("execution_attempts", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column(
                "execution_last_error",
                sa.Text(),
                nullable=True if mysql_family else False,
                server_default=None if mysql_family else "",
            )
        )
        batch.add_column(sa.Column("execution_started_at", _datetime6(), nullable=True))
        batch.add_column(sa.Column("execution_finished_at", _datetime6(), nullable=True))
        batch.add_column(sa.Column("execution_next_attempt_at", _datetime6(), nullable=True))
        batch.add_column(
            sa.Column("reservation_owner", sa.String(length=128), nullable=False, server_default="")
        )
        batch.add_column(sa.Column("reservation_lease_until", _datetime6(), nullable=True))
        batch.add_column(
            sa.Column("reservation_attempts", sa.Integer(), nullable=False, server_default="0")
        )
        batch.create_unique_constraint(
            "uq_source_sync_runs_idempotency",
            ["source_id", "source_generation", "dataset_generation", "idempotency_key"],
        )
        batch.create_unique_constraint(
            "uq_source_sync_runs_retry_of",
            ["retry_of_run_id"],
        )
        batch.create_foreign_key(
            "fk_source_sync_runs_retry_of",
            "source_sync_runs",
            ["retry_of_run_id"],
            ["id"],
        )
        batch.create_check_constraint(
            "ck_source_sync_runs_idempotency_pair",
            "(idempotency_key IS NULL AND request_hash IS NULL) OR "
            "(idempotency_key IS NOT NULL AND request_hash IS NOT NULL)",
        )
        batch.create_check_constraint(
            "ck_source_sync_runs_retry_not_self",
            "retry_of_run_id IS NULL OR retry_of_run_id <> id",
        )
        trigger_column = sa.column(sa.quoted_name("trigger", True), sa.String(length=16))
        retry_of_column = sa.column("retry_of_run_id", sa.String(length=64))
        batch.create_check_constraint(
            "ck_source_sync_runs_retry_trigger",
            sa.or_(
                sa.and_(trigger_column == "retry", retry_of_column.is_not(None)),
                sa.and_(trigger_column != "retry", retry_of_column.is_(None)),
            ),
        )
        batch.create_check_constraint(
            "ck_source_sync_runs_execution_state",
            "execution_state IN ('pending', 'executing', 'failed', 'completed')",
        )
        batch.create_check_constraint(
            "ck_source_sync_runs_execution_attempts",
            "execution_attempts >= 0",
        )
        batch.create_check_constraint(
            "ck_source_sync_runs_execution_owner",
            "(execution_state = 'executing' AND execution_owner <> '' "
            "AND execution_lease_until IS NOT NULL AND execution_heartbeat_at IS NOT NULL) OR "
            "(execution_state <> 'executing' AND execution_owner = '' "
            "AND execution_lease_until IS NULL AND execution_heartbeat_at IS NULL)",
        )
        batch.create_check_constraint(
            "ck_source_sync_runs_reservation_attempts",
            "reservation_attempts >= 0",
        )
        batch.create_check_constraint(
            "ck_source_sync_runs_reservation_owner",
            "(reservation_owner = '' AND reservation_lease_until IS NULL) OR "
            "(reservation_owner <> '' AND reservation_lease_until IS NOT NULL)",
        )

    if mysql_family:
        op.execute(
            "UPDATE source_sync_runs SET execution_last_error = '' "
            "WHERE execution_last_error IS NULL"
        )
        op.alter_column(
            "source_sync_runs",
            "execution_last_error",
            existing_type=sa.Text(),
            nullable=False,
        )

    legacy_runs = sa.table(
        "source_sync_runs",
        sa.column("status", sa.String(length=24)),
        sa.column("execution_state", sa.String(length=16)),
        sa.column("execution_next_attempt_at", sa.DateTime()),
    )
    op.execute(
        legacy_runs.update()
        .where(legacy_runs.c.status == "running")
        .values(execution_state="pending", execution_next_attempt_at=sa.func.current_timestamp())
    )
    op.execute(
        legacy_runs.update()
        .where(legacy_runs.c.status != "running")
        .values(execution_state="completed", execution_next_attempt_at=None)
    )


def downgrade() -> None:
    # Older schemas have no retry lineage column. Normalize retry rows before any
    # schema change so a populated downgrade remains semantically valid.
    source_sync_runs = sa.table(
        "source_sync_runs",
        sa.column("status", sa.String(length=24)),
        sa.column(sa.quoted_name("trigger", True), sa.String(length=16)),
        sa.column("retry_of_run_id", sa.String(length=64)),
        sa.column("finished_at", sa.DateTime()),
        sa.column("fetch_error", sa.Text()),
    )
    op.execute(
        source_sync_runs.update()
        .where(source_sync_runs.c.status == "running")
        .values(
            status="failed",
            finished_at=sa.func.current_timestamp(),
            fetch_error="source execution interrupted by 0013 downgrade",
        )
    )
    op.execute(
        source_sync_runs.update()
        .where(source_sync_runs.c.retry_of_run_id.is_not(None))
        .values(trigger="manual", retry_of_run_id=None)
    )
    with op.batch_alter_table("source_sync_runs") as batch:
        batch.drop_constraint("ck_source_sync_runs_reservation_owner", type_="check")
        batch.drop_constraint("ck_source_sync_runs_reservation_attempts", type_="check")
        batch.drop_constraint("ck_source_sync_runs_execution_owner", type_="check")
        batch.drop_constraint("ck_source_sync_runs_execution_attempts", type_="check")
        batch.drop_constraint("ck_source_sync_runs_execution_state", type_="check")
        batch.drop_constraint("ck_source_sync_runs_retry_trigger", type_="check")
        batch.drop_constraint("ck_source_sync_runs_retry_not_self", type_="check")
        batch.drop_constraint("ck_source_sync_runs_idempotency_pair", type_="check")
        batch.drop_constraint("fk_source_sync_runs_retry_of", type_="foreignkey")
        batch.drop_constraint("uq_source_sync_runs_retry_of", type_="unique")
        batch.drop_constraint("uq_source_sync_runs_idempotency", type_="unique")
        batch.drop_column("reservation_attempts")
        batch.drop_column("reservation_lease_until")
        batch.drop_column("reservation_owner")
        batch.drop_column("execution_next_attempt_at")
        batch.drop_column("execution_finished_at")
        batch.drop_column("execution_started_at")
        batch.drop_column("execution_last_error")
        batch.drop_column("execution_attempts")
        batch.drop_column("execution_heartbeat_at")
        batch.drop_column("execution_lease_until")
        batch.drop_column("execution_owner")
        batch.drop_column("execution_state")
        batch.drop_column("retry_of_run_id")
        batch.drop_column("request_hash")
        batch.drop_column("idempotency_key")
