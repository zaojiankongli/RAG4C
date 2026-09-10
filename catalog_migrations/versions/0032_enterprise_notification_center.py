"""Add Tenant-safe in-app Notification Center authority.

Revision ID: 0032_enterprise_notification_center
Revises: 0031_enterprise_release_quality_operations
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import mysql

revision: str = "0032_enterprise_notification_center"
down_revision: str | None = "0031_enterprise_release_quality_operations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "tenant_notification_subscriptions",
    "tenant_notifications",
    "tenant_notification_recipients",
    "tenant_notification_receipts",
    "tenant_notification_events",
)
IMMUTABLE_TABLES = (
    "tenant_notifications",
    "tenant_notification_recipients",
    "tenant_notification_events",
)
EVENT_INSERT_TRIGGER = "trg_tenant_notification_events_validate_insert"


def _datetime6():
    return (
        sa.DateTime()
        .with_variant(mysql.DATETIME(fsp=6), "mysql")
        .with_variant(mysql.DATETIME(fsp=6), "mariadb")
    )


def _dialect_name() -> str:
    return str(op.get_context().dialect.name).casefold()


def _subscription_active_key_check():
    status = sa.column("status", sa.String(16))
    account_id = sa.column("account_id", sa.String(64))
    category = sa.column("category", sa.String(16))
    active_key = sa.column("active_subscription_key", sa.String(96))
    return sa.or_(
        sa.and_(status == "archived", active_key.is_(None)),
        sa.and_(
            status == "active",
            active_key == account_id + sa.literal(":") + category,
        ),
    )


def _create_guards() -> None:
    dialect = _dialect_name()
    if dialect == "sqlite":
        for table in IMMUTABLE_TABLES:
            for operation in ("UPDATE", "DELETE"):
                name = f"trg_{table}_no_{operation.casefold()}"
                op.execute(
                    f"CREATE TRIGGER {name} BEFORE {operation} ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, '{table} are immutable'); END"
                )
        op.execute(
            f"CREATE TRIGGER {EVENT_INSERT_TRIGGER} BEFORE INSERT ON tenant_notification_events "
            "BEGIN "
            "SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM tenant_notification_receipts r "
            "WHERE r.tenant_id=NEW.tenant_id AND r.notification_id=NEW.notification_id "
            "AND r.account_id=NEW.account_id) THEN RAISE(ABORT, 'notification event receipt required') END; "
            "SELECT CASE WHEN NEW.sequence=1 AND (NEW.event_type<>'materialized' "
            "OR NEW.previous_event_digest IS NOT NULL OR EXISTS (SELECT 1 FROM tenant_notification_events e "
            "WHERE e.tenant_id=NEW.tenant_id AND e.notification_id=NEW.notification_id "
            "AND e.account_id=NEW.account_id)) THEN RAISE(ABORT, 'invalid notification event first link') END; "
            "SELECT CASE WHEN NEW.sequence>1 AND NOT EXISTS (SELECT 1 FROM tenant_notification_events e "
            "WHERE e.tenant_id=NEW.tenant_id AND e.notification_id=NEW.notification_id "
            "AND e.account_id=NEW.account_id AND e.sequence=NEW.sequence-1 "
            "AND e.event_digest=NEW.previous_event_digest) THEN RAISE(ABORT, 'invalid notification event previous link') END; "
            "END"
        )
    elif dialect in {"mysql", "mariadb"}:
        for table in IMMUTABLE_TABLES:
            for operation in ("UPDATE", "DELETE"):
                name = f"trg_{table}_no_{operation.casefold()}"
                op.execute(
                    f"CREATE TRIGGER {name} BEFORE {operation} ON {table} FOR EACH ROW "
                    f"SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{table} are immutable'"
                )
        op.execute(
            f"CREATE TRIGGER {EVENT_INSERT_TRIGGER} BEFORE INSERT ON tenant_notification_events "
            "FOR EACH ROW BEGIN "
            "IF NOT EXISTS (SELECT 1 FROM tenant_notification_receipts r "
            "WHERE r.tenant_id=NEW.tenant_id AND r.notification_id=NEW.notification_id "
            "AND r.account_id=NEW.account_id) THEN SIGNAL SQLSTATE '45000' "
            "SET MESSAGE_TEXT='notification event receipt required'; END IF; "
            "IF NEW.sequence=1 THEN "
            "IF NEW.event_type<>'materialized' OR NEW.previous_event_digest IS NOT NULL "
            "OR EXISTS (SELECT 1 FROM tenant_notification_events e WHERE e.tenant_id=NEW.tenant_id "
            "AND e.notification_id=NEW.notification_id AND e.account_id=NEW.account_id) "
            "THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='invalid notification event first link'; END IF; "
            "ELSEIF NOT EXISTS (SELECT 1 FROM tenant_notification_events e "
            "WHERE e.tenant_id=NEW.tenant_id AND e.notification_id=NEW.notification_id "
            "AND e.account_id=NEW.account_id AND e.sequence=NEW.sequence-1 "
            "AND e.event_digest=NEW.previous_event_digest) THEN SIGNAL SQLSTATE '45000' "
            "SET MESSAGE_TEXT='invalid notification event previous link'; END IF; "
            "END"
        )
    elif dialect == "postgresql":
        op.execute(
            "CREATE FUNCTION rag4c_notification_immutable() RETURNS trigger LANGUAGE plpgsql "
            "AS $$ BEGIN RAISE EXCEPTION 'Notification Center immutable authority'; END; $$"
        )
        for table in IMMUTABLE_TABLES:
            for operation in ("UPDATE", "DELETE"):
                name = f"trg_{table}_no_{operation.casefold()}"
                op.execute(
                    f"CREATE TRIGGER {name} BEFORE {operation} ON {table} "
                    "FOR EACH ROW EXECUTE FUNCTION rag4c_notification_immutable()"
                )
        op.execute(
            "CREATE FUNCTION rag4c_notification_event_validate() RETURNS trigger LANGUAGE plpgsql "
            "AS $$ BEGIN "
            "IF NOT EXISTS (SELECT 1 FROM tenant_notification_receipts r "
            "WHERE r.tenant_id=NEW.tenant_id AND r.notification_id=NEW.notification_id "
            "AND r.account_id=NEW.account_id) THEN RAISE EXCEPTION 'notification event receipt required'; END IF; "
            "IF NEW.sequence=1 THEN "
            "IF NEW.event_type<>'materialized' OR NEW.previous_event_digest IS NOT NULL "
            "OR EXISTS (SELECT 1 FROM tenant_notification_events e WHERE e.tenant_id=NEW.tenant_id "
            "AND e.notification_id=NEW.notification_id AND e.account_id=NEW.account_id) "
            "THEN RAISE EXCEPTION 'invalid notification event first link'; END IF; "
            "ELSIF NOT EXISTS (SELECT 1 FROM tenant_notification_events e "
            "WHERE e.tenant_id=NEW.tenant_id AND e.notification_id=NEW.notification_id "
            "AND e.account_id=NEW.account_id AND e.sequence=NEW.sequence-1 "
            "AND e.event_digest=NEW.previous_event_digest) THEN "
            "RAISE EXCEPTION 'invalid notification event previous link'; END IF; "
            "RETURN NEW; END; $$"
        )
        op.execute(
            f"CREATE TRIGGER {EVENT_INSERT_TRIGGER} BEFORE INSERT ON tenant_notification_events "
            "FOR EACH ROW EXECUTE FUNCTION rag4c_notification_event_validate()"
        )


def _drop_guards() -> None:
    dialect = _dialect_name()
    if dialect == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS {EVENT_INSERT_TRIGGER} ON tenant_notification_events")
    else:
        op.execute(f"DROP TRIGGER IF EXISTS {EVENT_INSERT_TRIGGER}")
    for table in IMMUTABLE_TABLES:
        for operation in ("update", "delete"):
            name = f"trg_{table}_no_{operation}"
            if dialect == "postgresql":
                op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
            else:
                op.execute(f"DROP TRIGGER IF EXISTS {name}")
    if dialect == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS rag4c_notification_event_validate()")
        op.execute("DROP FUNCTION IF EXISTS rag4c_notification_immutable()")


def _guard_downgrade() -> None:
    connection = op.get_bind()
    counts = {
        table: int(connection.scalar(text(f"SELECT COUNT(*) FROM {table}")) or 0)
        for table in TABLES
    }
    if any(counts.values()):
        detail = ", ".join(f"{table}={count}" for table, count in counts.items() if count)
        raise RuntimeError(f"0032 downgrade blocked by Notification Center authority: {detail}")


def upgrade() -> None:
    _is_mysql = op.get_bind().dialect.name == "mysql"
    if context.is_offline_mode() and _dialect_name() == "sqlite":
        raise RuntimeError("0032 SQLite offline upgrade is unsupported; use online migration")

    with op.batch_alter_table("tenant_members") as batch:
        batch.create_unique_constraint(
            "uq_tenant_members_tenant_account", ["tenant_id", "account_id"]
        )

    op.create_table(
        "tenant_notification_subscriptions",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("preference", sa.String(16), nullable=False, server_default="subscribed"),
        sa.Column("active_subscription_key", sa.String(96), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("minimum_severity", sa.String(16), nullable=False, server_default="warning"),
        sa.Column("muted_until", _datetime6(), nullable=True),
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
            "category IN ('quality','approval')", name="ck_notification_subscriptions_category"
        ),
        sa.CheckConstraint(
            "status IN ('active','archived')", name="ck_notification_subscriptions_status"
        ),
        sa.CheckConstraint(
            "preference IN ('subscribed','muted')", name="ck_notification_subscriptions_preference"
        ),
        sa.CheckConstraint(
            "minimum_severity IN ('info','warning','critical')",
            name="ck_notification_subscriptions_severity",
        ),
        sa.CheckConstraint("revision > 0", name="ck_notification_subscriptions_revision"),
        sa.CheckConstraint(
            _subscription_active_key_check(), name="ck_notification_subscriptions_active_key"
        ),
        sa.CheckConstraint(
            "(preference='subscribed' AND muted_until IS NULL) OR preference='muted'",
            name="ck_notification_subscriptions_mute",
        ),
        sa.CheckConstraint(
            "(status='active' AND active_subscription_key IS NOT NULL AND archived_at IS NULL AND archived_by IS NULL) OR "
            "(status='archived' AND active_subscription_key IS NULL AND archived_at IS NOT NULL AND archived_by IS NOT NULL)",
            name="ck_notification_subscriptions_lifecycle",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_notification_subscriptions_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_notification_subscriptions_member",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_notification_subscriptions_scope_id"),
        sa.UniqueConstraint(
            "tenant_id", "active_subscription_key", name="uq_notification_subscriptions_active_key"
        ),
    )
    op.create_index(
        "ix_notification_subscriptions_account",
        "tenant_notification_subscriptions",
        ["tenant_id", "account_id", "status", "category", "id"],
    )

    op.create_table(
        "tenant_notifications",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("source_dataset_id", sa.String(64), nullable=True),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("action_required", sa.Boolean(), nullable=False),
        sa.Column("mandatory", sa.Boolean(), nullable=False),
        sa.Column("notification_key", sa.String(64), nullable=False),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.Column("title_code", sa.String(64), nullable=False),
        sa.Column("summary_code", sa.String(64), nullable=False),
        sa.Column("safe_facts_json", sa.JSON(), nullable=False),
        sa.Column("target_route_code", sa.String(48), nullable=False),
        sa.Column("target_route_params_json", sa.JSON(), nullable=False),
        sa.Column("occurred_at", _datetime6(), nullable=False),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "source_kind IN ('quality_alert','approval_pending_for_me')",
            name="ck_tenant_notifications_source_kind",
        ),
        sa.CheckConstraint(
            "category IN ('quality','approval')", name="ck_tenant_notifications_category"
        ),
        sa.CheckConstraint(
            "severity IN ('info','warning','critical')", name="ck_tenant_notifications_severity"
        ),
        sa.CheckConstraint("source_revision > 0", name="ck_tenant_notifications_source_revision"),
        sa.CheckConstraint(
            "action_required IN (false,true) AND mandatory IN (false,true)",
            name="ck_tenant_notifications_flags",
        ),
        sa.CheckConstraint(
            "(source_kind='quality_alert' AND source_dataset_id IS NOT NULL AND category='quality') OR "
            "(source_kind='approval_pending_for_me' AND source_dataset_id IS NULL AND category='approval')",
            name="ck_tenant_notifications_source_scope",
        ),
        sa.CheckConstraint(
            "(target_route_code='knowledge_quality_operations' AND source_kind='quality_alert') OR "
            "(target_route_code='enterprise_approval' AND source_kind='approval_pending_for_me')",
            name="ck_tenant_notifications_route",
        ),
        sa.CheckConstraint(
            "length(notification_key)=64 AND lower(notification_key)=notification_key AND "
            "length(source_digest)=64 AND lower(source_digest)=source_digest",
            name="ck_tenant_notifications_digests",
        ),
        sa.CheckConstraint(
            "length(title_code) BETWEEN 1 AND 64 AND length(summary_code) BETWEEN 1 AND 64",
            name="ck_tenant_notifications_codes",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_notifications_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_tenant_notifications_dataset",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tenant_notifications_scope_id"),
        sa.UniqueConstraint(
            "tenant_id", "notification_key", name="uq_tenant_notifications_notification_key"
        ),
    )
    op.create_index(
        "ix_tenant_notifications_source",
        "tenant_notifications",
        ["tenant_id", "source_kind", "source_id", "source_revision", "id"],
    )
    op.create_index(
        "ix_tenant_notifications_category_time",
        "tenant_notifications",
        ["tenant_id", "category", "severity", "occurred_at", "id"],
    )

    op.create_table(
        "tenant_notification_recipients",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("notification_id", sa.String(64), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("recipient_reason", sa.String(32), nullable=False),
        sa.Column("mandatory", sa.Boolean(), nullable=False),
        sa.Column("assignment_digest", sa.String(64), nullable=False),
        sa.Column("assigned_at", _datetime6(), nullable=False),
        sa.CheckConstraint(
            "recipient_reason IN ('tenant_owner','tenant_admin','dataset_owner','eligible_approver','explicit_subscription')",
            name="ck_notification_recipients_reason",
        ),
        sa.CheckConstraint(
            "mandatory IN (false,true)", name="ck_notification_recipients_mandatory"
        ),
        sa.CheckConstraint(
            "length(assignment_digest)=64 AND lower(assignment_digest)=assignment_digest",
            name="ck_notification_recipients_digest",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_notification_recipients_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "notification_id"],
            ["tenant_notifications.tenant_id", "tenant_notifications.id"],
            name="fk_notification_recipients_notification",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_notification_recipients_member",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_notification_recipients_scope_id"),
        sa.UniqueConstraint(
            "tenant_id", "notification_id", "account_id", name="uq_notification_recipients_identity"
        ),
    )
    op.create_index(
        "ix_notification_recipients_account",
        "tenant_notification_recipients",
        ["tenant_id", "account_id", "assigned_at", "id"],
    )

    op.create_table(
        "tenant_notification_receipts",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("notification_id", sa.String(64), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="unread"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("read_at", _datetime6(), nullable=True),
        sa.Column("archived_at", _datetime6(), nullable=True),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.CheckConstraint(
            "status IN ('unread','read','archived')", name="ck_notification_receipts_status"
        ),
        sa.CheckConstraint("revision > 0", name="ck_notification_receipts_revision"),
        sa.CheckConstraint(
            "(status='unread' AND read_at IS NULL AND archived_at IS NULL) OR "
            "(status='read' AND read_at IS NOT NULL AND archived_at IS NULL) OR "
            "(status='archived' AND archived_at IS NOT NULL)",
            name="ck_notification_receipts_lifecycle",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_notification_receipts_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "notification_id", "account_id"],
            [
                "tenant_notification_recipients.tenant_id",
                "tenant_notification_recipients.notification_id",
                "tenant_notification_recipients.account_id",
            ],
            name="fk_notification_receipts_recipient",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_notification_receipts_scope_id"),
        sa.UniqueConstraint(
            "tenant_id", "notification_id", "account_id", name="uq_notification_receipts_identity"
        ),
    )
    op.create_index(
        "ix_notification_receipts_inbox",
        "tenant_notification_receipts",
        ["tenant_id", "account_id", "status", "updated_at", "notification_id"],
    )

    op.create_table(
        "tenant_notification_events",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("notification_id", sa.String(64), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(24), nullable=False),
        sa.Column("previous_event_digest", sa.String(64), nullable=True),
        sa.Column("event_digest", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("safe_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("occurred_at", _datetime6(), nullable=False),
        sa.CheckConstraint("sequence > 0", name="ck_notification_events_sequence"),
        sa.CheckConstraint(
            "event_type IN ('materialized','marked_read','marked_unread','archived')",
            name="ck_notification_events_type",
        ),
        sa.CheckConstraint(
            "length(event_digest)=64 AND lower(event_digest)=event_digest AND "
            "(previous_event_digest IS NULL OR (length(previous_event_digest)=64 AND lower(previous_event_digest)=previous_event_digest))",
            name="ck_notification_events_digests",
        ),
        sa.CheckConstraint(
            "(sequence=1 AND previous_event_digest IS NULL) OR "
            "(sequence>1 AND previous_event_digest IS NOT NULL)",
            name="ck_notification_events_hash_chain",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_notification_events_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "notification_id", "account_id"],
            [
                "tenant_notification_recipients.tenant_id",
                "tenant_notification_recipients.notification_id",
                "tenant_notification_recipients.account_id",
            ],
            name="fk_notification_events_recipient",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_notification_events_scope_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "notification_id",
            "account_id",
            "sequence",
            name="uq_notification_events_stream_sequence",
        ),
    )
    op.create_index(
        "ix_notification_events_stream",
        "tenant_notification_events",
        ["tenant_id", "notification_id", "account_id", "sequence", "id"],
    )
    op.create_index(
        "ix_notification_events_time",
        "tenant_notification_events",
        ["tenant_id", "account_id", "occurred_at", "id"],
    )

    _create_guards()


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("0032 downgrade requires online preflight")
    _guard_downgrade()
    _drop_guards()
    op.drop_index("ix_notification_events_time", table_name="tenant_notification_events")
    op.drop_index("ix_notification_events_stream", table_name="tenant_notification_events")
    op.drop_table("tenant_notification_events")
    op.drop_index("ix_notification_receipts_inbox", table_name="tenant_notification_receipts")
    op.drop_table("tenant_notification_receipts")
    op.drop_index("ix_notification_recipients_account", table_name="tenant_notification_recipients")
    op.drop_table("tenant_notification_recipients")
    op.drop_index("ix_tenant_notifications_category_time", table_name="tenant_notifications")
    op.drop_index("ix_tenant_notifications_source", table_name="tenant_notifications")
    op.drop_table("tenant_notifications")
    op.drop_index(
        "ix_notification_subscriptions_account", table_name="tenant_notification_subscriptions"
    )
    op.drop_table("tenant_notification_subscriptions")
    with op.batch_alter_table("tenant_members") as batch:
        batch.drop_constraint("uq_tenant_members_tenant_account", type_="unique")
