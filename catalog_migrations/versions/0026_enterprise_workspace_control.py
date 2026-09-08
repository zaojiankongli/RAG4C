"""Add the tenant workspace control-plane authority.

Revision ID: 0026_enterprise_workspace_control
Revises: 0025_enterprise_approval_control
Create Date: 2026-08-27

The migration creates an explicit workspace layer without changing the existing
Dataset authorization engine.  Existing tenants receive a deterministic default
workspace, existing Datasets receive a primary binding, and only existing active
TenantMembers are copied into the workspace membership table.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy import Connection, text
from sqlalchemy.dialects import mysql

revision: str = "0026_enterprise_workspace_control"
down_revision: str | None = "0025_enterprise_approval_control"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WORKSPACE_DEFAULT_ID_PREFIX = "workspace-default-"
WORKSPACE_DEFAULT_CODE = "default"
WORKSPACE_DEFAULT_SLOT = "default"
WORKSPACE_DEFAULT_NAME_SUFFIX = " Default Workspace"
WORKSPACE_DEFAULT_NAME_MAX_LENGTH = 128
WORKSPACE_DEFAULT_NORMALIZED_NAME_MAX_LENGTH = 256
WORKSPACE_DEFAULT_DESCRIPTION = "Deterministic compatibility workspace created by migration 0026."
WORKSPACE_MIGRATION_ACTOR = "migration:0026"

WORKSPACE_INDEX_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "ix_tenant_workspaces_tenant_status_updated",
        ("tenant_id", "status", "updated_at", "id"),
    ),
    (
        "ix_tenant_workspaces_tenant_default",
        ("tenant_id", "status", "active_default_slot", "id"),
    ),
)
WORKSPACE_MEMBER_INDEX_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "ix_tenant_workspace_members_tenant_workspace_status",
        ("tenant_id", "workspace_id", "status", "id"),
    ),
    (
        "ix_tenant_workspace_members_tenant_account_status",
        ("tenant_id", "account_id", "status", "id"),
    ),
)
WORKSPACE_DATASET_INDEX_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "ix_tenant_workspace_datasets_tenant_workspace_status",
        ("tenant_id", "workspace_id", "status", "id"),
    ),
    (
        "ix_tenant_workspace_datasets_tenant_dataset_status",
        ("tenant_id", "dataset_id", "status", "id"),
    ),
)


def _datetime6():
    return (
        sa.DateTime()
        .with_variant(mysql.DATETIME(fsp=6), "mysql")
        .with_variant(mysql.DATETIME(fsp=6), "mariadb")
    )


def _render_rows(rows: list[Any]) -> str:
    return ", ".join(
        "/".join("<empty>" if value is None or value == "" else str(value) for value in row)
        for row in rows
    )


def _default_workspace_display_name(tenant_name: Any) -> str:
    """Build a deterministic display name that fits both name columns.

    ``Tenant.name`` is already bounded to 128 characters, but appending a
    suffix without budgeting for it would overflow ``tenant_workspaces.name``.
    The prefix is therefore accumulated one code point at a time, also checking
    the case-folded value so ``normalized_name`` remains within 256 characters
    for Unicode names whose casefold expands.
    """

    value = str(tenant_name or "").strip()
    if not value:
        raise RuntimeError("0026 workspace backfill requires a non-empty tenant name")

    prefix: list[str] = []
    for character in value:
        candidate = "".join(prefix) + character + WORKSPACE_DEFAULT_NAME_SUFFIX
        if len(candidate) > WORKSPACE_DEFAULT_NAME_MAX_LENGTH:
            break
        if len(candidate.casefold()) > WORKSPACE_DEFAULT_NORMALIZED_NAME_MAX_LENGTH:
            break
        prefix.append(character)

    if not prefix:
        prefix = ["Tenant"]
    display_name = "".join(prefix) + WORKSPACE_DEFAULT_NAME_SUFFIX
    normalized_name = display_name.casefold()
    if (
        len(display_name) > WORKSPACE_DEFAULT_NAME_MAX_LENGTH
        or len(normalized_name) > WORKSPACE_DEFAULT_NORMALIZED_NAME_MAX_LENGTH
    ):
        raise RuntimeError("0026 workspace backfill generated an oversized default workspace name")
    return display_name


def _validate_workspace_preflight(connection: Connection) -> None:
    """Reject orphaned scope rows before creating any workspace authority.

    The previous revisions normally protect these references with FKs, but the
    preflight is intentionally explicit so a legacy/SQLite catalog with foreign
    keys disabled cannot be silently backfilled into a misleading workspace graph.
    """

    tenants = list(connection.execute(text("SELECT id, name FROM tenants ORDER BY id")))
    oversized_tenants = [
        row for row in tenants if len(f"{WORKSPACE_DEFAULT_ID_PREFIX}{str(row.id)}") > 128
    ]
    orphan_datasets = list(
        connection.execute(
            text(
                "SELECT d.id, d.tenant_id FROM datasets AS d "
                "LEFT JOIN tenants AS t ON t.id = d.tenant_id "
                "WHERE t.id IS NULL ORDER BY d.id LIMIT 20"
            )
        )
    )
    orphan_members = list(
        connection.execute(
            text(
                "SELECT m.id, m.tenant_id, m.account_id FROM tenant_members AS m "
                "LEFT JOIN tenants AS t ON t.id = m.tenant_id "
                "LEFT JOIN accounts AS a ON a.id = m.account_id "
                "WHERE t.id IS NULL OR a.id IS NULL "
                "ORDER BY m.id LIMIT 20"
            )
        )
    )
    invalid_tenant_names = [row for row in tenants if not str(row.name or "").strip()]
    problems: list[str] = []
    if oversized_tenants:
        problems.append(
            "tenant ids cannot fit deterministic workspace ids: " + _render_rows(oversized_tenants)
        )
    if invalid_tenant_names:
        problems.append("tenants missing names: " + _render_rows(invalid_tenant_names))
    if orphan_datasets:
        problems.append("datasets reference missing tenants: " + _render_rows(orphan_datasets))
    if orphan_members:
        problems.append(
            "tenant members reference missing scope rows: " + _render_rows(orphan_members)
        )
    if problems:
        raise RuntimeError("0026 workspace preflight failed; " + "; ".join(problems))


def _create_indexes(
    table_name: str,
    specs: tuple[tuple[str, tuple[str, ...]], ...],
) -> None:
    for name, columns in specs:
        op.create_index(name, table_name, list(columns), unique=False)


def _backfill_workspace_authority(connection: Connection) -> None:
    """Backfill deterministic, tenant-safe workspace compatibility facts."""

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    tenants = list(connection.execute(text("SELECT id, name FROM tenants ORDER BY id")))
    role_map = {
        "owner": "owner",
        "admin": "admin",
        "editor": "editor",
        "member": "viewer",
    }

    for tenant in tenants:
        tenant_id = str(tenant.id)
        workspace_id = f"{WORKSPACE_DEFAULT_ID_PREFIX}{tenant_id}"
        workspace_name = _default_workspace_display_name(tenant.name)
        connection.execute(
            text(
                "INSERT INTO tenant_workspaces "
                "(id, tenant_id, code, name, normalized_name, description, status, environment, "
                "is_default, active_default_slot, revision, created_at, created_by, updated_at, updated_by) "
                "VALUES (:id, :tenant_id, :code, :name, :normalized_name, :description, :status, "
                ":environment, :is_default, :active_default_slot, :revision, :created_at, :created_by, "
                ":updated_at, :updated_by)"
            ),
            {
                "id": workspace_id,
                "tenant_id": tenant_id,
                "code": WORKSPACE_DEFAULT_CODE,
                "name": workspace_name,
                "normalized_name": workspace_name.casefold(),
                "description": WORKSPACE_DEFAULT_DESCRIPTION,
                "status": "active",
                "environment": "production",
                "is_default": True,
                "active_default_slot": WORKSPACE_DEFAULT_SLOT,
                "revision": 1,
                "created_at": now,
                "created_by": WORKSPACE_MIGRATION_ACTOR,
                "updated_at": now,
                "updated_by": WORKSPACE_MIGRATION_ACTOR,
            },
        )

    active_members = list(
        connection.execute(
            text(
                "SELECT tenant_id, account_id, role FROM tenant_members "
                "WHERE status = 'active' ORDER BY tenant_id, account_id"
            )
        )
    )
    for member in active_members:
        role = role_map.get(str(member.role))
        if role is None:
            raise RuntimeError(
                f"0026 workspace preflight found unsupported active member role: {member.role}"
            )
        tenant_id = str(member.tenant_id)
        account_id = str(member.account_id)
        connection.execute(
            text(
                "INSERT INTO tenant_workspace_members "
                "(tenant_id, workspace_id, account_id, role, status, revision, created_at, "
                "created_by, updated_at, updated_by) VALUES "
                "(:tenant_id, :workspace_id, :account_id, :role, 'active', 1, :created_at, "
                ":created_by, :updated_at, :updated_by)"
            ),
            {
                "tenant_id": tenant_id,
                "workspace_id": f"{WORKSPACE_DEFAULT_ID_PREFIX}{tenant_id}",
                "account_id": account_id,
                "role": role,
                "created_at": now,
                "created_by": account_id,
                "updated_at": now,
                "updated_by": account_id,
            },
        )

    datasets = list(
        connection.execute(text("SELECT tenant_id, id FROM datasets ORDER BY tenant_id, id"))
    )
    for dataset in datasets:
        tenant_id = str(dataset.tenant_id)
        connection.execute(
            text(
                "INSERT INTO tenant_workspace_datasets "
                "(tenant_id, workspace_id, dataset_id, binding_kind, active_primary_slot, status, "
                "revision, created_at, created_by, updated_at, updated_by) VALUES "
                "(:tenant_id, :workspace_id, :dataset_id, 'primary', 'primary', 'active', 1, "
                ":created_at, :created_by, :updated_at, :updated_by)"
            ),
            {
                "tenant_id": tenant_id,
                "workspace_id": f"{WORKSPACE_DEFAULT_ID_PREFIX}{tenant_id}",
                "dataset_id": str(dataset.id),
                "created_at": now,
                "created_by": WORKSPACE_MIGRATION_ACTOR,
                "updated_at": now,
                "updated_by": WORKSPACE_MIGRATION_ACTOR,
            },
        )


def upgrade() -> None:
    if not context.is_offline_mode():
        _validate_workspace_preflight(op.get_bind())

    op.create_table(
        "tenant_workspaces",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("normalized_name", sa.String(length=256), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'active'")
        ),
        sa.Column(
            "environment",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'production'"),
        ),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("active_default_slot", sa.String(length=16), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
        sa.Column("archived_at", _datetime6(), nullable=True),
        sa.Column("archived_by", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tenant_workspaces_scope_id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_tenant_workspaces_tenant_code"),
        sa.UniqueConstraint(
            "tenant_id", "normalized_name", name="uq_tenant_workspaces_tenant_normalized_name"
        ),
        sa.UniqueConstraint(
            "tenant_id", "active_default_slot", name="uq_tenant_workspaces_active_default"
        ),
        sa.CheckConstraint("status IN ('active','archived')", name="ck_tenant_workspaces_status"),
        sa.CheckConstraint(
            "environment IN ('development','testing','production')",
            name="ck_tenant_workspaces_environment",
        ),
        sa.CheckConstraint("revision > 0", name="ck_tenant_workspaces_revision_positive"),
        sa.CheckConstraint(
            "(status = 'active' AND archived_at IS NULL AND archived_by IS NULL) "
            "OR (status = 'archived' AND archived_at IS NOT NULL AND archived_by IS NOT NULL)",
            name="ck_tenant_workspaces_lifecycle_evidence",
        ),
        sa.CheckConstraint(
            "(is_default = false AND active_default_slot IS NULL) OR "
            "(is_default = true AND status = 'active' AND active_default_slot = 'default')",
            name="ck_tenant_workspaces_active_default_slot",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_tenant_workspaces_tenant"),
    )
    _create_indexes("tenant_workspaces", WORKSPACE_INDEX_SPECS)

    op.create_table(
        "tenant_workspace_members",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("account_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'active'")
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
        sa.Column("removed_at", _datetime6(), nullable=True),
        sa.Column("removed_by", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "account_id",
            name="uq_tenant_workspace_members_scope_account",
        ),
        sa.CheckConstraint(
            "role IN ('owner','admin','editor','viewer')", name="ck_tenant_workspace_members_role"
        ),
        sa.CheckConstraint(
            "status IN ('active','removed')", name="ck_tenant_workspace_members_status"
        ),
        sa.CheckConstraint("revision > 0", name="ck_tenant_workspace_members_revision_positive"),
        sa.CheckConstraint(
            "status <> 'removed' OR (removed_at IS NOT NULL AND removed_by IS NOT NULL)",
            name="ck_tenant_workspace_members_removed_evidence",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["tenant_workspaces.tenant_id", "tenant_workspaces.id"],
            name="fk_tenant_workspace_members_scope_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_workspace_members_scope_member",
        ),
    )
    _create_indexes("tenant_workspace_members", WORKSPACE_MEMBER_INDEX_SPECS)

    op.create_table(
        "tenant_workspace_datasets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("binding_kind", sa.String(length=16), nullable=False),
        sa.Column("active_primary_slot", sa.String(length=16), nullable=True),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'active'")
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
        sa.Column("removed_at", _datetime6(), nullable=True),
        sa.Column("removed_by", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "dataset_id",
            name="uq_tenant_workspace_datasets_scope_binding",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "active_primary_slot",
            name="uq_tenant_workspace_datasets_active_primary",
        ),
        sa.CheckConstraint(
            "binding_kind IN ('primary','shared')", name="ck_tenant_workspace_datasets_binding_kind"
        ),
        sa.CheckConstraint(
            "status IN ('active','removed')", name="ck_tenant_workspace_datasets_status"
        ),
        sa.CheckConstraint("revision > 0", name="ck_tenant_workspace_datasets_revision_positive"),
        sa.CheckConstraint(
            "(status = 'active' AND binding_kind = 'primary' "
            "AND active_primary_slot IS NOT NULL AND active_primary_slot = 'primary') "
            "OR ((status <> 'active' OR binding_kind <> 'primary') AND active_primary_slot IS NULL)",
            name="ck_tenant_workspace_datasets_active_primary_slot",
        ),
        sa.CheckConstraint(
            "status <> 'removed' OR (removed_at IS NOT NULL AND removed_by IS NOT NULL)",
            name="ck_tenant_workspace_datasets_removed_evidence",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["tenant_workspaces.tenant_id", "tenant_workspaces.id"],
            name="fk_tenant_workspace_datasets_scope_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_tenant_workspace_datasets_scope_dataset",
        ),
    )
    _create_indexes("tenant_workspace_datasets", WORKSPACE_DATASET_INDEX_SPECS)

    if not context.is_offline_mode():
        _backfill_workspace_authority(op.get_bind())


def downgrade() -> None:
    for name, _columns in reversed(WORKSPACE_DATASET_INDEX_SPECS):
        op.drop_index(name, table_name="tenant_workspace_datasets")
    op.drop_table("tenant_workspace_datasets")

    for name, _columns in reversed(WORKSPACE_MEMBER_INDEX_SPECS):
        op.drop_index(name, table_name="tenant_workspace_members")
    op.drop_table("tenant_workspace_members")

    for name, _columns in reversed(WORKSPACE_INDEX_SPECS):
        op.drop_index(name, table_name="tenant_workspaces")
    op.drop_table("tenant_workspaces")
