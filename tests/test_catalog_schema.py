from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Boolean, DateTime, JSON, Integer, String, create_engine, inspect, text
from sqlalchemy.dialects import mysql

from models.orm import Base
from tests.test_enterprise_release_quality_migration import (
    seed_0028,
    upgrade_0029,
    upgrade_0030,
)


BASELINE_CATALOG_TABLES = {
    "accounts",
    "apps",
    "datasets",
    "document_segments",
    "documents",
    "metadata_fields",
    "tenant_members",
    "tenants",
    "workflows",
}
CURRENT_CATALOG_TABLES = BASELINE_CATALOG_TABLES | {
    "document_ingest_attempts",
    "document_ingest_spans",
    "index_operations",
    "index_dead_letters",
    "data_sources",
    "source_sync_runs",
    "source_schedules",
    "source_sync_items",
    "source_document_states",
    "chunk_heads",
    "chunk_revisions",
    "knowledge_folders",
    "knowledge_tags",
    "document_tags",
    "knowledge_audit_events",
    "document_versions",
    "qa_knowledge",
    "qa_alternative_questions",
    "document_delete_batches",
    "document_delete_operations",
    "retrieval_experiments",
    "retrieval_judgments",
    "tenant_audit_events",
    "tenant_organization_units",
    "tenant_organization_unit_members",
    "tenant_groups",
    "tenant_group_members",
    "dataset_access_grants",
    "tenant_invitations",
    "dataset_acl_mutation_requests",
    "tenant_control_mutation_requests",
    "tenant_verified_domains",
    "tenant_identity_providers",
    "tenant_scim_tokens",
    "tenant_scim_user_links",
    "tenant_scim_group_links",
    "tenant_audit_retention_policies",
    "tenant_audit_legal_holds",
    "tenant_audit_export_jobs",
    "tenant_oidc_login_transactions",
    "tenant_oidc_subject_links",
    "tenant_sso_sessions",
    "tenant_approval_policies",
    "tenant_approval_policy_approvers",
    "tenant_approval_requests",
    "tenant_approval_decisions",
    "tenant_workspaces",
    "tenant_workspace_members",
    "tenant_workspace_datasets",
    "tenant_workspace_authorization_policies",
    "dataset_workspace_ownerships",
    "app_dataset_references",
    "tenant_release_channels",
    "dataset_release_manifests",
    "dataset_release_entries",
    "dataset_release_events",
    "dataset_channel_releases",
    "tenant_release_quality_gate_policies",
    "dataset_quality_baselines",
    "dataset_quality_baseline_items",
    "dataset_release_quality_certifications",
    "dataset_release_quality_certification_evidence",
    "dataset_release_quality_waivers",
    "dataset_release_quality_events",
    "tenant_release_quality_slo_policies",
    "tenant_release_quality_scan_schedules",
    "tenant_release_quality_scan_runs",
    "dataset_release_quality_observations",
    "dataset_release_quality_alerts",
    "dataset_release_recertification_jobs",
    "tenant_notification_subscriptions",
    "tenant_notifications",
    "tenant_notification_recipients",
    "tenant_notification_receipts",
    "tenant_notification_events",
    "tenant_content_retention_policies",
    "tenant_document_recycle_entries",
    "tenant_document_legal_holds",
    "tenant_document_purge_requests",
    "tenant_document_recovery_events",
    "tenant_task_projections",
    "tenant_task_operator_actions",
    "tenant_task_events",
    "tenant_task_saved_views",
    "tenant_task_reconciliation_runs",
    "tenant_automation_rules",
    "tenant_automation_rule_revisions",
    "tenant_automation_source_cursors",
    "tenant_automation_runs",
    "tenant_automation_action_requests",
    "tenant_automation_events",
    "tenant_knowledge_serving_profiles",
    "tenant_knowledge_serving_policy_revisions",
    "tenant_knowledge_serving_snapshots",
    "tenant_knowledge_serving_stage_facts",
    "tenant_knowledge_serving_evidence_links",
    "tenant_knowledge_serving_events",
    "tenant_knowledge_operations_profiles",
    "tenant_knowledge_conversation_sessions",
    "tenant_knowledge_query_facts",
    "tenant_knowledge_feedback_facts",
    "tenant_knowledge_review_cases",
    "tenant_knowledge_review_events",
    "tenant_knowledge_improvement_candidates",
    "qa_negative_questions",
    "storage_backends",
    "tenant_knowledge_answer_facts",
    "tenant_knowledge_answer_evidence_refs",
}


def catalog_schema() -> ModuleType:
    try:
        return importlib.import_module("core.catalog_schema")
    except ModuleNotFoundError:
        pytest.fail("core.catalog_schema is missing")


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def test_upgrade_catalog_creates_current_versioned_schema(tmp_path: Path) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / "fresh.db")

    api.upgrade_catalog(url)

    engine = create_engine(url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert tables == CURRENT_CATALOG_TABLES | {"alembic_version"}
        state = api.inspect_catalog_schema(engine)
        assert state.revision == api.HEAD_REVISION
        assert state.status == "current"
    finally:
        engine.dispose()


def test_current_head_requires_all_head_tables(tmp_path: Path) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / "missing-content-table.db")
    api.upgrade_catalog(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE qa_alternative_questions"))
    try:
        state = api.inspect_catalog_schema(engine)
        assert state.status == "incomplete"
        assert state.missing_tables == ("qa_alternative_questions",)
        with pytest.raises(api.CatalogSchemaError, match="qa_alternative_questions"):
            api.verify_catalog_schema(engine)
    finally:
        engine.dispose()


def test_stamp_existing_catalog_preserves_business_tables(tmp_path: Path) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / "existing.db")
    api.upgrade_catalog(url, api.BASELINE_REVISION)
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE alembic_version"))
    before = set(inspect(engine).get_table_names())
    engine.dispose()

    api.stamp_existing_catalog(url)

    engine = create_engine(url)
    try:
        after = set(inspect(engine).get_table_names())
        assert before == BASELINE_CATALOG_TABLES
        assert after == before | {"alembic_version"}
        assert api.inspect_catalog_schema(engine).revision == api.BASELINE_REVISION
    finally:
        engine.dispose()


def test_verify_unstamped_catalog_is_read_only(tmp_path: Path) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / "unstamped.db")
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    before = set(inspect(engine).get_table_names())

    with pytest.raises(api.CatalogSchemaError, match="not stamped"):
        api.verify_catalog_schema(engine)

    after = set(inspect(engine).get_table_names())
    assert after == before
    assert "alembic_version" not in after
    engine.dispose()


def test_verify_rejects_future_catalog_revision(tmp_path: Path) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / "future.db")
    api.upgrade_catalog(url)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num='9999_future'"))
        with pytest.raises(api.CatalogSchemaError, match="newer than this application"):
            api.verify_catalog_schema(engine)
    finally:
        engine.dispose()


def test_database_label_redacts_credentials() -> None:
    api = catalog_schema()
    label = api.safe_database_label(
        "mysql+pymysql://catalog_user:super-secret@db.internal:3306/rag4c?charset=utf8mb4"
    )
    assert label == "mysql+pymysql://db.internal:3306/rag4c"
    assert "catalog_user" not in label
    assert "super-secret" not in label


def test_catalog_get_engine_verify_mode_never_bootstraps_empty_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = catalog_schema()
    from core import catalog

    url = sqlite_url(tmp_path / "verify-empty.db")
    catalog.reset_engine()
    monkeypatch.setattr(catalog, "_resolve_db_url", lambda: (url, None))
    monkeypatch.setattr(catalog, "_catalog_schema_mode", lambda: "verify", raising=False)

    with pytest.raises(api.CatalogSchemaError, match="schema is empty"):
        catalog.get_engine()

    probe = create_engine(url)
    try:
        assert inspect(probe).get_table_names() == []
    finally:
        probe.dispose()
        catalog.reset_engine()


def test_catalog_get_engine_verify_mode_accepts_stamped_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core import catalog

    url = sqlite_url(tmp_path / "verify-current.db")
    catalog_schema().upgrade_catalog(url)
    catalog.reset_engine()
    monkeypatch.setattr(catalog, "_resolve_db_url", lambda: (url, None))
    monkeypatch.setattr(catalog, "_catalog_schema_mode", lambda: "verify", raising=False)

    engine = catalog.get_engine()

    assert catalog_schema().inspect_catalog_schema(engine).status == "current"
    catalog.reset_engine()


def test_all_catalog_revision_ids_fit_alembic_mysql_version_column() -> None:
    api = catalog_schema()
    script = api.ScriptDirectory.from_config(api._alembic_config("sqlite://"))

    revisions = [item.revision for item in script.walk_revisions()]

    assert revisions
    assert max(map(len, revisions)) <= 64


def test_stamp_existing_rejects_legacy_create_all_with_partial_membership_schema(
    tmp_path: Path,
) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / "partial.db")
    api.upgrade_catalog(url, api.BASELINE_REVISION)
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE alembic_version"))
    Base.metadata.create_all(engine)
    # create_all 不会给已存在的基线表补列，所以 0037_qa/0038 新增的列在这里天然缺失。
    # 本用例要模拟的唯一偏差是 tenant_members 成员关系不完整，不能让夹具顺带引入第二种偏差
    # （否则守卫会先以"unexpected missing dataset columns"拒绝，测的就不是它想测的东西）。
    with engine.begin() as connection:
        for table, column in (
            ("datasets", "storage_backend_id"),
            ("tenants", "default_storage_backend_id"),
        ):
            existing = {item["name"] for item in inspect(engine).get_columns(table)}
            if column not in existing:
                connection.execute(text(f'ALTER TABLE {table} ADD COLUMN "{column}" VARCHAR(64)'))
    before_columns = {item["name"] for item in inspect(engine).get_columns("documents")}
    assert "content_revision" not in before_columns
    assert "chunk_heads" in inspect(engine).get_table_names()
    engine.dispose()

    with pytest.raises(
        api.CatalogSchemaError,
        match="partial catalog repair incomplete for tenant_members",
    ) as exc_info:
        api.stamp_existing_catalog(url)

    assert "revision" in str(exc_info.value)
    assert "status" in str(exc_info.value)

    engine = create_engine(url)
    try:
        assert "alembic_version" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_catalog_chunk_authority_rollout_mode_defaults_to_shadow() -> None:
    from config.settings import CatalogSettings

    assert CatalogSettings().chunk_authority_mode == "shadow"


def test_head_manifest_rejects_missing_0009_column_fk_unique_check_and_index(
    tmp_path: Path,
) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / "damaged-content-manifest.db")
    api.upgrade_catalog(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table("qa_knowledge") as batch:
            batch.drop_column("source_uri")
            batch.drop_index("ix_qa_knowledge_scope_review")
            batch.drop_constraint("ck_qa_knowledge_review_status", type_="check")
        with operations.batch_alter_table("qa_alternative_questions") as batch:
            batch.drop_constraint("fk_qa_alternatives_scope_qa", type_="foreignkey")
            batch.drop_constraint("uq_qa_alternatives_qa_normalized_hash", type_="unique")

    try:
        state = api.inspect_catalog_schema(engine)
        assert state.status == "incomplete"
        issues = "\n".join(state.schema_issues)
        assert "qa_knowledge.source_uri" in issues
        assert "fk_qa_alternatives_scope_qa" in issues
        assert "uq_qa_alternatives_qa_normalized_hash" in issues
        assert "ck_qa_knowledge_review_status" in issues
        assert "ix_qa_knowledge_scope_review" in issues
        with pytest.raises(api.CatalogSchemaError, match="schema manifest"):
            api.verify_catalog_schema(engine)
    finally:
        engine.dispose()


def test_stamp_existing_rejects_any_partial_head_only_table_set(tmp_path: Path) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / "partial-head-only.db")
    api.upgrade_catalog(url, api.BASELINE_REVISION)
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE alembic_version"))
        connection.execute(text("CREATE TABLE qa_knowledge (id VARCHAR(64) PRIMARY KEY)"))
    engine.dispose()

    with pytest.raises(api.CatalogSchemaError, match="partial head-only"):
        api.stamp_existing_catalog(url)

    engine = create_engine(url)
    try:
        assert "alembic_version" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_stamp_existing_rejects_create_all_with_missing_head_constraint(
    tmp_path: Path,
) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / "missing-head-constraint.db")
    api.upgrade_catalog(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE alembic_version"))
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table("qa_alternative_questions") as batch:
            batch.drop_constraint("uq_qa_alternatives_qa_normalized_hash", type_="unique")
    engine.dispose()

    with pytest.raises(api.CatalogSchemaError, match="schema manifest"):
        api.stamp_existing_catalog(url)

    engine = create_engine(url)
    try:
        assert "alembic_version" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_mysql_legacy_repair_uses_datetime6_for_lifecycle_columns() -> None:
    api = catalog_schema()
    columns = api._legacy_document_repair_columns()

    for name in ("effective_from", "expires_at", "purge_after"):
        assert columns[name].type.compile(dialect=mysql.dialect()) == "DATETIME(6)"


def test_head_manifest_rejects_missing_dataset_profile_contract(tmp_path: Path) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / "damaged-dataset-profile-manifest.db")
    api.upgrade_catalog(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table("datasets") as batch:
            batch.drop_index("ix_datasets_scope_owner")
            batch.drop_constraint("fk_datasets_scope_owner_member", type_="foreignkey")
            batch.drop_constraint("ck_datasets_visibility", type_="check")
            batch.drop_column("default_language")

    try:
        state = api.inspect_catalog_schema(engine)
        assert state.status == "incomplete"
        issues = "\n".join(state.schema_issues)
        assert "datasets.default_language" in issues
        assert "fk_datasets_scope_owner_member" in issues
        assert "ck_datasets_visibility" in issues
        assert "ix_datasets_scope_owner" in issues
        with pytest.raises(api.CatalogSchemaError, match="schema manifest"):
            api.verify_catalog_schema(engine)
    finally:
        engine.dispose()


def test_head_manifest_rejects_nullable_dataset_profile_policy(tmp_path: Path) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / "nullable-dataset-policy.db")
    api.upgrade_catalog(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table("datasets") as batch:
            batch.alter_column(
                "profile_json",
                existing_type=JSON(),
                nullable=True,
            )
    try:
        state = api.inspect_catalog_schema(engine)
        assert state.status == "incomplete"
        assert "nullable column datasets.profile_json" in state.schema_issues
    finally:
        engine.dispose()


def test_head_manifest_rejects_invalid_dataset_profile_types_and_json_default(
    tmp_path: Path,
) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / "invalid-dataset-profile-types.db")
    api.upgrade_catalog(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table("datasets") as batch:
            batch.alter_column(
                "profile_json",
                existing_type=JSON(),
                type_=String(64),
                nullable=False,
                server_default="{}",
            )
            batch.alter_column(
                "profile_revision",
                existing_type=Integer(),
                type_=String(16),
                nullable=False,
            )
            batch.alter_column(
                "graph_enabled",
                existing_type=Boolean(),
                type_=Integer(),
                nullable=False,
            )
            batch.alter_column(
                "visibility",
                existing_type=String(16),
                type_=String(32),
                nullable=False,
            )
            batch.alter_column(
                "archived_at",
                type_=String(32),
                nullable=True,
            )
    try:
        state = api.inspect_catalog_schema(engine)
        assert state.status == "incomplete"
        issues = "\n".join(state.schema_issues)
        assert "invalid type datasets.profile_json: expected JSON" in issues
        assert "unexpected server default datasets.profile_json" in issues
        assert "invalid type datasets.profile_revision: expected Integer" in issues
        assert "invalid type datasets.graph_enabled: expected Boolean" in issues
        assert "invalid length datasets.visibility: expected 16" in issues
        assert "invalid type datasets.archived_at: expected DateTime" in issues
    finally:
        engine.dispose()


def test_mysql_manifest_accepts_reflected_boolean_and_normalizes_check_sql() -> None:
    api = catalog_schema()
    columns = [
        {"name": "graph_enabled", "type": mysql.TINYINT(display_width=1), "default": "0"},
        {"name": "qa_enabled", "type": mysql.TINYINT(display_width=1), "default": "1"},
    ]
    assert api._dataset_profile_column_issues(columns, dialect_name="mysql") == ()

    normalized = api._normalized_sql(
        "((`lifecycle_state` IN (_utf8mb4'active', _utf8mb4'dedeleting', "
        "_utf8mb4'dedelete_requested', _utf8mb4'source_sync')) "
        "OR (0 = `retrieval_enabled`))"
    )
    assert (
        "lifecycle_state in ('active', 'dedeleting', 'dedelete_requested', 'source_sync')"
        in normalized
    )
    assert "not retrieval_enabled" in normalized
    counts = api._normalized_sql(
        "((`completed_store_count` + `failed_store_count`) <= `required_store_count`)"
    )
    assert "completed_store_count + failed_store_count <= required_store_count" in counts


def test_dataset_profile_manifest_requires_mysql_datetime6(tmp_path: Path) -> None:
    from sqlalchemy.dialects import mysql

    api = catalog_schema()
    url = sqlite_url(tmp_path / "mysql-datetime-contract.db")
    api.upgrade_catalog(url)
    engine = create_engine(url)
    try:
        columns = inspect(engine).get_columns("datasets")
        for item in columns:
            if item["name"] in {"archived_at", "updated_at"}:
                item["type"] = mysql.DATETIME(fsp=0)
        issues = api._dataset_profile_column_issues(columns, dialect_name="mysql")
        assert "invalid type datasets.archived_at: expected DATETIME(6)" in issues
        assert "invalid type datasets.updated_at: expected DATETIME(6)" in issues
    finally:
        engine.dispose()


def test_head_manifest_rejects_missing_durable_delete_contract(tmp_path: Path) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / "damaged-durable-delete-manifest.db")
    api.upgrade_catalog(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table("document_delete_batches") as batch:
            batch.drop_index("ix_document_delete_batches_scope_status")
        with operations.batch_alter_table("source_sync_runs") as batch:
            batch.drop_constraint("ck_source_sync_runs_generations_counts", type_="check")
            batch.drop_column("pending_deletes")

    try:
        state = api.inspect_catalog_schema(engine)
        assert state.status == "incomplete"
        issues = "\n".join(state.schema_issues)
        assert "source_sync_runs.pending_deletes" in issues
        assert "ck_source_sync_runs_generations_counts" in issues
        assert "ix_document_delete_batches_scope_status" in issues
        with pytest.raises(api.CatalogSchemaError, match="schema manifest"):
            api.verify_catalog_schema(engine)
    finally:
        engine.dispose()


def test_durable_delete_manifest_requires_complete_states_counts_and_source_scope() -> None:
    api = catalog_schema()
    batch_fragments = api._HEAD_REQUIRED_CHECK_FRAGMENTS["document_delete_batches"][
        "ck_document_delete_batches_status"
    ]
    operation_fragments = api._HEAD_REQUIRED_CHECK_FRAGMENTS["document_delete_operations"][
        "ck_document_delete_operations_counts"
    ]
    source_fk = api._HEAD_REQUIRED_FOREIGN_KEYS["source_document_states"][
        "fk_source_document_states_scope_delete_operation"
    ]
    assert "completed" in batch_fragments
    assert {
        "request_index >= 0",
        "chunk_manifest_count >= 0",
        "quota_chunk_count >= 0",
        "required_store_count >= 0",
        "completed_store_count >= 0",
        "failed_store_count >= 0",
        "completed_store_count + failed_store_count <= required_store_count",
    } <= set(operation_fragments)
    assert source_fk == (
        ("doc_id", "delete_operation_id"),
        "document_delete_operations",
        ("document_id", "id"),
    )


def test_head_manifest_rejects_weakened_delete_checks_and_source_fk(
    tmp_path: Path,
) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / "weakened-durable-delete-contract.db")
    api.upgrade_catalog(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table("document_delete_batches") as batch:
            batch.drop_constraint("ck_document_delete_batches_status", type_="check")
            batch.create_check_constraint(
                "ck_document_delete_batches_status",
                "status IN ('preparing', 'running', 'failed')",
            )
        with operations.batch_alter_table("document_delete_operations") as batch:
            batch.drop_constraint("ck_document_delete_operations_counts", type_="check")
            batch.create_check_constraint(
                "ck_document_delete_operations_counts",
                "request_index >= 0 AND required_store_count >= 0",
            )
        with operations.batch_alter_table("source_document_states") as batch:
            batch.drop_constraint(
                "fk_source_document_states_scope_delete_operation",
                type_="foreignkey",
            )
            batch.create_foreign_key(
                "fk_source_document_states_delete_operation",
                "document_delete_operations",
                ["delete_operation_id"],
                ["id"],
            )

    try:
        state = api.inspect_catalog_schema(engine)
        assert state.status == "incomplete"
        issues = "\n".join(state.schema_issues)
        assert "ck_document_delete_batches_status" in issues
        assert "ck_document_delete_operations_counts" in issues
        assert "fk_source_document_states_scope_delete_operation" in issues
    finally:
        engine.dispose()


def test_head_manifest_includes_identity_and_source_uniqueness_constraints() -> None:
    api = catalog_schema()

    required = api._HEAD_REQUIRED_UNIQUES

    assert required["tenant_members"]["uq_tenant_members_account_tenant"] == (
        "account_id",
        "tenant_id",
    )
    assert required["document_segments"]["uq_document_segments_document_seq"] == (
        "document_id",
        "seq",
    )
    assert required["metadata_fields"]["uq_metadata_fields_dataset_key"] == (
        "dataset_id",
        "key",
    )
    assert required["documents"]["uq_documents_dataset_source_uri_hash"] == (
        "dataset_id",
        "source_uri_hash",
    )
    assert required["documents"]["uq_documents_dataset_source_external"] == (
        "dataset_id",
        "source_id",
        "external_id",
    )


def test_enterprise_access_graph_manifest_contract_is_complete() -> None:
    api = catalog_schema()

    expected_columns = {
        "tenant_organization_units": {
            "id",
            "tenant_id",
            "parent_id",
            "name",
            "code",
            "status",
            "sort_order",
            "revision",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
        },
        "tenant_organization_unit_members": {
            "id",
            "tenant_id",
            "organization_unit_id",
            "account_id",
            "status",
            "revision",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
        },
        "tenant_groups": {
            "id",
            "tenant_id",
            "name",
            "normalized_name",
            "description",
            "status",
            "revision",
            "created_at",
            "updated_at",
        },
        "tenant_group_members": {
            "id",
            "tenant_id",
            "group_id",
            "account_id",
            "status",
            "created_at",
            "created_by",
        },
        "dataset_access_grants": {
            "id",
            "tenant_id",
            "dataset_id",
            "subject_type",
            "subject_id",
            "role",
            "status",
            "revision",
            "created_at",
            "updated_at",
        },
        "tenant_invitations": {
            "id",
            "tenant_id",
            "email",
            "normalized_email",
            "role",
            "status",
            "token_hash",
            "expires_at",
            "accepted_at",
            "accepted_by",
            "invited_by",
            "revision",
            "created_at",
            "updated_at",
            "pending_email_key",
            "last_sent_at",
            "send_count",
            "revoked_at",
            "revoked_by",
            "updated_by",
        },
    }
    expected_uniques = {
        "tenant_organization_units": {
            "uq_tenant_organization_units_scope_id": ("tenant_id", "id"),
            "uq_tenant_organization_units_tenant_code": ("tenant_id", "code"),
            "uq_tenant_organization_units_tenant_parent_name": ("tenant_id", "parent_id", "name"),
        },
        "tenant_organization_unit_members": {
            "uq_tenant_organization_unit_members_unit_account": (
                "tenant_id",
                "organization_unit_id",
                "account_id",
            ),
        },
        "tenant_groups": {
            "uq_tenant_groups_scope_id": ("tenant_id", "id"),
            "uq_tenant_groups_tenant_normalized_name": ("tenant_id", "normalized_name"),
        },
        "tenant_group_members": {
            "uq_tenant_group_members_group_account": ("tenant_id", "group_id", "account_id"),
        },
        "dataset_access_grants": {
            "uq_dataset_access_grants_dataset_subject": (
                "tenant_id",
                "dataset_id",
                "subject_type",
                "subject_id",
            ),
        },
        "tenant_invitations": {
            "uq_tenant_invitations_pending_email": ("tenant_id", "pending_email_key"),
            "uq_tenant_invitations_token_hash": ("tenant_id", "token_hash"),
        },
    }
    expected_indexes = {
        "tenant_organization_units": {
            "ix_tenant_organization_units_tenant_parent_status": (
                "tenant_id",
                "parent_id",
                "status",
                "sort_order",
                "id",
            ),
            "ix_tenant_organization_units_tenant_status": (
                "tenant_id",
                "status",
                "sort_order",
                "id",
            ),
        },
        "tenant_organization_unit_members": {
            "ix_tenant_organization_unit_members_tenant_unit_status": (
                "tenant_id",
                "organization_unit_id",
                "status",
                "id",
            ),
            "ix_tenant_organization_unit_members_tenant_account_status": (
                "tenant_id",
                "account_id",
                "status",
                "id",
            ),
        },
        "tenant_groups": {
            "ix_tenant_groups_tenant_status_name": ("tenant_id", "status", "normalized_name", "id"),
        },
        "tenant_group_members": {
            "ix_tenant_group_members_tenant_account": ("tenant_id", "account_id", "id"),
            "ix_tenant_group_members_tenant_group": ("tenant_id", "group_id", "id"),
        },
        "dataset_access_grants": {
            "ix_dataset_access_grants_tenant_dataset_status": (
                "tenant_id",
                "dataset_id",
                "status",
                "id",
            ),
            "ix_dataset_access_grants_tenant_subject": (
                "tenant_id",
                "subject_type",
                "subject_id",
                "id",
            ),
        },
        "tenant_invitations": {
            "ix_tenant_invitations_tenant_status_expires": (
                "tenant_id",
                "status",
                "expires_at",
                "id",
            ),
            "ix_tenant_invitations_tenant_email": ("tenant_id", "normalized_email", "id"),
            "ix_tenant_invitations_tenant_status_updated": (
                "tenant_id",
                "status",
                "updated_at",
                "id",
            ),
        },
    }

    for table_name, columns in expected_columns.items():
        assert api._HEAD_REQUIRED_COLUMNS[table_name] == frozenset(columns)
        assert api._HEAD_REQUIRED_UNIQUES[table_name] == expected_uniques[table_name]
        assert api._HEAD_REQUIRED_INDEXES[table_name] == expected_indexes[table_name]
        assert table_name in api._HEAD_REQUIRED_FOREIGN_KEYS
        assert table_name in api._HEAD_REQUIRED_CHECK_FRAGMENTS

    assert api._HEAD_REQUIRED_FOREIGN_KEYS["tenant_group_members"][
        "fk_tenant_group_members_scope_account"
    ] == (
        ("account_id", "tenant_id"),
        "tenant_members",
        ("account_id", "tenant_id"),
    )
    assert api._HEAD_REQUIRED_FOREIGN_KEYS["dataset_access_grants"][
        "fk_dataset_access_grants_scope_dataset"
    ] == (
        ("tenant_id", "dataset_id"),
        "datasets",
        ("tenant_id", "id"),
    )
    assert api._HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_group_members"] == {
        "ck_tenant_group_members_status": ("active", "removed"),
    }
    assert set(api._HEAD_REQUIRED_CHECK_FRAGMENTS["dataset_access_grants"]) == {
        "ck_dataset_access_grants_subject_type",
        "ck_dataset_access_grants_role",
        "ck_dataset_access_grants_status",
        "ck_dataset_access_grants_revision_positive",
    }
    assert set(api._HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_invitations"]) == {
        "ck_tenant_invitations_role",
        "ck_tenant_invitations_status",
        "ck_tenant_invitations_revision_positive",
        "ck_tenant_invitations_send_count_positive",
        "ck_tenant_invitations_pending_email_key",
        "ck_tenant_invitations_accepted_evidence",
        "ck_tenant_invitations_revoked_evidence",
    }


def test_head_manifest_rejects_missing_enterprise_access_graph_index(
    tmp_path: Path,
) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / "damaged-access-graph-index.db")
    api.upgrade_catalog(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        Operations(MigrationContext.configure(connection)).drop_index(
            "ix_dataset_access_grants_tenant_subject",
            table_name="dataset_access_grants",
        )

    try:
        state = api.inspect_catalog_schema(engine)
        assert state.status == "incomplete"
        assert (
            "missing or invalid index dataset_access_grants.ix_dataset_access_grants_tenant_subject"
        ) in state.schema_issues
        with pytest.raises(api.CatalogSchemaError, match="schema manifest"):
            api.verify_catalog_schema(engine)
    finally:
        engine.dispose()


def test_dataset_acl_control_manifest_contract_is_complete() -> None:
    api = catalog_schema()

    assert "dataset_acl_mutation_requests" in api.HEAD_CATALOG_TABLES
    assert api._HEAD_REQUIRED_COLUMNS["datasets"] >= {
        "acl_mode",
        "acl_revision",
        "acl_enabled_at",
        "acl_enabled_by",
    }
    assert api._HEAD_REQUIRED_NOT_NULL["datasets"] >= {
        "acl_mode",
        "acl_revision",
    }
    assert api._HEAD_REQUIRED_CHECK_FRAGMENTS["datasets"]["ck_datasets_acl_mode"] == (
        "tenant_role",
        "dataset_acl",
    )
    assert api._HEAD_REQUIRED_CHECK_FRAGMENTS["datasets"]["ck_datasets_acl_revision_positive"] == (
        "acl_revision > 0",
    )

    table = "dataset_acl_mutation_requests"
    assert api._HEAD_REQUIRED_COLUMNS[table] == frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "actor_id",
            "idempotency_key",
            "request_hash",
            "operation",
            "status",
            "resource_id",
            "response_json",
            "http_status",
            "created_at",
            "completed_at",
        }
    )
    assert api._HEAD_REQUIRED_NOT_NULL[table] == frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "actor_id",
            "idempotency_key",
            "request_hash",
            "operation",
            "status",
            "created_at",
        }
    )
    assert api._HEAD_REQUIRED_UNIQUES[table] == {
        "uq_dataset_acl_mutation_requests_actor_key": (
            "tenant_id",
            "actor_id",
            "idempotency_key",
        ),
    }
    assert api._HEAD_REQUIRED_FOREIGN_KEYS[table] == {
        "fk_dataset_acl_mutation_requests_scope_dataset": (
            ("tenant_id", "dataset_id"),
            "datasets",
            ("tenant_id", "id"),
        ),
        "fk_dataset_acl_mutation_requests_scope_actor": (
            ("actor_id", "tenant_id"),
            "tenant_members",
            ("account_id", "tenant_id"),
        ),
    }
    assert api._HEAD_REQUIRED_CHECK_FRAGMENTS[table] == {
        "ck_dataset_acl_mutation_requests_status": (
            "pending",
            "completed",
            "failed",
        ),
        "ck_dataset_acl_mutation_requests_idempotency_key_length": (
            "length(idempotency_key)",
            "between 1 and 128",
        ),
    }
    assert api._HEAD_REQUIRED_INDEXES[table] == {
        "ix_dataset_acl_mutation_requests_tenant_dataset_status_created": (
            "tenant_id",
            "dataset_id",
            "status",
            "created_at",
            "id",
        ),
        "ix_dataset_acl_mutation_requests_tenant_actor_key": (
            "tenant_id",
            "actor_id",
            "idempotency_key",
        ),
    }


def test_dataset_acl_control_exposes_reusable_schema_evidence() -> None:
    api = catalog_schema()

    assert api.DATASET_ACL_CONTROL_REVISION == "0019_dataset_acl_control"
    assert api.DATASET_ACL_CONTROL_REQUIRED_TABLES == frozenset({"dataset_acl_mutation_requests"})
    assert api.DATASET_ACL_CONTROL_REQUIRED_COLUMNS == {
        "datasets": frozenset({"acl_mode", "acl_revision", "acl_enabled_at", "acl_enabled_by"}),
        "dataset_acl_mutation_requests": api._HEAD_REQUIRED_COLUMNS[
            "dataset_acl_mutation_requests"
        ],
    }
    assert api.DATASET_ACL_CONTROL_REQUIRED_NOT_NULL == {
        "datasets": frozenset({"acl_mode", "acl_revision"}),
        "dataset_acl_mutation_requests": api._HEAD_REQUIRED_NOT_NULL[
            "dataset_acl_mutation_requests"
        ],
    }
    assert api.DATASET_ACL_CONTROL_REQUIRED_UNIQUES == {
        "dataset_acl_mutation_requests": api._HEAD_REQUIRED_UNIQUES[
            "dataset_acl_mutation_requests"
        ],
    }
    assert api.DATASET_ACL_CONTROL_REQUIRED_FOREIGN_KEYS == {
        "dataset_acl_mutation_requests": api._HEAD_REQUIRED_FOREIGN_KEYS[
            "dataset_acl_mutation_requests"
        ],
    }
    assert api.DATASET_ACL_CONTROL_REQUIRED_CHECKS == {
        "datasets": frozenset({"ck_datasets_acl_mode", "ck_datasets_acl_revision_positive"}),
        "dataset_acl_mutation_requests": frozenset(
            {
                "ck_dataset_acl_mutation_requests_status",
                "ck_dataset_acl_mutation_requests_idempotency_key_length",
            }
        ),
    }
    assert api.DATASET_ACL_CONTROL_REQUIRED_INDEXES == {
        "dataset_acl_mutation_requests": api._HEAD_REQUIRED_INDEXES[
            "dataset_acl_mutation_requests"
        ],
    }
    assert {
        "datasets.acl_mode",
        "datasets.acl_revision",
        "datasets.acl_enabled_at",
        "datasets.acl_enabled_by",
        "datasets.ck_datasets_acl_mode",
        "datasets.ck_datasets_acl_revision_positive",
        "dataset_acl_mutation_requests.",
    } <= set(api.DATASET_ACL_CONTROL_ISSUE_FRAGMENTS)


def test_tenant_invitation_lifecycle_manifest_contract_is_complete() -> None:
    api = catalog_schema()
    ledger = "tenant_control_mutation_requests"

    assert api.TENANT_INVITATION_LIFECYCLE_REVISION == "0020_tenant_invitation_lifecycle"
    assert api.TENANT_INVITATION_LIFECYCLE_REQUIRED_TABLES == frozenset({ledger})
    assert api.TENANT_INVITATION_LIFECYCLE_REQUIRED_COLUMNS["tenant_invitations"] == frozenset(
        {
            "pending_email_key",
            "last_sent_at",
            "send_count",
            "revoked_at",
            "revoked_by",
            "updated_by",
        }
    )
    assert (
        api._HEAD_REQUIRED_COLUMNS[ledger]
        == api.TENANT_INVITATION_LIFECYCLE_REQUIRED_COLUMNS[ledger]
    )
    assert (
        api._HEAD_REQUIRED_NOT_NULL[ledger]
        == api.TENANT_INVITATION_LIFECYCLE_REQUIRED_NOT_NULL[ledger]
    )
    assert (
        api._HEAD_REQUIRED_UNIQUES[ledger]
        == api.TENANT_INVITATION_LIFECYCLE_REQUIRED_UNIQUES[ledger]
    )
    assert (
        api._HEAD_REQUIRED_FOREIGN_KEYS[ledger]
        == api.TENANT_INVITATION_LIFECYCLE_REQUIRED_FOREIGN_KEYS[ledger]
    )
    assert (
        api._HEAD_REQUIRED_CHECK_FRAGMENTS[ledger]
        == api.TENANT_INVITATION_LIFECYCLE_REQUIRED_CHECK_FRAGMENTS[ledger]
    )
    assert (
        api._HEAD_REQUIRED_INDEXES[ledger]
        == api.TENANT_INVITATION_LIFECYCLE_REQUIRED_INDEXES[ledger]
    )
    assert {
        "tenant_invitations.pending_email_key",
        "tenant_invitations.last_sent_at",
        "tenant_invitations.send_count",
        "tenant_invitations.revoked_at",
        "tenant_invitations.revoked_by",
        "tenant_invitations.updated_by",
        "tenant_control_mutation_requests.",
    } <= set(api.TENANT_INVITATION_LIFECYCLE_ISSUE_FRAGMENTS)


def test_enterprise_approval_control_manifest_contract_is_complete() -> None:
    api = catalog_schema()
    tables = {
        "tenant_approval_policies",
        "tenant_approval_policy_approvers",
        "tenant_approval_requests",
        "tenant_approval_decisions",
    }
    assert api.ENTERPRISE_APPROVAL_CONTROL_REVISION == "0025_enterprise_approval_control"
    assert api.ENTERPRISE_APPROVAL_CONTROL_REQUIRED_TABLES == tables
    assert tables <= api.HEAD_CATALOG_TABLES

    request = "tenant_approval_requests"
    assert {
        "snapshot_json",
        "payload_hash",
        "execution_ticket_hash",
        "required_approvals",
        "received_approvals",
    } <= api._HEAD_REQUIRED_COLUMNS[request]
    assert api._HEAD_REQUIRED_UNIQUES["tenant_approval_policies"][
        "uq_tenant_approval_policies_active_scope"
    ] == ("tenant_id", "active_scope_key")
    assert api._HEAD_REQUIRED_UNIQUES["tenant_approval_decisions"][
        "uq_tenant_approval_decisions_request_approver"
    ] == ("tenant_id", "request_id", "approver_id")
    assert api._HEAD_REQUIRED_FOREIGN_KEYS[request][
        "fk_tenant_approval_requests_scope_requester"
    ] == (
        ("requester_id", "tenant_id"),
        "tenant_members",
        ("account_id", "tenant_id"),
    )
    assert "ck_tenant_approval_requests_ticket_hash" in api._HEAD_REQUIRED_CHECK_FRAGMENTS[request]
    assert (
        "ck_tenant_approval_requests_approval_counts" in api._HEAD_REQUIRED_CHECK_FRAGMENTS[request]
    )
    assert "ix_tenant_approval_requests_tenant_status_expiry" in api._HEAD_REQUIRED_INDEXES[request]


def test_enterprise_workspace_control_manifest_contract_is_complete() -> None:
    api = catalog_schema()
    tables = {
        "tenant_workspaces",
        "tenant_workspace_members",
        "tenant_workspace_datasets",
    }

    assert api.ENTERPRISE_WORKSPACE_CONTROL_REVISION == "0026_enterprise_workspace_control"
    assert api.ENTERPRISE_WORKSPACE_CONTROL_REQUIRED_TABLES == tables
    assert tables <= api.HEAD_CATALOG_TABLES

    assert {
        "id",
        "tenant_id",
        "code",
        "name",
        "normalized_name",
        "description",
        "status",
        "environment",
        "is_default",
        "active_default_slot",
        "revision",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "archived_at",
        "archived_by",
    } <= api._HEAD_REQUIRED_COLUMNS["tenant_workspaces"]
    assert {
        "id",
        "tenant_id",
        "workspace_id",
        "account_id",
        "role",
        "status",
        "revision",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "removed_at",
        "removed_by",
    } <= api._HEAD_REQUIRED_COLUMNS["tenant_workspace_members"]
    assert {
        "id",
        "tenant_id",
        "workspace_id",
        "dataset_id",
        "binding_kind",
        "active_primary_slot",
        "status",
        "revision",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "removed_at",
        "removed_by",
    } <= api._HEAD_REQUIRED_COLUMNS["tenant_workspace_datasets"]

    assert api._HEAD_REQUIRED_UNIQUES["tenant_workspaces"][
        "uq_tenant_workspaces_active_default"
    ] == ("tenant_id", "active_default_slot")
    assert api._HEAD_REQUIRED_UNIQUES["tenant_workspace_datasets"][
        "uq_tenant_workspace_datasets_active_primary"
    ] == ("tenant_id", "dataset_id", "active_primary_slot")
    assert api._HEAD_REQUIRED_FOREIGN_KEYS["tenant_workspace_members"][
        "fk_tenant_workspace_members_scope_member"
    ] == (
        ("account_id", "tenant_id"),
        "tenant_members",
        ("account_id", "tenant_id"),
    )
    assert api._HEAD_REQUIRED_FOREIGN_KEYS["tenant_workspace_datasets"][
        "fk_tenant_workspace_datasets_scope_dataset"
    ] == (
        ("tenant_id", "dataset_id"),
        "datasets",
        ("tenant_id", "id"),
    )
    assert (
        "ck_tenant_workspaces_active_default_slot"
        in api._HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_workspaces"]
    )
    assert (
        "ck_tenant_workspace_datasets_active_primary_slot"
        in api._HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_workspace_datasets"]
    )
    assert api._HEAD_REQUIRED_INDEXES["tenant_workspaces"][
        "ix_tenant_workspaces_tenant_status_updated"
    ] == ("tenant_id", "status", "updated_at", "id")
    assert api.ENTERPRISE_WORKSPACE_CONTROL_ISSUE_FRAGMENTS == (
        "tenant_workspace_datasets.",
        "tenant_workspace_members.",
        "tenant_workspaces.",
    )


def test_workspace_manifest_requires_bidirectional_primary_and_lifecycle_checks() -> None:
    api = catalog_schema()
    assert api._HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_workspace_datasets"][
        "ck_tenant_workspace_datasets_active_primary_slot"
    ] == (
        "status = 'active'",
        "binding_kind = 'primary'",
        "active_primary_slot is not null",
        "active_primary_slot = 'primary'",
        "status <> 'active'",
        "binding_kind <> 'primary'",
        "active_primary_slot is null",
    )
    assert api._HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_workspaces"][
        "ck_tenant_workspaces_lifecycle_evidence"
    ] == (
        "status = 'active'",
        "archived_at is null",
        "archived_by is null",
        "status = 'archived'",
        "archived_at is not null",
        "archived_by is not null",
    )


def test_workspace_manifest_rejects_tautological_critical_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / "workspace-tautology.db")
    api.upgrade_catalog(url)
    engine = create_engine(url)
    real_inspector = inspect(engine)

    class TautologicalInspector:
        bind = real_inspector.bind

        def __getattr__(self, name: str):
            return getattr(real_inspector, name)

        def get_check_constraints(self, table_name: str):
            checks = [dict(item) for item in real_inspector.get_check_constraints(table_name)]
            if table_name == "tenant_workspace_datasets":
                for item in checks:
                    if item.get("name") == "ck_tenant_workspace_datasets_active_primary_slot":
                        item["sqltext"] = f"({item['sqltext']}) OR 1 = 1"
            return checks

    monkeypatch.setattr(api, "inspect", lambda _engine: TautologicalInspector())
    try:
        state = api.inspect_catalog_schema(engine)
        assert state.status == "incomplete"
        assert (
            "missing or invalid exact check "
            "tenant_workspace_datasets.ck_tenant_workspace_datasets_active_primary_slot"
        ) in state.schema_issues
    finally:
        engine.dispose()


def test_head_manifest_rejects_missing_workspace_index(tmp_path: Path) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / "damaged-workspace-index.db")
    api.upgrade_catalog(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        Operations(MigrationContext.configure(connection)).drop_index(
            "ix_tenant_workspace_datasets_tenant_dataset_status",
            table_name="tenant_workspace_datasets",
        )

    try:
        state = api.inspect_catalog_schema(engine)
        assert state.status == "incomplete"
        assert (
            "missing or invalid index "
            "tenant_workspace_datasets.ix_tenant_workspace_datasets_tenant_dataset_status"
        ) in state.schema_issues
        with pytest.raises(api.CatalogSchemaError, match="schema manifest"):
            api.verify_catalog_schema(engine)
    finally:
        engine.dispose()


def test_workspace_column_validator_accepts_sqlite_postgresql_and_mysql_contracts() -> None:
    api = catalog_schema()
    workspace_tables = {
        "tenant_workspaces",
        "tenant_workspace_members",
        "tenant_workspace_datasets",
    }
    for table_name in workspace_tables:
        table = Base.metadata.tables[table_name]
        columns = [
            {"name": column.name, "type": column.type, "nullable": column.nullable}
            for column in table.columns
        ]
        assert (
            api._enterprise_workspace_column_issues(table_name, columns, dialect_name="sqlite")
            == ()
        )
        assert (
            api._enterprise_workspace_column_issues(table_name, columns, dialect_name="postgresql")
            == ()
        )

        mysql_columns = [dict(column) for column in columns]
        for column in mysql_columns:
            if column["name"] == "is_default":
                column["type"] = mysql.TINYINT(display_width=1)
            elif isinstance(column["type"], DateTime):
                column["type"] = mysql.DATETIME(fsp=6)
        assert (
            api._enterprise_workspace_column_issues(table_name, mysql_columns, dialect_name="mysql")
            == ()
        )


@pytest.mark.parametrize(
    ("table_name", "column_name", "expected_issue"),
    [
        (
            "tenant_workspace_members",
            "workspace_id",
            "invalid length tenant_workspace_members.workspace_id: expected 128",
        ),
        (
            "tenant_workspace_datasets",
            "dataset_id",
            "invalid length tenant_workspace_datasets.dataset_id: expected 64",
        ),
    ],
)
def test_head_manifest_rejects_workspace_column_length_contract(
    tmp_path: Path, table_name: str, column_name: str, expected_issue: str
) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / f"workspace-invalid-length-{column_name}.db")
    api.upgrade_catalog(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table(table_name) as batch:
            batch.alter_column(
                column_name,
                existing_type=String(128 if column_name == "workspace_id" else 64),
                type_=String(64 if column_name == "workspace_id" else 32),
            )

    try:
        state = api.inspect_catalog_schema(engine)
        assert state.status == "incomplete"
        assert expected_issue in state.schema_issues
    finally:
        engine.dispose()


def test_head_manifest_rejects_workspace_revision_boolean_or_string_type(tmp_path: Path) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / "workspace-invalid-revision-type.db")
    api.upgrade_catalog(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table("tenant_workspaces") as batch:
            batch.alter_column(
                "revision", existing_type=Integer(), type_=String(16), nullable=False
            )

    try:
        state = api.inspect_catalog_schema(engine)
        assert state.status == "incomplete"
        assert "invalid type tenant_workspaces.revision: expected Integer" in state.schema_issues
    finally:
        engine.dispose()


def test_head_manifest_rejects_non_nullable_workspace_slots_and_removed_evidence(
    tmp_path: Path,
) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / "workspace-invalid-nullability.db")
    api.upgrade_catalog(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table("tenant_workspaces") as batch:
            batch.alter_column("active_default_slot", existing_type=String(16), nullable=False)
            batch.alter_column("archived_at", existing_type=DateTime(), nullable=False)
        with operations.batch_alter_table("tenant_workspace_datasets") as batch:
            batch.alter_column("active_primary_slot", existing_type=String(16), nullable=False)
            batch.alter_column("removed_by", existing_type=String(64), nullable=False)

    try:
        state = api.inspect_catalog_schema(engine)
        assert state.status == "incomplete"
        issues = "\n".join(state.schema_issues)
        assert (
            "non-nullable column tenant_workspaces.active_default_slot: expected nullable" in issues
        )
        assert "non-nullable column tenant_workspaces.archived_at: expected nullable" in issues
        assert (
            "non-nullable column tenant_workspace_datasets.active_primary_slot: expected nullable"
            in issues
        )
        assert (
            "non-nullable column tenant_workspace_datasets.removed_by: expected nullable" in issues
        )
    finally:
        engine.dispose()


def test_enterprise_workspace_authorization_manifest_contract_is_complete() -> None:
    api = catalog_schema()
    table = "tenant_workspace_authorization_policies"

    assert (
        api.ENTERPRISE_WORKSPACE_AUTHORIZATION_REVISION == "0027_enterprise_workspace_authorization"
    )
    assert api.ENTERPRISE_WORKSPACE_AUTHORIZATION_REQUIRED_TABLES == frozenset({table})
    assert table in api.HEAD_CATALOG_TABLES
    assert api._HEAD_REQUIRED_COLUMNS[table] == frozenset(
        {
            "id",
            "tenant_id",
            "workspace_id",
            "mode",
            "permission_model_version",
            "revision",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
            "enforced_at",
            "enforced_by",
            "disabled_at",
            "disabled_by",
        }
    )
    assert api._HEAD_REQUIRED_NOT_NULL[table] == frozenset(
        {
            "id",
            "tenant_id",
            "workspace_id",
            "mode",
            "permission_model_version",
            "revision",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
        }
    )
    assert api._HEAD_REQUIRED_UNIQUES[table] == {
        "uq_tenant_workspace_authorization_policies_scope_id": ("tenant_id", "id"),
        "uq_tenant_workspace_authorization_policies_scope_workspace": (
            "tenant_id",
            "workspace_id",
        ),
    }
    assert api._HEAD_REQUIRED_FOREIGN_KEYS[table] == {
        "fk_tenant_workspace_authorization_policies_tenant": (
            ("tenant_id",),
            "tenants",
            ("id",),
        ),
        "fk_tenant_workspace_authorization_policies_scope_workspace": (
            ("tenant_id", "workspace_id"),
            "tenant_workspaces",
            ("tenant_id", "id"),
        ),
    }
    assert set(api._HEAD_REQUIRED_CHECK_FRAGMENTS[table]) == {
        "ck_tenant_workspace_auth_policies_mode",
        "ck_tenant_workspace_auth_policies_model_version_positive",
        "ck_tenant_workspace_auth_policies_revision_positive",
        "ck_tenant_workspace_auth_policies_mode_evidence",
    }
    assert api._HEAD_REQUIRED_INDEXES[table] == {
        "ix_tw_auth_policies_tenant_mode_updated": (
            "tenant_id",
            "mode",
            "updated_at",
            "id",
        ),
        "ix_tw_auth_policies_tenant_workspace_mode": (
            "tenant_id",
            "workspace_id",
            "mode",
            "id",
        ),
    }
    assert api.ENTERPRISE_WORKSPACE_AUTHORIZATION_ISSUE_FRAGMENTS == (
        "tenant_workspace_authorization_policies.",
    )
    assert (
        "workspace_authorization_mode_change"
        in api._HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_approval_policies"][
            "ck_tenant_approval_policies_action_type"
        ]
    )
    assert (
        "workspace_authorization_mode_change"
        in api._HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_approval_requests"][
            "ck_tenant_approval_requests_action_type"
        ]
    )


def test_workspace_authorization_column_validator_accepts_supported_dialects() -> None:
    api = catalog_schema()
    table = Base.metadata.tables["tenant_workspace_authorization_policies"]
    columns = [
        {"name": column.name, "type": column.type, "nullable": column.nullable}
        for column in table.columns
    ]

    assert (
        api._enterprise_workspace_authorization_column_issues(columns, dialect_name="sqlite") == ()
    )
    assert (
        api._enterprise_workspace_authorization_column_issues(columns, dialect_name="postgresql")
        == ()
    )

    mysql_columns = [dict(column) for column in columns]
    for column in mysql_columns:
        if isinstance(column["type"], DateTime):
            column["type"] = mysql.DATETIME(fsp=6)
    assert (
        api._enterprise_workspace_authorization_column_issues(mysql_columns, dialect_name="mysql")
        == ()
    )


def test_head_manifest_rejects_workspace_authorization_contract_damage(tmp_path: Path) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / "damaged-workspace-authorization.db")
    api.upgrade_catalog(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        operations.drop_index(
            "ix_tw_auth_policies_tenant_workspace_mode",
            table_name="tenant_workspace_authorization_policies",
        )
        with operations.batch_alter_table("tenant_approval_requests") as batch:
            batch.drop_constraint("ck_tenant_approval_requests_action_type", type_="check")
            batch.create_check_constraint(
                "ck_tenant_approval_requests_action_type",
                "action_type IN ('catalog_upgrade','membership_bootstrap','dataset_acl_disable',"
                "'member_role_change','identity_provider_disable','audit_retention_execute')",
            )

    try:
        state = api.inspect_catalog_schema(engine)
        assert state.status == "incomplete"
        assert (
            "missing or invalid index tenant_workspace_authorization_policies."
            "ix_tw_auth_policies_tenant_workspace_mode"
        ) in state.schema_issues
        assert (
            "missing or invalid exact check tenant_approval_requests."
            "ck_tenant_approval_requests_action_type"
        ) in state.schema_issues
    finally:
        engine.dispose()


def test_head_manifest_rejects_tautological_workspace_authorization_lifecycle_check(
    tmp_path: Path,
) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / "workspace-authorization-tautology.db")
    api.upgrade_catalog(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table("tenant_workspace_authorization_policies") as batch:
            batch.drop_constraint("ck_tenant_workspace_auth_policies_mode_evidence", type_="check")
            batch.create_check_constraint(
                "ck_tenant_workspace_auth_policies_mode_evidence",
                "mode IN ('disabled','shadow','enforced') OR 1=1",
            )

    try:
        state = api.inspect_catalog_schema(engine)
        assert state.status == "incomplete"
        assert (
            "missing or invalid exact check tenant_workspace_authorization_policies."
            "ck_tenant_workspace_auth_policies_mode_evidence"
        ) in state.schema_issues
    finally:
        engine.dispose()


def test_enterprise_knowledge_base_registry_manifest_contract_is_complete() -> None:
    api = catalog_schema()
    tables = {"dataset_workspace_ownerships", "app_dataset_references"}

    assert (
        api.ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REVISION == "0028_enterprise_knowledge_base_registry"
    )
    assert api.ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REQUIRED_TABLES == tables
    assert tables <= api.HEAD_CATALOG_TABLES

    assert api._HEAD_REQUIRED_COLUMNS["dataset_workspace_ownerships"] == frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "workspace_id",
            "revision",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
            "last_transfer_at",
        }
    )
    assert api._HEAD_REQUIRED_COLUMNS["app_dataset_references"] == frozenset(
        {
            "id",
            "tenant_id",
            "app_id",
            "dataset_id",
            "reference_kind",
            "status",
            "active_slot",
            "revision",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
            "removed_at",
            "removed_by",
            "request_id",
            "release_mode",
            "release_channel_id",
            "pinned_release_id",
        }
    )
    assert api._HEAD_REQUIRED_NOT_NULL["dataset_workspace_ownerships"] == frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "workspace_id",
            "revision",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
        }
    )
    assert api._HEAD_REQUIRED_NOT_NULL["app_dataset_references"] == frozenset(
        {
            "id",
            "tenant_id",
            "app_id",
            "dataset_id",
            "reference_kind",
            "status",
            "revision",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
            "request_id",
            "release_mode",
        }
    )
    assert api._HEAD_REQUIRED_UNIQUES["apps"]["uq_apps_tenant_id"] == ("tenant_id", "id")
    assert api._HEAD_REQUIRED_UNIQUES["dataset_workspace_ownerships"] == {
        "uq_dataset_workspace_ownerships_scope_id": ("tenant_id", "id"),
        "uq_dataset_workspace_ownerships_scope_dataset": ("tenant_id", "dataset_id"),
    }
    assert api._HEAD_REQUIRED_UNIQUES["app_dataset_references"] == {
        "uq_app_dataset_references_scope_id": ("tenant_id", "id"),
        "uq_app_dataset_references_active_slot": (
            "tenant_id",
            "app_id",
            "dataset_id",
            "active_slot",
        ),
    }
    assert api._HEAD_REQUIRED_FOREIGN_KEYS["dataset_workspace_ownerships"] == {
        "fk_dataset_workspace_ownerships_tenant": (("tenant_id",), "tenants", ("id",)),
        "fk_dataset_workspace_ownerships_scope_dataset": (
            ("tenant_id", "dataset_id"),
            "datasets",
            ("tenant_id", "id"),
        ),
        "fk_dataset_workspace_ownerships_scope_workspace": (
            ("tenant_id", "workspace_id"),
            "tenant_workspaces",
            ("tenant_id", "id"),
        ),
    }
    assert api._HEAD_REQUIRED_FOREIGN_KEYS["app_dataset_references"] == {
        "fk_app_dataset_references_tenant": (("tenant_id",), "tenants", ("id",)),
        "fk_app_dataset_references_scope_app": (
            ("tenant_id", "app_id"),
            "apps",
            ("tenant_id", "id"),
        ),
        "fk_app_dataset_references_scope_dataset": (
            ("tenant_id", "dataset_id"),
            "datasets",
            ("tenant_id", "id"),
        ),
        "fk_app_dataset_references_scope_release_channel": (
            ("tenant_id", "release_channel_id"),
            "tenant_release_channels",
            ("tenant_id", "id"),
        ),
        "fk_app_dataset_references_scope_pinned_release": (
            ("tenant_id", "dataset_id", "pinned_release_id"),
            "dataset_release_manifests",
            ("tenant_id", "dataset_id", "id"),
        ),
    }
    assert api._HEAD_REQUIRED_CHECK_FRAGMENTS["dataset_workspace_ownerships"] == {
        "ck_dataset_workspace_ownerships_revision_positive": ("revision > 0",),
    }
    assert api._HEAD_REQUIRED_CHECK_FRAGMENTS["app_dataset_references"] == {
        "ck_app_dataset_references_reference_kind": ("reference_kind = 'knowledge'",),
        "ck_app_dataset_references_status": ("status in", "active", "removed"),
        "ck_app_dataset_references_revision_positive": ("revision > 0",),
        "ck_app_dataset_references_lifecycle_evidence": (
            "status = 'active'",
            "active_slot = 'active'",
            "removed_at is null",
            "removed_by is null",
            "status = 'removed'",
            "active_slot is null",
            "removed_at is not null",
            "removed_by is not null",
        ),
        "ck_app_dataset_references_release_binding": (
            "(release_mode = 'follow_channel' AND release_channel_id IS NOT NULL "
            "AND pinned_release_id IS NULL) OR (release_mode = 'pinned' "
            "AND release_channel_id IS NULL AND pinned_release_id IS NOT NULL)",
        ),
    }
    assert api._HEAD_REQUIRED_INDEXES["dataset_workspace_ownerships"] == {
        "ix_dataset_workspace_ownerships_tenant_workspace_dataset": (
            "tenant_id",
            "workspace_id",
            "dataset_id",
        ),
        "ix_dataset_workspace_ownerships_tenant_updated_dataset": (
            "tenant_id",
            "updated_at",
            "dataset_id",
        ),
    }
    assert api._HEAD_REQUIRED_INDEXES["app_dataset_references"] == {
        "ix_app_dataset_references_tenant_dataset_status": (
            "tenant_id",
            "dataset_id",
            "status",
            "id",
        ),
        "ix_app_dataset_references_tenant_app_status": (
            "tenant_id",
            "app_id",
            "status",
            "id",
        ),
        "ix_app_dataset_references_tenant_status_updated": (
            "tenant_id",
            "status",
            "updated_at",
            "id",
        ),
    }
    assert api.ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_ISSUE_FRAGMENTS == (
        "app_dataset_references.",
        "apps.uq_apps_tenant_id",
        "dataset_workspace_ownerships.",
    )
    assert api.ENTERPRISE_KNOWLEDGE_BASE_REGISTRY_REQUIRED_EXACT_CHECK_SQL[
        "app_dataset_references"
    ] == {
        "ck_app_dataset_references_reference_kind": "reference_kind = 'knowledge'",
        "ck_app_dataset_references_status": "status IN ('active','removed')",
        "ck_app_dataset_references_revision_positive": "revision > 0",
        "ck_app_dataset_references_lifecycle_evidence": (
            "(status = 'active' AND active_slot = 'active' AND removed_at IS NULL "
            "AND removed_by IS NULL) OR (status = 'removed' AND active_slot IS NULL "
            "AND removed_at IS NOT NULL AND removed_by IS NOT NULL)"
        ),
    }
    assert (
        "dataset_workspace_transfer"
        in api._HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_approval_policies"][
            "ck_tenant_approval_policies_action_type"
        ]
    )
    assert (
        "dataset_workspace_transfer"
        in api._HEAD_REQUIRED_CHECK_FRAGMENTS["tenant_approval_requests"][
            "ck_tenant_approval_requests_action_type"
        ]
    )


def test_registry_column_validator_accepts_sqlite_postgresql_and_mysql_contracts() -> None:
    api = catalog_schema()
    for table_name in ("dataset_workspace_ownerships", "app_dataset_references"):
        table = Base.metadata.tables[table_name]
        columns = [
            {"name": column.name, "type": column.type, "nullable": column.nullable}
            for column in table.columns
        ]
        assert (
            api._enterprise_knowledge_base_registry_column_issues(
                table_name, columns, dialect_name="sqlite"
            )
            == ()
        )
        assert (
            api._enterprise_knowledge_base_registry_column_issues(
                table_name, columns, dialect_name="postgresql"
            )
            == ()
        )

        mysql_columns = [dict(column) for column in columns]
        for column in mysql_columns:
            if isinstance(column["type"], DateTime):
                column["type"] = mysql.DATETIME(fsp=6)
        assert (
            api._enterprise_knowledge_base_registry_column_issues(
                table_name, mysql_columns, dialect_name="mysql"
            )
            == ()
        )


def test_head_manifest_rejects_knowledge_base_registry_contract_damage(tmp_path: Path) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / "damaged-knowledge-base-registry.db")
    api.upgrade_catalog(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        operations.drop_index(
            "ix_dataset_workspace_ownerships_tenant_updated_dataset",
            table_name="dataset_workspace_ownerships",
        )
        with operations.batch_alter_table("app_dataset_references") as batch:
            batch.drop_constraint("ck_app_dataset_references_lifecycle_evidence", type_="check")
            batch.create_check_constraint(
                "ck_app_dataset_references_lifecycle_evidence",
                "status IN ('active','removed') OR 1=1",
            )

    try:
        state = api.inspect_catalog_schema(engine)
        assert state.status == "incomplete"
        assert (
            "missing or invalid index dataset_workspace_ownerships."
            "ix_dataset_workspace_ownerships_tenant_updated_dataset"
        ) in state.schema_issues
        assert (
            "missing or invalid exact check app_dataset_references."
            "ck_app_dataset_references_lifecycle_evidence"
        ) in state.schema_issues
    finally:
        engine.dispose()


def test_workspace_authorization_capability_remains_available_at_0028(tmp_path: Path) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / "workspace-authorization-at-0028.db")
    api.upgrade_catalog(url)
    engine = create_engine(url)
    try:
        assert api.inspect_workspace_authorization_capability(engine) == ("ready", ())
    finally:
        engine.dispose()


def test_enterprise_knowledge_base_release_manifest_contract_is_complete() -> None:
    api = catalog_schema()
    release_tables = {
        "tenant_release_channels",
        "dataset_release_manifests",
        "dataset_release_entries",
        "dataset_release_events",
        "dataset_channel_releases",
    }

    assert (
        api.ENTERPRISE_KNOWLEDGE_BASE_RELEASES_REVISION == "0029_enterprise_knowledge_base_releases"
    )
    assert api.ENTERPRISE_NOTIFICATION_CENTER_REVISION == "0032_enterprise_notification_center"
    assert api.ENTERPRISE_KNOWLEDGE_BASE_RELEASE_TABLES == release_tables
    assert release_tables <= api.HEAD_CATALOG_TABLES
    assert api._HEAD_REQUIRED_COLUMNS["datasets"] >= {
        "serving_release_id",
        "release_revision",
    }
    assert api._HEAD_REQUIRED_COLUMNS["app_dataset_references"] >= {
        "release_mode",
        "release_channel_id",
        "pinned_release_id",
    }
    assert api.ENTERPRISE_APPROVAL_ACTION_TYPES_0029[-2:] == (
        "knowledge_base_release_publish",
        "knowledge_base_release_rollback",
    )

    assert api._HEAD_REQUIRED_UNIQUES["tenant_release_channels"] == {
        "uq_tenant_release_channels_scope_id": ("tenant_id", "id"),
        "uq_tenant_release_channels_tenant_code": ("tenant_id", "code"),
        "uq_tenant_release_channels_tenant_normalized_code": (
            "tenant_id",
            "normalized_code",
        ),
        "uq_tenant_release_channels_active_default": (
            "tenant_id",
            "active_default_slot",
        ),
    }
    assert api._HEAD_REQUIRED_FOREIGN_KEYS["dataset_channel_releases"][
        "fk_dataset_channel_releases_scope_active_release"
    ] == (
        ("tenant_id", "dataset_id", "active_release_id"),
        "dataset_release_manifests",
        ("tenant_id", "dataset_id", "id"),
    )
    assert api._HEAD_REQUIRED_INDEXES["dataset_release_manifests"][
        "ix_dataset_release_manifests_tenant_readiness"
    ] == ("tenant_id", "readiness_state", "created_at", "id")
    assert api.ENTERPRISE_KNOWLEDGE_BASE_RELEASE_REQUIRED_EXACT_CHECK_SQL["app_dataset_references"][
        "ck_app_dataset_references_release_binding"
    ].startswith("(release_mode = 'follow_channel'")


def test_release_capability_is_revision_aware_and_partial_fail_closed(tmp_path: Path) -> None:
    api = catalog_schema()
    pre_release_url = sqlite_url(tmp_path / "pre-release.db")
    api.upgrade_catalog(pre_release_url, "0028_enterprise_knowledge_base_registry")
    pre_release_engine = create_engine(pre_release_url)
    try:
        assert api.inspect_enterprise_knowledge_base_release_capability(pre_release_engine) == (
            "not_available",
            (),
        )
    finally:
        pre_release_engine.dispose()

    current_url = sqlite_url(tmp_path / "partial-release.db")
    api.upgrade_catalog(current_url)
    current_engine = create_engine(current_url)
    try:
        assert api.inspect_enterprise_knowledge_base_release_capability(current_engine) == (
            "ready",
            (),
        )
        with current_engine.begin() as connection:
            connection.execute(text("DROP TABLE dataset_release_entries"))
        status, issues = api.inspect_enterprise_knowledge_base_release_capability(current_engine)
        assert status == "unavailable"
        assert "missing table dataset_release_entries" in issues
    finally:
        current_engine.dispose()


def test_enterprise_release_quality_certification_manifest_contract_is_complete() -> None:
    api = catalog_schema()
    tables = {
        "tenant_release_quality_gate_policies",
        "dataset_quality_baselines",
        "dataset_quality_baseline_items",
        "dataset_release_quality_certifications",
        "dataset_release_quality_certification_evidence",
        "dataset_release_quality_waivers",
        "dataset_release_quality_events",
    }
    assert api.ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_TABLES == tables
    assert tables <= api.HEAD_CATALOG_TABLES
    assert api.ENTERPRISE_APPROVAL_ACTION_TYPES_0030[-1] == (
        "knowledge_base_release_quality_waiver"
    )
    for table in tables:
        assert api.ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REQUIRED_COLUMNS[table]
        assert api.ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REQUIRED_UNIQUES[table]
        assert api.ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REQUIRED_FOREIGN_KEYS[table]
        assert api.ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REQUIRED_CHECK_FRAGMENTS[table]
        assert api.ENTERPRISE_RELEASE_QUALITY_CERTIFICATION_REQUIRED_INDEXES[table]


def _insert_catalog_quality_policy(
    connection,
    *,
    policy_id: str,
    active_scope_key: str,
    policy_digest: str,
) -> None:
    connection.execute(
        text(
            "INSERT INTO tenant_release_quality_gate_policies "
            "(id,tenant_id,name,scope_type,scope_value,channel_id,active_scope_key,status,revision,"
            "min_experiment_count,min_judged_result_count,min_judgment_coverage_bps,"
            "min_exact_agreement_bps,min_mean_score_milli,max_conflicting_results,"
            "require_all_experiments_completed,require_no_degraded_results,"
            "max_certification_age_minutes,policy_digest,created_at,created_by,updated_at,updated_by) "
            "VALUES (:id,'tenant-a','Quality','global','*',NULL,:active_scope_key,'active',1,"
            "1,1,8000,8000,2000,0,1,1,1440,:policy_digest,CURRENT_TIMESTAMP,'owner-a',"
            "CURRENT_TIMESTAMP,'owner-a')"
        ),
        {
            "id": policy_id,
            "active_scope_key": active_scope_key,
            "policy_digest": policy_digest,
        },
    )


def test_quality_readiness_rejects_noncanonical_and_duplicate_active_policy_scope(
    tmp_path: Path,
) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / "quality-policy-readiness.db")
    seed_0028(url)
    upgrade_0029(url)
    upgrade_0030(url)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
            _insert_catalog_quality_policy(
                connection,
                policy_id="quality-policy-a",
                active_scope_key="global:*",
                policy_digest="a" * 64,
            )
            _insert_catalog_quality_policy(
                connection,
                policy_id="quality-policy-forged",
                active_scope_key="global:forged",
                policy_digest="b" * 64,
            )

        status, issues = api.inspect_enterprise_release_quality_certification_capability(engine)
        assert status == "unavailable"
        assert any("invalid active quality policy canonical scope" in issue for issue in issues)
        assert any("duplicate active quality policy scope" in issue for issue in issues)
    finally:
        engine.dispose()


class _ProbeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _TriggerProbeConnection:
    def __init__(self, dialect_name: str, *, bad_table: str):
        self.dialect = type("Dialect", (), {"name": dialect_name})()
        self.bad_table = bad_table

    def execute(self, statement):
        sql = str(statement).casefold()
        rows = []
        quality_tables = (
            "dataset_quality_baselines",
            "dataset_quality_baseline_items",
            "dataset_release_quality_certifications",
            "dataset_release_quality_certification_evidence",
            "dataset_release_quality_waivers",
            "dataset_release_quality_events",
        )
        for table in quality_tables:
            for operation in ("update", "delete"):
                name = f"trg_{table}_no_{operation}"
                target = self.bad_table if table == quality_tables[0] else table
                if self.dialect.name == "mysql":
                    statement_body = (
                        f"SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{table} are immutable'"
                    )
                    if "event_object_table" in sql:
                        rows.append((name, target, "BEFORE", operation.upper(), statement_body))
                    else:
                        rows.append((name, "BEFORE", operation.upper(), statement_body))
                else:
                    trigger_def = (
                        f"CREATE TRIGGER {name} BEFORE {operation.upper()} ON {target} "
                        "FOR EACH ROW EXECUTE FUNCTION rag4c_release_quality_immutable()"
                    )
                    if "relname" in sql or "tgrelid" in sql:
                        rows.append((name, target, trigger_def))
                    else:
                        rows.append((name, trigger_def))
        return _ProbeResult(rows)

    def scalar(self, statement):
        return 1


def test_quality_readiness_trigger_probe_checks_real_sqlite_target_table(tmp_path: Path) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / "quality-misbound-trigger.db")
    seed_0028(url)
    upgrade_0029(url)
    upgrade_0030(url)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP TRIGGER trg_dataset_quality_baselines_no_update"))
            connection.execute(
                text(
                    "CREATE TRIGGER trg_dataset_quality_baselines_no_update "
                    "BEFORE UPDATE ON tenants BEGIN "
                    "SELECT RAISE(ABORT, 'dataset_quality_baselines are immutable'); END"
                )
            )
        with engine.connect() as connection:
            issues = api._release_quality_guard_issues(connection)
        assert any(
            "invalid Release quality immutable trigger" in issue and "target table tenants" in issue
            for issue in issues
        )
    finally:
        engine.dispose()


@pytest.mark.parametrize("dialect_name", ["mysql", "postgresql"])
def test_quality_readiness_trigger_probe_checks_real_target_table(dialect_name: str) -> None:
    api = catalog_schema()
    connection = _TriggerProbeConnection(dialect_name, bad_table="tenants")
    issues = api._release_quality_guard_issues(connection)
    assert any(
        "invalid Release quality immutable trigger" in issue and "target table tenants" in issue
        for issue in issues
    )


def test_enterprise_release_quality_operations_manifest_contract_is_complete() -> None:
    api = catalog_schema()
    tables = {
        "tenant_release_quality_slo_policies",
        "tenant_release_quality_scan_schedules",
        "tenant_release_quality_scan_runs",
        "dataset_release_quality_observations",
        "dataset_release_quality_alerts",
        "dataset_release_recertification_jobs",
    }
    assert api.ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REVISION == (
        "0031_enterprise_release_quality_operations"
    )
    assert api.ENTERPRISE_RELEASE_QUALITY_OPERATIONS_TABLES == tables
    assert tables <= api.HEAD_CATALOG_TABLES
    for table in tables:
        assert api.ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REQUIRED_COLUMNS[table]
        assert api.ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REQUIRED_UNIQUES[table]
        assert api.ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REQUIRED_FOREIGN_KEYS[table]
        assert api.ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REQUIRED_CHECK_FRAGMENTS[table]
        assert api.ENTERPRISE_RELEASE_QUALITY_OPERATIONS_REQUIRED_INDEXES[table]


def test_enterprise_release_quality_operations_capability_is_ready_and_keeps_0030_ready(
    tmp_path: Path,
) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / "quality-operations-capability.db")
    api.upgrade_catalog(url)
    engine = create_engine(url)
    try:
        assert api.inspect_enterprise_release_quality_operations_capability(engine) == (
            "ready",
            (),
        )
        assert api.inspect_enterprise_release_quality_certification_capability(engine) == (
            "ready",
            (),
        )
    finally:
        engine.dispose()


def test_enterprise_release_quality_operations_readiness_rejects_malformed_active_identity(
    tmp_path: Path,
) -> None:
    api = catalog_schema()
    from test_enterprise_knowledge_base_release_snapshot import _release_engine

    engine, _ = _release_engine(tmp_path)
    try:
        with engine.begin() as connection:
            connection.execute(text("PRAGMA ignore_check_constraints=ON"))
            connection.execute(
                text(
                    "INSERT INTO tenant_release_quality_slo_policies "
                    "(id,tenant_id,name,scope_type,scope_value,channel_id,active_scope_key,status,revision,"
                    "certification_warning_minutes,certification_critical_minutes,waiver_warning_minutes,"
                    "max_open_alerts,auto_queue_recertification,require_passing_certification,"
                    "allow_active_waiver,policy_digest,created_at,created_by,updated_at,updated_by) VALUES "
                    "('malformed-slo','tenant-a','Malformed','global','*',NULL,'global:forged','active',1,"
                    "60,15,30,20,1,1,1,:digest,CURRENT_TIMESTAMP,'owner-a',CURRENT_TIMESTAMP,'owner-a')"
                ),
                {"digest": "a" * 64},
            )
            connection.execute(text("PRAGMA ignore_check_constraints=OFF"))

        status, issues = api.inspect_enterprise_release_quality_operations_capability(engine)
        assert status == "unavailable"
        assert any("invalid active SLO policy identity" in issue for issue in issues)
        state = api.inspect_catalog_schema(engine)
        assert state.status == "incomplete"
        assert any("invalid active SLO policy identity" in issue for issue in state.schema_issues)
    finally:
        engine.dispose()


def test_enterprise_notification_center_manifest_contract_is_complete() -> None:
    api = catalog_schema()
    tables = {
        "tenant_notification_subscriptions",
        "tenant_notifications",
        "tenant_notification_recipients",
        "tenant_notification_receipts",
        "tenant_notification_events",
    }
    assert api.ENTERPRISE_NOTIFICATION_CENTER_REVISION == "0032_enterprise_notification_center"
    assert api.ENTERPRISE_NOTIFICATION_CENTER_TABLES == tables
    assert tables <= api.HEAD_CATALOG_TABLES
    for table in tables:
        assert api.ENTERPRISE_NOTIFICATION_CENTER_REQUIRED_COLUMNS[table]
        assert api.ENTERPRISE_NOTIFICATION_CENTER_REQUIRED_UNIQUES[table]
        assert api.ENTERPRISE_NOTIFICATION_CENTER_REQUIRED_FOREIGN_KEYS[table]
        assert api.ENTERPRISE_NOTIFICATION_CENTER_REQUIRED_CHECK_FRAGMENTS[table]
        assert api.ENTERPRISE_NOTIFICATION_CENTER_REQUIRED_INDEXES[table]


def test_enterprise_notification_center_capability_is_ready_and_keeps_0032_parents_ready(
    tmp_path: Path,
) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / "notification-center-capability.db")
    api.upgrade_catalog(url)
    engine = create_engine(url)
    try:
        assert api.inspect_enterprise_notification_center_capability(engine) == ("ready", ())
        assert api.inspect_enterprise_release_quality_operations_capability(engine) == (
            "ready",
            (),
        )
    finally:
        engine.dispose()


def test_enterprise_notification_center_readiness_rejects_malformed_subscription_identity(
    tmp_path: Path,
) -> None:
    api = catalog_schema()
    from test_enterprise_knowledge_base_release_snapshot import _release_engine

    engine, now = _release_engine(tmp_path)
    try:
        with engine.begin() as connection:
            connection.execute(text("PRAGMA ignore_check_constraints=ON"))
            connection.execute(
                text(
                    "INSERT INTO tenant_notification_subscriptions "
                    "(id,tenant_id,account_id,category,status,preference,active_subscription_key,"
                    "revision,minimum_severity,created_at,created_by,updated_at,updated_by) VALUES "
                    "('malformed-sub','tenant-a','owner-a','quality','active','subscribed','forged',1,"
                    "'warning',:now,'owner-a',:now,'owner-a')"
                ),
                {"now": now},
            )
            connection.execute(text("PRAGMA ignore_check_constraints=OFF"))
        status, issues = api.inspect_enterprise_notification_center_capability(engine)
        assert status == "unavailable"
        assert any("invalid notification subscription identity" in issue for issue in issues)
        state = api.inspect_catalog_schema(engine)
        assert state.status == "incomplete"
    finally:
        engine.dispose()


def test_enterprise_content_recovery_manifest_contract_is_complete() -> None:
    api = catalog_schema()
    tables = {
        "tenant_content_retention_policies",
        "tenant_document_recycle_entries",
        "tenant_document_legal_holds",
        "tenant_document_purge_requests",
        "tenant_document_recovery_events",
    }
    assert api.ENTERPRISE_CONTENT_RECOVERY_REVISION == "0033_enterprise_content_recovery"
    assert api.ENTERPRISE_CONTENT_RECOVERY_REQUIRED_TABLES == tables
    assert tables <= api.HEAD_CATALOG_TABLES
    for table in tables:
        assert table in api._HEAD_REQUIRED_COLUMNS
        assert table in api._HEAD_REQUIRED_NOT_NULL
        assert table in api._HEAD_REQUIRED_UNIQUES
        assert table in api._HEAD_REQUIRED_FOREIGN_KEYS
        assert table in api._HEAD_REQUIRED_CHECK_FRAGMENTS
        assert table in api._HEAD_REQUIRED_INDEXES
    assert (
        "recycled"
        in api._HEAD_REQUIRED_CHECK_FRAGMENTS["documents"]["ck_documents_lifecycle_state"]
    )
    for table in ("tenant_approval_policies", "tenant_approval_requests"):
        assert (
            "document_purge" in api._HEAD_REQUIRED_CHECK_FRAGMENTS[table][f"ck_{table}_action_type"]
        )


def test_content_recovery_capability_requires_canonical_tables_and_event_guards(
    tmp_path: Path,
) -> None:
    api = catalog_schema()
    url = sqlite_url(tmp_path / "content-recovery-readiness.db")
    api.upgrade_catalog(url)
    engine = create_engine(url)
    try:
        state, issues = api.inspect_enterprise_content_recovery_capability(engine)
        assert state == "ready", issues
        assert issues == ()
        with engine.begin() as connection:
            connection.execute(text("DROP TRIGGER trg_tenant_document_recovery_events_no_update"))
        state, issues = api.inspect_enterprise_content_recovery_capability(engine)
        assert state == "unavailable"
        assert any("immutable trigger" in issue for issue in issues)
    finally:
        engine.dispose()
