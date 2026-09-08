"""Add Tenant-scoped enterprise document recovery authority.

Revision ID: 0033_enterprise_content_recovery
Revises: 0032_enterprise_notification_center
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import mysql

revision: str = "0033_enterprise_content_recovery"
down_revision: str | None = "0032_enterprise_notification_center"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "tenant_content_retention_policies",
    "tenant_document_recycle_entries",
    "tenant_document_legal_holds",
    "tenant_document_purge_requests",
    "tenant_document_recovery_events",
)
IMMUTABLE_TABLES = ("tenant_document_recovery_events",)
EVENT_INSERT_TRIGGER = "trg_tenant_document_recovery_events_validate_insert"
EVENT_IMMUTABLE_FUNCTION = "rag4c_content_recovery_event_immutable"
EVENT_VALIDATE_FUNCTION = "rag4c_content_recovery_event_validate"

APPROVAL_ACTION_TYPES_0032: tuple[str, ...] = (
    "catalog_upgrade",
    "membership_bootstrap",
    "dataset_acl_disable",
    "member_role_change",
    "identity_provider_disable",
    "audit_retention_execute",
    "workspace_authorization_mode_change",
    "dataset_workspace_transfer",
    "knowledge_base_release_publish",
    "knowledge_base_release_rollback",
    "knowledge_base_release_quality_waiver",
)
APPROVAL_ACTION_TYPES_0033 = APPROVAL_ACTION_TYPES_0032 + ("document_purge",)

DOCUMENT_LIFECYCLE_STATES_0032: tuple[str, ...] = (
    "active",
    "expired",
    "delete_requested",
    "deleting",
    "delete_failed",
    "deleted",
)
DOCUMENT_LIFECYCLE_STATES_0033 = (
    DOCUMENT_LIFECYCLE_STATES_0032[:2] + ("recycled",) + DOCUMENT_LIFECYCLE_STATES_0032[2:]
)


def _datetime6():
    return (
        sa.DateTime()
        .with_variant(mysql.DATETIME(fsp=6), "mysql")
        .with_variant(mysql.DATETIME(fsp=6), "mariadb")
    )


def _dialect_name() -> str:
    return str(op.get_context().dialect.name).casefold()


def _approval_action_check(actions: tuple[str, ...]) -> str:
    values = ",".join(f"'{action}'" for action in actions)
    return f"action_type IN ({values})"


def _document_lifecycle_check(states: tuple[str, ...]) -> str:
    values = ", ".join(f"'{state}'" for state in states)
    return f"lifecycle_state IN ({values})"


def _canonical_recycle_key_check():
    status = sa.column("status", sa.String(24))
    active_key = sa.column("active_recycle_key", sa.String(160))
    expected = (
        sa.column("dataset_id", sa.String(64))
        + sa.literal(":")
        + sa.column("document_id", sa.String(64))
    )
    return sa.or_(
        sa.and_(status == "recycled", active_key.is_not(None), active_key == expected),
        sa.and_(status == "restoring", active_key.is_not(None), active_key == expected),
        sa.and_(status == "purge_requested", active_key.is_not(None), active_key == expected),
        sa.and_(status.in_(("restored", "purged", "failed")), active_key.is_(None)),
    )


def _canonical_hold_key_check():
    status = sa.column("status", sa.String(16))
    active_key = sa.column("active_hold_key", sa.String(192))
    expected = (
        sa.column("recycle_entry_id", sa.String(64))
        + sa.literal(":")
        + sa.column("reason_code", sa.String(64))
    )
    return sa.or_(
        sa.and_(status == "active", active_key.is_not(None), active_key == expected),
        sa.and_(status == "released", active_key.is_(None)),
    )


def _replace_approval_checks(actions: tuple[str, ...]) -> None:
    sqltext = _approval_action_check(actions)
    for table_name in ("tenant_approval_policies", "tenant_approval_requests"):
        with op.batch_alter_table(table_name) as batch:
            batch.drop_constraint(f"ck_{table_name}_action_type", type_="check")
            batch.create_check_constraint(f"ck_{table_name}_action_type", sqltext)


def _replace_document_lifecycle_check(states: tuple[str, ...]) -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_constraint("ck_documents_lifecycle_state", type_="check")
        batch.create_check_constraint(
            "ck_documents_lifecycle_state", _document_lifecycle_check(states)
        )


def _create_guards() -> None:
    dialect = _dialect_name()
    if dialect == "sqlite":
        for operation in ("UPDATE", "DELETE"):
            name = f"trg_tenant_document_recovery_events_no_{operation.casefold()}"
            op.execute(
                f"CREATE TRIGGER {name} BEFORE {operation} ON tenant_document_recovery_events "
                "BEGIN SELECT RAISE(ABORT, 'tenant_document_recovery_events are immutable'); END"
            )
        op.execute(
            f"CREATE TRIGGER {EVENT_INSERT_TRIGGER} BEFORE INSERT ON tenant_document_recovery_events "
            "BEGIN "
            "SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM tenant_document_recycle_entries r "
            "WHERE r.tenant_id=NEW.tenant_id AND r.dataset_id=NEW.dataset_id "
            "AND r.document_id=NEW.document_id AND r.id=NEW.recycle_entry_id) "
            "THEN RAISE(ABORT, 'recovery event recycle entry required') END; "
            "SELECT CASE WHEN NEW.sequence=1 AND (NEW.event_type<>'recycled' "
            "OR NEW.previous_event_digest IS NOT NULL OR EXISTS (SELECT 1 "
            "FROM tenant_document_recovery_events e WHERE e.tenant_id=NEW.tenant_id "
            "AND e.dataset_id=NEW.dataset_id AND e.document_id=NEW.document_id "
            "AND e.recycle_entry_id=NEW.recycle_entry_id)) "
            "THEN RAISE(ABORT, 'invalid recovery event first link') END; "
            "SELECT CASE WHEN NEW.sequence>1 AND NOT EXISTS (SELECT 1 "
            "FROM tenant_document_recovery_events e WHERE e.tenant_id=NEW.tenant_id "
            "AND e.dataset_id=NEW.dataset_id AND e.document_id=NEW.document_id "
            "AND e.recycle_entry_id=NEW.recycle_entry_id AND e.sequence=NEW.sequence-1 "
            "AND e.event_digest=NEW.previous_event_digest) "
            "THEN RAISE(ABORT, 'invalid recovery event previous link') END; "
            "END"
        )
    elif dialect in {"mysql", "mariadb"}:
        for operation in ("UPDATE", "DELETE"):
            name = f"trg_tenant_document_recovery_events_no_{operation.casefold()}"
            op.execute(
                f"CREATE TRIGGER {name} BEFORE {operation} ON tenant_document_recovery_events "
                "FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
                "'tenant_document_recovery_events are immutable'"
            )
        op.execute(
            f"CREATE TRIGGER {EVENT_INSERT_TRIGGER} BEFORE INSERT ON tenant_document_recovery_events "
            "FOR EACH ROW BEGIN "
            "IF NOT EXISTS (SELECT 1 FROM tenant_document_recycle_entries r "
            "WHERE r.tenant_id=NEW.tenant_id AND r.dataset_id=NEW.dataset_id "
            "AND r.document_id=NEW.document_id AND r.id=NEW.recycle_entry_id) THEN "
            "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='recovery event recycle entry required'; END IF; "
            "IF NEW.sequence=1 THEN "
            "IF NEW.event_type<>'recycled' OR NEW.previous_event_digest IS NOT NULL "
            "OR EXISTS (SELECT 1 FROM tenant_document_recovery_events e "
            "WHERE e.tenant_id=NEW.tenant_id AND e.dataset_id=NEW.dataset_id "
            "AND e.document_id=NEW.document_id AND e.recycle_entry_id=NEW.recycle_entry_id) THEN "
            "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='invalid recovery event first link'; END IF; "
            "ELSEIF NEW.sequence>1 THEN "
            "IF NOT EXISTS (SELECT 1 FROM tenant_document_recovery_events e "
            "WHERE e.tenant_id=NEW.tenant_id AND e.dataset_id=NEW.dataset_id "
            "AND e.document_id=NEW.document_id AND e.recycle_entry_id=NEW.recycle_entry_id "
            "AND e.sequence=NEW.sequence-1 AND e.event_digest=NEW.previous_event_digest) THEN "
            "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='invalid recovery event previous link'; END IF; "
            "END IF; END"
        )
    elif dialect == "postgresql":
        op.execute(
            f"CREATE FUNCTION {EVENT_IMMUTABLE_FUNCTION}() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            "RAISE EXCEPTION 'tenant_document_recovery_events are immutable'; "
            "END; $$"
        )
        for operation in ("UPDATE", "DELETE"):
            name = f"trg_tenant_document_recovery_events_no_{operation.casefold()}"
            op.execute(
                f"CREATE TRIGGER {name} BEFORE {operation} ON tenant_document_recovery_events "
                "FOR EACH ROW EXECUTE FUNCTION rag4c_content_recovery_event_immutable()"
            )
        op.execute(
            f"CREATE FUNCTION {EVENT_VALIDATE_FUNCTION}() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            "IF NOT EXISTS (SELECT 1 FROM tenant_document_recycle_entries r "
            "WHERE r.tenant_id=NEW.tenant_id AND r.dataset_id=NEW.dataset_id "
            "AND r.document_id=NEW.document_id AND r.id=NEW.recycle_entry_id) THEN "
            "RAISE EXCEPTION 'recovery event recycle entry required'; END IF; "
            "IF NEW.sequence=1 THEN "
            "IF NEW.event_type<>'recycled' OR NEW.previous_event_digest IS NOT NULL "
            "OR EXISTS (SELECT 1 FROM tenant_document_recovery_events e "
            "WHERE e.tenant_id=NEW.tenant_id AND e.dataset_id=NEW.dataset_id "
            "AND e.document_id=NEW.document_id AND e.recycle_entry_id=NEW.recycle_entry_id) THEN "
            "RAISE EXCEPTION 'invalid recovery event first link'; END IF; "
            "ELSIF NEW.sequence>1 THEN "
            "IF NOT EXISTS (SELECT 1 FROM tenant_document_recovery_events e "
            "WHERE e.tenant_id=NEW.tenant_id AND e.dataset_id=NEW.dataset_id "
            "AND e.document_id=NEW.document_id AND e.recycle_entry_id=NEW.recycle_entry_id "
            "AND e.sequence=NEW.sequence-1 AND e.event_digest=NEW.previous_event_digest) THEN "
            "RAISE EXCEPTION 'invalid recovery event previous link'; END IF; "
            "END IF; RETURN NEW; END; $$"
        )
        op.execute(
            f"CREATE TRIGGER {EVENT_INSERT_TRIGGER} BEFORE INSERT ON tenant_document_recovery_events "
            "FOR EACH ROW EXECUTE FUNCTION rag4c_content_recovery_event_validate()"
        )


def _drop_guards() -> None:
    dialect = _dialect_name()
    if dialect == "postgresql":
        op.execute(
            f"DROP TRIGGER IF EXISTS {EVENT_INSERT_TRIGGER} ON tenant_document_recovery_events"
        )
    else:
        op.execute(f"DROP TRIGGER IF EXISTS {EVENT_INSERT_TRIGGER}")
    for operation in ("update", "delete"):
        name = f"trg_tenant_document_recovery_events_no_{operation}"
        if dialect == "postgresql":
            op.execute(f"DROP TRIGGER IF EXISTS {name} ON tenant_document_recovery_events")
        else:
            op.execute(f"DROP TRIGGER IF EXISTS {name}")
    if dialect == "postgresql":
        op.execute(f"DROP FUNCTION IF EXISTS {EVENT_VALIDATE_FUNCTION}()")
        op.execute(f"DROP FUNCTION IF EXISTS {EVENT_IMMUTABLE_FUNCTION}()")


def _guard_downgrade() -> None:
    connection = op.get_bind()
    counts = {
        table: int(connection.scalar(text(f"SELECT COUNT(*) FROM {table}")) or 0)
        for table in TABLES
    }
    recycled_documents = int(
        connection.scalar(text("SELECT COUNT(*) FROM documents WHERE lifecycle_state='recycled'"))
        or 0
    )
    if any(counts.values()) or recycled_documents:
        detail = ", ".join(f"{table}={count}" for table, count in counts.items() if count)
        if recycled_documents:
            detail = f"{detail}, documents.recycled={recycled_documents}".lstrip(", ")
        raise RuntimeError(f"0033 downgrade blocked by Content Recovery authority: {detail}")


def upgrade() -> None:
    if context.is_offline_mode() and _dialect_name() == "sqlite":
        raise RuntimeError("0033 SQLite offline upgrade is unsupported; use online migration")

    _replace_document_lifecycle_check(DOCUMENT_LIFECYCLE_STATES_0033)
    _replace_approval_checks(APPROVAL_ACTION_TYPES_0033)

    op.create_table(
        "tenant_content_retention_policies",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'active'")),
        sa.Column("retention_days", sa.Integer(), nullable=False, server_default=sa.text("30")),
        sa.Column("auto_purge_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "purge_requires_approval", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("updated_by", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "status IN ('active','paused')", name="ck_tenant_content_retention_policies_status"
        ),
        sa.CheckConstraint(
            "retention_days BETWEEN 1 AND 3650",
            name="ck_tenant_content_retention_policies_retention_days",
        ),
        sa.CheckConstraint(
            "auto_purge_enabled IN (false,true) AND purge_requires_approval IN (false,true)",
            name="ck_tenant_content_retention_policies_boolean_flags",
        ),
        sa.CheckConstraint(
            "revision > 0", name="ck_tenant_content_retention_policies_revision_positive"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_content_retention_policies_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_content_retention_policies_creator",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_content_retention_policies_updater",
        ),
        sa.UniqueConstraint("tenant_id", name="uq_tenant_content_retention_policies_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_tenant_content_retention_policies_scope_id"
        ),
    )
    op.create_index(
        "ix_tenant_content_retention_policies_tenant_status",
        "tenant_content_retention_policies",
        ["tenant_id", "status", "updated_at", "id"],
    )

    op.create_table(
        "tenant_document_recycle_entries",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("dataset_id", sa.String(64), nullable=False),
        sa.Column("document_id", sa.String(64), nullable=False),
        sa.Column("recycle_generation", sa.BigInteger(), nullable=False),
        sa.Column("active_recycle_key", sa.String(160), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default=sa.text("'recycled'")),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("document_mutation_generation", sa.BigInteger(), nullable=False),
        sa.Column("original_lifecycle_state", sa.String(16), nullable=False),
        sa.Column("original_retrieval_enabled", sa.Boolean(), nullable=False),
        sa.Column("retention_days_snapshot", sa.Integer(), nullable=False),
        sa.Column("recycled_at", _datetime6(), nullable=False),
        sa.Column("recycled_by", sa.String(64), nullable=False),
        sa.Column("purge_eligible_at", _datetime6(), nullable=False),
        sa.Column("restored_at", _datetime6(), nullable=True),
        sa.Column("restored_by", sa.String(64), nullable=True),
        sa.Column("purge_requested_at", _datetime6(), nullable=True),
        sa.Column("purged_at", _datetime6(), nullable=True),
        sa.Column("purged_by", sa.String(64), nullable=True),
        sa.Column("safe_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("snapshot_digest", sa.String(64), nullable=False),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.CheckConstraint(
            "status IN ('recycled','restoring','restored','purge_requested','purged','failed')",
            name="ck_tenant_document_recycle_entries_status",
        ),
        sa.CheckConstraint(
            "recycle_generation > 0", name="ck_tenant_document_recycle_entries_generation"
        ),
        sa.CheckConstraint(
            "revision > 0", name="ck_tenant_document_recycle_entries_revision_positive"
        ),
        sa.CheckConstraint(
            "document_mutation_generation >= 0",
            name="ck_tenant_document_recycle_entries_document_generation",
        ),
        sa.CheckConstraint(
            "original_lifecycle_state IN ('active','expired')",
            name="ck_tenant_document_recycle_entries_original_lifecycle",
        ),
        sa.CheckConstraint(
            "retention_days_snapshot BETWEEN 1 AND 3650",
            name="ck_tenant_document_recycle_entries_retention_snapshot",
        ),
        sa.CheckConstraint(
            "length(snapshot_digest)=64 AND lower(snapshot_digest)=snapshot_digest",
            name="ck_tenant_document_recycle_entries_snapshot_digest",
        ),
        sa.CheckConstraint(
            _canonical_recycle_key_check(),
            name="ck_tenant_document_recycle_entries_active_key",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_document_recycle_entries_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_tenant_document_recycle_entries_scope_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "document_id"],
            ["documents.tenant_id", "documents.dataset_id", "documents.id"],
            name="fk_tenant_document_recycle_entries_scope_document",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "recycled_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_document_recycle_entries_recycler",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "restored_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_document_recycle_entries_restorer",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "purged_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_document_recycle_entries_purger",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tenant_document_recycle_entries_scope_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "document_id",
            "recycle_generation",
            name="uq_tenant_document_recycle_entries_generation",
        ),
        sa.UniqueConstraint(
            "tenant_id", "active_recycle_key", name="uq_tenant_document_recycle_entries_active_key"
        ),
        sa.UniqueConstraint(
            "tenant_id", "dataset_id", "id", name="uq_tenant_document_recycle_entries_scope_entry"
        ),
    )
    op.create_index(
        "ix_tenant_document_recycle_entries_tenant_status_eligible",
        "tenant_document_recycle_entries",
        ["tenant_id", "status", "purge_eligible_at", "id"],
    )
    op.create_index(
        "ix_tenant_document_recycle_entries_scope_document",
        "tenant_document_recycle_entries",
        ["tenant_id", "dataset_id", "document_id", "recycle_generation", "id"],
    )

    op.create_table(
        "tenant_document_legal_holds",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("dataset_id", sa.String(64), nullable=False),
        sa.Column("document_id", sa.String(64), nullable=False),
        sa.Column("recycle_entry_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'active'")),
        sa.Column("active_hold_key", sa.String(192), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("safe_reason", sa.String(500), nullable=False),
        sa.Column("held_at", _datetime6(), nullable=False),
        sa.Column("held_by", sa.String(64), nullable=False),
        sa.Column("released_at", _datetime6(), nullable=True),
        sa.Column("released_by", sa.String(64), nullable=True),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.CheckConstraint(
            "status IN ('active','released')", name="ck_tenant_document_legal_holds_status"
        ),
        sa.CheckConstraint("revision > 0", name="ck_tenant_document_legal_holds_revision_positive"),
        sa.CheckConstraint(
            "length(reason_code) BETWEEN 1 AND 64",
            name="ck_tenant_document_legal_holds_reason_code",
        ),
        sa.CheckConstraint(
            "length(safe_reason) BETWEEN 1 AND 500",
            name="ck_tenant_document_legal_holds_safe_reason",
        ),
        sa.CheckConstraint(
            _canonical_hold_key_check(), name="ck_tenant_document_legal_holds_active_key"
        ),
        sa.CheckConstraint(
            "status <> 'released' OR (released_at IS NOT NULL AND released_by IS NOT NULL)",
            name="ck_tenant_document_legal_holds_release_evidence",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_document_legal_holds_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_tenant_document_legal_holds_scope_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "document_id"],
            ["documents.tenant_id", "documents.dataset_id", "documents.id"],
            name="fk_tenant_document_legal_holds_scope_document",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "recycle_entry_id"],
            [
                "tenant_document_recycle_entries.tenant_id",
                "tenant_document_recycle_entries.dataset_id",
                "tenant_document_recycle_entries.id",
            ],
            name="fk_tenant_document_legal_holds_scope_entry",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "held_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_document_legal_holds_holder",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "released_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_document_legal_holds_releaser",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tenant_document_legal_holds_scope_id"),
        sa.UniqueConstraint(
            "tenant_id", "active_hold_key", name="uq_tenant_document_legal_holds_active_key"
        ),
    )
    op.create_index(
        "ix_tenant_document_legal_holds_tenant_entry_status",
        "tenant_document_legal_holds",
        ["tenant_id", "dataset_id", "recycle_entry_id", "status", "id"],
    )

    op.create_table(
        "tenant_document_purge_requests",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("dataset_id", sa.String(64), nullable=False),
        sa.Column("document_id", sa.String(64), nullable=False),
        sa.Column("recycle_entry_id", sa.String(64), nullable=False),
        sa.Column(
            "status", sa.String(24), nullable=False, server_default=sa.text("'pending_approval'")
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("expected_entry_revision", sa.Integer(), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("idempotency_key_digest", sa.String(64), nullable=False),
        sa.Column("approval_request_id", sa.String(64), nullable=True),
        sa.Column("retention_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("legal_hold_count_snapshot", sa.Integer(), nullable=False),
        sa.Column("requested_at", _datetime6(), nullable=False),
        sa.Column("requested_by", sa.String(64), nullable=False),
        sa.Column("approved_at", _datetime6(), nullable=True),
        sa.Column("cancelled_at", _datetime6(), nullable=True),
        sa.Column("cancelled_by", sa.String(64), nullable=True),
        sa.Column("expires_at", _datetime6(), nullable=False),
        sa.Column("executed_at", _datetime6(), nullable=True),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.CheckConstraint(
            "status IN ('pending_approval','approved','cancelled','expired','executed')",
            name="ck_tenant_document_purge_requests_status",
        ),
        sa.CheckConstraint(
            "revision > 0", name="ck_tenant_document_purge_requests_revision_positive"
        ),
        sa.CheckConstraint(
            "expected_entry_revision > 0",
            name="ck_tenant_document_purge_requests_expected_entry_revision",
        ),
        sa.CheckConstraint(
            "length(request_digest)=64 AND lower(request_digest)=request_digest",
            name="ck_tenant_document_purge_requests_request_digest",
        ),
        sa.CheckConstraint(
            "length(idempotency_key_digest)=64 AND lower(idempotency_key_digest)=idempotency_key_digest",
            name="ck_tenant_document_purge_requests_idempotency_digest",
        ),
        sa.CheckConstraint(
            "legal_hold_count_snapshot >= 0",
            name="ck_tenant_document_purge_requests_hold_count",
        ),
        sa.CheckConstraint(
            "status <> 'approved' OR approved_at IS NOT NULL",
            name="ck_tenant_document_purge_requests_approved_evidence",
        ),
        sa.CheckConstraint(
            "status <> 'cancelled' OR (cancelled_at IS NOT NULL AND cancelled_by IS NOT NULL)",
            name="ck_tenant_document_purge_requests_cancelled_evidence",
        ),
        sa.CheckConstraint(
            "status <> 'executed' OR executed_at IS NOT NULL",
            name="ck_tenant_document_purge_requests_executed_evidence",
        ),
        sa.CheckConstraint(
            "approval_request_id IS NOT NULL",
            name="ck_tenant_document_purge_requests_approval_reference",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_document_purge_requests_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_tenant_document_purge_requests_scope_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "document_id"],
            ["documents.tenant_id", "documents.dataset_id", "documents.id"],
            name="fk_tenant_document_purge_requests_scope_document",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "recycle_entry_id"],
            [
                "tenant_document_recycle_entries.tenant_id",
                "tenant_document_recycle_entries.dataset_id",
                "tenant_document_recycle_entries.id",
            ],
            name="fk_tenant_document_purge_requests_scope_entry",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "approval_request_id"],
            ["tenant_approval_requests.tenant_id", "tenant_approval_requests.id"],
            name="fk_tenant_document_purge_requests_approval",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_document_purge_requests_requester",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "cancelled_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_document_purge_requests_canceller",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tenant_document_purge_requests_scope_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "requested_by",
            "idempotency_key_digest",
            name="uq_tenant_document_purge_requests_requester_key",
        ),
    )
    op.create_index(
        "ix_tenant_document_purge_requests_tenant_entry_status",
        "tenant_document_purge_requests",
        ["tenant_id", "dataset_id", "recycle_entry_id", "status", "created_at", "id"],
    )
    op.create_index(
        "ix_tenant_document_purge_requests_tenant_status_expiry",
        "tenant_document_purge_requests",
        ["tenant_id", "status", "expires_at", "id"],
    )

    op.create_table(
        "tenant_document_recovery_events",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("dataset_id", sa.String(64), nullable=False),
        sa.Column("document_id", sa.String(64), nullable=False),
        sa.Column("recycle_entry_id", sa.String(64), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(24), nullable=False),
        sa.Column("previous_event_digest", sa.String(64), nullable=True),
        sa.Column("event_digest", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("safe_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("occurred_at", _datetime6(), nullable=False),
        sa.CheckConstraint("sequence > 0", name="ck_tenant_document_recovery_events_sequence"),
        sa.CheckConstraint(
            "event_type IN ('recycled','restored','hold_applied','hold_released','purge_requested','purge_approved','purge_cancelled')",
            name="ck_tenant_document_recovery_events_type",
        ),
        sa.CheckConstraint(
            "length(event_digest)=64 AND lower(event_digest)=event_digest AND "
            "(previous_event_digest IS NULL OR (length(previous_event_digest)=64 AND lower(previous_event_digest)=previous_event_digest))",
            name="ck_tenant_document_recovery_events_digests",
        ),
        sa.CheckConstraint(
            "(sequence=1 AND previous_event_digest IS NULL) OR "
            "(sequence>1 AND previous_event_digest IS NOT NULL)",
            name="ck_tenant_document_recovery_events_hash_chain",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_document_recovery_events_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_tenant_document_recovery_events_scope_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "document_id"],
            ["documents.tenant_id", "documents.dataset_id", "documents.id"],
            name="fk_tenant_document_recovery_events_scope_document",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "recycle_entry_id"],
            [
                "tenant_document_recycle_entries.tenant_id",
                "tenant_document_recycle_entries.dataset_id",
                "tenant_document_recycle_entries.id",
            ],
            name="fk_tenant_document_recovery_events_scope_entry",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "actor_id"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_document_recovery_events_actor",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tenant_document_recovery_events_scope_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "document_id",
            "recycle_entry_id",
            "sequence",
            name="uq_tenant_document_recovery_events_stream_sequence",
        ),
    )
    op.create_index(
        "ix_tenant_document_recovery_events_scope_stream",
        "tenant_document_recovery_events",
        ["tenant_id", "dataset_id", "document_id", "recycle_entry_id", "sequence", "id"],
    )
    op.create_index(
        "ix_tenant_document_recovery_events_scope_time",
        "tenant_document_recovery_events",
        ["tenant_id", "dataset_id", "occurred_at", "id"],
    )

    _create_guards()


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("0033 downgrade requires online preflight")
    _guard_downgrade()
    _drop_guards()
    op.drop_index(
        "ix_tenant_document_recovery_events_scope_time",
        table_name="tenant_document_recovery_events",
    )
    op.drop_index(
        "ix_tenant_document_recovery_events_scope_stream",
        table_name="tenant_document_recovery_events",
    )
    op.drop_table("tenant_document_recovery_events")
    op.drop_index(
        "ix_tenant_document_purge_requests_tenant_status_expiry",
        table_name="tenant_document_purge_requests",
    )
    op.drop_index(
        "ix_tenant_document_purge_requests_tenant_entry_status",
        table_name="tenant_document_purge_requests",
    )
    op.drop_table("tenant_document_purge_requests")
    op.drop_index(
        "ix_tenant_document_legal_holds_tenant_entry_status",
        table_name="tenant_document_legal_holds",
    )
    op.drop_table("tenant_document_legal_holds")
    op.drop_index(
        "ix_tenant_document_recycle_entries_scope_document",
        table_name="tenant_document_recycle_entries",
    )
    op.drop_index(
        "ix_tenant_document_recycle_entries_tenant_status_eligible",
        table_name="tenant_document_recycle_entries",
    )
    op.drop_table("tenant_document_recycle_entries")
    op.drop_index(
        "ix_tenant_content_retention_policies_tenant_status",
        table_name="tenant_content_retention_policies",
    )
    op.drop_table("tenant_content_retention_policies")
    _replace_approval_checks(APPROVAL_ACTION_TYPES_0032)
    _replace_document_lifecycle_check(DOCUMENT_LIFECYCLE_STATES_0032)
