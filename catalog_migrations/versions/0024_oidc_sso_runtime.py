"""Add OIDC authorization-code runtime authority."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0024_oidc_sso_runtime"
down_revision = "0023_enterprise_audit_compliance"
branch_labels = None
depends_on = None


def d():
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


def upgrade():
    with op.batch_alter_table("tenant_identity_providers") as b:
        b.create_unique_constraint("uq_tenant_identity_providers_scope_id", ["tenant_id", "id"])
    op.create_table(
        "tenant_oidc_login_transactions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("state_digest", sa.String(64), nullable=False),
        sa.Column("nonce_digest", sa.String(64), nullable=False),
        sa.Column("pkce_verifier_ciphertext", sa.Text(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("redirect_uri", sa.String(1024), nullable=False),
        sa.Column("expires_at", d(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("consumed_at", d()),
        sa.Column("failed_at", d()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", d(), nullable=False),
        sa.Column("updated_at", d(), nullable=False),
        sa.UniqueConstraint("state_digest", name="uq_tenant_oidc_login_state"),
        sa.CheckConstraint(
            "status IN ('pending','consumed','failed','expired')",
            name="ck_tenant_oidc_login_status",
        ),
        sa.CheckConstraint(
            "length(state_digest)=64 AND length(nonce_digest)=64",
            name="ck_tenant_oidc_login_digests",
        ),
        sa.CheckConstraint(
            "key_version > 0 AND revision > 0", name="ck_tenant_oidc_login_revisions"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "provider_id"],
            ["tenant_identity_providers.tenant_id", "tenant_identity_providers.id"],
            name="fk_tenant_oidc_login_provider",
        ),
    )
    op.create_index(
        "ix_tenant_oidc_login_tenant_status",
        "tenant_oidc_login_transactions",
        ["tenant_id", "status", "expires_at", "id"],
    )
    op.create_table(
        "tenant_oidc_subject_links",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("issuer", sa.String(1024), nullable=False),
        sa.Column("subject_digest", sa.String(64), nullable=False),
        sa.Column("normalized_email", sa.String(256), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("last_login_at", d()),
        sa.Column("created_at", d(), nullable=False),
        sa.Column("updated_at", d(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "provider_id", "subject_digest", name="uq_tenant_oidc_subject"
        ),
        sa.UniqueConstraint(
            "tenant_id", "provider_id", "account_id", name="uq_tenant_oidc_account"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "provider_id"],
            ["tenant_identity_providers.tenant_id", "tenant_identity_providers.id"],
            name="fk_tenant_oidc_subject_provider",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_oidc_subject_member",
        ),
    )
    op.create_index(
        "ix_tenant_oidc_subject_tenant_email",
        "tenant_oidc_subject_links",
        ["tenant_id", "normalized_email", "id"],
    )
    op.create_table(
        "tenant_sso_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("session_token_hash", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("expires_at", d(), nullable=False),
        sa.Column("last_seen_at", d()),
        sa.Column("ip_hash", sa.String(64)),
        sa.Column("user_agent_hash", sa.String(64)),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("revoked_at", d()),
        sa.Column("revoked_by", sa.String(64)),
        sa.Column("created_at", d(), nullable=False),
        sa.Column("updated_at", d(), nullable=False),
        sa.UniqueConstraint("session_token_hash", name="uq_tenant_sso_session_token"),
        sa.CheckConstraint(
            "status IN ('active','revoked','expired')", name="ck_tenant_sso_sessions_status"
        ),
        sa.CheckConstraint(
            "length(session_token_hash)=64 AND revision > 0",
            name="ck_tenant_sso_sessions_digest_revision",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_sso_sessions_member",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "provider_id"],
            ["tenant_identity_providers.tenant_id", "tenant_identity_providers.id"],
            name="fk_tenant_sso_sessions_provider",
        ),
    )
    op.create_index(
        "ix_tenant_sso_sessions_tenant_status",
        "tenant_sso_sessions",
        ["tenant_id", "status", "expires_at", "id"],
    )


def downgrade():
    op.drop_table("tenant_sso_sessions")
    op.drop_table("tenant_oidc_subject_links")
    op.drop_table("tenant_oidc_login_transactions")
