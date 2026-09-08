"""Add authoritative knowledge-base dataset profiles.

Revision ID: 0010_dataset_profile
Revises: 0009_content
Create Date: 2026-08-25
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "0010_dataset_profile"
down_revision: str | None = "0009_content"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _datetime6():
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")

def upgrade() -> None:
    with op.batch_alter_table("datasets") as batch:
        batch.add_column(
            sa.Column(
                "profile_revision",
                sa.Integer(),
                nullable=False,
                server_default="1",
            )
        )
        batch.add_column(sa.Column("owner_id", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column(
                "visibility",
                sa.String(length=16),
                nullable=False,
                server_default="private",
            )
        )
        for name in (
            "profile_json",
            "parser_policy",
            "chunk_policy",
            "retrieval_policy",
            "retention_policy",
            "metadata_policy",
        ):
            batch.add_column(sa.Column(name, sa.JSON(), nullable=True))
        batch.add_column(
            sa.Column(
                "default_language",
                sa.String(length=32),
                nullable=False,
                server_default="zh-CN",
            )
        )
        batch.add_column(
            sa.Column(
                "graph_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column(
                "qa_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch.add_column(sa.Column("archived_at", _datetime6(), nullable=True))
        batch.add_column(sa.Column("archived_by", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("updated_at", _datetime6(), nullable=True))
        batch.create_check_constraint(
            "ck_datasets_profile_revision_positive",
            "profile_revision > 0",
        )
        batch.create_check_constraint(
            "ck_datasets_visibility",
            "visibility IN ('private', 'tenant', 'public')",
        )
        batch.create_check_constraint(
            "ck_datasets_status",
            "status IN ('active', 'archived', 'disabled')",
        )
        batch.create_foreign_key(
            "fk_datasets_scope_owner_member",
            "tenant_members",
            ["owner_id", "tenant_id"],
            ["account_id", "tenant_id"],
        )
        batch.create_index(
            "ix_datasets_scope_status",
            ["tenant_id", "status", "id"],
        )
        batch.create_index(
            "ix_datasets_scope_visibility",
            ["tenant_id", "visibility", "id"],
        )
        batch.create_index(
            "ix_datasets_scope_owner",
            ["tenant_id", "owner_id", "id"],
        )
        batch.create_index(
            "ix_datasets_scope_updated",
            ["tenant_id", "updated_at", "id"],
        )

    op.execute(
        sa.text(
            "UPDATE datasets SET updated_at=created_at "
            "WHERE updated_at IS NULL"
        )
    )

    json_columns = (
        "profile_json",
        "parser_policy",
        "chunk_policy",
        "retrieval_policy",
        "retention_policy",
        "metadata_policy",
    )
    for name in json_columns:
        op.execute(sa.text(f"UPDATE datasets SET {name}='{{}}' WHERE {name} IS NULL"))
    with op.batch_alter_table("datasets") as batch:
        for name in json_columns:
            batch.alter_column(name, existing_type=sa.JSON(), nullable=False)
        batch.alter_column(
            "updated_at",
            existing_type=_datetime6(),
            nullable=False,
            server_default=None,
        )


def downgrade() -> None:
    with op.batch_alter_table("datasets") as batch:
        batch.drop_index("ix_datasets_scope_updated")
        batch.drop_index("ix_datasets_scope_owner")
        batch.drop_index("ix_datasets_scope_visibility")
        batch.drop_index("ix_datasets_scope_status")
        batch.drop_constraint("fk_datasets_scope_owner_member", type_="foreignkey")
        batch.drop_constraint("ck_datasets_status", type_="check")
        batch.drop_constraint("ck_datasets_visibility", type_="check")
        batch.drop_constraint("ck_datasets_profile_revision_positive", type_="check")
        batch.drop_column("updated_at")
        batch.drop_column("archived_by")
        batch.drop_column("archived_at")
        batch.drop_column("qa_enabled")
        batch.drop_column("graph_enabled")
        batch.drop_column("default_language")
        batch.drop_column("metadata_policy")
        batch.drop_column("retention_policy")
        batch.drop_column("retrieval_policy")
        batch.drop_column("chunk_policy")
        batch.drop_column("parser_policy")
        batch.drop_column("profile_json")
        batch.drop_column("visibility")
        batch.drop_column("owner_id")
        batch.drop_column("profile_revision")
