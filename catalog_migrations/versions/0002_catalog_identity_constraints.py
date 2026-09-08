"""Add catalog identity constraints after duplicate audit.

Revision ID: 0002_identity
Revises: 0001_base
Create Date: 2026-08-24
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_identity"
down_revision: str | None = "0001_base"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tenant_members") as batch:
        batch.create_unique_constraint(
            "uq_tenant_members_account_tenant", ["account_id", "tenant_id"]
        )
    with op.batch_alter_table("document_segments") as batch:
        batch.create_unique_constraint(
            "uq_document_segments_document_seq", ["document_id", "seq"]
        )
    with op.batch_alter_table("metadata_fields") as batch:
        batch.create_unique_constraint(
            "uq_metadata_fields_dataset_key", ["dataset_id", "key"]
        )


def downgrade() -> None:
    with op.batch_alter_table("metadata_fields") as batch:
        batch.drop_constraint("uq_metadata_fields_dataset_key", type_="unique")
    with op.batch_alter_table("document_segments") as batch:
        batch.drop_constraint("uq_document_segments_document_seq", type_="unique")
    with op.batch_alter_table("tenant_members") as batch:
        batch.drop_constraint("uq_tenant_members_account_tenant", type_="unique")
