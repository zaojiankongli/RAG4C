"""Add Tenant-scoped Enterprise Automation & Workflow authority.

Revision ID: 0035_enterprise_automation_workflows
Revises: 0034_enterprise_task_operations
Create Date: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import mysql

revision: str = "0035_enterprise_automation_workflows"
down_revision: str | None = "0034_enterprise_task_operations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "tenant_automation_rules",
    "tenant_automation_rule_revisions",
    "tenant_automation_source_cursors",
    "tenant_automation_runs",
    "tenant_automation_action_requests",
    "tenant_automation_events",
)
TRIGGERS = (
    "task_failed",
    "task_source_stale",
    "source_sync_failed",
    "release_quality_alert_opened",
    "release_recertification_blocked",
    "approval_request_terminal",
)
CONDITIONS = (
    "always",
    "status_is",
    "action_required",
    "severity_at_least",
    "attempt_exhausted",
    "source_is_stale",
)
ACTIONS = ("notify_operator", "request_approval", "open_task_attention", "pause_rule")
EVENT_TYPES = (
    "rule_created",
    "revision_created",
    "revision_activated",
    "rule_paused",
    "trigger_observed",
    "condition_not_matched",
    "run_started",
    "action_requested",
    "action_rejected",
    "run_completed",
    "run_failed",
)
RULE_STATUSES = ("draft", "active", "paused", "archived")
CURSOR_STATUSES = ("idle", "claimed", "blocked")
RUN_STATUSES = ("started", "not_matched", "requested", "completed", "failed", "blocked")
ACTION_STATUSES = ("requested", "dispatched", "applied", "rejected", "expired")
REVISION_TABLE = "tenant_automation_rule_revisions"
EVENT_TABLE = "tenant_automation_events"
EVENT_INSERT_TRIGGER = "trg_tenant_automation_events_validate_insert"
IMMUTABLE_FUNCTION = "rag4c_automation_immutable"
EVENT_VALIDATE_FUNCTION = "rag4c_automation_event_validate"
SUPPORTED_DIALECTS = frozenset({"sqlite", "mysql", "mariadb", "postgresql"})


def _datetime6():
    return (
        sa.DateTime()
        .with_variant(mysql.DATETIME(fsp=6), "mysql")
        .with_variant(mysql.DATETIME(fsp=6), "mariadb")
    )


def _dialect_name() -> str:
    return str(op.get_context().dialect.name).casefold()


def _require_supported_dialect() -> str:
    dialect = _dialect_name()
    if dialect not in SUPPORTED_DIALECTS:
        raise RuntimeError(f"0035 unsupported database dialect: {dialect}")
    return dialect


def _quoted(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({_quoted(values)})"


def _create_immutable_triggers() -> None:
    dialect = _require_supported_dialect()
    if dialect == "sqlite":
        for table in (REVISION_TABLE, EVENT_TABLE):
            for operation in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER trg_{table}_no_{operation.casefold()} "
                    f"BEFORE {operation} ON {table} BEGIN "
                    "SELECT RAISE(ABORT, 'automation immutable authority'); END"
                )
        op.execute(
            f"CREATE TRIGGER {EVENT_INSERT_TRIGGER} BEFORE INSERT ON {EVENT_TABLE} BEGIN "
            "SELECT CASE WHEN NEW.sequence=1 AND NEW.event_type NOT IN ('rule_created','run_started') "
            "THEN RAISE(ABORT,'automation first event invalid') END; "
            "SELECT CASE WHEN NEW.sequence=1 AND NEW.previous_event_digest IS NOT NULL "
            "THEN RAISE(ABORT,'automation first previous digest invalid') END; "
            "SELECT CASE WHEN NEW.sequence>1 AND NEW.previous_event_digest IS NULL "
            "THEN RAISE(ABORT,'automation previous digest required') END; "
            f"SELECT CASE WHEN NEW.sequence>1 AND NOT EXISTS (SELECT 1 FROM {EVENT_TABLE} e "
            "WHERE e.tenant_id=NEW.tenant_id AND e.stream_key=NEW.stream_key "
            "AND e.sequence=NEW.sequence-1 AND e.event_digest=NEW.previous_event_digest) "
            "THEN RAISE(ABORT,'automation event chain invalid') END; END"
        )
        return
    if dialect in {"mysql", "mariadb"}:
        for table in (REVISION_TABLE, EVENT_TABLE):
            for operation in ("UPDATE", "DELETE"):
                name = f"trg_{table}_no_{operation.casefold()}"
                op.execute(
                    f"CREATE TRIGGER {name} BEFORE {operation} ON {table} "
                    "FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='automation immutable authority'"
                )
        op.execute(
            f"CREATE TRIGGER {EVENT_INSERT_TRIGGER} BEFORE INSERT ON {EVENT_TABLE} FOR EACH ROW BEGIN "
            "IF NEW.sequence=1 AND (NEW.event_type NOT IN ('rule_created','run_started') "
            "OR NEW.previous_event_digest IS NOT NULL) THEN SIGNAL SQLSTATE '45000' "
            "SET MESSAGE_TEXT='automation first event invalid'; END IF; "
            "IF NEW.sequence>1 AND (NEW.previous_event_digest IS NULL OR NOT EXISTS "
            f"(SELECT 1 FROM {EVENT_TABLE} e WHERE e.tenant_id=NEW.tenant_id "
            "AND e.stream_key=NEW.stream_key AND e.sequence=NEW.sequence-1 "
            "AND e.event_digest=NEW.previous_event_digest)) THEN SIGNAL SQLSTATE '45000' "
            "SET MESSAGE_TEXT='automation event chain invalid'; END IF; END"
        )
        return
    if dialect == "postgresql":
        op.execute(
            f"CREATE FUNCTION {IMMUTABLE_FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN RAISE EXCEPTION 'automation immutable authority'; END $$"
        )
        for table in (REVISION_TABLE, EVENT_TABLE):
            for operation in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER trg_{table}_no_{operation.casefold()} BEFORE {operation} ON {table} "
                    f"FOR EACH ROW EXECUTE FUNCTION {IMMUTABLE_FUNCTION}()"
                )
        op.execute(
            f"CREATE FUNCTION {EVENT_VALIDATE_FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN IF NEW.sequence=1 THEN IF NEW.event_type NOT IN ('rule_created','run_started') "
            "OR NEW.previous_event_digest IS NOT NULL THEN RAISE EXCEPTION 'automation first event invalid'; "
            "END IF; ELSE IF NEW.previous_event_digest IS NULL OR NOT EXISTS "
            f"(SELECT 1 FROM {EVENT_TABLE} e WHERE e.tenant_id=NEW.tenant_id "
            "AND e.stream_key=NEW.stream_key AND e.sequence=NEW.sequence-1 "
            "AND e.event_digest=NEW.previous_event_digest) THEN RAISE EXCEPTION 'automation event chain invalid'; "
            "END IF; END IF; RETURN NEW; END $$"
        )
        op.execute(
            f"CREATE TRIGGER {EVENT_INSERT_TRIGGER} BEFORE INSERT ON {EVENT_TABLE} "
            f"FOR EACH ROW EXECUTE FUNCTION {EVENT_VALIDATE_FUNCTION}()"
        )


def _drop_triggers() -> None:
    dialect = _require_supported_dialect()
    for table in (REVISION_TABLE, EVENT_TABLE):
        for operation in ("update", "delete"):
            name = f"trg_{table}_no_{operation}"
            if dialect == "postgresql":
                op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
            else:
                op.execute(f"DROP TRIGGER IF EXISTS {name}")
    if dialect == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS {EVENT_INSERT_TRIGGER} ON {EVENT_TABLE}")
        op.execute(f"DROP FUNCTION IF EXISTS {EVENT_VALIDATE_FUNCTION}()")
        op.execute(f"DROP FUNCTION IF EXISTS {IMMUTABLE_FUNCTION}()")
    else:
        op.execute(f"DROP TRIGGER IF EXISTS {EVENT_INSERT_TRIGGER}")


def _guard_downgrade() -> None:
    bind = op.get_bind()
    counts = {
        table: int(bind.scalar(text(f"SELECT COUNT(*) FROM {table}")) or 0) for table in TABLES
    }
    if any(counts.values()):
        detail = ", ".join(f"{table}={count}" for table, count in counts.items() if count)
        raise RuntimeError(f"0035 downgrade blocked by Automation authority: {detail}")


def upgrade() -> None:
    dialect = _require_supported_dialect()
    if context.is_offline_mode() and dialect == "sqlite":
        raise RuntimeError("0035 SQLite offline upgrade is unsupported; use online migration")

    rule_foreign_keys: list[sa.ForeignKeyConstraint] = [
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_automation_rules_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["tenant_workspaces.tenant_id", "tenant_workspaces.id"],
            name="fk_tenant_automation_rules_scope_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_tenant_automation_rules_scope_dataset",
        ),
    ]
    if dialect == "sqlite":
        rule_foreign_keys.append(
            sa.ForeignKeyConstraint(
                ["tenant_id", "current_revision_id"],
                ["tenant_automation_rule_revisions.tenant_id", "tenant_automation_rule_revisions.id"],
                name="fk_tenant_automation_rules_current_revision",
            )
        )

    op.create_table(
        "tenant_automation_rules",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("normalized_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("active_rule_key", sa.String(128), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("current_revision_id", sa.String(64), nullable=True),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("dataset_id", sa.String(64), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("updated_by", sa.String(64), nullable=False),
        sa.Column("archived_at", _datetime6(), nullable=True),
        sa.Column("archived_by", sa.String(64), nullable=True),
        sa.CheckConstraint(_in("status", RULE_STATUSES), name="ck_tenant_automation_rules_status"),
        sa.CheckConstraint("revision > 0", name="ck_tenant_automation_rules_revision"),
        sa.CheckConstraint(
            "priority BETWEEN 0 AND 1000", name="ck_tenant_automation_rules_priority"
        ),
        sa.CheckConstraint(
            "(status='active' AND active_rule_key=normalized_name AND current_revision_id IS NOT NULL) OR "
            "(status<>'active' AND active_rule_key IS NULL)",
            name="ck_tenant_automation_rules_active_identity",
        ),
        sa.CheckConstraint(
            "(status='archived' AND archived_at IS NOT NULL AND archived_by IS NOT NULL) OR "
            "(status<>'archived' AND archived_at IS NULL AND archived_by IS NULL)",
            name="ck_tenant_automation_rules_lifecycle",
        ),
        *rule_foreign_keys,
        sa.UniqueConstraint("tenant_id", "id", name="uq_tenant_automation_rules_scope_id"),
        sa.UniqueConstraint(
            "tenant_id", "active_rule_key", name="uq_tenant_automation_rules_active_key"
        ),
    )
    op.create_index(
        "ix_tenant_automation_rules_tenant_status_updated",
        "tenant_automation_rules",
        ["tenant_id", "status", "updated_at", "id"],
    )

    op.create_table(
        "tenant_automation_rule_revisions",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("rule_id", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("trigger_code", sa.String(48), nullable=False),
        sa.Column("condition_code", sa.String(48), nullable=False),
        sa.Column("condition_params_json", sa.JSON(), nullable=False),
        sa.Column("action_plan_json", sa.JSON(), nullable=False),
        sa.Column("definition_digest", sa.String(64), nullable=False),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_tenant_automation_rule_revisions_revision"),
        sa.CheckConstraint(
            _in("trigger_code", TRIGGERS), name="ck_tenant_automation_rule_revisions_trigger"
        ),
        sa.CheckConstraint(
            _in("condition_code", CONDITIONS), name="ck_tenant_automation_rule_revisions_condition"
        ),
        sa.CheckConstraint(
            "condition_params_json IS NOT NULL AND action_plan_json IS NOT NULL",
            name="ck_tenant_automation_rule_revisions_definition",
        ),
        sa.CheckConstraint(
            "length(definition_digest)=64 AND lower(definition_digest)=definition_digest",
            name="ck_tenant_automation_rule_revisions_digest",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_automation_rule_revisions_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "rule_id"],
            ["tenant_automation_rules.tenant_id", "tenant_automation_rules.id"],
            name="fk_tenant_automation_rule_revisions_rule",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tenant_automation_rule_revisions_scope_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "rule_id",
            "revision",
            name="uq_tenant_automation_rule_revisions_rule_revision",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "rule_id",
            "definition_digest",
            name="uq_tenant_automation_rule_revisions_definition",
        ),
    )
    op.create_index(
        "ix_tenant_automation_rule_revisions_rule_created",
        "tenant_automation_rule_revisions",
        ["tenant_id", "rule_id", "created_at", "id"],
    )
    if dialect != "sqlite":
        op.create_foreign_key(
            "fk_tenant_automation_rules_current_revision",
            "tenant_automation_rules",
            "tenant_automation_rule_revisions",
            ["tenant_id", "current_revision_id"],
            ["tenant_id", "id"],
        )

    op.create_table(
        "tenant_automation_source_cursors",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("rule_id", sa.String(64), nullable=False),
        sa.Column("source_kind", sa.String(48), nullable=False),
        sa.Column("source_stream_id", sa.String(128), nullable=False),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_event_digest", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'idle'")),
        sa.Column("lease_owner", sa.String(64), nullable=True),
        sa.Column("lease_until", _datetime6(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.CheckConstraint(
            _in("source_kind", TRIGGERS), name="ck_tenant_automation_source_cursors_source_kind"
        ),
        sa.CheckConstraint(
            _in("status", CURSOR_STATUSES), name="ck_tenant_automation_source_cursors_status"
        ),
        sa.CheckConstraint(
            "last_sequence >= 0 AND revision > 0",
            name="ck_tenant_automation_source_cursors_revision",
        ),
        sa.CheckConstraint(
            "(last_sequence=0 AND last_event_digest IS NULL) OR (last_sequence>0 AND length(last_event_digest)=64 AND lower(last_event_digest)=last_event_digest)",
            name="ck_tenant_automation_source_cursors_digest",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_until IS NULL) OR (lease_owner IS NOT NULL AND lease_until IS NOT NULL)",
            name="ck_tenant_automation_source_cursors_lease",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_automation_source_cursors_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "rule_id"],
            ["tenant_automation_rules.tenant_id", "tenant_automation_rules.id"],
            name="fk_tenant_automation_source_cursors_rule",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tenant_automation_source_cursors_scope_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "rule_id",
            "source_kind",
            "source_stream_id",
            name="uq_tenant_automation_source_cursors_stream",
        ),
    )
    op.create_index(
        "ix_tenant_automation_source_cursors_tenant_status_updated",
        "tenant_automation_source_cursors",
        ["tenant_id", "status", "updated_at", "id"],
    )

    op.create_table(
        "tenant_automation_runs",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("rule_id", sa.String(64), nullable=False),
        sa.Column("rule_revision_id", sa.String(64), nullable=False),
        sa.Column("trigger_event_id", sa.String(128), nullable=False),
        sa.Column("trigger_event_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default=sa.text("'started'")),
        sa.Column("condition_matched", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("action_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requested_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("idempotency_digest", sa.String(64), nullable=False),
        sa.Column("started_at", _datetime6(), nullable=False),
        sa.Column("completed_at", _datetime6(), nullable=True),
        sa.Column("safe_error_code", sa.String(64), nullable=True),
        sa.Column("safe_error", sa.String(512), nullable=True),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.CheckConstraint(_in("status", RUN_STATUSES), name="ck_tenant_automation_runs_status"),
        sa.CheckConstraint(
            "condition_matched IN (false,true)", name="ck_tenant_automation_runs_condition"
        ),
        sa.CheckConstraint(
            "action_count >= 0 AND requested_count >= 0 AND rejected_count >= 0 AND requested_count + rejected_count <= action_count",
            name="ck_tenant_automation_runs_counts",
        ),
        sa.CheckConstraint(
            "length(trigger_event_digest)=64 AND lower(trigger_event_digest)=trigger_event_digest AND length(idempotency_digest)=64 AND lower(idempotency_digest)=idempotency_digest",
            name="ck_tenant_automation_runs_digest",
        ),
        sa.CheckConstraint(
            "(status='started' AND completed_at IS NULL) OR (status<>'started' AND completed_at IS NOT NULL)",
            name="ck_tenant_automation_runs_lifecycle",
        ),
        sa.CheckConstraint(
            "(safe_error_code IS NULL AND safe_error IS NULL) OR (safe_error_code IS NOT NULL AND safe_error IS NOT NULL)",
            name="ck_tenant_automation_runs_error",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_automation_runs_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "rule_id"],
            ["tenant_automation_rules.tenant_id", "tenant_automation_rules.id"],
            name="fk_tenant_automation_runs_rule",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "rule_revision_id"],
            ["tenant_automation_rule_revisions.tenant_id", "tenant_automation_rule_revisions.id"],
            name="fk_tenant_automation_runs_revision",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tenant_automation_runs_scope_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "rule_id",
            "trigger_event_digest",
            name="uq_tenant_automation_runs_trigger_replay",
        ),
    )
    op.create_index(
        "ix_tenant_automation_runs_tenant_status_started",
        "tenant_automation_runs",
        ["tenant_id", "status", "started_at", "id"],
    )

    op.create_table(
        "tenant_automation_action_requests",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("rule_id", sa.String(64), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("action_code", sa.String(48), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default=sa.text("'requested'")),
        sa.Column("target_kind", sa.String(48), nullable=True),
        sa.Column("target_id", sa.String(128), nullable=True),
        sa.Column("target_revision", sa.Integer(), nullable=True),
        sa.Column("target_digest", sa.String(64), nullable=True),
        sa.Column("idempotency_key_digest", sa.String(64), nullable=False),
        sa.Column("safe_params_json", sa.JSON(), nullable=False),
        sa.Column("safe_reason", sa.String(512), nullable=False),
        sa.Column("approval_request_id", sa.String(64), nullable=True),
        sa.Column("notification_id", sa.String(64), nullable=True),
        sa.Column("task_id", sa.String(64), nullable=True),
        sa.Column("requested_at", _datetime6(), nullable=False),
        sa.Column("dispatched_at", _datetime6(), nullable=True),
        sa.Column("applied_at", _datetime6(), nullable=True),
        sa.Column("rejected_at", _datetime6(), nullable=True),
        sa.Column("expires_at", _datetime6(), nullable=False),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.CheckConstraint(
            _in("action_code", ACTIONS), name="ck_tenant_automation_action_requests_action"
        ),
        sa.CheckConstraint(
            _in("status", ACTION_STATUSES), name="ck_tenant_automation_action_requests_status"
        ),
        sa.CheckConstraint("step_index >= 0", name="ck_tenant_automation_action_requests_step"),
        sa.CheckConstraint(
            "safe_params_json IS NOT NULL", name="ck_tenant_automation_action_requests_params"
        ),
        sa.CheckConstraint(
            "length(idempotency_key_digest)=64 AND lower(idempotency_key_digest)=idempotency_key_digest",
            name="ck_tenant_automation_action_requests_idempotency",
        ),
        sa.CheckConstraint(
            "(target_revision IS NULL AND target_digest IS NULL) OR (target_revision > 0 AND length(target_digest)=64 AND lower(target_digest)=target_digest)",
            name="ck_tenant_automation_action_requests_target_fence",
        ),
        sa.CheckConstraint(
            "(status='requested' AND dispatched_at IS NULL AND applied_at IS NULL AND rejected_at IS NULL) OR (status='dispatched' AND dispatched_at IS NOT NULL AND applied_at IS NULL AND rejected_at IS NULL) OR (status='applied' AND applied_at IS NOT NULL AND rejected_at IS NULL) OR (status='rejected' AND rejected_at IS NOT NULL AND applied_at IS NULL) OR (status='expired' AND applied_at IS NULL)",
            name="ck_tenant_automation_action_requests_lifecycle",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_automation_action_requests_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["tenant_automation_runs.tenant_id", "tenant_automation_runs.id"],
            name="fk_tenant_automation_action_requests_run",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "rule_id"],
            ["tenant_automation_rules.tenant_id", "tenant_automation_rules.id"],
            name="fk_tenant_automation_action_requests_rule",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "approval_request_id"],
            ["tenant_approval_requests.tenant_id", "tenant_approval_requests.id"],
            name="fk_tenant_automation_action_requests_approval",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "notification_id"],
            ["tenant_notifications.tenant_id", "tenant_notifications.id"],
            name="fk_tenant_automation_action_requests_notification",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["tenant_task_projections.tenant_id", "tenant_task_projections.id"],
            name="fk_tenant_automation_action_requests_task",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_tenant_automation_action_requests_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "step_index",
            name="uq_tenant_automation_action_requests_run_step",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key_digest",
            name="uq_tenant_automation_action_requests_idempotency",
        ),
    )
    op.create_index(
        "ix_tenant_automation_action_requests_tenant_status_requested",
        "tenant_automation_action_requests",
        ["tenant_id", "status", "requested_at", "id"],
    )

    op.create_table(
        "tenant_automation_events",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("rule_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("stream_key", sa.String(128), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column("previous_event_digest", sa.String(64), nullable=True),
        sa.Column("event_digest", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("safe_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("occurred_at", _datetime6(), nullable=False),
        sa.CheckConstraint(_in("event_type", EVENT_TYPES), name="ck_tenant_automation_events_type"),
        sa.CheckConstraint("sequence > 0", name="ck_tenant_automation_events_sequence"),
        sa.CheckConstraint(
            "length(event_digest)=64 AND lower(event_digest)=event_digest AND (previous_event_digest IS NULL OR (length(previous_event_digest)=64 AND lower(previous_event_digest)=previous_event_digest))",
            name="ck_tenant_automation_events_digest",
        ),
        sa.CheckConstraint(
            "(sequence=1 AND previous_event_digest IS NULL) OR (sequence>1 AND previous_event_digest IS NOT NULL)",
            name="ck_tenant_automation_events_hash_chain",
        ),
        sa.CheckConstraint(
            "safe_snapshot_json IS NOT NULL", name="ck_tenant_automation_events_snapshot"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_automation_events_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "rule_id"],
            ["tenant_automation_rules.tenant_id", "tenant_automation_rules.id"],
            name="fk_tenant_automation_events_rule",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["tenant_automation_runs.tenant_id", "tenant_automation_runs.id"],
            name="fk_tenant_automation_events_run",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tenant_automation_events_scope_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "stream_key",
            "sequence",
            name="uq_tenant_automation_events_stream_sequence",
        ),
    )
    op.create_index(
        "ix_tenant_automation_events_tenant_time",
        "tenant_automation_events",
        ["tenant_id", "occurred_at", "id"],
    )
    _create_immutable_triggers()


def downgrade() -> None:
    dialect = _require_supported_dialect()
    if context.is_offline_mode():
        raise RuntimeError("0035 downgrade requires online preflight")
    _guard_downgrade()
    _drop_triggers()
    for name, table in (
        ("ix_tenant_automation_events_tenant_time", "tenant_automation_events"),
        (
            "ix_tenant_automation_action_requests_tenant_status_requested",
            "tenant_automation_action_requests",
        ),
        ("ix_tenant_automation_runs_tenant_status_started", "tenant_automation_runs"),
        (
            "ix_tenant_automation_source_cursors_tenant_status_updated",
            "tenant_automation_source_cursors",
        ),
        ("ix_tenant_automation_rule_revisions_rule_created", "tenant_automation_rule_revisions"),
        ("ix_tenant_automation_rules_tenant_status_updated", "tenant_automation_rules"),
    ):
        op.drop_index(name, table_name=table)
    if dialect != "sqlite":
        op.drop_constraint(
            "fk_tenant_automation_rules_current_revision",
            "tenant_automation_rules",
            type_="foreignkey",
        )
    for table in reversed(TABLES):
        op.drop_table(table)
