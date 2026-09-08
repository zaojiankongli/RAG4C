"""Add tenant-scoped enterprise approval authority."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "0025_enterprise_approval_control"
down_revision: str | None = "0024_oidc_sso_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


APPROVAL_ACTION_TYPES: tuple[str, ...] = (
    "catalog_upgrade",
    "membership_bootstrap",
    "dataset_acl_disable",
    "member_role_change",
    "identity_provider_disable",
    "audit_retention_execute",
)


def _datetime6():
    return (
        sa.DateTime()
        .with_variant(mysql.DATETIME(fsp=6), "mysql")
        .with_variant(mysql.DATETIME(fsp=6), "mariadb")
    )


DECISION_IMMUTABLE_UPDATE_TRIGGER = "trg_tenant_approval_decisions_no_update"
DECISION_IMMUTABLE_DELETE_TRIGGER = "trg_tenant_approval_decisions_no_delete"
DECISION_IMMUTABLE_FUNCTION = "rag4c_tenant_approval_decisions_immutable"
DECISION_IMMUTABLE_MESSAGE = "tenant_approval_decisions are immutable"


def _dialect_name() -> str:
    return str(op.get_context().dialect.name).casefold()


def _create_decision_immutable_guards() -> None:
    dialect = _dialect_name()
    if dialect == "sqlite":
        op.execute(
            f"CREATE TRIGGER {DECISION_IMMUTABLE_UPDATE_TRIGGER} "
            "BEFORE UPDATE ON tenant_approval_decisions BEGIN "
            f"SELECT RAISE(ABORT, '{DECISION_IMMUTABLE_MESSAGE}'); END"
        )
        op.execute(
            f"CREATE TRIGGER {DECISION_IMMUTABLE_DELETE_TRIGGER} "
            "BEFORE DELETE ON tenant_approval_decisions BEGIN "
            f"SELECT RAISE(ABORT, '{DECISION_IMMUTABLE_MESSAGE}'); END"
        )
    elif dialect in {"mysql", "mariadb"}:
        op.execute(
            f"CREATE TRIGGER {DECISION_IMMUTABLE_UPDATE_TRIGGER} "
            "BEFORE UPDATE ON tenant_approval_decisions FOR EACH ROW "
            "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
            f"'{DECISION_IMMUTABLE_MESSAGE}'"
        )
        op.execute(
            f"CREATE TRIGGER {DECISION_IMMUTABLE_DELETE_TRIGGER} "
            "BEFORE DELETE ON tenant_approval_decisions FOR EACH ROW "
            "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
            f"'{DECISION_IMMUTABLE_MESSAGE}'"
        )
    elif dialect == "postgresql":
        op.execute(
            f"CREATE FUNCTION {DECISION_IMMUTABLE_FUNCTION}() "
            "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            f"RAISE EXCEPTION '{DECISION_IMMUTABLE_MESSAGE}'; END; $$"
        )
        for action in ("UPDATE", "DELETE"):
            trigger_name = (
                DECISION_IMMUTABLE_UPDATE_TRIGGER
                if action == "UPDATE"
                else DECISION_IMMUTABLE_DELETE_TRIGGER
            )
            op.execute(
                f"CREATE TRIGGER {trigger_name} "
                f"BEFORE {action} ON tenant_approval_decisions FOR EACH ROW "
                f"EXECUTE FUNCTION {DECISION_IMMUTABLE_FUNCTION}()"
            )


def _drop_decision_immutable_guards() -> None:
    dialect = _dialect_name()
    if dialect == "postgresql":
        op.execute(
            f"DROP TRIGGER IF EXISTS {DECISION_IMMUTABLE_UPDATE_TRIGGER} "
            "ON tenant_approval_decisions"
        )
        op.execute(
            f"DROP TRIGGER IF EXISTS {DECISION_IMMUTABLE_DELETE_TRIGGER} "
            "ON tenant_approval_decisions"
        )
        op.execute(f"DROP FUNCTION IF EXISTS {DECISION_IMMUTABLE_FUNCTION}()")
    elif dialect in {"sqlite", "mysql", "mariadb"}:
        op.execute(f"DROP TRIGGER IF EXISTS {DECISION_IMMUTABLE_UPDATE_TRIGGER}")
        op.execute(f"DROP TRIGGER IF EXISTS {DECISION_IMMUTABLE_DELETE_TRIGGER}")


def upgrade() -> None:
    op.create_table(
        "tenant_approval_policies",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("resource_scope", sa.String(length=512), nullable=True),
        sa.Column("active_scope_key", sa.String(length=512), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "required_approvals",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "request_expiry_minutes",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1440"),
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
        sa.Column("disabled_at", _datetime6(), nullable=True),
        sa.Column("disabled_by", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tenant_approval_policies_scope_id"),
        sa.UniqueConstraint(
            "tenant_id", "active_scope_key", name="uq_tenant_approval_policies_active_scope"
        ),
        sa.CheckConstraint(
            "action_type IN ('catalog_upgrade','membership_bootstrap','dataset_acl_disable','member_role_change','identity_provider_disable','audit_retention_execute')",
            name="ck_tenant_approval_policies_action_type",
        ),
        sa.CheckConstraint(
            "status IN ('active','disabled')", name="ck_tenant_approval_policies_status"
        ),
        sa.CheckConstraint(
            "required_approvals BETWEEN 1 AND 5",
            name="ck_tenant_approval_policies_required_count",
        ),
        sa.CheckConstraint(
            "request_expiry_minutes BETWEEN 15 AND 10080",
            name="ck_tenant_approval_policies_expiry_minutes",
        ),
        sa.CheckConstraint("revision > 0", name="ck_tenant_approval_policies_revision_positive"),
        sa.CheckConstraint(
            "(status='active' AND active_scope_key IS NOT NULL) OR (status='disabled' AND active_scope_key IS NULL)",
            name="ck_tenant_approval_policies_active_scope",
        ),
        sa.CheckConstraint(
            "status <> 'disabled' OR (disabled_at IS NOT NULL AND disabled_by IS NOT NULL)",
            name="ck_tenant_approval_policies_disabled_evidence",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_approval_policies_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["created_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_policies_creator",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_policies_updater",
        ),
        sa.ForeignKeyConstraint(
            ["disabled_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_policies_disabler",
        ),
    )
    op.create_index(
        "ix_tenant_approval_policies_tenant_status_action",
        "tenant_approval_policies",
        ["tenant_id", "status", "action_type", "updated_at", "id"],
    )

    op.create_table(
        "tenant_approval_policy_approvers",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("policy_id", sa.String(length=64), nullable=False),
        sa.Column("approver_kind", sa.String(length=16), nullable=False),
        sa.Column("approver_ref", sa.String(length=128), nullable=False),
        sa.Column("account_id", sa.String(length=64), nullable=True),
        sa.Column("group_id", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'active'"),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "policy_id",
            "approver_kind",
            "approver_ref",
            name="uq_tenant_approval_policy_approvers_identity",
        ),
        sa.CheckConstraint(
            "approver_kind IN ('account','role','group')",
            name="ck_tenant_approval_policy_approvers_kind",
        ),
        sa.CheckConstraint(
            "status IN ('active','disabled')",
            name="ck_tenant_approval_policy_approvers_status",
        ),
        sa.CheckConstraint(
            "revision > 0", name="ck_tenant_approval_policy_approvers_revision_positive"
        ),
        sa.CheckConstraint(
            "(approver_kind='account' AND account_id IS NOT NULL AND group_id IS NULL AND approver_ref=account_id) OR "
            "(approver_kind='group' AND account_id IS NULL AND group_id IS NOT NULL AND approver_ref=group_id) OR "
            "(approver_kind='role' AND account_id IS NULL AND group_id IS NULL AND length(approver_ref) BETWEEN 1 AND 128)",
            name="ck_tenant_approval_policy_approvers_reference",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            ["tenant_approval_policies.tenant_id", "tenant_approval_policies.id"],
            name="fk_tenant_approval_policy_approvers_scope_policy",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_policy_approvers_scope_account",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "group_id"],
            ["tenant_groups.tenant_id", "tenant_groups.id"],
            name="fk_tenant_approval_policy_approvers_scope_group",
        ),
        sa.ForeignKeyConstraint(
            ["created_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_policy_approvers_creator",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_policy_approvers_updater",
        ),
    )
    op.create_index(
        "ix_tenant_approval_policy_approvers_tenant_policy_status",
        "tenant_approval_policy_approvers",
        ["tenant_id", "policy_id", "status", "approver_kind", "id"],
    )
    op.create_index(
        "ix_tenant_approval_policy_approvers_tenant_account",
        "tenant_approval_policy_approvers",
        ["tenant_id", "account_id", "status", "id"],
    )

    op.create_table(
        "tenant_approval_requests",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("policy_id", sa.String(length=64), nullable=False),
        sa.Column("requester_id", sa.String(length=64), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=512), nullable=True),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("required_approvals", sa.Integer(), nullable=False),
        sa.Column("received_approvals", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("expires_at", _datetime6(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("execution_ticket_hash", sa.String(length=64), nullable=True),
        sa.Column("ticket_issued_at", _datetime6(), nullable=True),
        sa.Column("ticket_consumed_at", _datetime6(), nullable=True),
        sa.Column("rejected_at", _datetime6(), nullable=True),
        sa.Column("rejected_by", sa.String(length=64), nullable=True),
        sa.Column("rejection_comment", sa.String(length=500), nullable=True),
        sa.Column("cancelled_at", _datetime6(), nullable=True),
        sa.Column("cancelled_by", sa.String(length=64), nullable=True),
        sa.Column("executed_at", _datetime6(), nullable=True),
        sa.Column("executed_by", sa.String(length=64), nullable=True),
        sa.Column("execution_failed_at", _datetime6(), nullable=True),
        sa.Column("execution_failed_by", sa.String(length=64), nullable=True),
        sa.Column("execution_error", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tenant_approval_requests_scope_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "requester_id",
            "idempotency_key",
            name="uq_tenant_approval_requests_requester_key",
        ),
        sa.CheckConstraint(
            "action_type IN ('catalog_upgrade','membership_bootstrap','dataset_acl_disable','member_role_change','identity_provider_disable','audit_retention_execute')",
            name="ck_tenant_approval_requests_action_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','cancelled','expired','executing','executed','execution_failed')",
            name="ck_tenant_approval_requests_status",
        ),
        sa.CheckConstraint(
            "required_approvals BETWEEN 1 AND 5 AND received_approvals BETWEEN 0 AND required_approvals",
            name="ck_tenant_approval_requests_approval_counts",
        ),
        sa.CheckConstraint("revision > 0", name="ck_tenant_approval_requests_revision_positive"),
        sa.CheckConstraint(
            "length(payload_hash)=64", name="ck_tenant_approval_requests_payload_hash"
        ),
        sa.CheckConstraint(
            "length(idempotency_key) BETWEEN 1 AND 128",
            name="ck_tenant_approval_requests_idempotency_key_length",
        ),
        sa.CheckConstraint(
            "execution_ticket_hash IS NULL OR length(execution_ticket_hash)=64",
            name="ck_tenant_approval_requests_ticket_hash",
        ),
        sa.CheckConstraint(
            "status <> 'rejected' OR (rejected_at IS NOT NULL AND rejected_by IS NOT NULL AND rejection_comment IS NOT NULL AND length(rejection_comment) BETWEEN 1 AND 500)",
            name="ck_tenant_approval_requests_rejected_evidence",
        ),
        sa.CheckConstraint(
            "status <> 'cancelled' OR (cancelled_at IS NOT NULL AND cancelled_by IS NOT NULL)",
            name="ck_tenant_approval_requests_cancelled_evidence",
        ),
        sa.CheckConstraint(
            "status <> 'executing' OR (ticket_consumed_at IS NOT NULL AND execution_ticket_hash IS NOT NULL)",
            name="ck_tenant_approval_requests_executing_evidence",
        ),
        sa.CheckConstraint(
            "status <> 'executed' OR (executed_at IS NOT NULL AND executed_by IS NOT NULL AND execution_ticket_hash IS NOT NULL)",
            name="ck_tenant_approval_requests_executed_evidence",
        ),
        sa.CheckConstraint(
            "status <> 'execution_failed' OR (execution_failed_at IS NOT NULL AND execution_failed_by IS NOT NULL AND execution_error IS NOT NULL AND length(execution_error) BETWEEN 1 AND 512)",
            name="ck_tenant_approval_requests_execution_failed_evidence",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            ["tenant_approval_policies.tenant_id", "tenant_approval_policies.id"],
            name="fk_tenant_approval_requests_scope_policy",
        ),
        sa.ForeignKeyConstraint(
            ["requester_id", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_requests_scope_requester",
        ),
        sa.ForeignKeyConstraint(
            ["created_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_requests_creator",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_requests_updater",
        ),
        sa.ForeignKeyConstraint(
            ["rejected_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_requests_rejector",
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_requests_canceller",
        ),
        sa.ForeignKeyConstraint(
            ["executed_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_requests_executor",
        ),
        sa.ForeignKeyConstraint(
            ["execution_failed_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_requests_failure_actor",
        ),
    )
    op.create_index(
        "ix_tenant_approval_requests_tenant_status_expiry",
        "tenant_approval_requests",
        ["tenant_id", "status", "expires_at", "id"],
    )
    op.create_index(
        "ix_tenant_approval_requests_tenant_requester_status",
        "tenant_approval_requests",
        ["tenant_id", "requester_id", "status", "created_at", "id"],
    )
    op.create_index(
        "ix_tenant_approval_requests_tenant_action_resource",
        "tenant_approval_requests",
        ["tenant_id", "action_type", "resource_type", "resource_id", "id"],
    )

    op.create_table(
        "tenant_approval_decisions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("approver_id", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("comment", sa.String(length=500), nullable=False, server_default=sa.text("''")),
        sa.Column("decided_at", _datetime6(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "request_id",
            "approver_id",
            name="uq_tenant_approval_decisions_request_approver",
        ),
        sa.CheckConstraint(
            "decision IN ('approved','rejected')",
            name="ck_tenant_approval_decisions_decision",
        ),
        sa.CheckConstraint(
            "length(comment) <= 500", name="ck_tenant_approval_decisions_comment_length"
        ),
        sa.CheckConstraint(
            "decision <> 'rejected' OR length(comment) BETWEEN 1 AND 500",
            name="ck_tenant_approval_decisions_rejection_comment",
        ),
        sa.CheckConstraint("revision > 0", name="ck_tenant_approval_decisions_revision_positive"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            ["tenant_approval_requests.tenant_id", "tenant_approval_requests.id"],
            name="fk_tenant_approval_decisions_scope_request",
        ),
        sa.ForeignKeyConstraint(
            ["approver_id", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_decisions_scope_approver",
        ),
        sa.ForeignKeyConstraint(
            ["created_by", "tenant_id"],
            ["tenant_members.account_id", "tenant_members.tenant_id"],
            name="fk_tenant_approval_decisions_creator",
        ),
    )
    op.create_index(
        "ix_tenant_approval_decisions_tenant_request_time",
        "tenant_approval_decisions",
        ["tenant_id", "request_id", "decided_at", "id"],
    )
    op.create_index(
        "ix_tenant_approval_decisions_tenant_approver_time",
        "tenant_approval_decisions",
        ["tenant_id", "approver_id", "decided_at", "id"],
    )
    _create_decision_immutable_guards()


def downgrade() -> None:
    _drop_decision_immutable_guards()
    op.drop_table("tenant_approval_decisions")
    op.drop_table("tenant_approval_requests")
    op.drop_table("tenant_approval_policy_approvers")
    op.drop_table("tenant_approval_policies")
