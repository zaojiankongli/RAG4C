from __future__ import annotations

from sqlalchemy import CheckConstraint, String

from models import orm

TABLE_NAMES = {
    "tenant_knowledge_operations_profiles",
    "tenant_knowledge_conversation_sessions",
    "tenant_knowledge_query_facts",
    "tenant_knowledge_feedback_facts",
    "tenant_knowledge_review_cases",
    "tenant_knowledge_review_events",
    "tenant_knowledge_improvement_candidates",
}


def _fk(table, name: str):
    return next(item for item in table.foreign_key_constraints if item.name == name)


def test_operations_orm_exposes_exactly_seven_tables_and_no_raw_content_columns() -> None:
    tables = {name for name in orm.Base.metadata.tables if name in TABLE_NAMES}
    assert tables == TABLE_NAMES
    protected = {
        "query", "answer", "prompt", "token", "credential", "content",
        "raw_query", "raw_answer", "prompt_text", "access_token",
        "credential_json", "content_text", "content_body",
    }
    for table_name in TABLE_NAMES:
        for column in orm.Base.metadata.tables[table_name].columns:
            assert column.name.casefold() not in protected, (table_name, column.name)


def test_operations_orm_has_tenant_leading_composite_ownership_fks() -> None:
    expected = {
        ("tenant_knowledge_operations_profiles", "fk_tenant_knowledge_operations_profiles_dataset"): (("tenant_id", "dataset_id"), "datasets", ("tenant_id", "id")),
        ("tenant_knowledge_conversation_sessions", "fk_tenant_knowledge_conversation_sessions_profile"): (("tenant_id", "profile_id"), "tenant_knowledge_operations_profiles", ("tenant_id", "id")),
        ("tenant_knowledge_conversation_sessions", "fk_tenant_knowledge_conversation_sessions_dataset"): (("tenant_id", "dataset_id"), "datasets", ("tenant_id", "id")),
        ("tenant_knowledge_query_facts", "fk_tenant_knowledge_query_facts_profile"): (("tenant_id", "profile_id"), "tenant_knowledge_operations_profiles", ("tenant_id", "id")),
        ("tenant_knowledge_query_facts", "fk_tenant_knowledge_query_facts_session"): (("tenant_id", "profile_id", "session_id"), "tenant_knowledge_conversation_sessions", ("tenant_id", "profile_id", "id")),
        ("tenant_knowledge_feedback_facts", "fk_tenant_knowledge_feedback_facts_query"): (("tenant_id", "profile_id", "session_id", "query_fact_id"), "tenant_knowledge_query_facts", ("tenant_id", "profile_id", "session_id", "id")),
        ("tenant_knowledge_review_cases", "fk_tenant_knowledge_review_cases_query"): (("tenant_id", "profile_id", "dataset_id", "query_fact_id"), "tenant_knowledge_query_facts", ("tenant_id", "profile_id", "dataset_id", "id")),
        ("tenant_knowledge_review_events", "fk_tenant_knowledge_review_events_case"): (("tenant_id", "case_id"), "tenant_knowledge_review_cases", ("tenant_id", "id")),
        ("tenant_knowledge_improvement_candidates", "fk_tenant_knowledge_improvement_candidates_case"): (("tenant_id", "linked_case_id"), "tenant_knowledge_review_cases", ("tenant_id", "id")),
    }
    for (table_name, name), (local, remote_table, remote_columns) in expected.items():
        fk = _fk(orm.Base.metadata.tables[table_name], name)
        assert tuple(fk.column_keys) == local
        assert tuple(element.column.table.name for element in fk.elements) == (remote_table,) * len(local)
        assert tuple(element.column.name for element in fk.elements) == remote_columns


def test_operations_orm_contract_checks_are_present() -> None:
    checks = {
        table_name: {
            str(constraint.name): str(constraint.sqltext).casefold()
            for constraint in orm.Base.metadata.tables[table_name].constraints
            if isinstance(constraint, CheckConstraint) and constraint.name
        }
        for table_name in TABLE_NAMES
    }
    assert "channel_code in ('web','api','wecom','dingtalk','custom')" in checks["tenant_knowledge_conversation_sessions"]["ck_tenant_knowledge_conversation_sessions_channel"]
    assert "route_code in ('rag','cache','fallback','abstain','changed')" in checks["tenant_knowledge_query_facts"]["ck_tenant_knowledge_query_facts_route"]
    assert "status in ('open','triaged','investigating','resolved','dismissed')" in checks["tenant_knowledge_review_cases"]["ck_tenant_knowledge_review_cases_status"]
    assert "candidate_type in ('qa_gap','document_gap','source_gap','retrieval_tuning','citation_policy','refusal_policy')" in checks["tenant_knowledge_improvement_candidates"]["ck_tenant_knowledge_improvement_candidates_type"]
    for table_name, check_name in (
        ("tenant_knowledge_conversation_sessions", "ck_tenant_knowledge_conversation_sessions_digests"),
        ("tenant_knowledge_query_facts", "ck_tenant_knowledge_query_facts_digests"),
        ("tenant_knowledge_feedback_facts", "ck_tenant_knowledge_feedback_facts_digest"),
        ("tenant_knowledge_review_events", "ck_tenant_knowledge_review_events_digests"),
    ):
        expression = checks[table_name][check_name]
        assert "length(" in expression and "=64" in expression and "lower(" in expression
    assert isinstance(orm.Base.metadata.tables["tenant_knowledge_query_facts"].c.safe_query_preview.type, String)
    assert orm.Base.metadata.tables["tenant_knowledge_query_facts"].c.safe_query_preview.type.length == 160
    assert orm.Base.metadata.tables["tenant_knowledge_feedback_facts"].c.safe_comment_preview.type.length == 160
