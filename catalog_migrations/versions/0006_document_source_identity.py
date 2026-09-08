"""Add stable source identity and logical folder paths to documents.

Revision ID: 0006_source_id
Revises: 0005_source_sync
Create Date: 2026-08-24
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0006_source_id"
down_revision: str | None = "0005_source_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("source_uri", sa.String(length=1024), nullable=True))
        batch.add_column(sa.Column("source_uri_hash", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("external_id", sa.String(length=512), nullable=True))
        batch.add_column(sa.Column("logical_folder_path", sa.String(length=1024), nullable=True))
        batch.add_column(sa.Column("source_type", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("source_id", sa.String(length=64), nullable=True))
        batch.create_index("ix_documents_source_id", ["source_id"], unique=False)
        batch.create_unique_constraint(
            "uq_documents_dataset_source_uri_hash", ["dataset_id", "source_uri_hash"]
        )
        batch.create_unique_constraint(
            "uq_documents_dataset_source_external",
            ["dataset_id", "source_id", "external_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_constraint("uq_documents_dataset_source_external", type_="unique")
        batch.drop_constraint("uq_documents_dataset_source_uri_hash", type_="unique")
        batch.drop_index("ix_documents_source_id")
        batch.drop_column("source_id")
        batch.drop_column("source_type")
        batch.drop_column("logical_folder_path")
        batch.drop_column("external_id")
        batch.drop_column("source_uri_hash")
        batch.drop_column("source_uri")
