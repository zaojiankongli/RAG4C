"""Add tenant organization, groups, invitations, and dataset access grants.

Revision ID: 0017_enterprise_access_graph
Revises: 0016_enterprise_membership
Create Date: 2026-08-26
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "0017_enterprise_access_graph"
down_revision: str | None = "0016_enterprise_membership"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ACCESS_GRAPH_INDEX_SPECS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "tenant_organization_units": (
        (
            "ix_tenant_organization_units_tenant_parent_status",
            ("tenant_id", "parent_id", "status", "sort_order", "id"),
        ),
        (
            "ix_tenant_organization_units_tenant_status",
            ("tenant_id", "status", "sort_order", "id"),
        ),
    ),
    "tenant_groups": (
        (
            "ix_tenant_groups_tenant_status_name",
            ("tenant_id", "status", "normalized_name", "id"),
        ),
    ),
    "tenant_group_members": (
        (
            "ix_tenant_group_members_tenant_account",
            ("tenant_id", "account_id", "id"),
        ),
        (
            "ix_tenant_group_members_tenant_group",
            ("tenant_id", "group_id", "id"),
        ),
    ),
    "dataset_access_grants": (
        (
            "ix_dataset_access_grants_tenant_dataset_status",
            ("tenant_id", "dataset_id", "status", "id"),
        ),
        (
            "ix_dataset_access_grants_tenant_subject",
            ("tenant_id", "subject_type", "subject_id", "id"),
        ),
    ),
    "tenant_invitations": (
        (
            "ix_tenant_invitations_tenant_status_expires",
            ("tenant_id", "status", "expires_at", "id"),
        ),
        (
            "ix_tenant_invitations_tenant_email",
            ("tenant_id", "normalized_email", "id"),
        ),
    ),
}


def _datetime6():
    return (
        sa.DateTime()
        .with_variant(mysql.DATETIME(fsp=6), "mysql")
        .with_variant(mysql.DATETIME(fsp=6), "mariadb")
    )


def _create_indexes(table_name: str) -> None:
    for name, columns in ACCESS_GRAPH_INDEX_SPECS[table_name]:
        op.create_index(name, table_name, list(columns), unique=False)


def upgrade() -> None:
    op.create_table(
        "tenant_organization_units",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("parent_id", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
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
            server_default=sa.text("CURRENT_TIMESTAMP"),
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
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_by",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'system'"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_tenant_organization_units_status",
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_tenant_organization_units_revision_positive",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_tenant_organization_units_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "parent_id"],
            [
                "tenant_organization_units.tenant_id",
                "tenant_organization_units.id",
            ],
            name="fk_tenant_organization_units_scope_parent",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_tenant_organization_units_scope_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "code",
            name="uq_tenant_organization_units_tenant_code",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "parent_id",
            "name",
            name="uq_tenant_organization_units_tenant_parent_name",
        ),
    )
    _create_indexes("tenant_organization_units")

    op.create_table(
        "tenant_groups",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("normalized_name", sa.String(length=256), nullable=False),
        sa.Column(
            "description",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
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
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            _datetime6(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_tenant_groups_status",
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_tenant_groups_revision_positive",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_tenant_groups_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tenant_groups_scope_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "normalized_name",
            name="uq_tenant_groups_tenant_normalized_name",
        ),
    )
    _create_indexes("tenant_groups")

    op.create_table(
        "tenant_group_members",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("group_id", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "created_at",
            _datetime6(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "created_by",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'system'"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'removed')",
            name="ck_tenant_group_members_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "group_id"],
            ["tenant_groups.tenant_id", "tenant_groups.id"],
            name="fk_tenant_group_members_scope_group",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_group_members_scope_account",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "group_id",
            "account_id",
            name="uq_tenant_group_members_group_account",
        ),
    )
    _create_indexes("tenant_group_members")

    op.create_table(
        "dataset_access_grants",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("subject_type", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
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
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            _datetime6(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "subject_type IN ('account', 'group', 'organization_unit')",
            name="ck_dataset_access_grants_subject_type",
        ),
        sa.CheckConstraint(
            "role IN ('viewer', 'editor', 'manager')",
            name="ck_dataset_access_grants_role",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_dataset_access_grants_status",
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_dataset_access_grants_revision_positive",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_dataset_access_grants_scope_dataset",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "subject_type",
            "subject_id",
            name="uq_dataset_access_grants_dataset_subject",
        ),
    )
    _create_indexes("dataset_access_grants")

    op.create_table(
        "tenant_invitations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=256), nullable=False),
        sa.Column("normalized_email", sa.String(length=256), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", _datetime6(), nullable=False),
        sa.Column("accepted_at", _datetime6(), nullable=True),
        sa.Column("accepted_by", sa.String(length=64), nullable=True),
        sa.Column("invited_by", sa.String(length=64), nullable=False),
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
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            _datetime6(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'editor', 'member')",
            name="ck_tenant_invitations_role",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'revoked', 'expired')",
            name="ck_tenant_invitations_status",
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_tenant_invitations_revision_positive",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_tenant_invitations_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_invitations_scope_inviter",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_invitations_scope_acceptor",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    _create_indexes("tenant_invitations")


def downgrade() -> None:
    op.drop_table("tenant_invitations")
    op.drop_table("dataset_access_grants")
    op.drop_table("tenant_group_members")
    op.drop_table("tenant_groups")
    op.drop_table("tenant_organization_units")
