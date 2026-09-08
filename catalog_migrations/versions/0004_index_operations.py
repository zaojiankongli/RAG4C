"""Add durable index operations and dead letters.

Revision ID: 0004_index_ops
Revises: 0003_ingest
Create Date: 2026-08-24
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0004_index_ops"
down_revision: str | None = "0003_ingest"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "index_operations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.String(length=64), nullable=False),
        sa.Column("attempt_id", sa.String(length=64), nullable=False),
        sa.Column("target_store", sa.String(length=32), nullable=False),
        sa.Column("operation", sa.String(length=24), nullable=False),
        sa.Column("dedup_key", sa.String(length=255), nullable=False),
        sa.Column("target_revision", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("claimed_by", sa.String(length=64), nullable=False),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("max_retries", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["attempt_id"], ["document_ingest_attempts.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedup_key", name="uq_index_operations_dedup_key"),
    )
    for name, columns in (
        ("ix_index_operations_tenant_id", ["tenant_id"]),
        ("ix_index_operations_dataset_id", ["dataset_id"]),
        ("ix_index_operations_document_id", ["document_id"]),
        ("ix_index_operations_attempt_id", ["attempt_id"]),
        ("ix_index_operations_status", ["status"]),
        ("ix_index_operations_lease_until", ["lease_until"]),
        ("ix_index_operations_next_retry_at", ["next_retry_at"]),
        ("ix_index_operations_ready", ["status", "next_retry_at", "created_at"]),
        ("ix_index_operations_document_revision", ["document_id", "target_revision"]),
        ("ix_index_operations_lease", ["status", "lease_until"]),
    ):
        op.create_index(name, "index_operations", columns, unique=False)

    op.create_table(
        "index_dead_letters",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("operation_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.String(length=64), nullable=False),
        sa.Column("attempt_id", sa.String(length=64), nullable=False),
        sa.Column("dedup_key", sa.String(length=255), nullable=False),
        sa.Column("target_store", sa.String(length=32), nullable=False),
        sa.Column("operation", sa.String(length=24), nullable=False),
        sa.Column("target_revision", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=64), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("failed_at", sa.DateTime(), nullable=False),
        sa.Column("requeued_to_operation_id", sa.String(length=64), nullable=True),
        sa.Column("operator_note", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["operation_id"], ["index_operations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id", name="uq_index_dead_letters_operation"),
    )
    for name, columns in (
        ("ix_index_dead_letters_operation_id", ["operation_id"]),
        ("ix_index_dead_letters_tenant_id", ["tenant_id"]),
        ("ix_index_dead_letters_dataset_id", ["dataset_id"]),
        ("ix_index_dead_letters_document_id", ["document_id"]),
        ("ix_index_dead_letters_attempt_id", ["attempt_id"]),
        ("ix_index_dead_letters_document_failed", ["document_id", "failed_at"]),
        ("ix_index_dead_letters_target_failed", ["target_store", "failed_at"]),
    ):
        op.create_index(name, "index_dead_letters", columns, unique=False)


def downgrade() -> None:
    op.drop_table("index_dead_letters")
    op.drop_table("index_operations")
