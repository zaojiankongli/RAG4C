"""Add SCIM provisioning link authority and token usage evidence."""

from __future__ import annotations
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0022_scim_provisioning_data_plane"
down_revision = "0021_enterprise_identity_federation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def dt6():
    return (
        sa.DateTime()
        .with_variant(mysql.DATETIME(fsp=6), "mysql")
        .with_variant(mysql.DATETIME(fsp=6), "mariadb")
    )


def upgrade():
    _is_mysql = op.get_bind().dialect.name == "mysql"
    with op.batch_alter_table("tenant_scim_tokens") as b:
        b.add_column(sa.Column("last_used_ip_hash", sa.String(64), nullable=True))
        b.add_column(
            sa.Column("use_count", sa.Integer(), nullable=False, server_default=sa.text("0"))
        )
        b.create_unique_constraint("uq_tenant_scim_tokens_scope_id", ["tenant_id", "id"])
    op.execute(
        sa.text(
            "UPDATE tenant_scim_tokens SET last_used_ip_hash='0000000000000000000000000000000000000000000000000000000000000000', use_count=1 WHERE last_used_at IS NOT NULL"
        )
    )
    with op.batch_alter_table("tenant_scim_tokens") as b:
        b.create_check_constraint(
            "ck_tenant_scim_tokens_usage_evidence",
            "(last_used_at IS NULL AND last_used_ip_hash IS NULL AND use_count=0) OR (last_used_at IS NOT NULL AND last_used_ip_hash IS NOT NULL AND length(last_used_ip_hash)=64 AND use_count>0)",
        )

    def link(name, target, col, display):
        op.create_table(
            name,
            sa.Column("id", sa.String(64), nullable=False),
            sa.Column("tenant_id", sa.String(64), nullable=False),
            sa.Column(col, sa.String(64), nullable=False),
            sa.Column("external_id", sa.String(256), nullable=False),
            sa.Column(display, sa.String(256), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column("last_provisioned_at", dt6(), nullable=False),
            sa.Column("source_token_id", sa.String(64), nullable=False),
            sa.Column(
                "created_at", dt6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
            ),
            sa.Column(
                "updated_at", dt6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_id", "external_id", name=f"uq_{name}_external_id"),
            sa.UniqueConstraint("tenant_id", display, name=f"uq_{name}_{display}"),
            sa.UniqueConstraint("tenant_id", col, name=f"uq_{name}_{col}"),
            sa.CheckConstraint("revision > 0", name=f"ck_{name}_revision_positive"),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name=f"fk_{name}_tenant"),
            sa.ForeignKeyConstraint(
                [col, "tenant_id"] if target == "tenant_members" else ["tenant_id", col],
                ["tenant_members.account_id", "tenant_members.tenant_id"]
                if target == "tenant_members"
                else [f"{target}.tenant_id", f"{target}.id"],
                name=f"fk_{name}_{col}",
            ),
            sa.ForeignKeyConstraint(
                ["tenant_id", "source_token_id"],
                ["tenant_scim_tokens.tenant_id", "tenant_scim_tokens.id"],
                name=f"fk_{name}_source_token",
            ),
        )
        op.create_index(f"ix_{name}_tenant_updated", name, ["tenant_id", "updated_at", "id"])

    link("tenant_scim_user_links", "tenant_members", "account_id", "user_name")
    link("tenant_scim_group_links", "tenant_groups", "group_id", "display_name")


def downgrade():
    op.drop_table("tenant_scim_group_links")
    op.drop_table("tenant_scim_user_links")
    with op.batch_alter_table("tenant_scim_tokens") as b:
        b.drop_constraint("ck_tenant_scim_tokens_usage_evidence", type_="check")
        b.drop_constraint("uq_tenant_scim_tokens_scope_id", type_="unique")
        b.drop_column("use_count")
        b.drop_column("last_used_ip_hash")
