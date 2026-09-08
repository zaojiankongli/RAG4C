"""Add enterprise audit compliance authority."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0023_enterprise_audit_compliance"
down_revision = "0022_scim_provisioning_data_plane"
branch_labels = None
depends_on = None


def d():
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


def upgrade():
    op.create_table(
        "tenant_audit_retention_policies",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("audit_retention_days", sa.Integer(), nullable=False),
        sa.Column("export_retention_days", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("last_preview_at", d()),
        sa.Column("last_execution_at", d()),
        sa.Column("last_executed_by", sa.String(64)),
        sa.Column("created_at", d(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("updated_at", d(), nullable=False),
        sa.Column("updated_by", sa.String(64), nullable=False),
        sa.UniqueConstraint("tenant_id", name="uq_tenant_audit_retention_policies_tenant"),
        sa.CheckConstraint(
            "audit_retention_days BETWEEN 30 AND 3650", name="ck_tenant_audit_retention_audit_days"
        ),
        sa.CheckConstraint(
            "export_retention_days BETWEEN 1 AND 365", name="ck_tenant_audit_retention_export_days"
        ),
        sa.CheckConstraint(
            "status IN ('active','paused')", name="ck_tenant_audit_retention_status"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_audit_retention_tenant"
        ),
    )
    op.create_index(
        "ix_tenant_audit_retention_status",
        "tenant_audit_retention_policies",
        ["tenant_id", "status"],
    )
    op.create_table(
        "tenant_audit_legal_holds",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("active_name_key", sa.String(128)),
        sa.Column("reason", sa.String(512), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("sequence_from", sa.BigInteger()),
        sa.Column("sequence_to", sa.BigInteger()),
        sa.Column("time_from", d()),
        sa.Column("time_to", d()),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", d(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("released_at", d()),
        sa.Column("released_by", sa.String(64)),
        sa.Column("updated_at", d(), nullable=False),
        sa.Column("updated_by", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "active_name_key", name="uq_tenant_audit_legal_holds_active_name"
        ),
        sa.CheckConstraint(
            "status IN ('active','released')", name="ck_tenant_audit_legal_holds_status"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_audit_legal_holds_tenant"
        ),
    )
    op.create_index(
        "ix_tenant_audit_legal_holds_status",
        "tenant_audit_legal_holds",
        ["tenant_id", "status", "updated_at", "id"],
    )
    op.create_table(
        "tenant_audit_export_jobs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("format", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("filters_json", sa.JSON(), nullable=False),
        sa.Column("sequence_from", sa.BigInteger()),
        sa.Column("sequence_to", sa.BigInteger()),
        sa.Column("time_from", d()),
        sa.Column("time_to", d()),
        sa.Column("object_key", sa.String(512)),
        sa.Column("sha256", sa.String(64)),
        sa.Column("byte_size", sa.BigInteger()),
        sa.Column("row_count", sa.BigInteger()),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("requested_at", d(), nullable=False),
        sa.Column("requested_by", sa.String(64), nullable=False),
        sa.Column("completed_at", d()),
        sa.Column("expires_at", d()),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_audit_export_jobs_tenant"
        ),
        sa.CheckConstraint("format IN ('ndjson','csv')", name="ck_tenant_audit_export_jobs_format"),
        sa.CheckConstraint(
            "status IN ('queued','running','completed','failed','expired')",
            name="ck_tenant_audit_export_jobs_status",
        ),
    )
    op.create_index(
        "ix_tenant_audit_export_jobs_status",
        "tenant_audit_export_jobs",
        ["tenant_id", "status", "requested_at", "id"],
    )


def downgrade():
    op.drop_table("tenant_audit_export_jobs")
    op.drop_table("tenant_audit_legal_holds")
    op.drop_table("tenant_audit_retention_policies")
