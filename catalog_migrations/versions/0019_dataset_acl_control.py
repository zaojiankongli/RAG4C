"""Add persistent Dataset ACL control and mutation idempotency ledger.

Revision ID: 0019_dataset_acl_control
Revises: 0018_organization_membership
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "0019_dataset_acl_control"
down_revision: str | None = "0018_organization_membership"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


DATASET_ACL_MUTATION_INDEX_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "ix_dataset_acl_mutation_requests_tenant_dataset_status_created",
        ("tenant_id", "dataset_id", "status", "created_at", "id"),
    ),
    (
        "ix_dataset_acl_mutation_requests_tenant_actor_key",
        ("tenant_id", "actor_id", "idempotency_key"),
    ),
)


def _datetime6():
    return (
        sa.DateTime()
        .with_variant(mysql.DATETIME(fsp=6), "mysql")
        .with_variant(mysql.DATETIME(fsp=6), "mariadb")
    )


def _backfill_dataset_acl_control() -> None:
    """Materialize the secure initial ACL mode from authoritative 0017 grants.

    ``acl_revision=1`` represents the first durable control state rather than a
    user mutation.  The earliest active grant timestamp is the only historical
    enablement time available in 0017, while ``migration:0019`` explicitly
    records that no authoritative enabling actor was persisted.  Datasets with
    no active grant retain the additive defaults ``tenant_role/1/NULL/NULL``.
    """

    op.execute(
        sa.text(
            "UPDATE datasets "
            "SET acl_mode='dataset_acl', "
            "acl_revision=1, "
            "acl_enabled_at=("
            "SELECT MIN(dataset_access_grants.created_at) "
            "FROM dataset_access_grants "
            "WHERE dataset_access_grants.tenant_id=datasets.tenant_id "
            "AND dataset_access_grants.dataset_id=datasets.id "
            "AND dataset_access_grants.status='active'"
            "), "
            "acl_enabled_by='migration:0019' "
            "WHERE EXISTS ("
            "SELECT 1 FROM dataset_access_grants "
            "WHERE dataset_access_grants.tenant_id=datasets.tenant_id "
            "AND dataset_access_grants.dataset_id=datasets.id "
            "AND dataset_access_grants.status='active'"
            ")"
        )
    )


def upgrade() -> None:
    _is_mysql = op.get_bind().dialect.name == "mysql"
    with op.batch_alter_table("datasets") as batch:
        batch.add_column(
            sa.Column(
                "acl_mode",
                sa.String(length=16),
                nullable=False,
                server_default=sa.text("'tenant_role'"),
            )
        )
        batch.add_column(
            sa.Column(
                "acl_revision",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )
        batch.add_column(sa.Column("acl_enabled_at", _datetime6(), nullable=True))
        batch.add_column(sa.Column("acl_enabled_by", sa.String(length=64), nullable=True))
        batch.create_check_constraint(
            "ck_datasets_acl_mode",
            "acl_mode IN ('tenant_role', 'dataset_acl')",
        )
        batch.create_check_constraint(
            "ck_datasets_acl_revision_positive",
            "acl_revision > 0",
        )

    _backfill_dataset_acl_control()

    op.create_table(
        "dataset_acl_mutation_requests",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column("response_json", sa.JSON(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            _datetime6(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("completed_at", _datetime6(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'failed')",
            name="ck_dataset_acl_mutation_requests_status",
        ),
        sa.CheckConstraint(
            "length(idempotency_key) BETWEEN 1 AND 128",
            name="ck_dataset_acl_mutation_requests_idempotency_key_length",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_dataset_acl_mutation_requests_scope_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_dataset_acl_mutation_requests_scope_actor",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "actor_id",
            "idempotency_key",
            name="uq_dataset_acl_mutation_requests_actor_key",
        ),
    )
    for name, columns in DATASET_ACL_MUTATION_INDEX_SPECS:
        op.create_index(
            name,
            "dataset_acl_mutation_requests",
            list(columns),
            unique=False,
        )


def downgrade() -> None:
    for name, _columns in reversed(DATASET_ACL_MUTATION_INDEX_SPECS):
        op.drop_index(name, table_name="dataset_acl_mutation_requests")
    op.drop_table("dataset_acl_mutation_requests")

    with op.batch_alter_table("datasets") as batch:
        batch.drop_constraint("ck_datasets_acl_revision_positive", type_="check")
        batch.drop_constraint("ck_datasets_acl_mode", type_="check")
        batch.drop_column("acl_enabled_by")
        batch.drop_column("acl_enabled_at")
        batch.drop_column("acl_revision")
        batch.drop_column("acl_mode")
