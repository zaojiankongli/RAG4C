"""Add tenant Workspace authorization rollout authority.

Revision ID: 0027_enterprise_workspace_authorization
Revises: 0026_enterprise_workspace_control
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

revision: str = "0027_enterprise_workspace_authorization"
down_revision: str | None = "0026_enterprise_workspace_control"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "tenant_workspace_authorization_policies"
WORKSPACE_AUTHORIZATION_ACTION = "workspace_authorization_mode_change"
WORKSPACE_AUTHORIZATION_MIGRATION_ACTOR = "migration:0027"
WORKSPACE_AUTHORIZATION_PERMISSION_MODEL_VERSION = 1
WORKSPACE_AUTHORIZATION_POLICY_ID_PREFIX = "workspace-authorization-"

APPROVAL_ACTION_TYPES_0026: tuple[str, ...] = (
    "catalog_upgrade",
    "membership_bootstrap",
    "dataset_acl_disable",
    "member_role_change",
    "identity_provider_disable",
    "audit_retention_execute",
)
APPROVAL_ACTION_TYPES_0027 = APPROVAL_ACTION_TYPES_0026 + (WORKSPACE_AUTHORIZATION_ACTION,)
APPROVAL_ACTION_CHECKS: tuple[tuple[str, str], ...] = (
    ("tenant_approval_policies", "ck_tenant_approval_policies_action_type"),
    ("tenant_approval_requests", "ck_tenant_approval_requests_action_type"),
)
POLICY_INDEX_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "ix_tw_auth_policies_tenant_mode_updated",
        ("tenant_id", "mode", "updated_at", "id"),
    ),
    (
        "ix_tw_auth_policies_tenant_workspace_mode",
        ("tenant_id", "workspace_id", "mode", "id"),
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


def _policy_id(tenant_id: str, workspace_id: str) -> str:
    digest = hashlib.sha256()
    digest.update(b"rag4c:workspace-authorization-policy:v1\x00")
    for value in (tenant_id, workspace_id):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return WORKSPACE_AUTHORIZATION_POLICY_ID_PREFIX + digest.hexdigest()[:40]


def _validate_workspace_preflight(connection: Connection) -> None:
    orphan_workspaces = list(
        connection.execute(
            text(
                "SELECT w.tenant_id, w.id FROM tenant_workspaces AS w "
                "LEFT JOIN tenants AS t ON t.id = w.tenant_id "
                "WHERE t.id IS NULL ORDER BY w.tenant_id, w.id LIMIT 20"
            )
        )
    )
    invalid_statuses = list(
        connection.execute(
            text(
                "SELECT tenant_id, id, status FROM tenant_workspaces "
                "WHERE status NOT IN ('active','archived') "
                "ORDER BY tenant_id, id LIMIT 20"
            )
        )
    )
    problems: list[str] = []
    if orphan_workspaces:
        problems.append("workspaces reference missing tenants: " + _render_rows(orphan_workspaces))
    if invalid_statuses:
        problems.append("workspaces have unsupported status: " + _render_rows(invalid_statuses))
    if problems:
        raise RuntimeError("0027 workspace authorization preflight failed; " + "; ".join(problems))


def _backfill_workspace_authorization(connection: Connection) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = list(
        connection.execute(
            text("SELECT tenant_id, id, status FROM tenant_workspaces ORDER BY tenant_id, id")
        )
    )
    policy_ids: set[str] = set()
    for row in rows:
        tenant_id = str(row.tenant_id)
        workspace_id = str(row.id)
        status = str(row.status)
        mode = "shadow" if status == "active" else "disabled"
        policy_id = _policy_id(tenant_id, workspace_id)
        if len(policy_id) != 64 or policy_id in policy_ids:
            raise RuntimeError("0027 workspace authorization policy id generation failed")
        policy_ids.add(policy_id)
        disabled_at = now if mode == "disabled" else None
        disabled_by = WORKSPACE_AUTHORIZATION_MIGRATION_ACTOR if mode == "disabled" else None
        connection.execute(
            text(
                f"INSERT INTO {TABLE_NAME} "
                "(id,tenant_id,workspace_id,mode,permission_model_version,revision,created_at,created_by,"
                "updated_at,updated_by,enforced_at,enforced_by,disabled_at,disabled_by) "
                "VALUES (:id,:tenant_id,:workspace_id,:mode,:permission_model_version,1,:created_at,"
                ":created_by,:updated_at,:updated_by,NULL,NULL,:disabled_at,:disabled_by)"
            ),
            {
                "id": policy_id,
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "mode": mode,
                "permission_model_version": WORKSPACE_AUTHORIZATION_PERMISSION_MODEL_VERSION,
                "created_at": now,
                "created_by": WORKSPACE_AUTHORIZATION_MIGRATION_ACTOR,
                "updated_at": now,
                "updated_by": WORKSPACE_AUTHORIZATION_MIGRATION_ACTOR,
                "disabled_at": disabled_at,
                "disabled_by": disabled_by,
            },
        )


def _guard_downgrade(connection: Connection) -> None:
    counts = {
        table_name: int(
            connection.execute(
                text(f"SELECT COUNT(*) FROM {table_name} WHERE action_type=:action"),
                {"action": WORKSPACE_AUTHORIZATION_ACTION},
            ).scalar_one()
        )
        for table_name, _constraint_name in APPROVAL_ACTION_CHECKS
    }
    if any(counts.values()):
        evidence = ", ".join(f"{table}={count}" for table, count in counts.items() if count)
        raise RuntimeError(
            "0027 downgrade blocked while workspace_authorization_mode_change approval rows remain; "
            + evidence
        )


def upgrade() -> None:
    if not context.is_offline_mode():
        _validate_workspace_preflight(op.get_bind())

    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column(
            "permission_model_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
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
        sa.Column("enforced_at", _datetime6(), nullable=True),
        sa.Column("enforced_by", sa.String(length=64), nullable=True),
        sa.Column("disabled_at", _datetime6(), nullable=True),
        sa.Column("disabled_by", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_tenant_workspace_authorization_policies_scope_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            name="uq_tenant_workspace_authorization_policies_scope_workspace",
        ),
        sa.CheckConstraint(
            "mode IN ('disabled','shadow','enforced')",
            name="ck_tenant_workspace_auth_policies_mode",
        ),
        sa.CheckConstraint(
            "permission_model_version > 0",
            name="ck_tenant_workspace_auth_policies_model_version_positive",
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_tenant_workspace_auth_policies_revision_positive",
        ),
        sa.CheckConstraint(
            "(mode = 'shadow' AND enforced_at IS NULL AND enforced_by IS NULL "
            "AND disabled_at IS NULL AND disabled_by IS NULL) OR "
            "(mode = 'enforced' AND enforced_at IS NOT NULL AND enforced_by IS NOT NULL "
            "AND disabled_at IS NULL AND disabled_by IS NULL) OR "
            "(mode = 'disabled' AND disabled_at IS NOT NULL AND disabled_by IS NOT NULL "
            "AND enforced_at IS NULL AND enforced_by IS NULL)",
            name="ck_tenant_workspace_auth_policies_mode_evidence",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_tenant_workspace_authorization_policies_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["tenant_workspaces.tenant_id", "tenant_workspaces.id"],
            name="fk_tenant_workspace_authorization_policies_scope_workspace",
        ),
    )
    for name, columns in POLICY_INDEX_SPECS:
        op.create_index(name, TABLE_NAME, list(columns), unique=False)

    if not context.is_offline_mode():
        _backfill_workspace_authorization(op.get_bind())

    _replace_approval_action_checks(APPROVAL_ACTION_TYPES_0027)


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0027 downgrade requires an online preflight for "
            "workspace_authorization_mode_change approval rows"
        )

    _guard_downgrade(op.get_bind())
    _replace_approval_action_checks(APPROVAL_ACTION_TYPES_0026)
    for name, _columns in reversed(POLICY_INDEX_SPECS):
        op.drop_index(name, table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
