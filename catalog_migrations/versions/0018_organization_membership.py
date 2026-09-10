"""Add tenant organization-unit membership assignments.

Revision ID: 0018_organization_membership
Revises: 0017_enterprise_access_graph
Create Date: 2026-08-26
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "0018_organization_membership"
down_revision: str | None = "0017_enterprise_access_graph"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ORGANIZATION_MEMBERSHIP_INDEX_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "ix_tenant_organization_unit_members_tenant_unit_status",
        ("tenant_id", "organization_unit_id", "status", "id"),
    ),
    (
        "ix_tenant_organization_unit_members_tenant_account_status",
        ("tenant_id", "account_id", "status", "id"),
    ),
)


def _datetime6():
    return (
        sa.DateTime()
        .with_variant(mysql.DATETIME(fsp=6), "mysql")
        .with_variant(mysql.DATETIME(fsp=6), "mariadb")
    )


def upgrade() -> None:
    _is_mysql = op.get_bind().dialect.name == "mysql"
    op.create_table(
        "tenant_organization_unit_members",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("organization_unit_id", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "created_at",
            _datetime6(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "created_by",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'system'"),
        ),
        sa.Column(
            "updated_at",
            _datetime6(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_by",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'system'"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'removed')",
            name="ck_tenant_organization_unit_members_status",
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_tenant_organization_unit_members_revision_positive",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "organization_unit_id"],
            ["tenant_organization_units.tenant_id", "tenant_organization_units.id"],
            name="fk_tenant_organization_unit_members_scope_unit",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_organization_unit_members_scope_account",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "organization_unit_id",
            "account_id",
            name="uq_tenant_organization_unit_members_unit_account",
        ),
    )
    for name, columns in ORGANIZATION_MEMBERSHIP_INDEX_SPECS:
        op.create_index(name, "tenant_organization_unit_members", list(columns), unique=False)


def downgrade() -> None:
    op.drop_table("tenant_organization_unit_members")
