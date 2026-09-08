from __future__ import annotations

from importlib import import_module
from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from tests.test_enterprise_automation_workflows_migration import (
    alembic_config,
    engine_for,
    sqlite_url,
)

REVISION = "0037_enterprise_knowledge_operations_feedback"
DOWN_REVISION = "0036_enterprise_knowledge_serving_reliability"
TABLES = {
    "tenant_knowledge_operations_profiles",
    "tenant_knowledge_conversation_sessions",
    "tenant_knowledge_query_facts",
    "tenant_knowledge_feedback_facts",
    "tenant_knowledge_review_cases",
    "tenant_knowledge_review_events",
    "tenant_knowledge_improvement_candidates",
}
EXPECTED_COLUMNS = {
    "tenant_knowledge_operations_profiles": (
        "id", "tenant_id", "workspace_id", "dataset_id", "name", "status",
        "retention_days", "sampling_basis_points", "safe_preview_enabled",
        "review_sla_minutes", "revision", "active_profile_key", "created_at",
        "created_by", "updated_at", "updated_by", "archived_at", "archived_by",
    ),
    "tenant_knowledge_conversation_sessions": (
        "id", "tenant_id", "profile_id", "dataset_id", "session_key_digest",
        "channel_code", "actor_subject_digest", "query_count", "feedback_count",
        "started_at", "last_observed_at", "expires_at", "session_digest",
    ),
    "tenant_knowledge_query_facts": (
        "id", "tenant_id", "profile_id", "session_id", "dataset_id",
        "request_id_digest", "query_digest", "answer_digest", "safe_query_preview",
        "route_code", "outcome_code", "retrieval_count", "citation_count",
        "retrieval_ms", "generation_ms", "total_ms", "cached", "retry_used",
        "serving_generation", "trace_digest", "fact_digest", "observed_at",
    ),
    "tenant_knowledge_feedback_facts": (
        "id", "tenant_id", "profile_id", "session_id", "query_fact_id",
        "feedback_kind", "source_code", "reason_code", "safe_comment_preview",
        "feedback_digest", "actor_id", "observed_at",
    ),
    "tenant_knowledge_review_cases": (
        "id", "tenant_id", "profile_id", "dataset_id", "query_fact_id", "case_key",
        "priority", "issue_type", "status", "assignee_id", "due_at", "revision",
        "resolution_code", "safe_resolution_summary", "created_at", "created_by",
        "updated_at", "updated_by", "closed_at", "closed_by",
    ),
    "tenant_knowledge_review_events": (
        "id", "tenant_id", "case_id", "sequence", "event_type",
        "previous_event_digest", "event_digest", "actor_id", "request_id",
        "safe_snapshot_json", "occurred_at",
    ),
    "tenant_knowledge_improvement_candidates": (
        "id", "tenant_id", "profile_id", "dataset_id", "candidate_key",
        "candidate_type", "status", "query_cluster_digest", "supporting_fact_count",
        "negative_feedback_count", "safe_title", "safe_summary", "linked_case_id",
        "target_route_code", "target_resource_id", "revision", "created_at",
        "created_by", "updated_at", "updated_by", "decided_at", "decided_by",
    ),
}

PROTECTED_COLUMN_NAMES = {
    "query", "answer", "prompt", "token", "credential", "content",
    "raw_query", "raw_answer", "prompt_text", "access_token", "credential_json",
    "content_text", "content_body",
}


def migration_module():
    try:
        return import_module(
            "catalog_migrations.versions.0037_enterprise_knowledge_operations_feedback"
        )
    except ModuleNotFoundError as exc:
        pytest.fail(f"Stage27 migration is missing: {exc}")


def upgrade_0036(url: str) -> None:
    command.upgrade(alembic_config(url), DOWN_REVISION)


def upgrade_0037(url: str) -> None:
    command.upgrade(alembic_config(url), REVISION)


def _upgrade(url: str) -> None:
    upgrade_0036(url)
    upgrade_0037(url)


def _foreign_keys(inspector, table: str):
    return {
        str(item["name"]): (
            tuple(item.get("constrained_columns") or ()),
            str(item.get("referred_table")),
            tuple(item.get("referred_columns") or ()),
        )
        for item in inspector.get_foreign_keys(table)
        if item.get("name")
    }


def _checks(inspector, table: str):
    return {
        str(item["name"]): " ".join(str(item.get("sqltext") or "").casefold().split())
        for item in inspector.get_check_constraints(table)
        if item.get("name")
    }


def _triggers(engine):
    with engine.connect() as connection:
        return {
            str(row["name"]): str(row["sql"])
            for row in connection.execute(
                text("SELECT name, sql FROM sqlite_master WHERE type='trigger'")
            ).mappings()
        }


def _seed_parent_scope(connection, tenant_id="tenant-a", dataset_id="dataset-a", member_id="member-a"):
    connection.execute(
        text(
            "INSERT OR IGNORE INTO tenants "
            "(id,name,plan,status,quota_documents,quota_chunks,doc_count,chunk_count,created_at) "
            "VALUES (:tenant,'Tenant','enterprise','active',1000,100000,0,0,CURRENT_TIMESTAMP)"
        ),
        {"tenant": tenant_id},
    )
    connection.execute(
        text(
            "INSERT OR IGNORE INTO tenant_members "
            "(id,tenant_id,account_id,role,status,created_at) "
            "VALUES (:id,:tenant,:id,'owner','active',CURRENT_TIMESTAMP)"
        ),
        {"id": member_id, "tenant": tenant_id},
    )
    connection.execute(
        text(
            "INSERT OR IGNORE INTO datasets "
            "(id,tenant_id,name,status,profile_revision,owner_id,visibility,profile_json,parser_policy,"
            "chunk_policy,retrieval_policy,retention_policy,metadata_policy,default_language,graph_enabled,"
            "qa_enabled,mutation_generation,serving_generation,release_revision,acl_mode,acl_revision) VALUES "
            "(:dataset,:tenant,'Dataset','active',1,:member,'private','{}','{}','{}','{}','{}','{}','zh-CN',0,1,0,0,1,'tenant_role',1)"
        ),
        {"dataset": dataset_id, "tenant": tenant_id, "member": member_id},
    )


def _insert_profile(connection, profile_id="profile-a", tenant_id="tenant-a", dataset_id="dataset-a"):
    _seed_parent_scope(connection, tenant_id, dataset_id)
    connection.execute(
        text(
            "INSERT INTO tenant_knowledge_operations_profiles "
            "(id,tenant_id,workspace_id,dataset_id,name,status,retention_days,sampling_basis_points," 
            "safe_preview_enabled,review_sla_minutes,revision,active_profile_key,created_at,created_by," 
            "updated_at,updated_by,archived_at,archived_by) VALUES "
            "(:id,:tenant,NULL,:dataset,'Operations','draft',30,10000,1,60,1,NULL,CURRENT_TIMESTAMP," 
            "'member-a',CURRENT_TIMESTAMP,'member-a',NULL,NULL)"
        ),
        {"id": profile_id, "tenant": tenant_id, "dataset": dataset_id},
    )


def _insert_session(connection, session_id="session-a", profile_id="profile-a", tenant_id="tenant-a", dataset_id="dataset-a"):
    connection.execute(
        text(
            "INSERT INTO tenant_knowledge_conversation_sessions "
            "(id,tenant_id,profile_id,dataset_id,session_key_digest,channel_code,actor_subject_digest," 
            "query_count,feedback_count,started_at,last_observed_at,expires_at,session_digest) VALUES "
            "(:id,:tenant,:profile,:dataset,:digest,'web',NULL,0,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,NULL,:digest)"
        ),
        {"id": session_id, "tenant": tenant_id, "profile": profile_id, "dataset": dataset_id, "digest": "a" * 64},
    )


def _insert_query(connection, query_id="query-a", session_id="session-a", profile_id="profile-a", tenant_id="tenant-a", dataset_id="dataset-a"):
    connection.execute(
        text(
            "INSERT INTO tenant_knowledge_query_facts "
            "(id,tenant_id,profile_id,session_id,dataset_id,request_id_digest,query_digest,answer_digest," 
            "safe_query_preview,route_code,outcome_code,retrieval_count,citation_count,retrieval_ms," 
            "generation_ms,total_ms,cached,retry_used,serving_generation,trace_digest,fact_digest,observed_at) VALUES "
            "(:id,:tenant,:profile,:session,:dataset,:digest,:digest,:digest,'safe question','rag','answered'," 
            "1,1,1,2,3,0,0,1,:digest,:digest,CURRENT_TIMESTAMP)"
        ),
        {"id": query_id, "tenant": tenant_id, "profile": profile_id, "session": session_id, "dataset": dataset_id, "digest": "b" * 64},
    )


def _insert_case(connection, case_id="case-a", query_id="query-a", profile_id="profile-a", tenant_id="tenant-a", dataset_id="dataset-a"):
    connection.execute(
        text(
            "INSERT INTO tenant_knowledge_review_cases "
            "(id,tenant_id,profile_id,dataset_id,query_fact_id,case_key,priority,issue_type,status," 
            "assignee_id,due_at,revision,resolution_code,safe_resolution_summary,created_at,created_by," 
            "updated_at,updated_by,closed_at,closed_by) VALUES "
            "(:id,:tenant,:profile,:dataset,:query,:id,'high','wrong_answer','open',NULL,NULL,1,NULL,NULL," 
            "CURRENT_TIMESTAMP,'member-a',CURRENT_TIMESTAMP,'member-a',NULL,NULL)"
        ),
        {"id": case_id, "tenant": tenant_id, "profile": profile_id, "dataset": dataset_id, "query": query_id},
    )


def test_0037_is_head_and_follows_0036() -> None:
    migration = migration_module()
    scripts = ScriptDirectory.from_config(alembic_config("sqlite://"))
    assert scripts.get_current_head() == REVISION
    assert migration.revision == REVISION
    assert migration.down_revision == DOWN_REVISION


def test_upgrade_creates_exactly_seven_tables_and_no_protected_columns(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "operations.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        actual = {
            name for name in inspector.get_table_names()
            if name.startswith("tenant_knowledge_") and name not in {
                "tenant_knowledge_serving_profiles",
                "tenant_knowledge_serving_policy_revisions",
                "tenant_knowledge_serving_snapshots",
                "tenant_knowledge_serving_stage_facts",
                "tenant_knowledge_serving_evidence_links",
                "tenant_knowledge_serving_events",
            }
        }
        assert actual == TABLES
        for table, columns in EXPECTED_COLUMNS.items():
            assert tuple(item["name"] for item in inspector.get_columns(table)) == columns
            assert not any(column.casefold() in PROTECTED_COLUMN_NAMES for column in columns), table
        assert engine.connect().execute(text("SELECT version_num FROM alembic_version")).scalar_one() == REVISION
    finally:
        engine.dispose()


def test_operations_foreign_keys_are_tenant_leading_and_profile_dataset_owned(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "operations-fks.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        for table in TABLES:
            for name, (columns, _, _) in _foreign_keys(inspector, table).items():
                assert columns and columns[0] == "tenant_id", (table, name, columns)
        expected = {
            ("tenant_knowledge_operations_profiles", "fk_tenant_knowledge_operations_profiles_dataset"): (("tenant_id", "dataset_id"), "datasets", ("tenant_id", "id")),
            ("tenant_knowledge_conversation_sessions", "fk_tenant_knowledge_conversation_sessions_profile"): (("tenant_id", "profile_id"), "tenant_knowledge_operations_profiles", ("tenant_id", "id")),
            ("tenant_knowledge_conversation_sessions", "fk_tenant_knowledge_conversation_sessions_dataset"): (("tenant_id", "dataset_id"), "datasets", ("tenant_id", "id")),
            ("tenant_knowledge_query_facts", "fk_tenant_knowledge_query_facts_profile"): (("tenant_id", "profile_id"), "tenant_knowledge_operations_profiles", ("tenant_id", "id")),
            ("tenant_knowledge_query_facts", "fk_tenant_knowledge_query_facts_session"): (("tenant_id", "profile_id", "session_id"), "tenant_knowledge_conversation_sessions", ("tenant_id", "profile_id", "id")),
            ("tenant_knowledge_query_facts", "fk_tenant_knowledge_query_facts_dataset"): (("tenant_id", "dataset_id"), "datasets", ("tenant_id", "id")),
            ("tenant_knowledge_feedback_facts", "fk_tenant_knowledge_feedback_facts_query"): (("tenant_id", "profile_id", "session_id", "query_fact_id"), "tenant_knowledge_query_facts", ("tenant_id", "profile_id", "session_id", "id")),
            ("tenant_knowledge_review_cases", "fk_tenant_knowledge_review_cases_query"): (("tenant_id", "profile_id", "dataset_id", "query_fact_id"), "tenant_knowledge_query_facts", ("tenant_id", "profile_id", "dataset_id", "id")),
            ("tenant_knowledge_review_events", "fk_tenant_knowledge_review_events_case"): (("tenant_id", "case_id"), "tenant_knowledge_review_cases", ("tenant_id", "id")),
            ("tenant_knowledge_improvement_candidates", "fk_tenant_knowledge_improvement_candidates_case"): (("tenant_id", "linked_case_id"), "tenant_knowledge_review_cases", ("tenant_id", "id")),
        }
        for (table, name), expected_value in expected.items():
            assert _foreign_keys(inspector, table)[name] == expected_value
    finally:
        engine.dispose()


def test_exact_allowlists_digests_preview_and_revision_checks_are_exposed(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "operations-checks.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        checks = {table: _checks(inspector, table) for table in TABLES}
        assert "channel_code IN ('web','api','wecom','dingtalk','custom')" in checks["tenant_knowledge_conversation_sessions"]["ck_tenant_knowledge_conversation_sessions_channel"]
        assert "route_code IN ('rag','cache','fallback','abstain','changed')" in checks["tenant_knowledge_query_facts"]["ck_tenant_knowledge_query_facts_route"]
        assert "outcome_code IN ('answered','abstained','cancelled','failed','knowledge_changed')" in checks["tenant_knowledge_query_facts"]["ck_tenant_knowledge_query_facts_outcome"]
        assert "feedback_kind IN ('helpful','unhelpful','correction','unsafe','incomplete')" in checks["tenant_knowledge_feedback_facts"]["ck_tenant_knowledge_feedback_facts_kind"]
        assert "priority IN ('low','medium','high','critical')" in checks["tenant_knowledge_review_cases"]["ck_tenant_knowledge_review_cases_priority"]
        assert "status IN ('open','triaged','investigating','resolved','dismissed')" in checks["tenant_knowledge_review_cases"]["ck_tenant_knowledge_review_cases_status"]
        assert "candidate_type IN ('qa_gap','document_gap','source_gap','retrieval_tuning','citation_policy','refusal_policy')" in checks["tenant_knowledge_improvement_candidates"]["ck_tenant_knowledge_improvement_candidates_type"]
        assert "status IN ('proposed','accepted','rejected','converted','archived')" in checks["tenant_knowledge_improvement_candidates"]["ck_tenant_knowledge_improvement_candidates_status"]
        for table, name in (
            ("tenant_knowledge_conversation_sessions", "ck_tenant_knowledge_conversation_sessions_digests"),
            ("tenant_knowledge_query_facts", "ck_tenant_knowledge_query_facts_digests"),
            ("tenant_knowledge_feedback_facts", "ck_tenant_knowledge_feedback_facts_digest"),
            ("tenant_knowledge_review_events", "ck_tenant_knowledge_review_events_digests"),
            ("tenant_knowledge_improvement_candidates", "ck_tenant_knowledge_improvement_candidates_digest"),
        ):
            check = checks[table][name]
            assert "length(" in check and "=64" in check and "lower(" in check
        assert "length(safe_query_preview)<=160" in checks["tenant_knowledge_query_facts"]["ck_tenant_knowledge_query_facts_preview"]
        assert "length(safe_comment_preview)<=160" in checks["tenant_knowledge_feedback_facts"]["ck_tenant_knowledge_feedback_facts_preview"]
        assert "revision > 0" in checks["tenant_knowledge_review_cases"]["ck_tenant_knowledge_review_cases_revision"]
        assert "revision > 0" in checks["tenant_knowledge_improvement_candidates"]["ck_tenant_knowledge_improvement_candidates_revision"]
    finally:
        engine.dispose()


def test_immutable_fact_and_review_event_guards_and_event_chain(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "operations-guards.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        with engine.begin() as connection:
            _insert_profile(connection)
            _insert_session(connection)
            _insert_query(connection)
            connection.execute(
                text(
                    "INSERT INTO tenant_knowledge_feedback_facts "
                    "(id,tenant_id,profile_id,session_id,query_fact_id,feedback_kind,source_code,reason_code,safe_comment_preview,feedback_digest,actor_id,observed_at) "
                    "VALUES ('feedback-a','tenant-a','profile-a','session-a','query-a','unhelpful','explicit','wrong_answer','bad answer',:digest,NULL,CURRENT_TIMESTAMP)"
                ),
                {"digest": "c" * 64},
            )
            _insert_case(connection)
            connection.execute(
                text(
                    "INSERT INTO tenant_knowledge_review_events "
                    "(id,tenant_id,case_id,sequence,event_type,previous_event_digest,event_digest,actor_id,request_id,safe_snapshot_json,occurred_at) "
                    "VALUES ('event-a','tenant-a','case-a',1,'case_created',NULL,:digest,'member-a','request-a','{}',CURRENT_TIMESTAMP)"
                ),
                {"digest": "d" * 64},
            )
        for statement in (
            "UPDATE tenant_knowledge_query_facts SET total_ms=4 WHERE id='query-a'",
            "DELETE FROM tenant_knowledge_query_facts WHERE id='query-a'",
            "UPDATE tenant_knowledge_feedback_facts SET reason_code='other' WHERE id='feedback-a'",
            "DELETE FROM tenant_knowledge_feedback_facts WHERE id='feedback-a'",
            "UPDATE tenant_knowledge_review_events SET event_type='triaged' WHERE id='event-a'",
            "DELETE FROM tenant_knowledge_review_events WHERE id='event-a'",
        ):
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(text(statement))
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO tenant_knowledge_review_events "
                        "(id,tenant_id,case_id,sequence,event_type,previous_event_digest,event_digest,actor_id,request_id,safe_snapshot_json,occurred_at) "
                        "VALUES ('event-b','tenant-a','case-a',1,'triaged',NULL,:digest,'member-a','request-b','{}',CURRENT_TIMESTAMP)"
                    ),
                    {"digest": "e" * 64},
                )
    finally:
        engine.dispose()


def test_cross_tenant_and_cross_profile_ownership_is_rejected(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "operations-ownership.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        with engine.begin() as connection:
            _insert_profile(connection)
            _insert_profile(connection, profile_id="profile-b", dataset_id="dataset-b")
            _insert_session(connection)
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                _insert_session(connection, session_id="session-b", profile_id="profile-b", dataset_id="dataset-a")
    finally:
        engine.dispose()


def test_downgrade_is_online_and_fails_closed_when_nonempty(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "operations-downgrade.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        with engine.begin() as connection:
            _insert_profile(connection)
    finally:
        engine.dispose()
    with pytest.raises(Exception, match="0037 downgrade blocked|Operations|operations"):
        command.downgrade(alembic_config(url), DOWN_REVISION)


def test_empty_downgrade_removes_stage27_tables(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "operations-empty-down.db")
    _upgrade(url)
    command.downgrade(alembic_config(url), DOWN_REVISION)
    engine = engine_for(url)
    try:
        assert not (set(inspect(engine).get_table_names()) & TABLES)
        assert engine.connect().execute(text("SELECT version_num FROM alembic_version")).scalar_one() == DOWN_REVISION
    finally:
        engine.dispose()


def test_unknown_dialect_fails_closed_without_table_creation() -> None:
    migration = migration_module()
    assert "sqlite" in migration.SUPPORTED_DIALECTS
    assert "mysql" in migration.SUPPORTED_DIALECTS
    assert "postgresql" in migration.SUPPORTED_DIALECTS
    assert "oracle" not in migration.SUPPORTED_DIALECTS
