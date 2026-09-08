"""Add secure tenant invitation lifecycle and generic tenant mutation ledger.

Revision ID: 0020_tenant_invitation_lifecycle
Revises: 0019_dataset_acl_control
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy import Connection, text
from sqlalchemy.dialects import mysql


revision: str = "0020_tenant_invitation_lifecycle"
down_revision: str | None = "0019_dataset_acl_control"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


class TenantInvitationLifecyclePreflightError(RuntimeError):
    """Raised when existing invitation data cannot satisfy 0020 invariants."""


INVITATION_INDEX_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "ix_tenant_invitations_tenant_status_updated",
        ("tenant_id", "status", "updated_at", "id"),
    ),
)
TENANT_CONTROL_LEDGER_INDEX_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "ix_tenant_control_mutation_requests_tenant_status_created",
        ("tenant_id", "status", "created_at", "id"),
    ),
    (
        "ix_tenant_control_mutation_requests_tenant_actor_created",
        ("tenant_id", "actor_id", "created_at", "id"),
    ),
    (
        "ix_tenant_control_mutation_requests_tenant_resource_created",
        ("tenant_id", "resource_type", "resource_id", "created_at", "id"),
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


def _validate_invitation_preflight(connection: Connection) -> None:
    duplicate_pending = list(
        connection.execute(
            text(
                "SELECT tenant_id, normalized_email, COUNT(*) AS invitation_count "
                "FROM tenant_invitations WHERE status='pending' "
                "GROUP BY tenant_id, normalized_email HAVING COUNT(*) > 1 "
                "ORDER BY tenant_id, normalized_email LIMIT 20"
            )
        )
    )
    duplicate_tokens = list(
        connection.execute(
            text(
                "SELECT tenant_id, token_hash, COUNT(*) AS token_count "
                "FROM tenant_invitations GROUP BY tenant_id, token_hash "
                "HAVING COUNT(*) > 1 ORDER BY tenant_id, token_hash LIMIT 20"
            )
        )
    )
    invalid_accepted = list(
        connection.execute(
            text(
                "SELECT id, tenant_id, normalized_email FROM tenant_invitations "
                "WHERE status='accepted' "
                "AND (accepted_at IS NULL OR accepted_by IS NULL) "
                "ORDER BY tenant_id, id LIMIT 20"
            )
        )
    )
    problems: list[str] = []
    if duplicate_pending:
        problems.append("duplicate pending tenant invitations: " + _render_rows(duplicate_pending))
    if duplicate_tokens:
        problems.append(
            "duplicate tenant invitation token hashes: " + _render_rows(duplicate_tokens)
        )
    if invalid_accepted:
        problems.append(
            "accepted invitations missing acceptance evidence: " + _render_rows(invalid_accepted)
        )
    if problems:
        raise TenantInvitationLifecyclePreflightError("; ".join(problems))


def _backfill_invitation_lifecycle() -> None:
    """Project deterministic compatibility facts before enforcing constraints.

    Existing rows predate send/revoke actor columns.  ``last_sent_at`` and
    ``updated_by`` use the authoritative creation/inviter facts specified by the
    design.  Legacy revoked rows use their last persisted update and inviter as
    compatibility evidence; the runbook explicitly treats this as migration
    attribution, not reconstructed historical audit truth.
    """

    op.execute(
        sa.text(
            "UPDATE tenant_invitations SET "
            "pending_email_key=CASE WHEN status='pending' THEN normalized_email ELSE NULL END, "
            "last_sent_at=created_at, "
            "send_count=1, "
            "revoked_at=CASE WHEN status='revoked' THEN updated_at ELSE NULL END, "
            "revoked_by=CASE WHEN status='revoked' THEN invited_by ELSE NULL END, "
            "updated_by=invited_by"
        )
    )


def upgrade() -> None:
    if not context.is_offline_mode():
        _validate_invitation_preflight(op.get_bind())

    with op.batch_alter_table("tenant_invitations") as batch:
        batch.add_column(sa.Column("pending_email_key", sa.String(length=256), nullable=True))
        batch.add_column(sa.Column("last_sent_at", _datetime6(), nullable=True))
        batch.add_column(
            sa.Column(
                "send_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )
        batch.add_column(sa.Column("revoked_at", _datetime6(), nullable=True))
        batch.add_column(sa.Column("revoked_by", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("updated_by", sa.String(length=64), nullable=True))

    _backfill_invitation_lifecycle()

    with op.batch_alter_table("tenant_invitations") as batch:
        batch.alter_column("last_sent_at", existing_type=_datetime6(), nullable=False)
        batch.alter_column("updated_by", existing_type=sa.String(length=64), nullable=False)
        batch.create_check_constraint(
            "ck_tenant_invitations_send_count_positive",
            "send_count > 0",
        )
        batch.create_check_constraint(
            "ck_tenant_invitations_pending_email_key",
            "(status = 'pending' AND pending_email_key IS NOT NULL "
            "AND pending_email_key = normalized_email) OR "
            "(status <> 'pending' AND pending_email_key IS NULL)",
        )
        batch.create_check_constraint(
            "ck_tenant_invitations_accepted_evidence",
            "status <> 'accepted' OR (accepted_at IS NOT NULL AND accepted_by IS NOT NULL)",
        )
        batch.create_check_constraint(
            "ck_tenant_invitations_revoked_evidence",
            "status <> 'revoked' OR (revoked_at IS NOT NULL AND revoked_by IS NOT NULL)",
        )
        batch.create_unique_constraint(
            "uq_tenant_invitations_pending_email",
            ["tenant_id", "pending_email_key"],
        )
        batch.create_unique_constraint(
            "uq_tenant_invitations_token_hash",
            ["tenant_id", "token_hash"],
        )
        batch.create_foreign_key(
            "fk_tenant_invitations_scope_revoker",
            "tenant_members",
            ["revoked_by", "tenant_id"],
            ["account_id", "tenant_id"],
        )
        batch.create_foreign_key(
            "fk_tenant_invitations_scope_updater",
            "tenant_members",
            ["updated_by", "tenant_id"],
            ["account_id", "tenant_id"],
        )
        for name, columns in INVITATION_INDEX_SPECS:
            batch.create_index(name, list(columns), unique=False)

    op.create_table(
        "tenant_control_mutation_requests",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("response_json", sa.JSON(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            _datetime6(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("completed_at", _datetime6(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'failed')",
            name="ck_tenant_control_mutation_requests_status",
        ),
        sa.CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_tenant_control_mutation_requests_idempotency_digest",
        ),
        sa.CheckConstraint(
            "length(request_hash) = 64",
            name="ck_tenant_control_mutation_requests_request_hash",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_tenant_control_mutation_requests_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["accounts.id"],
            name="fk_tenant_control_mutation_requests_actor",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "actor_id",
            "idempotency_key",
            name="uq_tenant_control_mutation_requests_actor_key",
        ),
    )
    for name, columns in TENANT_CONTROL_LEDGER_INDEX_SPECS:
        op.create_index(
            name,
            "tenant_control_mutation_requests",
            list(columns),
            unique=False,
        )


def downgrade() -> None:
    for name, _columns in reversed(TENANT_CONTROL_LEDGER_INDEX_SPECS):
        op.drop_index(name, table_name="tenant_control_mutation_requests")
    op.drop_table("tenant_control_mutation_requests")

    with op.batch_alter_table("tenant_invitations") as batch:
        for name, _columns in reversed(INVITATION_INDEX_SPECS):
            batch.drop_index(name)
        batch.drop_constraint("fk_tenant_invitations_scope_updater", type_="foreignkey")
        batch.drop_constraint("fk_tenant_invitations_scope_revoker", type_="foreignkey")
        batch.drop_constraint("uq_tenant_invitations_token_hash", type_="unique")
        batch.drop_constraint("uq_tenant_invitations_pending_email", type_="unique")
        batch.drop_constraint("ck_tenant_invitations_revoked_evidence", type_="check")
        batch.drop_constraint("ck_tenant_invitations_accepted_evidence", type_="check")
        batch.drop_constraint("ck_tenant_invitations_pending_email_key", type_="check")
        batch.drop_constraint("ck_tenant_invitations_send_count_positive", type_="check")
        batch.drop_column("updated_by")
        batch.drop_column("revoked_by")
        batch.drop_column("revoked_at")
        batch.drop_column("send_count")
        batch.drop_column("last_sent_at")
        batch.drop_column("pending_email_key")
