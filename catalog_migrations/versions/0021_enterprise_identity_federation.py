"""Add enterprise identity federation control-plane authority.

Revision ID: 0021_enterprise_identity_federation
Revises: 0020_tenant_invitation_lifecycle
Create Date: 2026-08-26
"""

from __future__ import annotations
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0021_enterprise_identity_federation"
down_revision = "0020_tenant_invitation_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _datetime6():
    return (
        sa.DateTime()
        .with_variant(mysql.DATETIME(fsp=6), "mysql")
        .with_variant(mysql.DATETIME(fsp=6), "mariadb")
    )


def upgrade():
    with op.batch_alter_table("alembic_version") as batch:
        batch.alter_column(
            "version_num",
            existing_type=sa.String(length=32),
            type_=sa.String(length=64),
            nullable=False,
        )

    op.create_table(
        "tenant_verified_domains",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("normalized_domain", sa.String(253), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'pending'")),
        sa.Column(
            "verification_method",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'dns_txt'"),
        ),
        sa.Column("challenge_token", sa.String(128), nullable=False),
        sa.Column("txt_host", sa.String(320), nullable=False),
        sa.Column("txt_value", sa.String(512), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("last_checked_at", _datetime6(), nullable=True),
        sa.Column("verified_at", _datetime6(), nullable=True),
        sa.Column("verified_by", sa.String(64), nullable=True),
        sa.Column("revoked_at", _datetime6(), nullable=True),
        sa.Column("revoked_by", sa.String(64), nullable=True),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("updated_by", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_domain", name="uq_tenant_verified_domains_global_domain"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tenant_verified_domains_scope_id"),
        sa.CheckConstraint(
            "status IN ('pending','verified','revoked')", name="ck_tenant_verified_domains_status"
        ),
        sa.CheckConstraint(
            "verification_method='dns_txt'", name="ck_tenant_verified_domains_method"
        ),
        sa.CheckConstraint("revision > 0", name="ck_tenant_verified_domains_revision_positive"),
        sa.CheckConstraint(
            "status <> 'verified' OR (verified_at IS NOT NULL AND verified_by IS NOT NULL)",
            name="ck_tenant_verified_domains_verified_evidence",
        ),
        sa.CheckConstraint(
            "status <> 'revoked' OR (revoked_at IS NOT NULL AND revoked_by IS NOT NULL)",
            name="ck_tenant_verified_domains_revoked_evidence",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_verified_domains_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["created_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_verified_domains_creator",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_verified_domains_updater",
        ),
        sa.ForeignKeyConstraint(
            ["verified_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_verified_domains_verifier",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_verified_domains_revoker",
        ),
    )
    op.create_index(
        "ix_tenant_verified_domains_tenant_status_updated",
        "tenant_verified_domains",
        ["tenant_id", "status", "updated_at", "id"],
    )
    op.create_table(
        "tenant_identity_providers",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("provider_type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("active_slot", sa.String(16), nullable=True),
        sa.Column("trusted_domain_id", sa.String(64), nullable=False),
        sa.Column("issuer_url", sa.String(1024), nullable=True),
        sa.Column("client_id", sa.String(256), nullable=True),
        sa.Column("secret_ref", sa.String(512), nullable=True),
        sa.Column("scopes", sa.String(512), nullable=True),
        sa.Column("entity_id", sa.String(512), nullable=True),
        sa.Column("sso_url", sa.String(1024), nullable=True),
        sa.Column("metadata_url", sa.String(1024), nullable=True),
        sa.Column("certificate_fingerprint", sa.String(128), nullable=True),
        sa.Column(
            "validation_state", sa.String(16), nullable=False, server_default=sa.text("'unchecked'")
        ),
        sa.Column("last_validated_at", _datetime6(), nullable=True),
        sa.Column("validation_error", sa.String(512), nullable=True),
        sa.Column("metadata_hash", sa.String(64), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("updated_by", sa.String(64), nullable=False),
        sa.Column("activated_at", _datetime6(), nullable=True),
        sa.Column("activated_by", sa.String(64), nullable=True),
        sa.Column("disabled_at", _datetime6(), nullable=True),
        sa.Column("disabled_by", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "active_slot", name="uq_tenant_identity_providers_active_slot"
        ),
        sa.CheckConstraint(
            "provider_type IN ('oidc','saml')", name="ck_tenant_identity_providers_type"
        ),
        sa.CheckConstraint(
            "status IN ('draft','active','disabled')", name="ck_tenant_identity_providers_status"
        ),
        sa.CheckConstraint(
            "validation_state IN ('unchecked','valid','invalid','unavailable')",
            name="ck_tenant_identity_providers_validation",
        ),
        sa.CheckConstraint("revision > 0", name="ck_tenant_identity_providers_revision_positive"),
        sa.CheckConstraint(
            "(status='active' AND active_slot='primary') OR (status<>'active' AND active_slot IS NULL)",
            name="ck_tenant_identity_providers_active_slot",
        ),
        sa.CheckConstraint(
            "(provider_type='oidc' AND issuer_url IS NOT NULL AND client_id IS NOT NULL AND secret_ref IS NOT NULL) OR (provider_type='saml' AND entity_id IS NOT NULL AND sso_url IS NOT NULL AND certificate_fingerprint IS NOT NULL)",
            name="ck_tenant_identity_providers_type_fields",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_identity_providers_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "trusted_domain_id"],
            ["tenant_verified_domains.tenant_id", "tenant_verified_domains.id"],
            name="fk_tenant_identity_providers_domain",
        ),
    )
    op.create_index(
        "ix_tenant_identity_providers_tenant_status",
        "tenant_identity_providers",
        ["tenant_id", "status", "updated_at", "id"],
    )
    op.create_table(
        "tenant_scim_tokens",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("active_name_key", sa.String(128), nullable=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("token_prefix", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'active'")),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("expires_at", _datetime6(), nullable=False),
        sa.Column("last_used_at", _datetime6(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("issued_at", _datetime6(), nullable=False),
        sa.Column("issued_by", sa.String(64), nullable=False),
        sa.Column("revoked_at", _datetime6(), nullable=True),
        sa.Column("revoked_by", sa.String(64), nullable=True),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "active_name_key", name="uq_tenant_scim_tokens_active_name"
        ),
        sa.UniqueConstraint("tenant_id", "token_hash", name="uq_tenant_scim_tokens_hash"),
        sa.CheckConstraint(
            "status IN ('active','revoked','expired')", name="ck_tenant_scim_tokens_status"
        ),
        sa.CheckConstraint("length(token_hash)=64", name="ck_tenant_scim_tokens_hash_length"),
        sa.CheckConstraint("revision > 0", name="ck_tenant_scim_tokens_revision_positive"),
        sa.CheckConstraint(
            "(status='active' AND active_name_key=name) OR (status<>'active' AND active_name_key IS NULL)",
            name="ck_tenant_scim_tokens_active_name",
        ),
        sa.CheckConstraint(
            "status <> 'revoked' OR (revoked_at IS NOT NULL AND revoked_by IS NOT NULL)",
            name="ck_tenant_scim_tokens_revoked_evidence",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_tenant_scim_tokens_tenant"),
        sa.ForeignKeyConstraint(
            ["issued_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_scim_tokens_issuer",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_scim_tokens_revoker",
        ),
    )
    op.create_index(
        "ix_tenant_scim_tokens_tenant_status_expires",
        "tenant_scim_tokens",
        ["tenant_id", "status", "expires_at", "id"],
    )


def downgrade():
    op.drop_table("tenant_scim_tokens")
    op.drop_table("tenant_identity_providers")
    op.drop_table("tenant_verified_domains")
