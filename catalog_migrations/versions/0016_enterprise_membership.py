"""Add enterprise member lifecycle and tenant-scoped audit history.

Revision ID: 0016_enterprise_membership
Revises: 0015_document_catalog_indexes
Create Date: 2026-08-26
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy import Connection, text
from sqlalchemy.dialects import mysql

revision: str = "0016_enterprise_membership"
down_revision: str | None = "0015_document_catalog_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


class EnterpriseMembershipPreflightError(RuntimeError):
    """Raised when existing membership data cannot satisfy 0016 invariants."""


TENANT_MEMBER_INDEX_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "ix_tenant_members_tenant_status_role_id",
        ("tenant_id", "status", "role", "id"),
    ),
    (
        "ix_tenant_members_tenant_account_status",
        ("tenant_id", "account_id", "status"),
    ),
)

# Keep both narrow lookup indexes and keyset-friendly composite indexes. Actor
# and target foreign keys are intentionally omitted: audit history must remain
# readable after an account is removed from the live directory.
ENTERPRISE_AUDIT_INDEX_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ix_tenant_audit_events_tenant_id", ("tenant_id",)),
    ("ix_tenant_audit_events_actor_id", ("actor_id",)),
    ("ix_tenant_audit_events_action", ("action",)),
    ("ix_tenant_audit_events_resource_type", ("resource_type",)),
    ("ix_tenant_audit_events_resource_id", ("resource_id",)),
    ("ix_tenant_audit_events_target_account_id", ("target_account_id",)),
    ("ix_tenant_audit_scope_sequence", ("tenant_id", "sequence")),
    (
        "ix_tenant_audit_scope_time",
        ("tenant_id", "occurred_at", "sequence"),
    ),
    (
        "ix_tenant_audit_actor_time",
        ("tenant_id", "actor_id", "occurred_at", "sequence"),
    ),
    (
        "ix_tenant_audit_resource_time",
        ("tenant_id", "resource_type", "resource_id", "occurred_at", "sequence"),
    ),
    (
        "ix_tenant_audit_target_time",
        ("tenant_id", "target_account_id", "occurred_at", "sequence"),
    ),
    ("ix_tenant_audit_request", ("tenant_id", "request_id")),
)


def _datetime6():
    return (
        sa.DateTime()
        .with_variant(mysql.DATETIME(fsp=6), "mysql")
        .with_variant(mysql.DATETIME(fsp=6), "mariadb")
    )


def _render_rows(rows: list[Any]) -> str:
    rendered = []
    for row in rows:
        values = ["<empty>" if value is None or value == "" else str(value) for value in row]
        rendered.append("/".join(values))
    return ", ".join(rendered)


def _validate_tenant_membership_preflight(connection: Connection) -> None:
    """Read existing membership data and reject unsafe 0016 upgrades."""

    invalid_roles = list(
        connection.execute(
            text(
                "SELECT id, tenant_id, account_id, role "
                "FROM tenant_members "
                "WHERE role IS NULL OR role = '' "
                "OR role NOT IN ('owner', 'admin', 'editor', 'member') "
                "ORDER BY id LIMIT 20"
            )
        )
    )
    duplicate_members = list(
        connection.execute(
            text(
                "SELECT tenant_id, account_id, COUNT(*) AS member_count "
                "FROM tenant_members "
                "GROUP BY tenant_id, account_id "
                "HAVING COUNT(*) > 1 "
                "ORDER BY tenant_id, account_id LIMIT 20"
            )
        )
    )
    orphan_accounts = list(
        connection.execute(
            text(
                "SELECT m.id, m.tenant_id, m.account_id "
                "FROM tenant_members AS m "
                "LEFT JOIN accounts AS a ON a.id = m.account_id "
                "WHERE a.id IS NULL "
                "ORDER BY m.id LIMIT 20"
            )
        )
    )
    orphan_tenants = list(
        connection.execute(
            text(
                "SELECT m.id, m.tenant_id, m.account_id "
                "FROM tenant_members AS m "
                "LEFT JOIN tenants AS t ON t.id = m.tenant_id "
                "WHERE t.id IS NULL "
                "ORDER BY m.id LIMIT 20"
            )
        )
    )
    ownerless_active_tenants = list(
        connection.execute(
            text(
                "SELECT t.id "
                "FROM tenants AS t "
                "LEFT JOIN tenant_members AS m "
                "  ON m.tenant_id = t.id AND m.role = 'owner' "
                "WHERE t.status = 'active' "
                "GROUP BY t.id "
                "HAVING COUNT(m.id) = 0 "
                "ORDER BY t.id LIMIT 20"
            )
        )
    )

    violations: list[str] = []
    if invalid_roles:
        violations.append("invalid or empty roles: " + _render_rows(invalid_roles))
    if duplicate_members:
        violations.append("duplicate membership pairs: " + _render_rows(duplicate_members))
    if orphan_accounts or orphan_tenants:
        orphan_rows = orphan_accounts + orphan_tenants
        violations.append("orphan member relations: " + _render_rows(orphan_rows))
    if ownerless_active_tenants:
        violations.append(
            "active tenants without an owner: " + _render_rows(ownerless_active_tenants)
        )

    if violations:
        raise EnterpriseMembershipPreflightError(
            "0016 enterprise membership preflight blocked; " + "; ".join(violations)
        )


def upgrade() -> None:
    _is_mysql = op.get_bind().dialect.name == "mysql"
    # Offline SQL generation has no data connection to inspect. Runtime
    # upgrades always validate before the first DDL statement so an unsafe
    # legacy catalog is left structurally untouched.
    if not context.is_offline_mode():
        _validate_tenant_membership_preflight(op.get_bind())

    with op.batch_alter_table("tenant_members") as batch:
        batch.add_column(
            sa.Column(
                "status",
                sa.String(length=16),
                nullable=False,
                server_default=sa.text("'active'"),
            )
        )
        batch.add_column(
            sa.Column(
                "revision",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )
        batch.add_column(
            sa.Column(
                "updated_at",
                _datetime6(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP"),
            )
        )
        batch.add_column(
            sa.Column(
                "updated_by",
                sa.String(length=64),
                nullable=False,
                server_default=sa.text("'migration:0016'"),
            )
        )
        batch.add_column(sa.Column("suspended_at", _datetime6(), nullable=True))
        batch.add_column(
            sa.Column("suspended_by", sa.String(length=64), nullable=True)
        )
        batch.create_check_constraint(
            "ck_tenant_members_role",
            "role IN ('owner', 'admin', 'editor', 'member')",
        )
        batch.create_check_constraint(
            "ck_tenant_members_status",
            "status IN ('active', 'suspended')",
        )
        batch.create_check_constraint(
            "ck_tenant_members_revision_positive",
            "revision > 0",
        )
        for name, columns in TENANT_MEMBER_INDEX_SPECS:
            batch.create_index(name, list(columns))

    op.create_table(
        "tenant_audit_events",
        sa.Column(
            "sequence",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("actor_name_snapshot", sa.String(length=128), nullable=False),
        sa.Column("actor_email_snapshot", sa.String(length=256), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=512), nullable=False),
        sa.Column("target_account_id", sa.String(length=64), nullable=True),
        sa.Column("before_snapshot", sa.JSON(), nullable=True),
        sa.Column("after_snapshot", sa.JSON(), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("request_ip", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", _datetime6(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_tenant_audit_events_tenant",
        ),
        sa.PrimaryKeyConstraint("sequence"),
        sa.UniqueConstraint("id", name="uq_tenant_audit_events_id"),
    )
    for name, columns in ENTERPRISE_AUDIT_INDEX_SPECS:
        op.create_index(name, "tenant_audit_events", list(columns), unique=False)


def downgrade() -> None:
    for name, _columns in reversed(ENTERPRISE_AUDIT_INDEX_SPECS):
        op.drop_index(name, table_name="tenant_audit_events")
    op.drop_table("tenant_audit_events")

    with op.batch_alter_table("tenant_members") as batch:
        for name, _columns in reversed(TENANT_MEMBER_INDEX_SPECS):
            batch.drop_index(name)
        batch.drop_constraint("ck_tenant_members_revision_positive", type_="check")
        batch.drop_constraint("ck_tenant_members_status", type_="check")
        batch.drop_constraint("ck_tenant_members_role", type_="check")
        batch.drop_column("suspended_by")
        batch.drop_column("suspended_at")
        batch.drop_column("updated_by")
        batch.drop_column("updated_at")
        batch.drop_column("revision")
        batch.drop_column("status")
