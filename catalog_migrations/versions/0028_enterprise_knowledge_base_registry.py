"""Add authoritative Dataset ownership and Application references.

Revision ID: 0028_enterprise_knowledge_base_registry
Revises: 0027_enterprise_workspace_authorization
Create Date: 2026-08-27
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
import hashlib
from typing import Any

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy import Connection, text
from sqlalchemy.dialects import mysql

revision: str = "0028_enterprise_knowledge_base_registry"
down_revision: str | None = "0027_enterprise_workspace_authorization"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OWNERSHIP_TABLE = "dataset_workspace_ownerships"
REFERENCE_TABLE = "app_dataset_references"
APP_SCOPE_UNIQUE = "uq_apps_tenant_id"
DATASET_WORKSPACE_TRANSFER_ACTION = "dataset_workspace_transfer"
REGISTRY_MIGRATION_ACTOR = "migration:0028"
DATASET_WORKSPACE_OWNERSHIP_ID_PREFIX = "dataset-ownership-"

APPROVAL_ACTION_TYPES_0027: tuple[str, ...] = (
    "catalog_upgrade",
    "membership_bootstrap",
    "dataset_acl_disable",
    "member_role_change",
    "identity_provider_disable",
    "audit_retention_execute",
    "workspace_authorization_mode_change",
)
APPROVAL_ACTION_TYPES_0028 = APPROVAL_ACTION_TYPES_0027 + (DATASET_WORKSPACE_TRANSFER_ACTION,)
APPROVAL_ACTION_CHECKS: tuple[tuple[str, str], ...] = (
    ("tenant_approval_policies", "ck_tenant_approval_policies_action_type"),
    ("tenant_approval_requests", "ck_tenant_approval_requests_action_type"),
)

OWNERSHIP_INDEX_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "ix_dataset_workspace_ownerships_tenant_workspace_dataset",
        ("tenant_id", "workspace_id", "dataset_id"),
    ),
    (
        "ix_dataset_workspace_ownerships_tenant_updated_dataset",
        ("tenant_id", "updated_at", "dataset_id"),
    ),
)
REFERENCE_INDEX_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "ix_app_dataset_references_tenant_dataset_status",
        ("tenant_id", "dataset_id", "status", "id"),
    ),
    (
        "ix_app_dataset_references_tenant_app_status",
        ("tenant_id", "app_id", "status", "id"),
    ),
    (
        "ix_app_dataset_references_tenant_status_updated",
        ("tenant_id", "status", "updated_at", "id"),
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


def _approval_action_check(actions: tuple[str, ...]) -> str:
    values = ",".join(f"'{action}'" for action in actions)
    return f"action_type IN ({values})"


def _replace_approval_action_checks(actions: tuple[str, ...]) -> None:
    sqltext = _approval_action_check(actions)
    for table_name, constraint_name in APPROVAL_ACTION_CHECKS:
        with op.batch_alter_table(table_name) as batch:
            batch.drop_constraint(constraint_name, type_="check")
            batch.create_check_constraint(constraint_name, sqltext)


def _ownership_id(tenant_id: str, dataset_id: str, workspace_id: str) -> str:
    digest = hashlib.sha256()
    digest.update(b"rag4c:dataset-workspace-ownership:v1\x00")
    for value in (tenant_id, dataset_id, workspace_id):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    suffix_length = 64 - len(DATASET_WORKSPACE_OWNERSHIP_ID_PREFIX)
    return DATASET_WORKSPACE_OWNERSHIP_ID_PREFIX + digest.hexdigest()[:suffix_length]


def _validate_registry_preflight(connection: Connection) -> None:
    orphan_datasets = list(
        connection.execute(
            text(
                "SELECT d.tenant_id, d.id FROM datasets AS d "
                "LEFT JOIN tenants AS t ON t.id=d.tenant_id "
                "WHERE t.id IS NULL ORDER BY d.tenant_id,d.id LIMIT 20"
            )
        )
    )
    orphan_workspaces = list(
        connection.execute(
            text(
                "SELECT w.tenant_id, w.id FROM tenant_workspaces AS w "
                "LEFT JOIN tenants AS t ON t.id=w.tenant_id "
                "WHERE t.id IS NULL ORDER BY w.tenant_id,w.id LIMIT 20"
            )
        )
    )
    orphan_bindings = list(
        connection.execute(
            text(
                "SELECT b.tenant_id,b.dataset_id,b.workspace_id "
                "FROM tenant_workspace_datasets AS b "
                "LEFT JOIN datasets AS d ON d.tenant_id=b.tenant_id AND d.id=b.dataset_id "
                "LEFT JOIN tenant_workspaces AS w "
                "ON w.tenant_id=b.tenant_id AND w.id=b.workspace_id "
                "WHERE d.id IS NULL OR w.id IS NULL "
                "ORDER BY b.tenant_id,b.dataset_id,b.workspace_id LIMIT 20"
            )
        )
    )
    duplicate_primary = list(
        connection.execute(
            text(
                "SELECT tenant_id,dataset_id,COUNT(*) AS binding_count "
                "FROM tenant_workspace_datasets "
                "WHERE status='active' AND binding_kind='primary' "
                "AND active_primary_slot='primary' "
                "GROUP BY tenant_id,dataset_id HAVING COUNT(*) > 1 "
                "ORDER BY tenant_id,dataset_id LIMIT 20"
            )
        )
    )
    inactive_primary_workspace = list(
        connection.execute(
            text(
                "SELECT b.tenant_id,b.dataset_id,b.workspace_id,w.status "
                "FROM tenant_workspace_datasets AS b "
                "JOIN tenant_workspaces AS w "
                "ON w.tenant_id=b.tenant_id AND w.id=b.workspace_id "
                "WHERE b.status='active' AND b.binding_kind='primary' "
                "AND b.active_primary_slot='primary' AND w.status <> 'active' "
                "ORDER BY b.tenant_id,b.dataset_id,b.workspace_id LIMIT 20"
            )
        )
    )
    oversized_ids = list(
        connection.execute(
            text(
                "SELECT 'dataset' AS kind,tenant_id,id FROM datasets "
                "WHERE length(tenant_id)>64 OR length(id)>64 "
                "UNION ALL SELECT 'workspace',tenant_id,id FROM tenant_workspaces "
                "WHERE length(tenant_id)>64 OR length(id)>128 "
                "ORDER BY kind,tenant_id,id LIMIT 20"
            )
        )
    )

    problems: list[str] = []
    if orphan_datasets:
        problems.append("datasets reference missing tenants: " + _render_rows(orphan_datasets))
    if orphan_workspaces:
        problems.append("workspaces reference missing tenants: " + _render_rows(orphan_workspaces))
    if orphan_bindings:
        problems.append(
            "workspace bindings reference missing scope rows: " + _render_rows(orphan_bindings)
        )
    if duplicate_primary:
        problems.append("duplicate active primary bindings: " + _render_rows(duplicate_primary))
    if inactive_primary_workspace:
        problems.append(
            "active primary bindings reference inactive workspaces: "
            + _render_rows(inactive_primary_workspace)
        )
    if oversized_ids:
        problems.append("registry scope ids exceed column bounds: " + _render_rows(oversized_ids))
    if problems:
        raise RuntimeError("0028 registry preflight failed; " + "; ".join(problems))


def _backfill_dataset_workspace_ownerships(connection: Connection) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = list(
        connection.execute(
            text(
                "SELECT b.tenant_id,b.dataset_id,b.workspace_id "
                "FROM tenant_workspace_datasets AS b "
                "JOIN datasets AS d ON d.tenant_id=b.tenant_id AND d.id=b.dataset_id "
                "JOIN tenant_workspaces AS w "
                "ON w.tenant_id=b.tenant_id AND w.id=b.workspace_id "
                "WHERE b.status='active' AND b.binding_kind='primary' "
                "AND b.active_primary_slot='primary' AND w.status='active' "
                "ORDER BY b.tenant_id,b.dataset_id,b.workspace_id"
            )
        )
    )
    ownership_ids: set[str] = set()
    for row in rows:
        tenant_id = str(row.tenant_id)
        dataset_id = str(row.dataset_id)
        workspace_id = str(row.workspace_id)
        ownership_id = _ownership_id(tenant_id, dataset_id, workspace_id)
        if len(ownership_id) != 64 or ownership_id in ownership_ids:
            raise RuntimeError(
                "0028 registry backfill generated a duplicate or oversized ownership id"
            )
        ownership_ids.add(ownership_id)
        connection.execute(
            text(
                "INSERT INTO dataset_workspace_ownerships "
                "(id,tenant_id,dataset_id,workspace_id,revision,created_at,created_by,"
                "updated_at,updated_by,last_transfer_at) "
                "VALUES (:id,:tenant_id,:dataset_id,:workspace_id,1,:now,:actor,:now,:actor,NULL)"
            ),
            {
                "id": ownership_id,
                "tenant_id": tenant_id,
                "dataset_id": dataset_id,
                "workspace_id": workspace_id,
                "now": now,
                "actor": REGISTRY_MIGRATION_ACTOR,
            },
        )


def _guard_downgrade(connection: Connection) -> None:
    counts = {
        OWNERSHIP_TABLE: int(
            connection.execute(text(f"SELECT COUNT(*) FROM {OWNERSHIP_TABLE}")).scalar_one()
        ),
        REFERENCE_TABLE: int(
            connection.execute(text(f"SELECT COUNT(*) FROM {REFERENCE_TABLE}")).scalar_one()
        ),
    }
    counts.update(
        {
            table_name: int(
                connection.execute(
                    text(f"SELECT COUNT(*) FROM {table_name} WHERE action_type=:action"),
                    {"action": DATASET_WORKSPACE_TRANSFER_ACTION},
                ).scalar_one()
            )
            for table_name, _constraint_name in APPROVAL_ACTION_CHECKS
        }
    )
    if any(counts.values()):
        evidence = ", ".join(f"{table}={count}" for table, count in counts.items() if count)
        raise RuntimeError(
            "0028 downgrade blocked while ownership, application reference or "
            "dataset_workspace_transfer approval facts remain; " + evidence
        )


def upgrade() -> None:
    _is_mysql = op.get_bind().dialect.name == "mysql"
    if not context.is_offline_mode():
        _validate_registry_preflight(op.get_bind())

    with op.batch_alter_table("apps") as batch:
        batch.create_unique_constraint(APP_SCOPE_UNIQUE, ["tenant_id", "id"])

    op.create_table(
        OWNERSHIP_TABLE,
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
        sa.Column("last_transfer_at", _datetime6(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_dataset_workspace_ownerships_scope_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            name="uq_dataset_workspace_ownerships_scope_dataset",
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_dataset_workspace_ownerships_revision_positive",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_dataset_workspace_ownerships_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_dataset_workspace_ownerships_scope_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["tenant_workspaces.tenant_id", "tenant_workspaces.id"],
            name="fk_dataset_workspace_ownerships_scope_workspace",
        ),
    )
    for name, columns in OWNERSHIP_INDEX_SPECS:
        op.create_index(name, OWNERSHIP_TABLE, list(columns), unique=False)

    op.create_table(
        REFERENCE_TABLE,
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("app_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("reference_kind", sa.String(length=16), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'active'")
        ),
        sa.Column("active_slot", sa.String(length=16), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
        sa.Column("removed_at", _datetime6(), nullable=True),
        sa.Column("removed_by", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_app_dataset_references_scope_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "app_id",
            "dataset_id",
            "active_slot",
            name="uq_app_dataset_references_active_slot",
        ),
        sa.CheckConstraint(
            "reference_kind = 'knowledge'",
            name="ck_app_dataset_references_reference_kind",
        ),
        sa.CheckConstraint(
            "status IN ('active','removed')",
            name="ck_app_dataset_references_status",
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_app_dataset_references_revision_positive",
        ),
        sa.CheckConstraint(
            "(status = 'active' AND active_slot = 'active' AND removed_at IS NULL "
            "AND removed_by IS NULL) OR (status = 'removed' AND active_slot IS NULL "
            "AND removed_at IS NOT NULL AND removed_by IS NOT NULL)",
            name="ck_app_dataset_references_lifecycle_evidence",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_app_dataset_references_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "app_id"],
            ["apps.tenant_id", "apps.id"],
            name="fk_app_dataset_references_scope_app",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_app_dataset_references_scope_dataset",
        ),
    )
    for name, columns in REFERENCE_INDEX_SPECS:
        op.create_index(name, REFERENCE_TABLE, list(columns), unique=False)

    if not context.is_offline_mode():
        _backfill_dataset_workspace_ownerships(op.get_bind())

    _replace_approval_action_checks(APPROVAL_ACTION_TYPES_0028)


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0028 downgrade requires an online preflight for ownership, application reference "
            "and dataset_workspace_transfer approval facts"
        )

    _guard_downgrade(op.get_bind())
    _replace_approval_action_checks(APPROVAL_ACTION_TYPES_0027)
    for name, _columns in reversed(REFERENCE_INDEX_SPECS):
        op.drop_index(name, table_name=REFERENCE_TABLE)
    op.drop_table(REFERENCE_TABLE)
    for name, _columns in reversed(OWNERSHIP_INDEX_SPECS):
        op.drop_index(name, table_name=OWNERSHIP_TABLE)
    op.drop_table(OWNERSHIP_TABLE)
    with op.batch_alter_table("apps") as batch:
        batch.drop_constraint(APP_SCOPE_UNIQUE, type_="unique")
