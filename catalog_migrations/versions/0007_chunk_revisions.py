"""Add authoritative chunk heads and immutable revisions.

Revision ID: 0007_chunk_rev
Revises: 0006_source_id
Create Date: 2026-08-24
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0007_chunk_rev"
down_revision: str | None = "0006_source_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chunk_heads",
        sa.Column("id", sa.String(length=512), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.String(length=64), nullable=False),
        sa.Column("parent_chunk_id", sa.String(length=512), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_role", sa.String(length=16), nullable=False),
        sa.Column("document_revision", sa.Integer(), nullable=False),
        sa.Column("content_revision", sa.Integer(), nullable=False),
        sa.Column("source_content", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("desired_index_revision", sa.Integer(), nullable=False),
        sa.Column("indexed_revision", sa.Integer(), nullable=False),
        sa.Column("index_status", sa.String(length=24), nullable=False),
        sa.Column("editor_id", sa.String(length=64), nullable=False),
        sa.Column("edit_source", sa.String(length=24), nullable=False),
        sa.Column("context_header", sa.Text(), nullable=False),
        sa.Column("chunk_metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "document_revision",
            "chunk_index",
            name="uq_chunk_heads_document_revision_index",
        ),
    )
    for name, columns in (
        ("ix_chunk_heads_tenant_id", ["tenant_id"]),
        ("ix_chunk_heads_dataset_id", ["dataset_id"]),
        ("ix_chunk_heads_document_id", ["document_id"]),
        ("ix_chunk_heads_content_hash", ["content_hash"]),
        ("ix_chunk_heads_index_status", ["index_status"]),
        ("ix_chunk_heads_projection", ["document_id", "document_revision", "enabled"]),
        ("ix_chunk_heads_parent", ["parent_chunk_id"]),
    ):
        op.create_index(name, "chunk_heads", columns, unique=False)

    op.create_table(
        "chunk_revisions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("chunk_id", sa.String(length=512), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("editor_id", sa.String(length=64), nullable=False),
        sa.Column("edit_source", sa.String(length=24), nullable=False),
        sa.Column("edited_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunk_heads.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chunk_id", "revision", name="uq_chunk_revisions_chunk_revision"),
    )
    for name, columns in (
        ("ix_chunk_revisions_chunk_id", ["chunk_id"]),
        ("ix_chunk_revisions_tenant_id", ["tenant_id"]),
        ("ix_chunk_revisions_dataset_id", ["dataset_id"]),
        ("ix_chunk_revisions_document_id", ["document_id"]),
        ("ix_chunk_revisions_chunk_edited", ["chunk_id", "edited_at"]),
    ):
        op.create_index(name, "chunk_revisions", columns, unique=False)


def downgrade() -> None:
    op.drop_table("chunk_revisions")
    op.drop_table("chunk_heads")
