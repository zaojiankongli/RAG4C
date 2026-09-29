"""Add Tenant-scoped Enterprise Knowledge Serving & Reliability authority.

Revision ID: 0036_enterprise_knowledge_serving_reliability
Revises: 0035_enterprise_automation_workflows
Create Date: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import mysql

revision: str = "0036_enterprise_knowledge_serving_reliability"
down_revision: str | None = "0035_enterprise_automation_workflows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "tenant_knowledge_serving_profiles",
    "tenant_knowledge_serving_policy_revisions",
    "tenant_knowledge_serving_snapshots",
    "tenant_knowledge_serving_stage_facts",
    "tenant_knowledge_serving_evidence_links",
    "tenant_knowledge_serving_events",
)
IMMUTABLE_TABLES = TABLES[1:]
STAGE_CODES = ("source", "parse", "chunk", "index", "serve")
STAGE_STATES = ("ready", "lagging", "blocked", "missing", "unavailable")
OVERALL_STATES = ("ready", "degraded", "blocked", "unavailable")
PROFILE_STATUSES = ("draft", "active", "paused", "archived")
EVIDENCE_KINDS = (
    "source",
    "source_sync_run",
    "document",
    "ingest_attempt",
    "chunk_head",
    "index_operation",
    "release",
    "certification",
    "task",
)
ROUTE_CODES = (
    "knowledge_sources",
    "knowledge_documents",
    "knowledge_indexing",
    "knowledge_base_releases",
    "release_quality",
    "enterprise_tasks",
)
EVIDENCE_KIND_ROUTE_PAIRS = (
    ("source", "knowledge_sources"),
    ("source_sync_run", "knowledge_sources"),
    ("document", "knowledge_documents"),
    ("ingest_attempt", "knowledge_documents"),
    ("chunk_head", "knowledge_documents"),
    ("index_operation", "knowledge_indexing"),
    ("release", "knowledge_base_releases"),
    ("certification", "release_quality"),
    ("task", "enterprise_tasks"),
)
EVENT_TYPES = (
    "profile_created",
    "policy_revision_created",
    "policy_activated",
    "snapshot_recorded",
    "stage_degraded",
    "stage_blocked",
    "service_recovered",
)
EVENT_TABLE = "tenant_knowledge_serving_events"
EVENT_INSERT_TRIGGER = "trg_tenant_knowledge_serving_events_validate_insert"
IMMUTABLE_FUNCTION = "rag4c_knowledge_serving_immutable"
EVENT_VALIDATE_FUNCTION = "rag4c_knowledge_serving_event_validate"
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
        raise RuntimeError(f"0036 unsupported database dialect: {dialect}")
    return dialect


def _quoted(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({_quoted(values)})"


def _evidence_kind_route_check() -> str:
    return " OR ".join(
        f"(evidence_kind='{kind}' AND route_code='{route}')"
        for kind, route in EVIDENCE_KIND_ROUTE_PAIRS
    )


def _create_guards() -> None:
    dialect = _require_supported_dialect()
    if dialect == "sqlite":
        for table in IMMUTABLE_TABLES:
            for operation in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER trg_{table}_no_{operation.casefold()} "
                    f"BEFORE {operation} ON {table} BEGIN "
                    "SELECT RAISE(ABORT, 'knowledge serving immutable authority'); END"
                )
        op.execute(
            f"CREATE TRIGGER {EVENT_INSERT_TRIGGER} BEFORE INSERT ON {EVENT_TABLE} BEGIN "
            "SELECT CASE WHEN NEW.sequence=1 AND NEW.event_type<>'profile_created' "
            "THEN RAISE(ABORT,'knowledge serving first event invalid') END; "
            "SELECT CASE WHEN NEW.sequence=1 AND NEW.previous_event_digest IS NOT NULL "
            "THEN RAISE(ABORT,'knowledge serving first previous digest invalid') END; "
            "SELECT CASE WHEN NEW.sequence>1 AND NEW.previous_event_digest IS NULL "
            "THEN RAISE(ABORT,'knowledge serving previous digest required') END; "
            f"SELECT CASE WHEN NEW.sequence>1 AND NOT EXISTS (SELECT 1 FROM {EVENT_TABLE} e "
            "WHERE e.tenant_id=NEW.tenant_id AND e.profile_id=NEW.profile_id "
            "AND e.stream_key=NEW.stream_key AND e.sequence=NEW.sequence-1 "
            "AND e.event_digest=NEW.previous_event_digest) "
            "THEN RAISE(ABORT,'knowledge serving event predecessor invalid') END; END"
        )
        return
    if dialect in {"mysql", "mariadb"}:
        for table in IMMUTABLE_TABLES:
            for operation in ("UPDATE", "DELETE"):
                name = f"trg_{table}_no_{operation.casefold()}"
                op.execute(
                    f"CREATE TRIGGER {name} BEFORE {operation} ON {table} "
                    "FOR EACH ROW SIGNAL SQLSTATE '45000' "
                    "SET MESSAGE_TEXT='knowledge serving immutable authority'"
                )
        op.execute(
            f"CREATE TRIGGER {EVENT_INSERT_TRIGGER} BEFORE INSERT ON {EVENT_TABLE} FOR EACH ROW BEGIN "
            "IF NEW.sequence=1 AND (NEW.event_type<>'profile_created' "
            "OR NEW.previous_event_digest IS NOT NULL) THEN SIGNAL SQLSTATE '45000' "
            "SET MESSAGE_TEXT='knowledge serving first event invalid'; END IF; "
            "IF NEW.sequence>1 AND (NEW.previous_event_digest IS NULL OR NOT EXISTS "
            f"(SELECT 1 FROM {EVENT_TABLE} e WHERE e.tenant_id=NEW.tenant_id "
            "AND e.profile_id=NEW.profile_id AND e.stream_key=NEW.stream_key "
            "AND e.sequence=NEW.sequence-1 AND e.event_digest=NEW.previous_event_digest)) "
            "THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='knowledge serving event predecessor invalid'; END IF; END"
        )
        return
    if dialect == "postgresql":
        op.execute(
            f"CREATE FUNCTION {IMMUTABLE_FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN RAISE EXCEPTION 'knowledge serving immutable authority'; END $$"
        )
        for table in IMMUTABLE_TABLES:
            for operation in ("UPDATE", "DELETE"):
                name = f"trg_{table}_no_{operation.casefold()}"
                op.execute(
                    f"CREATE TRIGGER {name} BEFORE {operation} ON {table} "
                    f"FOR EACH ROW EXECUTE FUNCTION {IMMUTABLE_FUNCTION}()"
                )
        op.execute(
            f"CREATE FUNCTION {EVENT_VALIDATE_FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN IF NEW.sequence=1 THEN IF NEW.event_type<>'profile_created' "
            "OR NEW.previous_event_digest IS NOT NULL THEN RAISE EXCEPTION 'knowledge serving first event invalid'; "
            "END IF; ELSE IF NEW.previous_event_digest IS NULL OR NOT EXISTS "
            f"(SELECT 1 FROM {EVENT_TABLE} e WHERE e.tenant_id=NEW.tenant_id "
            "AND e.profile_id=NEW.profile_id AND e.stream_key=NEW.stream_key "
            "AND e.sequence=NEW.sequence-1 AND e.event_digest=NEW.previous_event_digest) "
            "THEN RAISE EXCEPTION 'knowledge serving event predecessor invalid'; END IF; "
            "END IF; RETURN NEW; END $$"
        )
        op.execute(
            f"CREATE TRIGGER {EVENT_INSERT_TRIGGER} BEFORE INSERT ON {EVENT_TABLE} "
            f"FOR EACH ROW EXECUTE FUNCTION {EVENT_VALIDATE_FUNCTION}()"
        )


def _drop_guards() -> None:
    # 目标名是本迁移头部定义/派生的常量（IMMUTABLE_TABLES = TABLES[1:] 按
    # 「trg_{表}_no_{update|delete}」规则创建；EVENT_INSERT_TRIGGER / EVENT_TABLE /
    # EVENT_VALIDATE_FUNCTION / IMMUTABLE_FUNCTION 同在头部定义）。DROP 守卫按安全
    # 扫描要求写成完整字面量；迁移是冻结产物，这些名字不会再变。
    dialect = _require_supported_dialect()
    if dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_tenant_knowledge_serving_policy_revisions_no_update"
            " ON tenant_knowledge_serving_policy_revisions"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_tenant_knowledge_serving_policy_revisions_no_delete"
            " ON tenant_knowledge_serving_policy_revisions"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_tenant_knowledge_serving_snapshots_no_update"
            " ON tenant_knowledge_serving_snapshots"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_tenant_knowledge_serving_snapshots_no_delete"
            " ON tenant_knowledge_serving_snapshots"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_tenant_knowledge_serving_stage_facts_no_update"
            " ON tenant_knowledge_serving_stage_facts"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_tenant_knowledge_serving_stage_facts_no_delete"
            " ON tenant_knowledge_serving_stage_facts"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_tenant_knowledge_serving_evidence_links_no_update"
            " ON tenant_knowledge_serving_evidence_links"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_tenant_knowledge_serving_evidence_links_no_delete"
            " ON tenant_knowledge_serving_evidence_links"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_tenant_knowledge_serving_events_no_update"
            " ON tenant_knowledge_serving_events"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_tenant_knowledge_serving_events_no_delete"
            " ON tenant_knowledge_serving_events"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_tenant_knowledge_serving_events_validate_insert"
            " ON tenant_knowledge_serving_events"
        )
        op.execute("DROP FUNCTION IF EXISTS rag4c_knowledge_serving_event_validate()")
        op.execute("DROP FUNCTION IF EXISTS rag4c_knowledge_serving_immutable()")
    else:
        op.execute("DROP TRIGGER IF EXISTS trg_tenant_knowledge_serving_policy_revisions_no_update")
        op.execute("DROP TRIGGER IF EXISTS trg_tenant_knowledge_serving_policy_revisions_no_delete")
        op.execute("DROP TRIGGER IF EXISTS trg_tenant_knowledge_serving_snapshots_no_update")
        op.execute("DROP TRIGGER IF EXISTS trg_tenant_knowledge_serving_snapshots_no_delete")
        op.execute("DROP TRIGGER IF EXISTS trg_tenant_knowledge_serving_stage_facts_no_update")
        op.execute("DROP TRIGGER IF EXISTS trg_tenant_knowledge_serving_stage_facts_no_delete")
        op.execute("DROP TRIGGER IF EXISTS trg_tenant_knowledge_serving_evidence_links_no_update")
        op.execute("DROP TRIGGER IF EXISTS trg_tenant_knowledge_serving_evidence_links_no_delete")
        op.execute("DROP TRIGGER IF EXISTS trg_tenant_knowledge_serving_events_no_update")
        op.execute("DROP TRIGGER IF EXISTS trg_tenant_knowledge_serving_events_no_delete")
        op.execute("DROP TRIGGER IF EXISTS trg_tenant_knowledge_serving_events_validate_insert")


def _guard_downgrade() -> None:
    bind = op.get_bind()
    counts = {
        table: int(bind.scalar(text(f"SELECT COUNT(*) FROM {table}")) or 0) for table in TABLES
    }
    if any(counts.values()):
        detail = ", ".join(f"{table}={count}" for table, count in counts.items() if count)
        raise RuntimeError(f"0036 downgrade blocked by Knowledge Serving authority: {detail}")


def _profile_foreign_keys(*, include_current_pointers: bool) -> list[sa.ForeignKeyConstraint]:
    result: list[sa.ForeignKeyConstraint] = [
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_knowledge_serving_profiles_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["tenant_workspaces.tenant_id", "tenant_workspaces.id"],
            name="fk_tenant_knowledge_serving_profiles_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name="fk_tenant_knowledge_serving_profiles_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_knowledge_serving_profiles_creator",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_knowledge_serving_profiles_updater",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "archived_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_knowledge_serving_profiles_archiver",
        ),
    ]
    if include_current_pointers:
        result.extend(
            [
                sa.ForeignKeyConstraint(
                    ["tenant_id", "id", "current_policy_revision_id"],
                    [
                        "tenant_knowledge_serving_policy_revisions.tenant_id",
                        "tenant_knowledge_serving_policy_revisions.profile_id",
                        "tenant_knowledge_serving_policy_revisions.id",
                    ],
                    name="fk_tenant_knowledge_serving_profiles_current_policy",
                ),
                sa.ForeignKeyConstraint(
                    ["tenant_id", "id", "current_snapshot_id"],
                    [
                        "tenant_knowledge_serving_snapshots.tenant_id",
                        "tenant_knowledge_serving_snapshots.profile_id",
                        "tenant_knowledge_serving_snapshots.id",
                    ],
                    name="fk_tenant_knowledge_serving_profiles_current_snapshot",
                ),
            ]
        )
    return result


def upgrade() -> None:
    _is_mysql = op.get_bind().dialect.name == "mysql"
    dialect = _require_supported_dialect()
    if context.is_offline_mode() and dialect == "sqlite":
        raise RuntimeError("0036 SQLite offline upgrade is unsupported; use online migration")

    op.create_table(
        "tenant_knowledge_serving_profiles",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=True),
        sa.Column("dataset_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("normalized_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("active_profile_key", sa.String(128), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("current_policy_revision_id", sa.String(64), nullable=True),
        sa.Column("current_snapshot_id", sa.String(64), nullable=True),
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
            _in("status", PROFILE_STATUSES), name="ck_tenant_knowledge_serving_profiles_status"
        ),
        sa.CheckConstraint("revision > 0", name="ck_tenant_knowledge_serving_profiles_revision"),
        sa.CheckConstraint(
            "active_profile_key IS NULL OR active_profile_key=dataset_id",
            name="ck_tenant_knowledge_serving_profiles_active_identity",
        ),
        sa.CheckConstraint(
            "(status='active' AND active_profile_key=dataset_id AND current_policy_revision_id IS NOT NULL) OR (status<>'active' AND active_profile_key IS NULL)",
            name="ck_tenant_knowledge_serving_profiles_active_policy",
        ),
        sa.CheckConstraint(
            "(status='archived' AND active_profile_key IS NULL AND archived_at IS NOT NULL AND archived_by IS NOT NULL) OR (status<>'archived' AND archived_at IS NULL AND archived_by IS NULL)",
            name="ck_tenant_knowledge_serving_profiles_lifecycle",
        ),
        *_profile_foreign_keys(include_current_pointers=dialect == "sqlite"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_tenant_knowledge_serving_profiles_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "dataset_id", name="uq_tenant_knowledge_serving_profiles_dataset_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "active_profile_key",
            name="uq_tenant_knowledge_serving_profiles_active_key",
        ),
    )
    op.create_index(
        "ix_tenant_knowledge_serving_profiles_tenant_status_updated",
        "tenant_knowledge_serving_profiles",
        ["tenant_id", "status", "updated_at", "id"],
    )

    op.create_table(
        "tenant_knowledge_serving_policy_revisions",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("max_source_staleness_seconds", sa.Integer(), nullable=False),
        sa.Column("max_parse_lag_seconds", sa.Integer(), nullable=False),
        sa.Column("max_index_lag_seconds", sa.Integer(), nullable=False),
        sa.Column("max_failed_document_count", sa.Integer(), nullable=False),
        sa.Column("max_pending_index_count", sa.Integer(), nullable=False),
        sa.Column(
            "require_current_release", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "require_passing_certification", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("policy_digest", sa.String(64), nullable=False),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "revision > 0 AND max_source_staleness_seconds >= 0 AND max_parse_lag_seconds >= 0 AND max_index_lag_seconds >= 0 AND max_failed_document_count >= 0 AND max_pending_index_count >= 0",
            name="ck_tenant_knowledge_serving_policy_revisions_thresholds",
        ),
        sa.CheckConstraint(
            "length(policy_digest)=64 AND lower(policy_digest)=policy_digest",
            name="ck_tenant_knowledge_serving_policy_revisions_digest",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "profile_id"],
            ["tenant_knowledge_serving_profiles.tenant_id", "tenant_knowledge_serving_profiles.id"],
            name="fk_tenant_knowledge_serving_policy_revisions_profile",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_knowledge_serving_policy_revisions_creator",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_tenant_knowledge_serving_policy_revisions_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "profile_id",
            "id",
            name="uq_tenant_knowledge_serving_policy_revisions_profile_scope",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "profile_id",
            "revision",
            name="uq_tenant_knowledge_serving_policy_revisions_profile_revision",
        ),
    )
    op.create_index(
        "ix_tenant_knowledge_serving_policy_revisions_profile_created",
        "tenant_knowledge_serving_policy_revisions",
        ["tenant_id", "profile_id", "created_at", "id"],
    )

    op.create_table(
        "tenant_knowledge_serving_snapshots",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("policy_revision_id", sa.String(64), nullable=False),
        sa.Column("observation_key", sa.String(192), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column("ready_source_count", sa.Integer(), nullable=False),
        sa.Column("stale_source_count", sa.Integer(), nullable=False),
        sa.Column("active_document_count", sa.Integer(), nullable=False),
        sa.Column("failed_document_count", sa.Integer(), nullable=False),
        sa.Column("pending_index_count", sa.Integer(), nullable=False),
        sa.Column("expected_serving_generation", sa.Integer(), nullable=False),
        sa.Column("observed_serving_generation", sa.Integer(), nullable=False),
        sa.Column("current_release_id", sa.String(64), nullable=True),
        sa.Column("current_certification_id", sa.String(64), nullable=True),
        sa.Column("stage_count", sa.Integer(), nullable=False),
        sa.Column("ready_stage_count", sa.Integer(), nullable=False),
        sa.Column("blocked_stage_count", sa.Integer(), nullable=False),
        sa.Column("snapshot_digest", sa.String(64), nullable=False),
        sa.Column("as_of", _datetime6(), nullable=False),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.CheckConstraint(
            _in("state", OVERALL_STATES), name="ck_tenant_knowledge_serving_snapshots_state"
        ),
        sa.CheckConstraint(
            "source_count >= 0 AND ready_source_count >= 0 AND stale_source_count >= 0 AND active_document_count >= 0 AND failed_document_count >= 0 AND pending_index_count >= 0 AND expected_serving_generation >= 0 AND observed_serving_generation >= 0 AND stage_count=5 AND ready_stage_count BETWEEN 0 AND 5 AND blocked_stage_count BETWEEN 0 AND 5 AND ready_source_count <= source_count AND stale_source_count <= source_count",
            name="ck_tenant_knowledge_serving_snapshots_counts",
        ),
        sa.CheckConstraint(
            "length(snapshot_digest)=64 AND lower(snapshot_digest)=snapshot_digest",
            name="ck_tenant_knowledge_serving_snapshots_digest",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "profile_id"],
            ["tenant_knowledge_serving_profiles.tenant_id", "tenant_knowledge_serving_profiles.id"],
            name="fk_tenant_knowledge_serving_snapshots_profile",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "profile_id", "policy_revision_id"],
            [
                "tenant_knowledge_serving_policy_revisions.tenant_id",
                "tenant_knowledge_serving_policy_revisions.profile_id",
                "tenant_knowledge_serving_policy_revisions.id",
            ],
            name="fk_tenant_knowledge_serving_snapshots_policy",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "current_release_id"],
            ["dataset_release_manifests.tenant_id", "dataset_release_manifests.id"],
            name="fk_tenant_knowledge_serving_snapshots_release",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "current_certification_id"],
            [
                "dataset_release_quality_certifications.tenant_id",
                "dataset_release_quality_certifications.id",
            ],
            name="fk_tenant_knowledge_serving_snapshots_certification",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_knowledge_serving_snapshots_creator",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_tenant_knowledge_serving_snapshots_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "profile_id",
            "id",
            name="uq_tenant_knowledge_serving_snapshots_profile_scope",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "profile_id",
            "observation_key",
            name="uq_tenant_knowledge_serving_snapshots_observation",
        ),
        sa.UniqueConstraint(
            "tenant_id", "snapshot_digest", name="uq_tenant_knowledge_serving_snapshots_digest"
        ),
    )
    op.create_index(
        "ix_tenant_knowledge_serving_snapshots_profile_as_of",
        "tenant_knowledge_serving_snapshots",
        ["tenant_id", "profile_id", "as_of", "id"],
    )

    op.create_table(
        "tenant_knowledge_serving_stage_facts",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("snapshot_id", sa.String(64), nullable=False),
        sa.Column("stage_code", sa.String(16), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("ready_count", sa.Integer(), nullable=False),
        sa.Column("warning_count", sa.Integer(), nullable=False),
        sa.Column("pending_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("lag_seconds", sa.Integer(), nullable=False),
        sa.Column("expected_revision", sa.Integer(), nullable=True),
        sa.Column("observed_revision", sa.Integer(), nullable=True),
        sa.Column("expected_digest", sa.String(64), nullable=True),
        sa.Column("observed_digest", sa.String(64), nullable=True),
        sa.Column("safe_error_code", sa.String(128), nullable=True),
        sa.Column("safe_error", sa.String(512), nullable=True),
        sa.Column("stage_digest", sa.String(64), nullable=False),
        sa.Column("observed_at", _datetime6(), nullable=False),
        sa.CheckConstraint(
            "(stage_code='source' AND sequence=1) OR (stage_code='parse' AND sequence=2) OR (stage_code='chunk' AND sequence=3) OR (stage_code='index' AND sequence=4) OR (stage_code='serve' AND sequence=5)",
            name="ck_tenant_knowledge_serving_stage_facts_stage",
        ),
        sa.CheckConstraint(
            "sequence BETWEEN 1 AND 5", name="ck_tenant_knowledge_serving_stage_facts_sequence"
        ),
        sa.CheckConstraint(
            _in("state", STAGE_STATES), name="ck_tenant_knowledge_serving_stage_facts_state"
        ),
        sa.CheckConstraint(
            "item_count >= 0 AND ready_count >= 0 AND warning_count >= 0 AND pending_count >= 0 AND error_count >= 0 AND ready_count <= item_count AND warning_count <= item_count AND pending_count <= item_count AND error_count <= item_count AND lag_seconds >= 0 AND (expected_revision IS NULL OR expected_revision > 0) AND (observed_revision IS NULL OR observed_revision > 0)",
            name="ck_tenant_knowledge_serving_stage_facts_counts",
        ),
        sa.CheckConstraint(
            "(safe_error_code IS NULL AND safe_error IS NULL) OR (safe_error_code IS NOT NULL AND safe_error IS NOT NULL)",
            name="ck_tenant_knowledge_serving_stage_facts_error",
        ),
        sa.CheckConstraint(
            "length(stage_digest)=64 AND lower(stage_digest)=stage_digest AND (expected_digest IS NULL OR (length(expected_digest)=64 AND lower(expected_digest)=expected_digest)) AND (observed_digest IS NULL OR (length(observed_digest)=64 AND lower(observed_digest)=observed_digest))",
            name="ck_tenant_knowledge_serving_stage_facts_digest",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "profile_id"],
            ["tenant_knowledge_serving_profiles.tenant_id", "tenant_knowledge_serving_profiles.id"],
            name="fk_tenant_knowledge_serving_stage_facts_profile",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "profile_id", "snapshot_id"],
            [
                "tenant_knowledge_serving_snapshots.tenant_id",
                "tenant_knowledge_serving_snapshots.profile_id",
                "tenant_knowledge_serving_snapshots.id",
            ],
            name="fk_tenant_knowledge_serving_stage_facts_snapshot",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_tenant_knowledge_serving_stage_facts_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "profile_id",
            "snapshot_id",
            "id",
            name="uq_tenant_knowledge_serving_stage_facts_profile_snapshot_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "snapshot_id",
            "stage_code",
            name="uq_tenant_knowledge_serving_stage_facts_stage",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "snapshot_id",
            "sequence",
            name="uq_tenant_knowledge_serving_stage_facts_sequence",
        ),
    )
    op.create_index(
        "ix_tenant_knowledge_serving_stage_facts_snapshot_sequence",
        "tenant_knowledge_serving_stage_facts",
        ["tenant_id", "snapshot_id", "sequence", "id"],
    )

    op.create_table(
        "tenant_knowledge_serving_evidence_links",
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("snapshot_id", sa.String(64), nullable=False),
        sa.Column("stage_fact_id", sa.String(64), nullable=False),
        sa.Column("evidence_kind", sa.String(32), nullable=False),
        sa.Column("resource_id", sa.String(128), nullable=False),
        sa.Column("resource_revision", sa.Integer(), nullable=True),
        sa.Column("resource_digest", sa.String(64), nullable=True),
        sa.Column("route_code", sa.String(64), nullable=False),
        sa.Column("safe_label", sa.String(256), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column(
            "created_at", _datetime6(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)") if _is_mysql else sa.text("CURRENT_TIMESTAMP")
        ),
        sa.CheckConstraint(
            _in("evidence_kind", EVIDENCE_KINDS),
            name="ck_tenant_knowledge_serving_evidence_links_kind",
        ),
        sa.CheckConstraint(
            _in("route_code", ROUTE_CODES), name="ck_tenant_knowledge_serving_evidence_links_route"
        ),
        sa.CheckConstraint(
            _evidence_kind_route_check(),
            name="ck_tenant_knowledge_serving_evidence_links_kind_route",
        ),
        sa.CheckConstraint(
            "resource_id <> '' AND (resource_revision IS NULL OR resource_revision >= 1) AND (resource_digest IS NULL OR (length(resource_digest)=64 AND lower(resource_digest)=resource_digest)) AND length(evidence_digest)=64 AND lower(evidence_digest)=evidence_digest",
            name="ck_tenant_knowledge_serving_evidence_links_digest",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "profile_id"],
            ["tenant_knowledge_serving_profiles.tenant_id", "tenant_knowledge_serving_profiles.id"],
            name="fk_tenant_knowledge_serving_evidence_links_profile",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "profile_id", "snapshot_id"],
            [
                "tenant_knowledge_serving_snapshots.tenant_id",
                "tenant_knowledge_serving_snapshots.profile_id",
                "tenant_knowledge_serving_snapshots.id",
            ],
            name="fk_tenant_knowledge_serving_evidence_links_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "profile_id", "snapshot_id", "stage_fact_id"],
            [
                "tenant_knowledge_serving_stage_facts.tenant_id",
                "tenant_knowledge_serving_stage_facts.profile_id",
                "tenant_knowledge_serving_stage_facts.snapshot_id",
                "tenant_knowledge_serving_stage_facts.id",
            ],
            name="fk_tenant_knowledge_serving_evidence_links_stage_fact",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_tenant_knowledge_serving_evidence_links_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "evidence_digest", name="uq_tenant_knowledge_serving_evidence_links_digest"
        ),
    )
    op.create_index(
        "ix_tenant_knowledge_serving_evidence_links_snapshot_kind",
        "tenant_knowledge_serving_evidence_links",
        ["tenant_id", "snapshot_id", "evidence_kind", "id"],
    )

    op.create_table(
        EVENT_TABLE,
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("snapshot_id", sa.String(64), nullable=True),
        sa.Column("stream_key", sa.String(128), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("previous_event_digest", sa.String(64), nullable=True),
        sa.Column("event_digest", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("safe_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("occurred_at", _datetime6(), nullable=False),
        sa.CheckConstraint(
            _in("event_type", EVENT_TYPES), name="ck_tenant_knowledge_serving_events_type"
        ),
        sa.CheckConstraint("sequence > 0", name="ck_tenant_knowledge_serving_events_sequence"),
        sa.CheckConstraint(
            "length(event_digest)=64 AND lower(event_digest)=event_digest AND (previous_event_digest IS NULL OR (length(previous_event_digest)=64 AND lower(previous_event_digest)=previous_event_digest))",
            name="ck_tenant_knowledge_serving_events_digest",
        ),
        sa.CheckConstraint(
            "(sequence=1 AND previous_event_digest IS NULL) OR (sequence>1 AND previous_event_digest IS NOT NULL)",
            name="ck_tenant_knowledge_serving_events_hash_chain",
        ),
        sa.CheckConstraint(
            "safe_snapshot_json IS NOT NULL", name="ck_tenant_knowledge_serving_events_snapshot"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "profile_id"],
            ["tenant_knowledge_serving_profiles.tenant_id", "tenant_knowledge_serving_profiles.id"],
            name="fk_tenant_knowledge_serving_events_profile",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "profile_id", "snapshot_id"],
            [
                "tenant_knowledge_serving_snapshots.tenant_id",
                "tenant_knowledge_serving_snapshots.profile_id",
                "tenant_knowledge_serving_snapshots.id",
            ],
            name="fk_tenant_knowledge_serving_events_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "actor_id"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name="fk_tenant_knowledge_serving_events_actor",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tenant_knowledge_serving_events_scope_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "stream_key",
            "sequence",
            name="uq_tenant_knowledge_serving_events_stream_sequence",
        ),
        sa.UniqueConstraint(
            "tenant_id", "event_digest", name="uq_tenant_knowledge_serving_events_digest"
        ),
    )
    op.create_index(
        "ix_tenant_knowledge_serving_events_profile_time",
        EVENT_TABLE,
        ["tenant_id", "profile_id", "occurred_at", "id"],
    )

    if dialect != "sqlite":
        op.create_foreign_key(
            "fk_tenant_knowledge_serving_profiles_current_policy",
            "tenant_knowledge_serving_profiles",
            "tenant_knowledge_serving_policy_revisions",
            ["tenant_id", "id", "current_policy_revision_id"],
            ["tenant_id", "profile_id", "id"],
        )
        op.create_foreign_key(
            "fk_tenant_knowledge_serving_profiles_current_snapshot",
            "tenant_knowledge_serving_profiles",
            "tenant_knowledge_serving_snapshots",
            ["tenant_id", "id", "current_snapshot_id"],
            ["tenant_id", "profile_id", "id"],
        )
    _create_guards()


def downgrade() -> None:
    dialect = _require_supported_dialect()
    if context.is_offline_mode():
        raise RuntimeError("0036 downgrade requires online preflight")
    _guard_downgrade()
    _drop_guards()
    if dialect != "sqlite":
        op.drop_constraint(
            "fk_tenant_knowledge_serving_profiles_current_snapshot",
            "tenant_knowledge_serving_profiles",
            type_="foreignkey",
        )
        op.drop_constraint(
            "fk_tenant_knowledge_serving_profiles_current_policy",
            "tenant_knowledge_serving_profiles",
            type_="foreignkey",
        )
    for name, table in (
        ("ix_tenant_knowledge_serving_events_profile_time", "tenant_knowledge_serving_events"),
        (
            "ix_tenant_knowledge_serving_evidence_links_snapshot_kind",
            "tenant_knowledge_serving_evidence_links",
        ),
        (
            "ix_tenant_knowledge_serving_stage_facts_snapshot_sequence",
            "tenant_knowledge_serving_stage_facts",
        ),
        (
            "ix_tenant_knowledge_serving_snapshots_profile_as_of",
            "tenant_knowledge_serving_snapshots",
        ),
        (
            "ix_tenant_knowledge_serving_policy_revisions_profile_created",
            "tenant_knowledge_serving_policy_revisions",
        ),
        (
            "ix_tenant_knowledge_serving_profiles_tenant_status_updated",
            "tenant_knowledge_serving_profiles",
        ),
    ):
        op.drop_index(name, table_name=table)
    for table in reversed(TABLES):
        op.drop_table(table)
