from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from core import catalog_schema as manifest

REVISION = "0037_enterprise_knowledge_operations_feedback"
TABLES = frozenset(
    {
        "tenant_knowledge_operations_profiles",
        "tenant_knowledge_conversation_sessions",
        "tenant_knowledge_query_facts",
        "tenant_knowledge_feedback_facts",
        "tenant_knowledge_review_cases",
        "tenant_knowledge_review_events",
        "tenant_knowledge_improvement_candidates",
    }
)


def _url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _upgrade(url: str) -> None:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")


def test_stage27_manifest_exposes_exact_seven_table_capability() -> None:
    assert manifest.ENTERPRISE_KNOWLEDGE_OPERATIONS_FEEDBACK_REVISION == REVISION
    assert manifest.ENTERPRISE_KNOWLEDGE_OPERATIONS_FEEDBACK_REQUIRED_TABLES == TABLES
    # 0037 之后链条有意推进过（QA/FAQ 线），这里断言"它仍是被注册的链上一环"，
    # 而非"它是全局 head"——后者每次新增迁移都会腐坏。
    assert REVISION in manifest.ENTERPRISE_APPROVAL_CONTROL_REQUIRED_EXACT_CHECK_SQL_BY_REVISION
    assert TABLES <= manifest.HEAD_CATALOG_TABLES
    for table in TABLES:
        assert table in manifest.ENTERPRISE_KNOWLEDGE_OPERATIONS_FEEDBACK_REQUIRED_COLUMNS
        assert table in manifest.ENTERPRISE_KNOWLEDGE_OPERATIONS_FEEDBACK_REQUIRED_NOT_NULL
        assert table in manifest.ENTERPRISE_KNOWLEDGE_OPERATIONS_FEEDBACK_REQUIRED_UNIQUES
        assert table in manifest.ENTERPRISE_KNOWLEDGE_OPERATIONS_FEEDBACK_REQUIRED_FOREIGN_KEYS
        assert table in manifest.ENTERPRISE_KNOWLEDGE_OPERATIONS_FEEDBACK_REQUIRED_CHECK_FRAGMENTS
        assert table in manifest.ENTERPRISE_KNOWLEDGE_OPERATIONS_FEEDBACK_REQUIRED_INDEXES


def test_stage27_empty_authority_is_ready_and_catalog_is_current(tmp_path: Path) -> None:
    url = _url(tmp_path / "stage27-empty.db")
    _upgrade(url)
    engine = create_engine(url)
    try:
        assert manifest.inspect_enterprise_knowledge_operations_feedback_capability(engine) == (
            "ready",
            (),
        )
        assert manifest.inspect_enterprise_knowledge_serving_reliability_capability(engine) == (
            "ready",
            (),
        )
        state = manifest.inspect_catalog_schema(engine)
        # 动态对齐真头：仍完整断言 current，只是不把 head 写死成历史版本号
        assert state.revision == manifest.HEAD_REVISION
        assert state.head_revision == manifest.HEAD_REVISION
        assert state.status == "current"
    finally:
        engine.dispose()


def test_stage27_partial_authority_fails_closed(tmp_path: Path) -> None:
    url = _url(tmp_path / "stage27-partial.db")
    _upgrade(url)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE tenant_knowledge_feedback_facts"))
        state, issues = manifest.inspect_enterprise_knowledge_operations_feedback_capability(engine)
        assert state == "unavailable"
        assert any("tenant_knowledge_feedback_facts" in issue for issue in issues)
    finally:
        engine.dispose()


def test_stage27_unknown_dialect_fails_closed() -> None:
    class UnknownDialect:
        name = "oracle"

    class UnknownBind:
        dialect = UnknownDialect()

    state, issues = manifest.inspect_enterprise_knowledge_operations_feedback_capability(
        UnknownBind()
    )
    assert state == "unavailable"
    assert any("dialect" in issue.lower() for issue in issues)
