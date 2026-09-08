from __future__ import annotations

from importlib import import_module
from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from tests.test_enterprise_content_recovery_migration import _upgrade as upgrade_0033
from tests.test_enterprise_knowledge_base_release_migration import (
    alembic_config,
    engine_for,
    sqlite_url,
)

REVISION = "0034_enterprise_task_operations"
DOWN_REVISION = "0033_enterprise_content_recovery"
TABLES = {
    "tenant_task_projections",
    "tenant_task_operator_actions",
    "tenant_task_events",
    "tenant_task_saved_views",
    "tenant_task_reconciliation_runs",
}
EXPECTED_COLUMNS = {
    "tenant_task_projections": (
        "id",
        "tenant_id",
        "source_kind",
        "source_id",
        "source_revision",
        "source_digest",
        "dataset_id",
        "workspace_id",
        "category",
        "normalized_status",
        "action_required",
        "progress_percent",
        "attempt_number",
        "max_attempts",
        "lease_owner",
        "lease_until",
        "safe_error_code",
        "safe_error",
        "target_route_code",
        "target_route_params_json",
        "source_current",
        "projection_digest",
        "occurred_at",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
    ),
    "tenant_task_operator_actions": (
        "id",
        "tenant_id",
        "task_id",
        "action_type",
        "status",
        "expected_source_revision",
        "expected_source_digest",
        "idempotency_key_digest",
        "actor_id",
        "request_id",
        "safe_reason",
        "result_code",
        "requested_at",
        "dispatched_at",
        "applied_at",
        "rejected_at",
        "expires_at",
        "created_at",
        "updated_at",
    ),
    "tenant_task_events": (
        "id",
        "tenant_id",
        "task_id",
        "sequence",
        "event_type",
        "previous_event_digest",
        "event_digest",
        "actor_id",
        "request_id",
        "safe_snapshot_json",
        "occurred_at",
    ),
    "tenant_task_saved_views": (
        "id",
        "tenant_id",
        "account_id",
        "name",
        "normalized_name",
        "status",
        "active_view_key",
        "revision",
        "filters_json",
        "filter_digest",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "archived_at",
        "archived_by",
    ),
    "tenant_task_reconciliation_runs": (
        "id",
        "tenant_id",
        "status",
        "source_kinds_json",
        "source_inventory_digest",
        "source_count",
        "created_count",
        "updated_count",
        "stale_count",
        "invalid_count",
        "started_at",
        "completed_at",
        "safe_error_code",
        "safe_error",
        "created_at",
        "updated_at",
    ),
}


def migration_module():
    try:
        return import_module("catalog_migrations.versions.0034_enterprise_task_operations")
    except ModuleNotFoundError as exc:
        pytest.fail(f"Stage 24 migration is missing: {exc}")


def upgrade_0034(url: str) -> None:
    command.upgrade(alembic_config(url), REVISION)


def _upgrade(url: str) -> None:
    upgrade_0033(url)
    upgrade_0034(url)


def _checks(inspector, table: str) -> dict[str, str]:
    return {
        str(item["name"]): " ".join(str(item.get("sqltext") or "").casefold().split())
        for item in inspector.get_check_constraints(table)
        if item.get("name")
    }


def _foreign_keys(inspector, table: str) -> dict[str, tuple[tuple[str, ...], str, tuple[str, ...]]]:
    return {
        str(item["name"]): (
            tuple(item.get("constrained_columns") or ()),
            str(item.get("referred_table")),
            tuple(item.get("referred_columns") or ()),
        )
        for item in inspector.get_foreign_keys(table)
        if item.get("name")
    }


def _insert_projection(connection, *, task_id: str = "task-guard") -> None:
    connection.execute(
        text(
            "INSERT INTO tenant_task_projections "
            "(id,tenant_id,source_kind,source_id,source_revision,source_digest,"
            "dataset_id,workspace_id,category,normalized_status,action_required,progress_percent,"
            "attempt_number,max_attempts,lease_owner,lease_until,safe_error_code,safe_error,"
            "target_route_code,target_route_params_json,source_current,projection_digest,occurred_at,"
            "started_at,finished_at,created_at,updated_at) VALUES "
            "(:id,'tenant-a','source_sync','sync-guard',1,:digest,NULL,NULL,'sources','queued',0,0,"
            "0,3,NULL,NULL,NULL,NULL,'sources','{}',1,:digest,CURRENT_TIMESTAMP,NULL,NULL,"
            "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        ),
        {"id": task_id, "digest": "a" * 64},
    )


def _insert_event(connection, *, event_id: str = "event-guard", digest: str = "b" * 64) -> None:
    connection.execute(
        text(
            "INSERT INTO tenant_task_events "
            "(id,tenant_id,task_id,sequence,event_type,previous_event_digest,event_digest,"
            "actor_id,request_id,safe_snapshot_json,occurred_at) VALUES "
            "(:id,'tenant-a','task-guard',1,'materialized',NULL,:digest,'owner-a',"
            "'request-guard','{}',CURRENT_TIMESTAMP)"
        ),
        {"id": event_id, "digest": digest},
    )


def test_0034_is_head_and_follows_content_recovery() -> None:
    migration = migration_module()
    scripts = ScriptDirectory.from_config(alembic_config("sqlite://"))
    assert scripts.get_current_head() == REVISION
    assert migration.revision == REVISION
    assert migration.down_revision == DOWN_REVISION


def test_upgrade_creates_exactly_five_task_tables_and_contract(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "task-operations.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        task_tables = {
            name for name in inspector.get_table_names() if name.startswith("tenant_task_")
        }
        assert task_tables == TABLES
        for table, expected in EXPECTED_COLUMNS.items():
            assert tuple(item["name"] for item in inspector.get_columns(table)) == expected
        assert (
            engine.connect().execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == REVISION
        )
    finally:
        engine.dispose()


def test_index_operation_projection_category_is_persistable(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "task-operations-indexing-category.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenant_task_projections "
                    "(id,tenant_id,source_kind,source_id,source_revision,source_digest,category,"
                    "normalized_status,action_required,attempt_number,max_attempts,target_route_code,"
                    "target_route_params_json,source_current,projection_digest,occurred_at,created_at,updated_at) "
                    "VALUES ('index-task','tenant-a','index_operation','index-a',1,:digest,'indexing',"
                    "'queued',0,0,3,'documents','{}',1,:digest,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,"
                    "CURRENT_TIMESTAMP)"
                ),
                {"digest": "a" * 64},
            )
        assert (
            engine.connect()
            .execute(text("SELECT category FROM tenant_task_projections WHERE id='index-task'"))
            .scalar_one()
            == "indexing"
        )
    finally:
        engine.dispose()


def test_task_tables_have_tenant_leading_fks_and_canonical_identities(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "task-operations-identities.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        expected_uniques = {
            "tenant_task_projections": {
                "uq_tenant_task_projections_scope_id": ("tenant_id", "id"),
                "uq_tenant_task_projections_source": (
                    "tenant_id",
                    "source_kind",
                    "source_id",
                ),
            },
            "tenant_task_operator_actions": {
                "uq_tenant_task_operator_actions_scope_id": ("tenant_id", "id"),
                "uq_tenant_task_operator_actions_idempotency": (
                    "tenant_id",
                    "actor_id",
                    "idempotency_key_digest",
                ),
            },
            "tenant_task_events": {
                "uq_tenant_task_events_scope_id": ("tenant_id", "id"),
                "uq_tenant_task_events_stream_sequence": ("tenant_id", "task_id", "sequence"),
            },
            "tenant_task_saved_views": {
                "uq_tenant_task_saved_views_scope_id": ("tenant_id", "id"),
                "uq_tenant_task_saved_views_active_key": ("tenant_id", "active_view_key"),
            },
            "tenant_task_reconciliation_runs": {
                "uq_tenant_task_reconciliation_runs_scope_id": ("tenant_id", "id"),
            },
        }
        for table, expected in expected_uniques.items():
            actual = {
                str(item["name"]): tuple(item.get("column_names") or ())
                for item in inspector.get_unique_constraints(table)
                if item.get("name")
            }
            for name, columns in expected.items():
                assert actual.get(name) == columns, (table, name, actual)

        for table in TABLES:
            foreign_keys = _foreign_keys(inspector, table)
            assert foreign_keys, table
            for name, (columns, _target, _target_columns) in foreign_keys.items():
                assert columns[0] == "tenant_id", (table, name, columns)
    finally:
        engine.dispose()


def test_task_checks_cover_allowlists_safe_routes_and_lifecycles(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "task-operations-checks.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        projection = _checks(inspect(engine), "tenant_task_projections")
        assert "document_ingest" in projection["ck_tenant_task_projections_source_kind"]
        assert "release_recertification" in projection["ck_tenant_task_projections_source_kind"]
        assert "normalized_status" in projection["ck_tenant_task_projections_status"]
        assert "progress_percent" in projection["ck_tenant_task_projections_progress"]
        assert "target_route_code" in projection["ck_tenant_task_projections_route"]
        assert "projection_digest" in projection["ck_tenant_task_projections_digest"]

        action = _checks(inspect(engine), "tenant_task_operator_actions")
        assert "retry" in action["ck_tenant_task_operator_actions_type"]
        assert "acknowledge" in action["ck_tenant_task_operator_actions_type"]
        assert "requested" in action["ck_tenant_task_operator_actions_status"]
        assert "expired" in action["ck_tenant_task_operator_actions_status"]
        assert "expected_source_digest" in action["ck_tenant_task_operator_actions_digest"]

        view = _checks(inspect(engine), "tenant_task_saved_views")
        assert "filters_json" in view["ck_tenant_task_saved_views_filters"]
        assert "active_view_key" in view["ck_tenant_task_saved_views_lifecycle"]
        assert "archived" in view["ck_tenant_task_saved_views_status"]

        run = _checks(inspect(engine), "tenant_task_reconciliation_runs")
        assert "completed" in run["ck_tenant_task_reconciliation_runs_status"]
        assert "invalid_count" in run["ck_tenant_task_reconciliation_runs_counts"]
        assert "source_inventory_digest" in run["ck_tenant_task_reconciliation_runs_digest"]

        event = _checks(inspect(engine), "tenant_task_events")
        assert "materialized" in event["ck_tenant_task_events_type"]
        assert "action_rejected" in event["ck_tenant_task_events_type"]
        assert "previous_event_digest" in event["ck_tenant_task_events_hash_chain"]
    finally:
        engine.dispose()


def test_task_event_guards_enforce_immutable_and_append_only_chain(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "task-operations-event-guards.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        with engine.connect() as connection:
            names = {
                str(row[0])
                for row in connection.execute(
                    text("SELECT name FROM sqlite_master WHERE type='trigger'")
                )
            }
        assert {
            "trg_tenant_task_events_no_update",
            "trg_tenant_task_events_no_delete",
            "trg_tenant_task_events_validate_insert",
        } <= names

        with engine.begin() as connection:
            _insert_projection(connection)
            _insert_event(connection)
            with pytest.raises(Exception, match="immutable|task event|event"):
                connection.execute(
                    text(
                        "UPDATE tenant_task_events SET request_id='changed' WHERE id='event-guard'"
                    )
                )
            with pytest.raises(Exception, match="immutable|task event|event"):
                connection.execute(text("DELETE FROM tenant_task_events WHERE id='event-guard'"))
            with pytest.raises(Exception, match="previous|chain|event"):
                connection.execute(
                    text(
                        "INSERT INTO tenant_task_events "
                        "(id,tenant_id,task_id,sequence,event_type,previous_event_digest,event_digest,"
                        "actor_id,request_id,safe_snapshot_json,occurred_at) VALUES "
                        "('event-guard-2','tenant-a','task-guard',2,'status_changed',"
                        ":previous_digest,:event_digest,'owner-a','request-guard-2','{}',CURRENT_TIMESTAMP)"
                    ),
                    {"previous_digest": "c" * 64, "event_digest": "d" * 64},
                )
    finally:
        engine.dispose()


def test_offline_mysql_postgresql_emit_task_ddl_and_sqlite_fails_closed() -> None:
    for url, marker in (
        ("mysql+pymysql://u:p@localhost/rag4c", "DATETIME(6)"),
        ("postgresql+psycopg://u:p@localhost/rag4c", "TIMESTAMP"),
    ):
        output = StringIO()
        command.upgrade(
            alembic_config(url, output_buffer=output), f"{DOWN_REVISION}:{REVISION}", sql=True
        )
        sql = output.getvalue().upper()
        for table in TABLES:
            assert f"CREATE TABLE {table.upper()}" in sql
        assert "SOURCE_KIND" in sql
        assert "IDEMPOTENCY_KEY_DIGEST" in sql
        assert "ACTIVE_VIEW_KEY" in sql
        assert "EVENT_DIGEST" in sql
        assert marker in sql

    with pytest.raises(Exception, match="online|SQLite|sqlite"):
        command.upgrade(
            alembic_config("sqlite:///task-operations-offline.db", output_buffer=StringIO()),
            f"{DOWN_REVISION}:{REVISION}",
            sql=True,
        )


def test_clean_downgrade_restores_0033_and_nonempty_task_authority_blocks(tmp_path: Path) -> None:
    clean_url = sqlite_url(tmp_path / "task-operations-clean-down.db")
    _upgrade(clean_url)
    command.downgrade(alembic_config(clean_url), DOWN_REVISION)
    clean_engine = engine_for(clean_url)
    try:
        assert TABLES.isdisjoint(inspect(clean_engine).get_table_names())
        assert (
            clean_engine.connect()
            .execute(text("SELECT version_num FROM alembic_version"))
            .scalar_one()
            == DOWN_REVISION
        )
    finally:
        clean_engine.dispose()

    blocked_url = sqlite_url(tmp_path / "task-operations-blocked-down.db")
    _upgrade(blocked_url)
    blocked_engine = engine_for(blocked_url)
    with blocked_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenant_task_reconciliation_runs "
                "(id,tenant_id,status,source_kinds_json,source_inventory_digest,source_count,created_count,updated_count,"
                "stale_count,invalid_count,started_at,completed_at,safe_error_code,safe_error,created_at,updated_at) "
                "VALUES ('run-a','tenant-a','completed',:source_kinds,:digest,7,1,2,3,1,CURRENT_TIMESTAMP,"
                "CURRENT_TIMESTAMP,NULL,NULL,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            ),
            {"digest": "e" * 64, "source_kinds": '["source_sync"]'},
        )
    blocked_engine.dispose()
    with pytest.raises(Exception, match="0034|task|Task|operations"):
        command.downgrade(alembic_config(blocked_url), DOWN_REVISION)
