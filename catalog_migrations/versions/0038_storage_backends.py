"""Tenant-scoped storage backend registry.

Revision ID: 0038_storage_backends
Revises: 0037_qa_faq_ops
Create Date: 2026-09-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_storage_backends"
down_revision: str | None = "0037_qa_faq_ops"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "storage_backends",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False, server_default="local"),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("source", sa.String(16), nullable=False, server_default="user"),
        sa.Column("is_deleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_storage_backends_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_storage_backends_tenant_id"),
        sa.CheckConstraint(
            "provider IN ('local','minio','s3','cos','oss','tos','obs')",
            name="ck_storage_backends_provider",
        ),
        sa.CheckConstraint("status IN ('active','disabled')", name="ck_storage_backends_status"),
        sa.CheckConstraint("source IN ('user','env')", name="ck_storage_backends_source"),
        sa.CheckConstraint("is_deleted IN (0,1)", name="ck_storage_backends_is_deleted"),
    )
    op.create_index(
        "ix_storage_backends_tenant_live",
        "storage_backends",
        ["tenant_id", "is_deleted", "name"],
    )
    op.create_index(
        "ix_storage_backends_tenant_provider",
        "storage_backends",
        ["tenant_id", "provider"],
    )

    with op.batch_alter_table("tenants") as batch:
        batch.add_column(sa.Column("default_storage_backend_id", sa.String(64), nullable=True))
    with op.batch_alter_table("datasets") as batch:
        batch.add_column(sa.Column("storage_backend_id", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("datasets") as batch:
        batch.drop_column("storage_backend_id")
    with op.batch_alter_table("tenants") as batch:
        batch.drop_column("default_storage_backend_id")
    op.drop_index("ix_storage_backends_tenant_provider", table_name="storage_backends")
    op.drop_index("ix_storage_backends_tenant_live", table_name="storage_backends")
    op.drop_table("storage_backends")
