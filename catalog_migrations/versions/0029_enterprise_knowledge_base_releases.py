"""Add authoritative Knowledge Base Release Channels and immutable Manifests.

Revision ID: 0029_enterprise_knowledge_base_releases
Revises: 0028_enterprise_knowledge_base_registry
Create Date: 2026-08-28
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
import hashlib

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy import Connection, text
from sqlalchemy.dialects import mysql

revision: str = "0029_enterprise_knowledge_base_releases"
down_revision: str | None = "0028_enterprise_knowledge_base_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHANNEL_TABLE = "tenant_release_channels"
MANIFEST_TABLE = "dataset_release_manifests"
ENTRY_TABLE = "dataset_release_entries"
EVENT_TABLE = "dataset_release_events"
BINDING_TABLE = "dataset_channel_releases"
MIGRATION_ACTOR = "migration:0029"

DEFAULT_CHANNELS: tuple[tuple[str, str, str, int, bool], ...] = (
    ("development", "Development", "low", 10, False),
    ("testing", "Testing", "medium", 20, False),
    ("production", "Production", "high", 30, True),
)

APPROVAL_ACTION_TYPES_0028: tuple[str, ...] = (
    "catalog_upgrade",
    "membership_bootstrap",
    "dataset_acl_disable",
    "member_role_change",
    "identity_provider_disable",
    "audit_retention_execute",
    "workspace_authorization_mode_change",
    "dataset_workspace_transfer",
)
APPROVAL_ACTION_TYPES_0029 = APPROVAL_ACTION_TYPES_0028 + (
    "knowledge_base_release_publish",
    "knowledge_base_release_rollback",
)
APPROVAL_ACTION_CHECKS: tuple[tuple[str, str], ...] = (
    ("tenant_approval_policies", "ck_tenant_approval_policies_action_type"),
    ("tenant_approval_requests", "ck_tenant_approval_requests_action_type"),
)

CHANNEL_INDEX_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "ix_tenant_release_channels_tenant_status_order",
        ("tenant_id", "status", "promotion_order", "id"),
    ),
    (
        "ix_tenant_release_channels_tenant_default",
        ("tenant_id", "status", "active_default_slot", "id"),
    ),
)
MANIFEST_INDEX_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "ix_dataset_release_manifests_tenant_dataset_number",
        ("tenant_id", "dataset_id", "release_number", "id"),
    ),
    (
        "ix_dataset_release_manifests_tenant_dataset_created",
        ("tenant_id", "dataset_id", "created_at", "id"),
    ),
    (
        "ix_dataset_release_manifests_tenant_readiness",
        ("tenant_id", "readiness_state", "created_at", "id"),
    ),
)
ENTRY_INDEX_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "ix_dataset_release_entries_tenant_release_ordinal",
        ("tenant_id", "dataset_id", "release_id", "ordinal", "id"),
    ),
    (
        "ix_dataset_release_entries_tenant_resource",
        ("tenant_id", "dataset_id", "resource_type", "resource_id", "id"),
    ),
)
EVENT_INDEX_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "ix_dataset_release_events_tenant_release_time",
        ("tenant_id", "dataset_id", "release_id", "occurred_at", "id"),
    ),
    (
        "ix_dataset_release_events_tenant_channel_time",
        ("tenant_id", "channel_id", "occurred_at", "id"),
    ),
)
BINDING_INDEX_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "ix_dataset_channel_releases_tenant_dataset_status",
        ("tenant_id", "dataset_id", "status", "revision", "id"),
    ),
    (
        "ix_dataset_channel_releases_tenant_channel_status",
        ("tenant_id", "channel_id", "status", "id"),
    ),
)

IMMUTABLE_GUARDS: tuple[tuple[str, str, str], ...] = (
    (
        MANIFEST_TABLE,
        "trg_dataset_release_manifests_no_update",
        "trg_dataset_release_manifests_no_delete",
    ),
    (ENTRY_TABLE, "trg_dataset_release_entries_no_update", "trg_dataset_release_entries_no_delete"),
    (EVENT_TABLE, "trg_dataset_release_events_no_update", "trg_dataset_release_events_no_delete"),
)
IMMUTABLE_FUNCTION = "rag4c_dataset_release_content_immutable"


def _datetime6():
    return (
        sa.DateTime()
        .with_variant(mysql.DATETIME(fsp=6), "mysql")
        .with_variant(mysql.DATETIME(fsp=6), "mariadb")
    )


def _dialect_name() -> str:
    return str(op.get_context().dialect.name).casefold()


def _approval_action_check(actions: tuple[str, ...]) -> str:
    return "action_type IN (" + ",".join(f"'{action}'" for action in actions) + ")"


def _replace_approval_action_checks(actions: tuple[str, ...]) -> None:
    sqltext = _approval_action_check(actions)
    for table_name, constraint_name in APPROVAL_ACTION_CHECKS:
        with op.batch_alter_table(table_name) as batch:
            batch.drop_constraint(constraint_name, type_="check")
            batch.create_check_constraint(constraint_name, sqltext)


def _channel_id(tenant_id: str, code: str) -> str:
    tenant_bytes = tenant_id.encode("utf-8")
    payload = (
        b"rag4c:tenant-release-channel:v2:"
        + str(len(tenant_bytes)).encode("ascii")
        + b":"
        + tenant_bytes
        + b":"
        + code.encode("utf-8")
    )
    return "release-channel-" + hashlib.md5(payload, usedforsecurity=False).hexdigest()


def _seed_default_channels(connection: Connection) -> None:
    tenants = connection.execute(text("SELECT id FROM tenants ORDER BY id")).scalars().all()
    now = datetime.utcnow()
    for tenant_id in tenants:
        for code, name, risk_tier, promotion_order, is_default_serving in DEFAULT_CHANNELS:
            connection.execute(
                text(
                    f"INSERT INTO {CHANNEL_TABLE} "
                    "(id,tenant_id,code,normalized_code,name,status,risk_tier,promotion_order,"
                    "is_default_serving,active_default_slot,revision,created_at,created_by,"
                    "updated_at,updated_by,archived_at,archived_by) VALUES "
                    "(:id,:tenant_id,:code,:normalized_code,:name,'active',:risk_tier,:promotion_order,"
                    ":is_default_serving,:active_default_slot,1,:now,:actor,:now,:actor,NULL,NULL)"
                ),
                {
                    "id": _channel_id(str(tenant_id), code),
                    "tenant_id": tenant_id,
                    "code": code,
                    "normalized_code": code.casefold(),
                    "name": name,
                    "risk_tier": risk_tier,
                    "promotion_order": promotion_order,
                    "is_default_serving": is_default_serving,
                    "active_default_slot": "default" if is_default_serving else None,
                    "now": now,
                    "actor": MIGRATION_ACTOR,
                },
            )


def _offline_channel_id_sql(code: str) -> str:
    return (
        "CONCAT('release-channel-', MD5(CONCAT("
        "'rag4c:tenant-release-channel:v2:', OCTET_LENGTH(t.id), ':', t.id, ':" + code + "')))"
    )


def _emit_offline_default_channel_seed() -> None:
    dialect = _dialect_name()
    if dialect not in {"mysql", "mariadb", "postgresql"}:
        raise RuntimeError(
            "0029 offline upgrade is supported only for MySQL/PostgreSQL; SQLite requires online batch migration"
        )
    for code, name, risk_tier, promotion_order, is_default_serving in DEFAULT_CHANNELS:
        default_sql = "TRUE" if is_default_serving else "FALSE"
        slot_sql = "'default'" if is_default_serving else "NULL"
        op.execute(
            sa.text(
                f"INSERT INTO {CHANNEL_TABLE} "
                "(id,tenant_id,code,normalized_code,name,status,risk_tier,promotion_order,"
                "is_default_serving,active_default_slot,revision,created_at,created_by,"
                "updated_at,updated_by,archived_at,archived_by) "
                f"SELECT {_offline_channel_id_sql(code)}, t.id, '{code}', '{code}', "
                f"'{name}', 'active', '{risk_tier}', {promotion_order}, {default_sql}, "
                f"{slot_sql}, 1, CURRENT_TIMESTAMP, '{MIGRATION_ACTOR}', CURRENT_TIMESTAMP, "
                f"'{MIGRATION_ACTOR}', NULL, NULL FROM tenants AS t "
                f"WHERE NOT EXISTS (SELECT 1 FROM {CHANNEL_TABLE} AS c "
                f"WHERE c.tenant_id=t.id AND c.normalized_code='{code}')"
            )
        )


def _emit_offline_application_binding_backfill() -> None:
    op.execute(
        sa.text(
            "UPDATE app_dataset_references AS r SET release_mode='follow_channel', "
            "release_channel_id=(SELECT c.id FROM tenant_release_channels AS c "
            "WHERE c.tenant_id=r.tenant_id AND c.normalized_code='development'), "
            "pinned_release_id=NULL WHERE r.release_channel_id IS NULL"
        )
    )


def _create_immutable_guards() -> None:
    dialect = _dialect_name()
    if dialect == "sqlite":
        for table_name, update_trigger, delete_trigger in IMMUTABLE_GUARDS:
            message = f"{table_name} are immutable"
            op.execute(
                f"CREATE TRIGGER {update_trigger} BEFORE UPDATE ON {table_name} BEGIN "
                f"SELECT RAISE(ABORT, '{message}'); END"
            )
            op.execute(
                f"CREATE TRIGGER {delete_trigger} BEFORE DELETE ON {table_name} BEGIN "
                f"SELECT RAISE(ABORT, '{message}'); END"
            )
    elif dialect in {"mysql", "mariadb"}:
        for table_name, update_trigger, delete_trigger in IMMUTABLE_GUARDS:
            message = f"{table_name} are immutable"
            op.execute(
                f"CREATE TRIGGER {update_trigger} BEFORE UPDATE ON {table_name} FOR EACH ROW "
                "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
                f"'{message}'"
            )
            op.execute(
                f"CREATE TRIGGER {delete_trigger} BEFORE DELETE ON {table_name} FOR EACH ROW "
                "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
                f"'{message}'"
            )
    elif dialect == "postgresql":
        op.execute(
            f"CREATE FUNCTION {IMMUTABLE_FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN RAISE EXCEPTION 'dataset release content is immutable'; END; $$"
        )
        for table_name, update_trigger, delete_trigger in IMMUTABLE_GUARDS:
            op.execute(
                f"CREATE TRIGGER {update_trigger} BEFORE UPDATE ON {table_name} FOR EACH ROW "
                f"EXECUTE FUNCTION {IMMUTABLE_FUNCTION}()"
            )
            op.execute(
                f"CREATE TRIGGER {delete_trigger} BEFORE DELETE ON {table_name} FOR EACH ROW "
                f"EXECUTE FUNCTION {IMMUTABLE_FUNCTION}()"
            )


def _drop_immutable_guards() -> None:
    # 目标名是本迁移 IMMUTABLE_GUARDS 里逐条声明的常量（表名 + no_update/no_delete
    # 触发器名），IMMUTABLE_FUNCTION 同样在头部定义。DROP 守卫按安全扫描要求写成
    # 完整字面量；迁移是冻结产物，这些名字不会再变。
    dialect = _dialect_name()
    if dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_dataset_release_manifests_no_update"
            " ON dataset_release_manifests"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_dataset_release_manifests_no_delete"
            " ON dataset_release_manifests"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_dataset_release_entries_no_update"
            " ON dataset_release_entries"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_dataset_release_entries_no_delete"
            " ON dataset_release_entries"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_dataset_release_events_no_update"
            " ON dataset_release_events"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_dataset_release_events_no_delete"
            " ON dataset_release_events"
        )
        op.execute("DROP FUNCTION IF EXISTS rag4c_dataset_release_content_immutable()")
    elif dialect in {"sqlite", "mysql", "mariadb"}:
        op.execute("DROP TRIGGER IF EXISTS trg_dataset_release_manifests_no_update")
        op.execute("DROP TRIGGER IF EXISTS trg_dataset_release_manifests_no_delete")
        op.execute("DROP TRIGGER IF EXISTS trg_dataset_release_entries_no_update")
        op.execute("DROP TRIGGER IF EXISTS trg_dataset_release_entries_no_delete")
        op.execute("DROP TRIGGER IF EXISTS trg_dataset_release_events_no_update")
        op.execute("DROP TRIGGER IF EXISTS trg_dataset_release_events_no_delete")


def _backfill_application_release_bindings(connection: Connection) -> None:
    connection.execute(
        text(
            "UPDATE app_dataset_references SET release_channel_id = ("
            "SELECT c.id FROM tenant_release_channels AS c "
            "WHERE c.tenant_id=app_dataset_references.tenant_id AND c.code='development'"
            ") WHERE release_mode='follow_channel' AND release_channel_id IS NULL"
        )
    )
    missing = int(
        connection.execute(
            text(
                "SELECT COUNT(*) FROM app_dataset_references "
                "WHERE release_channel_id IS NULL OR release_mode IS NULL"
            )
        ).scalar_one()
    )
    if missing:
        raise RuntimeError(
            "0029 application reference backfill could not resolve a deterministic development channel"
        )


def _guard_downgrade(connection: Connection) -> None:
    release_counts = {
        table_name: int(connection.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar_one())
        for table_name in (MANIFEST_TABLE, ENTRY_TABLE, EVENT_TABLE, BINDING_TABLE)
    }
    pinned_count = int(
        connection.execute(
            text("SELECT COUNT(*) FROM app_dataset_references WHERE pinned_release_id IS NOT NULL")
        ).scalar_one()
    )
    serving_count = int(
        connection.execute(
            text("SELECT COUNT(*) FROM datasets WHERE serving_release_id IS NOT NULL")
        ).scalar_one()
    )
    custom_channel_count = int(
        connection.execute(
            text(
                "SELECT COUNT(*) FROM tenant_release_channels "
                "WHERE code NOT IN ('development','testing','production')"
            )
        ).scalar_one()
    )
    approval_counts = {
        table_name: int(
            connection.execute(
                text(
                    f"SELECT COUNT(*) FROM {table_name} WHERE action_type IN (:publish,:rollback)"
                ),
                {
                    "publish": "knowledge_base_release_publish",
                    "rollback": "knowledge_base_release_rollback",
                },
            ).scalar_one()
        )
        for table_name, _constraint_name in APPROVAL_ACTION_CHECKS
    }
    if (
        any(release_counts.values())
        or pinned_count
        or serving_count
        or custom_channel_count
        or any(approval_counts.values())
    ):
        evidence = ", ".join(
            [
                *(f"{table}={count}" for table, count in release_counts.items() if count),
                *([f"app_pins={pinned_count}"] if pinned_count else []),
                *([f"serving_projections={serving_count}"] if serving_count else []),
                *([f"custom_channels={custom_channel_count}"] if custom_channel_count else []),
                *(f"{table}={count}" for table, count in approval_counts.items() if count),
            ]
        )
        raise RuntimeError(
            "0029 downgrade blocked while release authority facts remain; " + evidence
        )


def upgrade() -> None:
    _is_mysql = op.get_bind().dialect.name == "mysql"
    if context.is_offline_mode() and _dialect_name() == "sqlite":
        raise RuntimeError(
            "0029 SQLite offline upgrade is unsupported; use an online batch migration after read-only preflight"
        )
    op.create_table(
        CHANNEL_TABLE,
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("normalized_code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'active'")
        ),
        sa.Column("risk_tier", sa.String(length=16), nullable=False),
        sa.Column("promotion_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_default_serving", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("active_default_slot", sa.String(length=16), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
        sa.Column("archived_at", _datetime6(), nullable=True),
        sa.Column("archived_by", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tenant_release_channels_scope_id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_tenant_release_channels_tenant_code"),
        sa.UniqueConstraint(
            "tenant_id", "normalized_code", name="uq_tenant_release_channels_tenant_normalized_code"
        ),
        sa.UniqueConstraint(
            "tenant_id", "active_default_slot", name="uq_tenant_release_channels_active_default"
        ),
        sa.CheckConstraint(
            "status IN ('active','archived')", name="ck_tenant_release_channels_status"
        ),
        sa.CheckConstraint(
            "risk_tier IN ('low','medium','high')", name="ck_tenant_release_channels_risk_tier"
        ),
        sa.CheckConstraint(
            "length(code) BETWEEN 1 AND 64", name="ck_tenant_release_channels_code_length"
        ),
        sa.CheckConstraint(
            "promotion_order >= 0", name="ck_tenant_release_channels_promotion_order_nonnegative"
        ),
        sa.CheckConstraint("revision > 0", name="ck_tenant_release_channels_revision_positive"),
        sa.CheckConstraint(
            "(status = 'active' AND archived_at IS NULL AND archived_by IS NULL) OR "
            "(status = 'archived' AND archived_at IS NOT NULL AND archived_by IS NOT NULL)",
            name="ck_tenant_release_channels_lifecycle_evidence",
        ),
        sa.CheckConstraint(
            "(is_default_serving = false AND active_default_slot IS NULL) OR "
            "(is_default_serving = true AND status = 'active' AND active_default_slot = 'default')",
            name="ck_tenant_release_channels_active_default_slot",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_release_channels_tenant"
        ),
    )
    for name, columns in CHANNEL_INDEX_SPECS:
        op.create_index(name, CHANNEL_TABLE, list(columns), unique=False)

    op.create_table(
        MANIFEST_TABLE,
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("release_number", sa.Integer(), nullable=False),
        sa.Column("profile_revision", sa.Integer(), nullable=False),
        sa.Column("ownership_revision", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.String(length=128), nullable=False),
        sa.Column("workspace_revision", sa.Integer(), nullable=False),
        sa.Column("mutation_generation", sa.BigInteger(), nullable=False),
        sa.Column("serving_generation", sa.BigInteger(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("policy_digest", sa.String(length=64), nullable=False),
        sa.Column("manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("readiness_digest", sa.String(length=64), nullable=False),
        sa.Column("readiness_state", sa.String(length=16), nullable=False),
        sa.Column("entry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("blocker_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("readiness_blockers_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_dataset_release_manifests_scope_id"),
        sa.UniqueConstraint(
            "tenant_id", "dataset_id", "id", name="uq_dataset_release_manifests_scope_dataset_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "release_number",
            name="uq_dataset_release_manifests_dataset_number",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "manifest_digest",
            name="uq_dataset_release_manifests_dataset_digest",
        ),
        sa.CheckConstraint(
            "release_number > 0", name="ck_dataset_release_manifests_release_number_positive"
        ),
        sa.CheckConstraint(
            "profile_revision > 0 AND ownership_revision > 0 AND workspace_revision > 0",
            name="ck_dataset_release_manifests_revisions_positive",
        ),
        sa.CheckConstraint(
            "mutation_generation >= 0 AND serving_generation >= 0",
            name="ck_dataset_release_manifests_generations_nonnegative",
        ),
        sa.CheckConstraint(
            "schema_version > 0", name="ck_dataset_release_manifests_schema_version_positive"
        ),
        sa.CheckConstraint(
            "length(policy_digest) = 64 AND lower(policy_digest) = policy_digest",
            name="ck_dataset_release_manifests_policy_digest",
        ),
        sa.CheckConstraint(
            "length(manifest_digest) = 64 AND lower(manifest_digest) = manifest_digest",
            name="ck_dataset_release_manifests_manifest_digest",
        ),
        sa.CheckConstraint(
            "length(readiness_digest) = 64 AND lower(readiness_digest) = readiness_digest",
            name="ck_dataset_release_manifests_readiness_digest",
        ),
        sa.CheckConstraint(
            "readiness_state IN ('ready','blocked','unavailable')",
            name="ck_dataset_release_manifests_readiness_state",
        ),
        sa.CheckConstraint(
            "entry_count >= 0 AND blocker_count >= 0",
            name="ck_dataset_release_manifests_counts_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_dataset_release_manifests_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_dataset_release_manifests_scope_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["tenant_workspaces.tenant_id", "tenant_workspaces.id"],
            name="fk_dataset_release_manifests_scope_workspace",
        ),
    )
    for name, columns in MANIFEST_INDEX_SPECS:
        op.create_index(name, MANIFEST_TABLE, list(columns), unique=False)

    op.create_table(
        ENTRY_TABLE,
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("release_id", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.String(length=128), nullable=False),
        sa.Column("resource_revision", sa.BigInteger(), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=True),
        sa.Column("safe_facts_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_dataset_release_entries_scope_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "release_id",
            "ordinal",
            name="uq_dataset_release_entries_release_ordinal",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "release_id",
            "resource_type",
            "resource_id",
            name="uq_dataset_release_entries_resource",
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_dataset_release_entries_ordinal_nonnegative"),
        sa.CheckConstraint(
            "resource_type IN ('dataset_profile','document_version','qa_revision','source_generation','projection_revision')",
            name="ck_dataset_release_entries_resource_type",
        ),
        sa.CheckConstraint(
            "resource_revision >= 0",
            name="ck_dataset_release_entries_resource_revision_nonnegative",
        ),
        sa.CheckConstraint(
            "content_digest IS NULL OR (length(content_digest) = 64 AND lower(content_digest) = content_digest)",
            name="ck_dataset_release_entries_content_digest",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_dataset_release_entries_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_dataset_release_entries_scope_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "release_id"],
            [
                "dataset_release_manifests.tenant_id",
                "dataset_release_manifests.dataset_id",
                "dataset_release_manifests.id",
            ],
            name="fk_dataset_release_entries_scope_release",
        ),
    )
    for name, columns in ENTRY_INDEX_SPECS:
        op.create_index(name, ENTRY_TABLE, list(columns), unique=False)

    op.create_table(
        EVENT_TABLE,
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("release_id", sa.String(length=64), nullable=False),
        sa.Column("channel_id", sa.String(length=128), nullable=True),
        sa.Column("event_type", sa.String(length=24), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column(
            "occurred_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("previous_binding_revision", sa.Integer(), nullable=True),
        sa.Column("current_binding_revision", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_dataset_release_events_scope_id"),
        sa.CheckConstraint(
            "event_type IN ('candidate_created','promoted','superseded','rolled_back','retired')",
            name="ck_dataset_release_events_event_type",
        ),
        sa.CheckConstraint(
            "length(reason) BETWEEN 1 AND 512", name="ck_dataset_release_events_reason_length"
        ),
        sa.CheckConstraint(
            "previous_binding_revision IS NULL OR previous_binding_revision >= 0",
            name="ck_dataset_release_events_previous_revision_nonnegative",
        ),
        sa.CheckConstraint(
            "current_binding_revision IS NULL OR current_binding_revision >= 0",
            name="ck_dataset_release_events_current_revision_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_dataset_release_events_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_dataset_release_events_scope_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "release_id"],
            [
                "dataset_release_manifests.tenant_id",
                "dataset_release_manifests.dataset_id",
                "dataset_release_manifests.id",
            ],
            name="fk_dataset_release_events_scope_release",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "channel_id"],
            ["tenant_release_channels.tenant_id", "tenant_release_channels.id"],
            name="fk_dataset_release_events_scope_channel",
        ),
    )
    for name, columns in EVENT_INDEX_SPECS:
        op.create_index(name, EVENT_TABLE, list(columns), unique=False)

    op.create_table(
        BINDING_TABLE,
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("dataset_id", sa.String(length=64), nullable=False),
        sa.Column("channel_id", sa.String(length=128), nullable=False),
        sa.Column("active_release_id", sa.String(length=64), nullable=True),
        sa.Column("previous_release_id", sa.String(length=64), nullable=True),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'active'")
        ),
        sa.Column("active_slot", sa.String(length=16), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("activated_at", _datetime6(), nullable=True),
        sa.Column("activated_by", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=False),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_dataset_channel_releases_scope_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "channel_id",
            name="uq_dataset_channel_releases_scope_channel",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "channel_id",
            "active_slot",
            name="uq_dataset_channel_releases_active_slot",
        ),
        sa.CheckConstraint(
            "status IN ('active','removed')", name="ck_dataset_channel_releases_status"
        ),
        sa.CheckConstraint("revision > 0", name="ck_dataset_channel_releases_revision_positive"),
        sa.CheckConstraint(
            "(status = 'active' AND active_slot = 'active' AND active_release_id IS NOT NULL "
            "AND activated_at IS NOT NULL AND activated_by IS NOT NULL) OR "
            "(status = 'removed' AND active_slot IS NULL AND active_release_id IS NULL)",
            name="ck_dataset_channel_releases_active_slot",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_dataset_channel_releases_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_dataset_channel_releases_scope_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "channel_id"],
            ["tenant_release_channels.tenant_id", "tenant_release_channels.id"],
            name="fk_dataset_channel_releases_scope_channel",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "active_release_id"],
            [
                "dataset_release_manifests.tenant_id",
                "dataset_release_manifests.dataset_id",
                "dataset_release_manifests.id",
            ],
            name="fk_dataset_channel_releases_scope_active_release",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id", "previous_release_id"],
            [
                "dataset_release_manifests.tenant_id",
                "dataset_release_manifests.dataset_id",
                "dataset_release_manifests.id",
            ],
            name="fk_dataset_channel_releases_scope_previous_release",
        ),
    )
    for name, columns in BINDING_INDEX_SPECS:
        op.create_index(name, BINDING_TABLE, list(columns), unique=False)

    if context.is_offline_mode():
        _emit_offline_default_channel_seed()
    else:
        _seed_default_channels(op.get_bind())

    with op.batch_alter_table("datasets") as batch:
        batch.add_column(sa.Column("serving_release_id", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column("release_revision", sa.Integer(), nullable=False, server_default=sa.text("1"))
        )
        batch.create_check_constraint(
            "ck_datasets_release_revision_positive", "release_revision > 0"
        )
        batch.create_foreign_key(
            "fk_datasets_scope_serving_release",
            MANIFEST_TABLE,
            ["tenant_id", "serving_release_id"],
            ["tenant_id", "id"],
        )

    with op.batch_alter_table("app_dataset_references") as batch:
        batch.add_column(
            sa.Column(
                "release_mode",
                sa.String(length=16),
                nullable=True,
                server_default=sa.text("'follow_channel'"),
            )
        )
        batch.add_column(sa.Column("release_channel_id", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("pinned_release_id", sa.String(length=64), nullable=True))

    if context.is_offline_mode():
        _emit_offline_application_binding_backfill()
    else:
        _backfill_application_release_bindings(op.get_bind())

    with op.batch_alter_table("app_dataset_references") as batch:
        batch.alter_column(
            "release_mode",
            existing_type=sa.String(length=16),
            existing_nullable=True,
            existing_server_default=sa.text("'follow_channel'"),
            nullable=False,
            server_default=sa.text("'follow_channel'"),
        )
        batch.create_check_constraint(
            "ck_app_dataset_references_release_binding",
            "(release_mode = 'follow_channel' AND release_channel_id IS NOT NULL AND pinned_release_id IS NULL) OR "
            "(release_mode = 'pinned' AND release_channel_id IS NULL AND pinned_release_id IS NOT NULL)",
        )
        batch.create_foreign_key(
            "fk_app_dataset_references_scope_release_channel",
            CHANNEL_TABLE,
            ["tenant_id", "release_channel_id"],
            ["tenant_id", "id"],
        )
        batch.create_foreign_key(
            "fk_app_dataset_references_scope_pinned_release",
            MANIFEST_TABLE,
            ["tenant_id", "dataset_id", "pinned_release_id"],
            ["tenant_id", "dataset_id", "id"],
        )

    _replace_approval_action_checks(APPROVAL_ACTION_TYPES_0029)
    _create_immutable_guards()


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0029 downgrade requires an online preflight for release manifests, channels, bindings and approval facts"
        )

    connection = op.get_bind()
    _guard_downgrade(connection)
    _drop_immutable_guards()

    with op.batch_alter_table("app_dataset_references") as batch:
        batch.drop_constraint("fk_app_dataset_references_scope_pinned_release", type_="foreignkey")
        batch.drop_constraint("fk_app_dataset_references_scope_release_channel", type_="foreignkey")
        batch.drop_constraint("ck_app_dataset_references_release_binding", type_="check")
        batch.drop_column("pinned_release_id")
        batch.drop_column("release_channel_id")
        batch.drop_column("release_mode")

    with op.batch_alter_table("datasets") as batch:
        batch.drop_constraint("fk_datasets_scope_serving_release", type_="foreignkey")
        batch.drop_constraint("ck_datasets_release_revision_positive", type_="check")
        batch.drop_column("release_revision")
        batch.drop_column("serving_release_id")

    _replace_approval_action_checks(APPROVAL_ACTION_TYPES_0028)

    for name, _columns in reversed(BINDING_INDEX_SPECS):
        op.drop_index(name, table_name=BINDING_TABLE)
    op.drop_table(BINDING_TABLE)
    for name, _columns in reversed(EVENT_INDEX_SPECS):
        op.drop_index(name, table_name=EVENT_TABLE)
    op.drop_table(EVENT_TABLE)
    for name, _columns in reversed(ENTRY_INDEX_SPECS):
        op.drop_index(name, table_name=ENTRY_TABLE)
    op.drop_table(ENTRY_TABLE)
    for name, _columns in reversed(MANIFEST_INDEX_SPECS):
        op.drop_index(name, table_name=MANIFEST_TABLE)
    op.drop_table(MANIFEST_TABLE)
    for name, _columns in reversed(CHANNEL_INDEX_SPECS):
        op.drop_index(name, table_name=CHANNEL_TABLE)
    op.drop_table(CHANNEL_TABLE)
