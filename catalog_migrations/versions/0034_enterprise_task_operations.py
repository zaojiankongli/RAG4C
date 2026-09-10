"""Add Tenant-scoped Enterprise Task Operations authority.

Revision ID: 0034_enterprise_task_operations
Revises: 0033_enterprise_content_recovery
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import mysql

revision: str = "0034_enterprise_task_operations"
down_revision: str | None = "0033_enterprise_content_recovery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "tenant_task_projections",
    "tenant_task_operator_actions",
    "tenant_task_events",
    "tenant_task_saved_views",
    "tenant_task_reconciliation_runs",
)
IMMUTABLE_TABLES = ("tenant_task_events",)
EVENT_INSERT_TRIGGER = "trg_tenant_task_events_validate_insert"
EVENT_IMMUTABLE_FUNCTION = "rag4c_task_event_immutable"
EVENT_VALIDATE_FUNCTION = "rag4c_task_event_validate"

TASK_SOURCE_KINDS: tuple[str, ...] = (
    "document_ingest",
    "index_operation",
    "source_sync",
    "document_delete",
    "audit_export",
    "release_quality_scan",
    "release_recertification",
)
TASK_CATEGORIES: tuple[str, ...] = (
    "documents",
    "indexing",
    "sources",
    "compliance",
    "quality",
)
TASK_STATUSES: tuple[str, ...] = (
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "blocked",
    "unavailable",
)
TASK_ACTION_TYPES: tuple[str, ...] = ("retry", "cancel", "acknowledge")
TASK_ACTION_STATUSES: tuple[str, ...] = (
    "requested",
    "dispatched",
    "applied",
    "rejected",
    "expired",
)
TASK_EVENT_TYPES: tuple[str, ...] = (
    "materialized",
    "status_changed",
    "source_stale",
    "action_requested",
    "action_applied",
    "action_rejected",
    "attention_acknowledged",
)
TASK_VIEW_STATUSES: tuple[str, ...] = ("active", "archived")
TASK_RECONCILIATION_STATUSES: tuple[str, ...] = ("running", "completed", "failed")
TASK_ROUTE_CODES: tuple[str, ...] = TASK_CATEGORIES


def _datetime6():
    return (
        sa.DateTime()
        .with_variant(mysql.DATETIME(fsp=6), "mysql")
        .with_variant(mysql.DATETIME(fsp=6), "mariadb")
    )


def _dialect_name() -> str:
    return str(op.get_context().dialect.name).casefold()


def _quoted_values(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


def _in_check(column_name: str, values: tuple[str, ...]) -> str:
    return f"{column_name} IN ({_quoted_values(values)})"


def _task_saved_view_active_key_check():
    status = sa.column("status", sa.String(16))
    account_id = sa.column("account_id", sa.String(64))
    normalized_name = sa.column("normalized_name", sa.String(128))
    active_key = sa.column("active_view_key", sa.String(192))
    return sa.or_(
        sa.and_(status == "archived", active_key.is_(None)),
        sa.and_(
            status == "active",
            active_key == account_id + sa.literal(":") + normalized_name,
        ),
    )


def _task_operator_action_lifecycle_check():
    status = sa.column("status", sa.String(24))
    dispatched_at = sa.column("dispatched_at", _datetime6())
    applied_at = sa.column("applied_at", _datetime6())
    rejected_at = sa.column("rejected_at", _datetime6())
    expires_at = sa.column("expires_at", _datetime6())
    return sa.or_(
        sa.and_(
            status == "requested",
            dispatched_at.is_(None),
            applied_at.is_(None),
            rejected_at.is_(None),
        ),
        sa.and_(
            status == "dispatched",
            dispatched_at.is_not(None),
            applied_at.is_(None),
            rejected_at.is_(None),
        ),
        sa.and_(status == "applied", applied_at.is_not(None), rejected_at.is_(None)),
        sa.and_(status == "rejected", rejected_at.is_not(None), applied_at.is_(None)),
        sa.and_(status == "expired", expires_at.is_not(None), applied_at.is_(None)),
    )


def _task_saved_view_lifecycle_check():
    status = sa.column("status", sa.String(16))
    active_key = sa.column("active_view_key", sa.String(192))
    archived_at = sa.column("archived_at", _datetime6())
    archived_by = sa.column("archived_by", sa.String(64))
    return sa.or_(
        sa.and_(
            status == "active",
            active_key.is_not(None),
            archived_at.is_(None),
            archived_by.is_(None),
        ),
        sa.and_(
            status == "archived",
            active_key.is_(None),
            archived_at.is_not(None),
            archived_by.is_not(None),
        ),
    )


def _task_reconciliation_lifecycle_state_check():
    status = sa.column("status", sa.String(16))
    completed_at = sa.column("completed_at", _datetime6())
    return sa.or_(
        sa.and_(status == "running", completed_at.is_(None)),
        sa.and_(status.in_(("completed", "failed")), completed_at.is_not(None)),
    )


def _task_reconciliation_error_check():
    safe_error_code = sa.column("safe_error_code", sa.String(64))
    safe_error = sa.column("safe_error", sa.String(512))
    return sa.or_(
        sa.and_(safe_error_code.is_(None), safe_error.is_(None)),
        sa.and_(safe_error_code.is_not(None), safe_error.is_not(None)),
    )


def _create_guards() -> None:
    dialect = _dialect_name()
    if dialect == "sqlite":
        for operation in ("UPDATE", "DELETE"):
            name = f"trg_tenant_task_events_no_{operation.casefold()}"
            op.execute(
                f"CREATE TRIGGER {name} BEFORE {operation} ON tenant_task_events "
                "BEGIN SELECT RAISE(ABORT, 'tenant_task_events are immutable'); END"
            )
        op.execute(
            f"CREATE TRIGGER {EVENT_INSERT_TRIGGER} BEFORE INSERT ON tenant_task_events "
            "BEGIN "
            "SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM tenant_task_projections p "
            "WHERE p.tenant_id=NEW.tenant_id AND p.id=NEW.task_id) "
            "THEN RAISE(ABORT, 'task event projection required') END; "
            "SELECT CASE WHEN NEW.sequence=1 AND (NEW.event_type<>'materialized' "
            "OR NEW.previous_event_digest IS NOT NULL OR EXISTS (SELECT 1 "
            "FROM tenant_task_events e WHERE e.tenant_id=NEW.tenant_id "
            "AND e.task_id=NEW.task_id)) "
            "THEN RAISE(ABORT, 'invalid task event first link') END; "
            "SELECT CASE WHEN NEW.sequence>1 AND NOT EXISTS (SELECT 1 "
            "FROM tenant_task_events e WHERE e.tenant_id=NEW.tenant_id "
            "AND e.task_id=NEW.task_id AND e.sequence=NEW.sequence-1 "
            "AND e.event_digest=NEW.previous_event_digest) "
            "THEN RAISE(ABORT, 'invalid task event previous link') END; "
            "END"
        )
    elif dialect in {"mysql", "mariadb"}:
        for operation in ("UPDATE", "DELETE"):
            name = f"trg_tenant_task_events_no_{operation.casefold()}"
            op.execute(
                f"CREATE TRIGGER {name} BEFORE {operation} ON tenant_task_events "
                "FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
                "'tenant_task_events are immutable'"
            )
        op.execute(
            f"CREATE TRIGGER {EVENT_INSERT_TRIGGER} BEFORE INSERT ON tenant_task_events "
            "FOR EACH ROW BEGIN "
            "IF NOT EXISTS (SELECT 1 FROM tenant_task_projections p "
            "WHERE p.tenant_id=NEW.tenant_id AND p.id=NEW.task_id) THEN "
            "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='task event projection required'; END IF; "
            "IF NEW.sequence=1 THEN "
            "IF NEW.event_type<>'materialized' OR NEW.previous_event_digest IS NOT NULL "
            "OR EXISTS (SELECT 1 FROM tenant_task_events e WHERE e.tenant_id=NEW.tenant_id "
            "AND e.task_id=NEW.task_id) THEN "
            "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='invalid task event first link'; END IF; "
            "ELSEIF NEW.sequence>1 THEN "
            "IF NOT EXISTS (SELECT 1 FROM tenant_task_events e "
            "WHERE e.tenant_id=NEW.tenant_id AND e.task_id=NEW.task_id "
            "AND e.sequence=NEW.sequence-1 AND e.event_digest=NEW.previous_event_digest) THEN "
            "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='invalid task event previous link'; END IF; "
            "END IF; END"
        )
    elif dialect == "postgresql":
        op.execute(
            f"CREATE FUNCTION {EVENT_IMMUTABLE_FUNCTION}() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            "RAISE EXCEPTION 'tenant_task_events are immutable'; "
            "END; $$"
        )
        for operation in ("UPDATE", "DELETE"):
            name = f"trg_tenant_task_events_no_{operation.casefold()}"
            op.execute(
                f"CREATE TRIGGER {name} BEFORE {operation} ON tenant_task_events "
                f"FOR EACH ROW EXECUTE FUNCTION {EVENT_IMMUTABLE_FUNCTION}()"
            )
        op.execute(
            f"CREATE FUNCTION {EVENT_VALIDATE_FUNCTION}() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            "IF NOT EXISTS (SELECT 1 FROM tenant_task_projections p "
            "WHERE p.tenant_id=NEW.tenant_id AND p.id=NEW.task_id) THEN "
            "RAISE EXCEPTION 'task event projection required'; END IF; "
            "IF NEW.sequence=1 THEN "
            "IF NEW.event_type<>'materialized' OR NEW.previous_event_digest IS NOT NULL "
            "OR EXISTS (SELECT 1 FROM tenant_task_events e WHERE e.tenant_id=NEW.tenant_id "
            "AND e.task_id=NEW.task_id) THEN "
            "RAISE EXCEPTION 'invalid task event first link'; END IF; "
            "ELSIF NOT EXISTS (SELECT 1 FROM tenant_task_events e "
            "WHERE e.tenant_id=NEW.tenant_id AND e.task_id=NEW.task_id "
            "AND e.sequence=NEW.sequence-1 AND e.event_digest=NEW.previous_event_digest) THEN "
            "RAISE EXCEPTION 'invalid task event previous link'; END IF; "
            "RETURN NEW; END; $$"
        )
        op.execute(
            f"CREATE TRIGGER {EVENT_INSERT_TRIGGER} BEFORE INSERT ON tenant_task_events "
            f"FOR EACH ROW EXECUTE FUNCTION {EVENT_VALIDATE_FUNCTION}()"
        )


def _drop_guards() -> None:
    dialect = _dialect_name()
    if dialect == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS {EVENT_INSERT_TRIGGER} ON tenant_task_events")
    else:
        op.execute(f"DROP TRIGGER IF EXISTS {EVENT_INSERT_TRIGGER}")
    for operation in ("update", "delete"):
        name = f"trg_tenant_task_events_no_{operation}"
        if dialect == "postgresql":
            op.execute(f"DROP TRIGGER IF EXISTS {name} ON tenant_task_events")
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
    if any(counts.values()):
        detail = ", ".join(f"{table}={count}" for table, count in counts.items() if count)
        raise RuntimeError(f"0034 downgrade blocked by Task Operations authority: {detail}")


def upgrade() -> None:
    _is_mysql = op.get_bind().dialect.name == "mysql"
    if context.is_offline_mode() and _dialect_name() == "sqlite":
        raise RuntimeError("0034 SQLite offline upgrade is unsupported; use online migration")

    op.create_table(
        "tenant_task_projections",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.Column("dataset_id", sa.String(64), nullable=True),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("category", sa.String(24), nullable=False),
        sa.Column(
            "normalized_status",
            sa.String(24),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("action_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("progress_percent", sa.Integer(), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("lease_owner", sa.String(64), nullable=True),
        sa.Column("lease_until", _datetime6(), nullable=True),
        sa.Column("safe_error_code", sa.String(64), nullable=True),
        sa.Column("safe_error", sa.String(512), nullable=True),
        sa.Column("target_route_code", sa.String(24), nullable=True),
        sa.Column("target_route_params_json", sa.JSON(), nullable=True),
        sa.Column("source_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("projection_digest", sa.String(64), nullable=False),
        sa.Column("occurred_at", _datetime6(), nullable=False),
        sa.Column("started_at", _datetime6(), nullable=True),
        sa.Column("finished_at", _datetime6(), nullable=True),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.CheckConstraint(
            _in_check("source_kind", TASK_SOURCE_KINDS),
            name="ck_tenant_task_projections_source_kind",
        ),
        sa.CheckConstraint(
            _in_check("category", TASK_CATEGORIES),
            name="ck_tenant_task_projections_category",
        ),
        sa.CheckConstraint(
            _in_check("normalized_status", TASK_STATUSES),
            name="ck_tenant_task_projections_status",
        ),
        sa.CheckConstraint(
            "source_revision > 0", name="ck_tenant_task_projections_source_revision"
        ),
        sa.CheckConstraint(
            "action_required IN (false,true)", name="ck_tenant_task_projections_action_required"
        ),
        sa.CheckConstraint(
            "progress_percent IS NULL OR progress_percent BETWEEN 0 AND 100",
            name="ck_tenant_task_projections_progress",
        ),
        sa.CheckConstraint(
            "attempt_number >= 0 AND max_attempts > 0 AND attempt_number <= max_attempts",
            name="ck_tenant_task_projections_attempts",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_until IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_until IS NOT NULL)",
            name="ck_tenant_task_projections_lease",
        ),
        sa.CheckConstraint(
            "(safe_error_code IS NULL AND safe_error IS NULL) OR "
            "(safe_error_code IS NOT NULL AND safe_error IS NOT NULL)",
            name="ck_tenant_task_projections_error",
        ),
        sa.CheckConstraint(
            "target_route_code IS NULL OR target_route_code IN ('documents','indexing','sources','compliance','quality')",
            name="ck_tenant_task_projections_route",
        ),
        sa.CheckConstraint(
            "length(source_digest)=64 AND lower(source_digest)=source_digest AND "
            "length(projection_digest)=64 AND lower(projection_digest)=projection_digest",
            name="ck_tenant_task_projections_digest",
        ),
        sa.CheckConstraint(
            "source_current IN (false,true)", name="ck_tenant_task_projections_current"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_task_projections_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_tenant_task_projections_scope_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["tenant_workspaces.tenant_id", "tenant_workspaces.id"],
            name="fk_tenant_task_projections_scope_workspace",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tenant_task_projections_scope_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_kind",
            "source_id",
            name="uq_tenant_task_projections_source",
        ),
    )
    op.create_index(
        "ix_tenant_task_projections_tenant_status_updated",
        "tenant_task_projections",
        ["tenant_id", "normalized_status", "updated_at", "id"],
    )
    op.create_index(
        "ix_tenant_task_projections_tenant_attention_updated",
        "tenant_task_projections",
        ["tenant_id", "action_required", "source_current", "updated_at", "id"],
    )
    op.create_index(
        "ix_tenant_task_projections_source_scope_updated",
        "tenant_task_projections",
        ["tenant_id", "source_kind", "source_current", "updated_at", "id"],
    )

    op.create_table(
        "tenant_task_operator_actions",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("action_type", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default=sa.text("'requested'")),
        sa.Column("expected_source_revision", sa.Integer(), nullable=False),
        sa.Column("expected_source_digest", sa.String(64), nullable=False),
        sa.Column("idempotency_key_digest", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("safe_reason", sa.String(512), nullable=False),
        sa.Column("result_code", sa.String(64), nullable=True),
        sa.Column("requested_at", _datetime6(), nullable=False),
        sa.Column("dispatched_at", _datetime6(), nullable=True),
        sa.Column("applied_at", _datetime6(), nullable=True),
        sa.Column("rejected_at", _datetime6(), nullable=True),
        sa.Column("expires_at", _datetime6(), nullable=True),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.CheckConstraint(
            _in_check("action_type", TASK_ACTION_TYPES),
            name="ck_tenant_task_operator_actions_type",
        ),
        sa.CheckConstraint(
            _in_check("status", TASK_ACTION_STATUSES),
            name="ck_tenant_task_operator_actions_status",
        ),
        sa.CheckConstraint(
            "expected_source_revision > 0",
            name="ck_tenant_task_operator_actions_revision",
        ),
        sa.CheckConstraint(
            "length(expected_source_digest)=64 AND lower(expected_source_digest)=expected_source_digest "
            "AND length(idempotency_key_digest)=64 AND lower(idempotency_key_digest)=idempotency_key_digest",
            name="ck_tenant_task_operator_actions_digest",
        ),
        sa.CheckConstraint(
            "length(safe_reason) BETWEEN 1 AND 512",
            name="ck_tenant_task_operator_actions_reason",
        ),
        sa.CheckConstraint(
            _task_operator_action_lifecycle_check(),
            name="ck_tenant_task_operator_actions_lifecycle",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_task_operator_actions_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["tenant_task_projections.tenant_id", "tenant_task_projections.id"],
            name="fk_tenant_task_operator_actions_task",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "actor_id"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_task_operator_actions_actor",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tenant_task_operator_actions_scope_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "actor_id",
            "idempotency_key_digest",
            name="uq_tenant_task_operator_actions_idempotency",
        ),
    )
    op.create_index(
        "ix_tenant_task_operator_actions_tenant_status_requested",
        "tenant_task_operator_actions",
        ["tenant_id", "status", "requested_at", "id"],
    )
    op.create_index(
        "ix_tenant_task_operator_actions_task_created",
        "tenant_task_operator_actions",
        ["tenant_id", "task_id", "created_at", "id"],
    )

    op.create_table(
        "tenant_task_events",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("previous_event_digest", sa.String(64), nullable=True),
        sa.Column("event_digest", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("safe_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("occurred_at", _datetime6(), nullable=False),
        sa.CheckConstraint("sequence > 0", name="ck_tenant_task_events_sequence"),
        sa.CheckConstraint(
            _in_check("event_type", TASK_EVENT_TYPES), name="ck_tenant_task_events_type"
        ),
        sa.CheckConstraint(
            "length(event_digest)=64 AND lower(event_digest)=event_digest AND "
            "(previous_event_digest IS NULL OR (length(previous_event_digest)=64 AND "
            "lower(previous_event_digest)=previous_event_digest))",
            name="ck_tenant_task_events_digests",
        ),
        sa.CheckConstraint(
            "(sequence=1 AND previous_event_digest IS NULL) OR "
            "(sequence>1 AND previous_event_digest IS NOT NULL)",
            name="ck_tenant_task_events_hash_chain",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_tenant_task_events_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["tenant_task_projections.tenant_id", "tenant_task_projections.id"],
            name="fk_tenant_task_events_task",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "actor_id"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_task_events_actor",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tenant_task_events_scope_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "task_id",
            "sequence",
            name="uq_tenant_task_events_stream_sequence",
        ),
    )
    op.create_index(
        "ix_tenant_task_events_task_sequence",
        "tenant_task_events",
        ["tenant_id", "task_id", "sequence", "id"],
    )
    op.create_index(
        "ix_tenant_task_events_tenant_time",
        "tenant_task_events",
        ["tenant_id", "occurred_at", "id"],
    )

    op.create_table(
        "tenant_task_saved_views",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("normalized_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'active'")),
        sa.Column("active_view_key", sa.String(192), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("filters_json", sa.JSON(), nullable=False),
        sa.Column("filter_digest", sa.String(64), nullable=False),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("updated_by", sa.String(64), nullable=False),
        sa.Column("archived_at", _datetime6(), nullable=True),
        sa.Column("archived_by", sa.String(64), nullable=True),
        sa.CheckConstraint(
            _in_check("status", TASK_VIEW_STATUSES), name="ck_tenant_task_saved_views_status"
        ),
        sa.CheckConstraint("revision > 0", name="ck_tenant_task_saved_views_revision"),
        sa.CheckConstraint(
            "length(name) BETWEEN 1 AND 128 AND length(normalized_name) BETWEEN 1 AND 128",
            name="ck_tenant_task_saved_views_name",
        ),
        sa.CheckConstraint(
            "filters_json IS NOT NULL AND length(filter_digest)=64 AND "
            "lower(filter_digest)=filter_digest",
            name="ck_tenant_task_saved_views_filters",
        ),
        sa.CheckConstraint(
            _task_saved_view_active_key_check(),
            name="ck_tenant_task_saved_views_active_key",
        ),
        sa.CheckConstraint(
            _task_saved_view_lifecycle_check(),
            name="ck_tenant_task_saved_views_lifecycle",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_task_saved_views_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_task_saved_views_member",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_task_saved_views_creator",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_task_saved_views_updater",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "archived_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_task_saved_views_archiver",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tenant_task_saved_views_scope_id"),
        sa.UniqueConstraint(
            "tenant_id", "active_view_key", name="uq_tenant_task_saved_views_active_key"
        ),
    )
    op.create_index(
        "ix_tenant_task_saved_views_account_status",
        "tenant_task_saved_views",
        ["tenant_id", "account_id", "status", "updated_at", "id"],
    )

    op.create_table(
        "tenant_task_reconciliation_runs",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'running'")),
        sa.Column("source_kinds_json", sa.JSON(), nullable=False),
        sa.Column("source_inventory_digest", sa.String(64), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stale_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("invalid_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", _datetime6(), nullable=False),
        sa.Column("completed_at", _datetime6(), nullable=True),
        sa.Column("safe_error_code", sa.String(64), nullable=True),
        sa.Column("safe_error", sa.String(512), nullable=True),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.CheckConstraint(
            _in_check("status", TASK_RECONCILIATION_STATUSES),
            name="ck_tenant_task_reconciliation_runs_status",
        ),
        sa.CheckConstraint(
            "source_kinds_json IS NOT NULL",
            name="ck_tenant_task_reconciliation_runs_source_kinds",
        ),
        sa.CheckConstraint(
            "length(source_inventory_digest)=64 AND lower(source_inventory_digest)=source_inventory_digest",
            name="ck_tenant_task_reconciliation_runs_digest",
        ),
        sa.CheckConstraint(
            "source_count >= 0 AND created_count >= 0 AND updated_count >= 0 AND "
            "stale_count >= 0 AND invalid_count >= 0",
            name="ck_tenant_task_reconciliation_runs_counts",
        ),
        sa.CheckConstraint(
            _task_reconciliation_lifecycle_state_check(),
            name="ck_tenant_task_reconciliation_runs_lifecycle_state",
        ),
        sa.CheckConstraint(
            _task_reconciliation_error_check(),
            name="ck_tenant_task_reconciliation_runs_lifecycle",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_task_reconciliation_runs_tenant"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tenant_task_reconciliation_runs_scope_id"),
    )
    op.create_index(
        "ix_tenant_task_reconciliation_runs_tenant_status_started",
        "tenant_task_reconciliation_runs",
        ["tenant_id", "status", "started_at", "id"],
    )

    _create_guards()


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("0034 downgrade requires online preflight")
    _guard_downgrade()
    _drop_guards()
    op.drop_index(
        "ix_tenant_task_reconciliation_runs_tenant_status_started",
        table_name="tenant_task_reconciliation_runs",
    )
    op.drop_table("tenant_task_reconciliation_runs")
    op.drop_index("ix_tenant_task_saved_views_account_status", table_name="tenant_task_saved_views")
    op.drop_table("tenant_task_saved_views")
    op.drop_index("ix_tenant_task_events_tenant_time", table_name="tenant_task_events")
    op.drop_index("ix_tenant_task_events_task_sequence", table_name="tenant_task_events")
    op.drop_table("tenant_task_events")
    op.drop_index(
        "ix_tenant_task_operator_actions_task_created",
        table_name="tenant_task_operator_actions",
    )
    op.drop_index(
        "ix_tenant_task_operator_actions_tenant_status_requested",
        table_name="tenant_task_operator_actions",
    )
    op.drop_table("tenant_task_operator_actions")
    op.drop_index(
        "ix_tenant_task_projections_source_scope_updated",
        table_name="tenant_task_projections",
    )
    op.drop_index(
        "ix_tenant_task_projections_tenant_attention_updated",
        table_name="tenant_task_projections",
    )
    op.drop_index(
        "ix_tenant_task_projections_tenant_status_updated",
        table_name="tenant_task_projections",
    )
    op.drop_table("tenant_task_projections")
