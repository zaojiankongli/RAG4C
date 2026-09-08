"""Add document revisions and ingest attempt/span history.

Revision ID: 0003_ingest
Revises: 0002_identity
Create Date: 2026-08-24
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0003_ingest"
down_revision: str | None = "0002_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("content_revision", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("desired_index_revision", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("indexed_revision", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("graph_revision", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("current_attempt_id", sa.String(length=64), nullable=True))
        batch.create_index("ix_documents_current_attempt_id", ["current_attempt_id"], unique=False)

    op.create_table(
        "document_ingest_attempts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.String(length=64), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("input_content_hash", sa.String(length=64), nullable=False),
        sa.Column("input_revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("primary_index_ready_at", sa.DateTime(), nullable=True),
        sa.Column("pending_subtasks", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("worker_id", sa.String(length=64), nullable=False),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "attempt_no", name="uq_ingest_attempt_document_no"),
    )
    op.create_index("ix_document_ingest_attempts_tenant_id", "document_ingest_attempts", ["tenant_id"], unique=False)
    op.create_index("ix_document_ingest_attempts_dataset_id", "document_ingest_attempts", ["dataset_id"], unique=False)
    op.create_index("ix_document_ingest_attempts_document_id", "document_ingest_attempts", ["document_id"], unique=False)
    op.create_index("ix_document_ingest_attempts_state", "document_ingest_attempts", ["state"], unique=False)
    op.create_index("ix_document_ingest_attempts_lease_until", "document_ingest_attempts", ["lease_until"], unique=False)
    op.create_index("ix_ingest_attempt_state_started", "document_ingest_attempts", ["state", "started_at"], unique=False)
    op.create_index("ix_ingest_attempt_document_created", "document_ingest_attempts", ["document_id", "created_at"], unique=False)

    op.create_table(
        "document_ingest_spans",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("attempt_id", sa.String(length=64), nullable=False),
        sa.Column("span_id", sa.String(length=64), nullable=False),
        sa.Column("parent_span_id", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("input", sa.JSON(), nullable=True),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("span_metadata", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["attempt_id"], ["document_ingest_attempts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attempt_id", "span_id", name="uq_ingest_span_attempt_span"),
    )
    op.create_index("ix_document_ingest_spans_attempt_id", "document_ingest_spans", ["attempt_id"], unique=False)
    op.create_index("ix_ingest_span_attempt_created", "document_ingest_spans", ["attempt_id", "created_at"], unique=False)
    op.create_index("ix_ingest_span_status_started", "document_ingest_spans", ["status", "started_at"], unique=False)
    op.create_index("ix_ingest_span_parent", "document_ingest_spans", ["parent_span_id"], unique=False)


def downgrade() -> None:
    op.drop_table("document_ingest_spans")
    op.drop_table("document_ingest_attempts")
    with op.batch_alter_table("documents") as batch:
        batch.drop_index("ix_documents_current_attempt_id")
        batch.drop_column("current_attempt_id")
        batch.drop_column("graph_revision")
        batch.drop_column("indexed_revision")
        batch.drop_column("desired_index_revision")
        batch.drop_column("content_revision")
