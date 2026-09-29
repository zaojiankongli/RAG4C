"""Add Tenant-scoped Enterprise Knowledge Operations & Feedback authority.

Revision ID: 0037_enterprise_knowledge_operations_feedback
Revises: 0036_enterprise_knowledge_serving_reliability
Create Date: 2026-09-10
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import mysql

revision: str = "0037_enterprise_knowledge_operations_feedback"
down_revision: str | None = "0036_enterprise_knowledge_serving_reliability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROFILE_TABLE = "tenant_knowledge_operations_profiles"
SESSION_TABLE = "tenant_knowledge_conversation_sessions"
QUERY_TABLE = "tenant_knowledge_query_facts"
FEEDBACK_TABLE = "tenant_knowledge_feedback_facts"
CASE_TABLE = "tenant_knowledge_review_cases"
EVENT_TABLE = "tenant_knowledge_review_events"
CANDIDATE_TABLE = "tenant_knowledge_improvement_candidates"

TABLES = (
    PROFILE_TABLE,
    SESSION_TABLE,
    QUERY_TABLE,
    FEEDBACK_TABLE,
    CASE_TABLE,
    EVENT_TABLE,
    CANDIDATE_TABLE,
)
IMMUTABLE_TABLES = (QUERY_TABLE, FEEDBACK_TABLE, EVENT_TABLE)

PROFILE_STATUSES = ("draft", "active", "paused", "archived")
CHANNEL_CODES = ("web", "api", "wecom", "dingtalk", "custom")
ROUTE_CODES = ("rag", "cache", "fallback", "abstain", "changed")
OUTCOME_CODES = ("answered", "abstained", "cancelled", "failed", "knowledge_changed")
FEEDBACK_KINDS = ("helpful", "unhelpful", "correction", "unsafe", "incomplete")
FEEDBACK_SOURCES = ("explicit", "operator", "policy", "implicit")
FEEDBACK_REASONS = (
    "wrong_answer",
    "no_citation",
    "outdated",
    "incomplete",
    "unsafe",
    "refused",
    "latency",
    "other",
)
CASE_PRIORITIES = ("low", "medium", "high", "critical")
CASE_ISSUE_TYPES = (
    "no_recall",
    "weak_recall",
    "citation_gap",
    "wrong_answer",
    "outdated_knowledge",
    "unsafe_answer",
    "refused",
    "latency",
)
CASE_STATUSES = ("open", "triaged", "investigating", "resolved", "dismissed")
CASE_RESOLUTION_CODES = ("knowledge_fixed", "not_a_defect", "duplicate", "wont_fix", "external")
EVENT_TYPES = (
    "case_created",
    "triaged",
    "assigned",
    "status_changed",
    "candidate_linked",
    "resolved",
    "dismissed",
)
CANDIDATE_TYPES = (
    "qa_gap",
    "document_gap",
    "source_gap",
    "retrieval_tuning",
    "citation_policy",
    "refusal_policy",
)
CANDIDATE_STATUSES = ("proposed", "accepted", "rejected", "converted", "archived")
CANDIDATE_TARGET_ROUTES = (
    "knowledge_documents",
    "qa_knowledge",
    "knowledge_sources",
    "retrieval_profile",
    "release_quality",
    "enterprise_tasks",
)

EVENT_INSERT_TRIGGER = "trg_tenant_knowledge_review_events_validate_insert"
IMMUTABLE_FUNCTION = "rag4c_knowledge_operations_immutable"
EVENT_VALIDATE_FUNCTION = "rag4c_knowledge_operations_event_validate"
SUPPORTED_DIALECTS = frozenset({"sqlite", "mysql", "mariadb", "postgresql"})


def _datetime6():
    return (
        sa.DateTime()
        .with_variant(mysql.DATETIME(fsp=6), "mysql")
        .with_variant(mysql.DATETIME(fsp=6), "mariadb")
    )


def _created_at(is_mysql: bool):
    return sa.Column(
        "created_at",
        _datetime6(),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP(6)") if is_mysql else sa.text("CURRENT_TIMESTAMP"),
    )


def _dialect_name() -> str:
    return str(op.get_context().dialect.name).casefold()


def _require_supported_dialect() -> str:
    dialect = _dialect_name()
    if dialect not in SUPPORTED_DIALECTS:
        raise RuntimeError(f"0037 unsupported database dialect: {dialect}")
    return dialect


def _quoted(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({_quoted(values)})"


def _digest(column: str) -> str:
    return f"length({column})=64 AND lower({column})={column}"


def _optional_digest(column: str) -> str:
    return f"({column} IS NULL OR ({_digest(column)}))"


def _create_guards() -> None:
    dialect = _require_supported_dialect()
    immutable_message = "knowledge operations immutable authority"
    if dialect == "sqlite":
        for table in IMMUTABLE_TABLES:
            for operation in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER trg_{table}_no_{operation.casefold()} "
                    f"BEFORE {operation} ON {table} BEGIN "
                    f"SELECT RAISE(ABORT, '{immutable_message}'); END"
                )
        op.execute(
            f"CREATE TRIGGER {EVENT_INSERT_TRIGGER} BEFORE INSERT ON {EVENT_TABLE} BEGIN "
            "SELECT CASE WHEN NEW.sequence=1 AND NEW.event_type<>'case_created' "
            "THEN RAISE(ABORT,'knowledge operations first event invalid') END; "
            "SELECT CASE WHEN NEW.sequence=1 AND NEW.previous_event_digest IS NOT NULL "
            "THEN RAISE(ABORT,'knowledge operations first previous digest invalid') END; "
            "SELECT CASE WHEN NEW.sequence>1 AND NEW.previous_event_digest IS NULL "
            "THEN RAISE(ABORT,'knowledge operations previous digest required') END; "
            f"SELECT CASE WHEN NEW.sequence>1 AND NOT EXISTS (SELECT 1 FROM {EVENT_TABLE} e "
            "WHERE e.tenant_id=NEW.tenant_id AND e.case_id=NEW.case_id "
            "AND e.sequence=NEW.sequence-1 AND e.event_digest=NEW.previous_event_digest) "
            "THEN RAISE(ABORT,'knowledge operations event predecessor invalid') END; END"
        )
        return
    if dialect in {"mysql", "mariadb"}:
        for table in IMMUTABLE_TABLES:
            for operation in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER trg_{table}_no_{operation.casefold()} "
                    f"BEFORE {operation} ON {table} FOR EACH ROW SIGNAL SQLSTATE '45000' "
                    f"SET MESSAGE_TEXT='{immutable_message}'"
                )
        op.execute(
            f"CREATE TRIGGER {EVENT_INSERT_TRIGGER} BEFORE INSERT ON {EVENT_TABLE} FOR EACH ROW BEGIN "
            "IF NEW.sequence=1 AND (NEW.event_type<>'case_created' "
            "OR NEW.previous_event_digest IS NOT NULL) THEN SIGNAL SQLSTATE '45000' "
            "SET MESSAGE_TEXT='knowledge operations first event invalid'; END IF; "
            "IF NEW.sequence>1 AND (NEW.previous_event_digest IS NULL OR NOT EXISTS "
            f"(SELECT 1 FROM {EVENT_TABLE} e WHERE e.tenant_id=NEW.tenant_id "
            "AND e.case_id=NEW.case_id AND e.sequence=NEW.sequence-1 "
            "AND e.event_digest=NEW.previous_event_digest)) "
            "THEN SIGNAL SQLSTATE '45000' "
            "SET MESSAGE_TEXT='knowledge operations event predecessor invalid'; END IF; END"
        )
        return
    if dialect == "postgresql":
        op.execute(
            f"CREATE FUNCTION {IMMUTABLE_FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $$ "
            f"BEGIN RAISE EXCEPTION '{immutable_message}'; END $$"
        )
        for table in IMMUTABLE_TABLES:
            for operation in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER trg_{table}_no_{operation.casefold()} "
                    f"BEFORE {operation} ON {table} FOR EACH ROW "
                    f"EXECUTE FUNCTION {IMMUTABLE_FUNCTION}()"
                )
        op.execute(
            f"CREATE FUNCTION {EVENT_VALIDATE_FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN IF NEW.sequence=1 THEN IF NEW.event_type<>'case_created' "
            "OR NEW.previous_event_digest IS NOT NULL "
            "THEN RAISE EXCEPTION 'knowledge operations first event invalid'; END IF; "
            "ELSE IF NEW.previous_event_digest IS NULL OR NOT EXISTS "
            f"(SELECT 1 FROM {EVENT_TABLE} e WHERE e.tenant_id=NEW.tenant_id "
            "AND e.case_id=NEW.case_id AND e.sequence=NEW.sequence-1 "
            "AND e.event_digest=NEW.previous_event_digest) "
            "THEN RAISE EXCEPTION 'knowledge operations event predecessor invalid'; END IF; "
            "END IF; RETURN NEW; END $$"
        )
        op.execute(
            f"CREATE TRIGGER {EVENT_INSERT_TRIGGER} BEFORE INSERT ON {EVENT_TABLE} "
            f"FOR EACH ROW EXECUTE FUNCTION {EVENT_VALIDATE_FUNCTION}()"
        )


def _drop_guards() -> None:
    # 目标名是本迁移头部定义的常量（IMMUTABLE_TABLES = (QUERY_TABLE, FEEDBACK_TABLE,
    # EVENT_TABLE) 按「trg_{表}_no_{update|delete}」规则创建；EVENT_INSERT_TRIGGER /
    # EVENT_VALIDATE_FUNCTION / IMMUTABLE_FUNCTION 同在头部定义）。DROP 守卫按安全
    # 扫描要求写成完整字面量；迁移是冻结产物，这些名字不会再变。
    dialect = _require_supported_dialect()
    if dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_tenant_knowledge_query_facts_no_update"
            " ON tenant_knowledge_query_facts"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_tenant_knowledge_query_facts_no_delete"
            " ON tenant_knowledge_query_facts"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_tenant_knowledge_feedback_facts_no_update"
            " ON tenant_knowledge_feedback_facts"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_tenant_knowledge_feedback_facts_no_delete"
            " ON tenant_knowledge_feedback_facts"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_tenant_knowledge_review_events_no_update"
            " ON tenant_knowledge_review_events"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_tenant_knowledge_review_events_no_delete"
            " ON tenant_knowledge_review_events"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_tenant_knowledge_review_events_validate_insert"
            " ON tenant_knowledge_review_events"
        )
        op.execute("DROP FUNCTION IF EXISTS rag4c_knowledge_operations_event_validate()")
        op.execute("DROP FUNCTION IF EXISTS rag4c_knowledge_operations_immutable()")
    else:
        op.execute("DROP TRIGGER IF EXISTS trg_tenant_knowledge_query_facts_no_update")
        op.execute("DROP TRIGGER IF EXISTS trg_tenant_knowledge_query_facts_no_delete")
        op.execute("DROP TRIGGER IF EXISTS trg_tenant_knowledge_feedback_facts_no_update")
        op.execute("DROP TRIGGER IF EXISTS trg_tenant_knowledge_feedback_facts_no_delete")
        op.execute("DROP TRIGGER IF EXISTS trg_tenant_knowledge_review_events_no_update")
        op.execute("DROP TRIGGER IF EXISTS trg_tenant_knowledge_review_events_no_delete")
        op.execute("DROP TRIGGER IF EXISTS trg_tenant_knowledge_review_events_validate_insert")


def _guard_downgrade() -> None:
    bind = op.get_bind()
    counts = {
        table: int(bind.scalar(text(f"SELECT COUNT(*) FROM {table}")) or 0) for table in TABLES
    }
    if any(counts.values()):
        detail = ", ".join(f"{table}={count}" for table, count in counts.items() if count)
        raise RuntimeError(f"0037 downgrade blocked by Knowledge Operations authority: {detail}")


def upgrade() -> None:
    dialect = _require_supported_dialect()
    # mysql.DATETIME(fsp=6) 的 variant 对 mariadb 同样生效（见 _datetime6），
    # 默认值表达式必须同源判定：只认 "mysql" 会让 MariaDB 上的 DATETIME(6) 列
    # 拿到无小数位的 CURRENT_TIMESTAMP，微秒静默丢失，而事件链/digest 定序依赖它。
    _is_mysql = dialect in {"mysql", "mariadb"}
    if context.is_offline_mode() and dialect == "sqlite":
        raise RuntimeError("0037 SQLite offline upgrade is unsupported; use online migration")

    op.create_table(
        PROFILE_TABLE,
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=True),
        sa.Column("dataset_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("retention_days", sa.Integer(), nullable=False, server_default=sa.text("30")),
        sa.Column(
            "sampling_basis_points", sa.Integer(), nullable=False, server_default=sa.text("10000")
        ),
        sa.Column(
            "safe_preview_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("review_sla_minutes", sa.Integer(), nullable=False, server_default=sa.text("60")),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("active_profile_key", sa.String(128), nullable=True),
        _created_at(_is_mysql),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column(
            "updated_at",
            _datetime6(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)")
            if _is_mysql
            else sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("updated_by", sa.String(64), nullable=False),
        sa.Column("archived_at", _datetime6(), nullable=True),
        sa.Column("archived_by", sa.String(64), nullable=True),
        sa.CheckConstraint(
            _in("status", PROFILE_STATUSES), name=f"ck_{PROFILE_TABLE}_status"
        ),
        sa.CheckConstraint("revision > 0", name=f"ck_{PROFILE_TABLE}_revision"),
        sa.CheckConstraint(
            "retention_days BETWEEN 1 AND 3650 AND sampling_basis_points BETWEEN 0 AND 10000 "
            "AND review_sla_minutes BETWEEN 1 AND 10080",
            name=f"ck_{PROFILE_TABLE}_policy",
        ),
        sa.CheckConstraint(
            "active_profile_key IS NULL OR active_profile_key=dataset_id",
            name=f"ck_{PROFILE_TABLE}_active_identity",
        ),
        sa.CheckConstraint(
            "(status='active' AND active_profile_key IS NOT NULL) OR "
            "(status<>'active' AND active_profile_key IS NULL)",
            name=f"ck_{PROFILE_TABLE}_active_key",
        ),
        sa.CheckConstraint(
            "(status='archived' AND archived_at IS NOT NULL AND archived_by IS NOT NULL) OR "
            "(status<>'archived' AND archived_at IS NULL AND archived_by IS NULL)",
            name=f"ck_{PROFILE_TABLE}_lifecycle",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=f"fk_{PROFILE_TABLE}_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["tenant_workspaces.tenant_id", "tenant_workspaces.id"],
            name=f"fk_{PROFILE_TABLE}_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name=f"fk_{PROFILE_TABLE}_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name=f"fk_{PROFILE_TABLE}_creator",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name=f"fk_{PROFILE_TABLE}_updater",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "archived_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name=f"fk_{PROFILE_TABLE}_archiver",
        ),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{PROFILE_TABLE}_scope_id"),
        sa.UniqueConstraint(
            "tenant_id", "id", "dataset_id", name=f"uq_{PROFILE_TABLE}_scope_id_dataset"
        ),
        sa.UniqueConstraint(
            "tenant_id", "dataset_id", name=f"uq_{PROFILE_TABLE}_dataset_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "active_profile_key", name=f"uq_{PROFILE_TABLE}_active_key"
        ),
    )
    op.create_index(
        f"ix_{PROFILE_TABLE}_tenant_status_updated",
        PROFILE_TABLE,
        ["tenant_id", "status", "updated_at", "id"],
    )

    op.create_table(
        SESSION_TABLE,
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("dataset_id", sa.String(64), nullable=False),
        sa.Column("session_key_digest", sa.String(64), nullable=False),
        sa.Column("channel_code", sa.String(16), nullable=False),
        sa.Column("actor_subject_digest", sa.String(64), nullable=True),
        sa.Column("query_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("feedback_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("started_at", _datetime6(), nullable=False),
        sa.Column("last_observed_at", _datetime6(), nullable=False),
        sa.Column("expires_at", _datetime6(), nullable=True),
        sa.Column("session_digest", sa.String(64), nullable=False),
        sa.CheckConstraint(
            _in("channel_code", CHANNEL_CODES), name=f"ck_{SESSION_TABLE}_channel"
        ),
        sa.CheckConstraint(
            f"{_digest('session_key_digest')} AND {_digest('session_digest')} "
            f"AND {_optional_digest('actor_subject_digest')}",
            name=f"ck_{SESSION_TABLE}_digests",
        ),
        sa.CheckConstraint(
            "query_count >= 0 AND feedback_count >= 0",
            name=f"ck_{SESSION_TABLE}_counts",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at>started_at",
            name=f"ck_{SESSION_TABLE}_expiry",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "profile_id"],
            [f"{PROFILE_TABLE}.tenant_id", f"{PROFILE_TABLE}.id"],
            name=f"fk_{SESSION_TABLE}_profile",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "profile_id", "dataset_id"],
            [f"{PROFILE_TABLE}.tenant_id", f"{PROFILE_TABLE}.id", f"{PROFILE_TABLE}.dataset_id"],
            name=f"fk_{SESSION_TABLE}_profile_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name=f"fk_{SESSION_TABLE}_dataset",
        ),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{SESSION_TABLE}_scope_id"),
        sa.UniqueConstraint(
            "tenant_id", "profile_id", "id", name=f"uq_{SESSION_TABLE}_profile_scope"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "profile_id",
            "session_key_digest",
            name=f"uq_{SESSION_TABLE}_session_key",
        ),
    )
    op.create_index(
        f"ix_{SESSION_TABLE}_profile_observed",
        SESSION_TABLE,
        ["tenant_id", "profile_id", "last_observed_at", "id"],
    )

    op.create_table(
        QUERY_TABLE,
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("dataset_id", sa.String(64), nullable=False),
        sa.Column("request_id_digest", sa.String(64), nullable=False),
        sa.Column("query_digest", sa.String(64), nullable=False),
        sa.Column("answer_digest", sa.String(64), nullable=False),
        sa.Column("safe_query_preview", sa.String(160), nullable=True),
        sa.Column("route_code", sa.String(16), nullable=False),
        sa.Column("outcome_code", sa.String(24), nullable=False),
        sa.Column("retrieval_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("citation_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("retrieval_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("generation_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("cached", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("retry_used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "serving_generation", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("trace_digest", sa.String(64), nullable=True),
        sa.Column("fact_digest", sa.String(64), nullable=False),
        sa.Column("observed_at", _datetime6(), nullable=False),
        sa.CheckConstraint(
            _in("route_code", ROUTE_CODES), name=f"ck_{QUERY_TABLE}_route"
        ),
        sa.CheckConstraint(
            _in("outcome_code", OUTCOME_CODES), name=f"ck_{QUERY_TABLE}_outcome"
        ),
        sa.CheckConstraint(
            f"{_digest('request_id_digest')} AND {_digest('query_digest')} "
            f"AND {_digest('answer_digest')} AND {_digest('fact_digest')} "
            f"AND {_optional_digest('trace_digest')}",
            name=f"ck_{QUERY_TABLE}_digests",
        ),
        sa.CheckConstraint(
            "safe_query_preview IS NULL OR length(safe_query_preview)<=160",
            name=f"ck_{QUERY_TABLE}_preview",
        ),
        sa.CheckConstraint(
            "retrieval_count >= 0 AND citation_count >= 0 AND citation_count<=retrieval_count "
            "AND retrieval_ms >= 0 AND generation_ms >= 0 AND total_ms >= 0 "
            "AND serving_generation >= 0",
            name=f"ck_{QUERY_TABLE}_metrics",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "profile_id"],
            [f"{PROFILE_TABLE}.tenant_id", f"{PROFILE_TABLE}.id"],
            name=f"fk_{QUERY_TABLE}_profile",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "profile_id", "dataset_id"],
            [f"{PROFILE_TABLE}.tenant_id", f"{PROFILE_TABLE}.id", f"{PROFILE_TABLE}.dataset_id"],
            name=f"fk_{QUERY_TABLE}_profile_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "profile_id", "session_id"],
            [
                f"{SESSION_TABLE}.tenant_id",
                f"{SESSION_TABLE}.profile_id",
                f"{SESSION_TABLE}.id",
            ],
            name=f"fk_{QUERY_TABLE}_session",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name=f"fk_{QUERY_TABLE}_dataset",
        ),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{QUERY_TABLE}_scope_id"),
        sa.UniqueConstraint(
            "tenant_id", "profile_id", "id", name=f"uq_{QUERY_TABLE}_profile_scope"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "profile_id",
            "session_id",
            "id",
            name=f"uq_{QUERY_TABLE}_profile_session_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "profile_id",
            "dataset_id",
            "id",
            name=f"uq_{QUERY_TABLE}_profile_dataset_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "profile_id",
            "session_id",
            "request_id_digest",
            name=f"uq_{QUERY_TABLE}_request",
        ),
        sa.UniqueConstraint(
            "tenant_id", "profile_id", "fact_digest", name=f"uq_{QUERY_TABLE}_digest"
        ),
    )
    op.create_index(
        f"ix_{QUERY_TABLE}_session_observed",
        QUERY_TABLE,
        ["tenant_id", "profile_id", "session_id", "observed_at", "id"],
    )
    op.create_index(
        f"ix_{QUERY_TABLE}_outcome_observed",
        QUERY_TABLE,
        ["tenant_id", "outcome_code", "observed_at", "id"],
    )

    op.create_table(
        FEEDBACK_TABLE,
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("query_fact_id", sa.String(64), nullable=False),
        sa.Column("feedback_kind", sa.String(16), nullable=False),
        sa.Column("source_code", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(32), nullable=False),
        sa.Column("safe_comment_preview", sa.String(160), nullable=True),
        sa.Column("feedback_digest", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=True),
        sa.Column("observed_at", _datetime6(), nullable=False),
        sa.CheckConstraint(
            _in("feedback_kind", FEEDBACK_KINDS), name=f"ck_{FEEDBACK_TABLE}_kind"
        ),
        sa.CheckConstraint(
            _in("source_code", FEEDBACK_SOURCES), name=f"ck_{FEEDBACK_TABLE}_source"
        ),
        sa.CheckConstraint(
            _in("reason_code", FEEDBACK_REASONS), name=f"ck_{FEEDBACK_TABLE}_reason"
        ),
        sa.CheckConstraint(
            _digest("feedback_digest"), name=f"ck_{FEEDBACK_TABLE}_digest"
        ),
        sa.CheckConstraint(
            "safe_comment_preview IS NULL OR length(safe_comment_preview)<=160",
            name=f"ck_{FEEDBACK_TABLE}_preview",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "profile_id", "session_id", "query_fact_id"],
            [
                f"{QUERY_TABLE}.tenant_id",
                f"{QUERY_TABLE}.profile_id",
                f"{QUERY_TABLE}.session_id",
                f"{QUERY_TABLE}.id",
            ],
            name=f"fk_{FEEDBACK_TABLE}_query",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "actor_id"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name=f"fk_{FEEDBACK_TABLE}_actor",
        ),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{FEEDBACK_TABLE}_scope_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "profile_id",
            "feedback_digest",
            name=f"uq_{FEEDBACK_TABLE}_digest_identity",
        ),
    )
    op.create_index(
        f"ix_{FEEDBACK_TABLE}_query_observed",
        FEEDBACK_TABLE,
        ["tenant_id", "profile_id", "query_fact_id", "observed_at", "id"],
    )

    op.create_table(
        CASE_TABLE,
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("dataset_id", sa.String(64), nullable=False),
        sa.Column("query_fact_id", sa.String(64), nullable=False),
        sa.Column("case_key", sa.String(128), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("issue_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default=sa.text("'open'")),
        sa.Column("assignee_id", sa.String(64), nullable=True),
        sa.Column("due_at", _datetime6(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("resolution_code", sa.String(32), nullable=True),
        sa.Column("safe_resolution_summary", sa.String(280), nullable=True),
        _created_at(_is_mysql),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column(
            "updated_at",
            _datetime6(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)")
            if _is_mysql
            else sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("updated_by", sa.String(64), nullable=False),
        sa.Column("closed_at", _datetime6(), nullable=True),
        sa.Column("closed_by", sa.String(64), nullable=True),
        sa.CheckConstraint(
            _in("priority", CASE_PRIORITIES), name=f"ck_{CASE_TABLE}_priority"
        ),
        sa.CheckConstraint(
            _in("issue_type", CASE_ISSUE_TYPES), name=f"ck_{CASE_TABLE}_issue"
        ),
        sa.CheckConstraint(_in("status", CASE_STATUSES), name=f"ck_{CASE_TABLE}_status"),
        sa.CheckConstraint("revision > 0", name=f"ck_{CASE_TABLE}_revision"),
        sa.CheckConstraint(
            "resolution_code IS NULL OR safe_resolution_summary IS NOT NULL",
            name=f"ck_{CASE_TABLE}_resolution",
        ),
        sa.CheckConstraint(
            "(status IN ('resolved','dismissed') AND closed_at IS NOT NULL "
            "AND closed_by IS NOT NULL) OR "
            "(status NOT IN ('resolved','dismissed') AND closed_at IS NULL "
            "AND closed_by IS NULL)",
            name=f"ck_{CASE_TABLE}_closure",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "profile_id"],
            [f"{PROFILE_TABLE}.tenant_id", f"{PROFILE_TABLE}.id"],
            name=f"fk_{CASE_TABLE}_profile",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "profile_id", "dataset_id"],
            [f"{PROFILE_TABLE}.tenant_id", f"{PROFILE_TABLE}.id", f"{PROFILE_TABLE}.dataset_id"],
            name=f"fk_{CASE_TABLE}_profile_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "profile_id", "dataset_id", "query_fact_id"],
            [
                f"{QUERY_TABLE}.tenant_id",
                f"{QUERY_TABLE}.profile_id",
                f"{QUERY_TABLE}.dataset_id",
                f"{QUERY_TABLE}.id",
            ],
            name=f"fk_{CASE_TABLE}_query",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name=f"fk_{CASE_TABLE}_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "assignee_id"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name=f"fk_{CASE_TABLE}_assignee",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name=f"fk_{CASE_TABLE}_creator",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name=f"fk_{CASE_TABLE}_updater",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "closed_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name=f"fk_{CASE_TABLE}_closer",
        ),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{CASE_TABLE}_scope_id"),
        sa.UniqueConstraint(
            "tenant_id", "profile_id", "id", name=f"uq_{CASE_TABLE}_profile_scope"
        ),
        sa.UniqueConstraint(
            "tenant_id", "profile_id", "case_key", name=f"uq_{CASE_TABLE}_case_key"
        ),
    )
    op.create_index(
        f"ix_{CASE_TABLE}_status_due", CASE_TABLE, ["tenant_id", "status", "due_at", "id"]
    )
    op.create_index(
        f"ix_{CASE_TABLE}_profile_updated",
        CASE_TABLE,
        ["tenant_id", "profile_id", "updated_at", "id"],
    )

    op.create_table(
        EVENT_TABLE,
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("case_id", sa.String(64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("previous_event_digest", sa.String(64), nullable=True),
        sa.Column("event_digest", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("safe_snapshot_json", sa.JSON(), nullable=False),
        sa.Column("occurred_at", _datetime6(), nullable=False),
        sa.CheckConstraint(
            _in("event_type", EVENT_TYPES), name=f"ck_{EVENT_TABLE}_type"
        ),
        sa.CheckConstraint("sequence > 0", name=f"ck_{EVENT_TABLE}_sequence"),
        sa.CheckConstraint(
            f"{_digest('event_digest')} AND {_optional_digest('previous_event_digest')}",
            name=f"ck_{EVENT_TABLE}_digests",
        ),
        sa.CheckConstraint(
            "(sequence=1 AND previous_event_digest IS NULL AND event_type='case_created') OR "
            "(sequence>1 AND previous_event_digest IS NOT NULL AND event_type<>'case_created')",
            name=f"ck_{EVENT_TABLE}_hash_chain",
        ),
        sa.CheckConstraint(
            "safe_snapshot_json IS NOT NULL", name=f"ck_{EVENT_TABLE}_snapshot"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            [f"{CASE_TABLE}.tenant_id", f"{CASE_TABLE}.id"],
            name=f"fk_{EVENT_TABLE}_case",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "actor_id"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name=f"fk_{EVENT_TABLE}_actor",
        ),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{EVENT_TABLE}_scope_id"),
        sa.UniqueConstraint(
            "tenant_id", "case_id", "sequence", name=f"uq_{EVENT_TABLE}_case_sequence"
        ),
        sa.UniqueConstraint(
            "tenant_id", "event_digest", name=f"uq_{EVENT_TABLE}_digest"
        ),
    )
    op.create_index(
        f"ix_{EVENT_TABLE}_case_occurred",
        EVENT_TABLE,
        ["tenant_id", "case_id", "occurred_at", "id"],
    )

    op.create_table(
        CANDIDATE_TABLE,
        sa.Column("id", sa.String(64), nullable=False, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("dataset_id", sa.String(64), nullable=False),
        sa.Column("candidate_key", sa.String(128), nullable=False),
        sa.Column("candidate_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'proposed'")),
        sa.Column("query_cluster_digest", sa.String(64), nullable=False),
        sa.Column(
            "supporting_fact_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "negative_feedback_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("safe_title", sa.String(160), nullable=False),
        sa.Column("safe_summary", sa.String(512), nullable=False),
        sa.Column("linked_case_id", sa.String(64), nullable=True),
        sa.Column("target_route_code", sa.String(64), nullable=False),
        sa.Column("target_resource_id", sa.String(128), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        _created_at(_is_mysql),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column(
            "updated_at",
            _datetime6(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)")
            if _is_mysql
            else sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("updated_by", sa.String(64), nullable=False),
        sa.Column("decided_at", _datetime6(), nullable=True),
        sa.Column("decided_by", sa.String(64), nullable=True),
        sa.CheckConstraint(
            _in("candidate_type", CANDIDATE_TYPES),
            name=f"ck_{CANDIDATE_TABLE}_type",
        ),
        sa.CheckConstraint(
            _in("status", CANDIDATE_STATUSES), name=f"ck_{CANDIDATE_TABLE}_status"
        ),
        sa.CheckConstraint(
            _digest("query_cluster_digest"), name=f"ck_{CANDIDATE_TABLE}_digest"
        ),
        sa.CheckConstraint("revision > 0", name=f"ck_{CANDIDATE_TABLE}_revision"),
        sa.CheckConstraint(
            "supporting_fact_count >= 0 AND negative_feedback_count >= 0 "
            "AND negative_feedback_count<=supporting_fact_count",
            name=f"ck_{CANDIDATE_TABLE}_counts",
        ),
        sa.CheckConstraint(
            "safe_title <> '' AND safe_summary <> '' AND length(safe_title)<=160 "
            "AND length(safe_summary)<=512",
            name=f"ck_{CANDIDATE_TABLE}_text",
        ),
        sa.CheckConstraint(
            _in("target_route_code", CANDIDATE_TARGET_ROUTES),
            name=f"ck_{CANDIDATE_TABLE}_route",
        ),
        sa.CheckConstraint(
            "target_resource_id IS NULL OR target_resource_id <> ''",
            name=f"ck_{CANDIDATE_TABLE}_target",
        ),
        sa.CheckConstraint(
            "(status='proposed' AND decided_at IS NULL AND decided_by IS NULL) OR "
            "(status<>'proposed' AND decided_at IS NOT NULL AND decided_by IS NOT NULL)",
            name=f"ck_{CANDIDATE_TABLE}_decision",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "profile_id"],
            [f"{PROFILE_TABLE}.tenant_id", f"{PROFILE_TABLE}.id"],
            name=f"fk_{CANDIDATE_TABLE}_profile",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "profile_id", "dataset_id"],
            [f"{PROFILE_TABLE}.tenant_id", f"{PROFILE_TABLE}.id", f"{PROFILE_TABLE}.dataset_id"],
            name=f"fk_{CANDIDATE_TABLE}_profile_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["datasets.tenant_id", "datasets.id"],
            name=f"fk_{CANDIDATE_TABLE}_dataset",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "linked_case_id"],
            [f"{CASE_TABLE}.tenant_id", f"{CASE_TABLE}.id"],
            name=f"fk_{CANDIDATE_TABLE}_case",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name=f"fk_{CANDIDATE_TABLE}_creator",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "updated_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name=f"fk_{CANDIDATE_TABLE}_updater",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "decided_by"],
            ["tenant_members.tenant_id", "tenant_members.account_id"],
            name=f"fk_{CANDIDATE_TABLE}_decider",
        ),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{CANDIDATE_TABLE}_scope_id"),
        sa.UniqueConstraint(
            "tenant_id", "profile_id", "id", name=f"uq_{CANDIDATE_TABLE}_profile_scope"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "profile_id",
            "candidate_key",
            name=f"uq_{CANDIDATE_TABLE}_candidate_key",
        ),
    )
    op.create_index(
        f"ix_{CANDIDATE_TABLE}_status_updated",
        CANDIDATE_TABLE,
        ["tenant_id", "status", "updated_at", "id"],
    )
    op.create_index(
        f"ix_{CANDIDATE_TABLE}_profile_type",
        CANDIDATE_TABLE,
        ["tenant_id", "profile_id", "candidate_type", "id"],
    )

    _create_guards()


def downgrade() -> None:
    _require_supported_dialect()
    if context.is_offline_mode():
        raise RuntimeError("0037 downgrade requires online preflight")
    _guard_downgrade()
    _drop_guards()
    for name, table in (
        (f"ix_{CANDIDATE_TABLE}_profile_type", CANDIDATE_TABLE),
        (f"ix_{CANDIDATE_TABLE}_status_updated", CANDIDATE_TABLE),
        (f"ix_{EVENT_TABLE}_case_occurred", EVENT_TABLE),
        (f"ix_{CASE_TABLE}_profile_updated", CASE_TABLE),
        (f"ix_{CASE_TABLE}_status_due", CASE_TABLE),
        (f"ix_{FEEDBACK_TABLE}_query_observed", FEEDBACK_TABLE),
        (f"ix_{QUERY_TABLE}_outcome_observed", QUERY_TABLE),
        (f"ix_{QUERY_TABLE}_session_observed", QUERY_TABLE),
        (f"ix_{SESSION_TABLE}_profile_observed", SESSION_TABLE),
        (f"ix_{PROFILE_TABLE}_tenant_status_updated", PROFILE_TABLE),
    ):
        op.drop_index(name, table_name=table)
    for table in reversed(TABLES):
        op.drop_table(table)
