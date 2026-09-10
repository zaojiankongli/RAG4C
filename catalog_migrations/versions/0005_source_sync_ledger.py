"""Add durable external source synchronization ledger.

Revision ID: 0005_source_sync
Revises: 0004_index_ops
Create Date: 2026-08-24
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0005_source_sync"
down_revision: str | None = "0004_index_ops"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "data_sources",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("effective_config", sa.JSON(), nullable=True),
        sa.Column("config_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("last_cursor", sa.JSON(), nullable=True),
        sa.Column("last_result", sa.JSON(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_data_sources_tenant_name"),
    )
    for name, columns in (
        ("ix_data_sources_tenant_id", ["tenant_id"]),
        ("ix_data_sources_dataset_id", ["dataset_id"]),
        ("ix_data_sources_status", ["status"]),
        ("ix_data_sources_dataset_status", ["dataset_id", "status"]),
    ):
        op.create_index(name, "data_sources", columns, unique=False)

    op.create_table(
        "source_sync_runs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column(sa.quoted_name("trigger", True), sa.String(length=24), nullable=False),
        sa.Column("force_full", sa.Integer(), nullable=False),
        sa.Column("dry_run", sa.Integer(), nullable=False),
        sa.Column("cursor_before", sa.JSON(), nullable=True),
        sa.Column("cursor_after", sa.JSON(), nullable=True),
        sa.Column("fetched", sa.Integer(), nullable=False),
        sa.Column("ingested", sa.Integer(), nullable=False),
        sa.Column("skipped", sa.Integer(), nullable=False),
        sa.Column("removed", sa.Integer(), nullable=False),
        sa.Column("chunks", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("fetch_error", sa.Text(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["data_sources.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns in (
        ("ix_source_sync_runs_source_id", ["source_id"]),
        ("ix_source_sync_runs_tenant_id", ["tenant_id"]),
        ("ix_source_sync_runs_dataset_id", ["dataset_id"]),
        ("ix_source_sync_runs_status", ["status"]),
        ("ix_source_sync_runs_source_started", ["source_id", "started_at"]),
        ("ix_source_sync_runs_status_started", ["status", "started_at"]),
    ):
        op.create_index(name, "source_sync_runs", columns, unique=False)

    op.create_table(
        "source_sync_items",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=512), nullable=False),
        sa.Column("doc_id", sa.String(length=128), nullable=False),
        sa.Column("source_uri", sa.String(length=1024), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("result", sa.String(length=24), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["source_sync_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "external_id", name="uq_source_sync_items_run_external"),
    )
    for name, columns in (
        ("ix_source_sync_items_run_id", ["run_id"]),
        ("ix_source_sync_items_source_id", ["source_id"]),
        ("ix_source_sync_items_doc_id", ["doc_id"]),
        ("ix_source_sync_items_result", ["result"]),
        ("ix_source_sync_items_source_result", ["source_id", "result"]),
    ):
        op.create_index(name, "source_sync_items", columns, unique=False)

    op.create_table(
        "source_document_states",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("doc_id", sa.String(length=128), nullable=False),
        sa.Column("external_id", sa.String(length=512), nullable=False),
        sa.Column("source_uri", sa.String(length=1024), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("last_run_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["data_sources.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "doc_id", name="uq_source_document_state_doc"),
    )
    for name, columns in (
        ("ix_source_document_states_source_id", ["source_id"]),
        ("ix_source_document_state_source_updated", ["source_id", "updated_at"]),
    ):
        op.create_index(name, "source_document_states", columns, unique=False)


def downgrade() -> None:
    op.drop_table("source_document_states")
    op.drop_table("source_sync_items")
    op.drop_table("source_sync_runs")
    op.drop_table("data_sources")
