from __future__ import annotations

from importlib import import_module
from io import StringIO
from pathlib import Path


import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from core import catalog_schema
from tests.test_enterprise_task_operations_migration import _upgrade as upgrade_0034
from tests.test_enterprise_knowledge_base_release_migration import (
    alembic_config,
    engine_for,
    sqlite_url,
)

REVISION = "0035_enterprise_automation_workflows"
DOWN_REVISION = "0034_enterprise_task_operations"
TABLES = {
    "tenant_automation_rules",
    "tenant_automation_rule_revisions",
    "tenant_automation_source_cursors",
    "tenant_automation_runs",
    "tenant_automation_action_requests",
    "tenant_automation_events",
}


def migration_module():
    try:
        return import_module("catalog_migrations.versions.0035_enterprise_automation_workflows")
    except ModuleNotFoundError as exc:
        pytest.fail(f"Stage25 migration is missing: {exc}")


def upgrade_0035(url: str) -> None:
    command.upgrade(alembic_config(url), REVISION)


def _upgrade(url: str) -> None:
    upgrade_0034(url)
    upgrade_0035(url)


def _checks(inspector, table: str) -> dict[str, str]:
    return {
        str(item["name"]): " ".join(str(item.get("sqltext") or "").casefold().split())
        for item in inspector.get_check_constraints(table)
        if item.get("name")
    }


def test_0035_is_head_and_follows_task_operations() -> None:
    migration = migration_module()
    scripts = ScriptDirectory.from_config(alembic_config("sqlite://"))
    assert scripts.get_current_head() == catalog_schema.HEAD_REVISION
    assert migration.revision == REVISION
    assert migration.down_revision == DOWN_REVISION


def test_upgrade_creates_exactly_six_automation_tables(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "automation-workflows.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        automation_tables = {
            name
            for name in inspect(engine).get_table_names()
            if name.startswith("tenant_automation_")
        }
        assert automation_tables == TABLES
        assert (
            engine.connect().execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == REVISION
        )
    finally:
        engine.dispose()


def test_automation_tables_use_tenant_leading_foreign_keys_and_canonical_uniques(
    tmp_path: Path,
) -> None:
    url = sqlite_url(tmp_path / "automation-workflows-constraints.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        for table in TABLES:
            for fk in inspector.get_foreign_keys(table):
                columns = tuple(fk.get("constrained_columns") or ())
                assert not columns or columns[0] == "tenant_id", (table, fk.get("name"), columns)
        expected = {
            "tenant_automation_rules": "uq_tenant_automation_rules_active_key",
            "tenant_automation_rule_revisions": "uq_tenant_automation_rule_revisions_rule_revision",
            "tenant_automation_source_cursors": "uq_tenant_automation_source_cursors_stream",
            "tenant_automation_runs": "uq_tenant_automation_runs_trigger_replay",
            "tenant_automation_action_requests": "uq_tenant_automation_action_requests_run_step",
            "tenant_automation_events": "uq_tenant_automation_events_stream_sequence",
        }
        for table, unique_name in expected.items():
            assert unique_name in {
                item.get("name") for item in inspector.get_unique_constraints(table)
            }
    finally:
        engine.dispose()


def test_sqlite_upgrade_downgrade_cycle_restores_current_revision_fk(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "automation-workflows-cycle.db")
    _upgrade(url)
    engine = engine_for(url)
    expected = (
        ("tenant_id", "current_revision_id"),
        "tenant_automation_rule_revisions",
        ("tenant_id", "id"),
    )
    try:
        inspector = inspect(engine)
        actual = {
            fk.get("name"): (
                tuple(fk.get("constrained_columns") or ()),
                fk.get("referred_table"),
                tuple(fk.get("referred_columns") or ()),
            )
            for fk in inspector.get_foreign_keys("tenant_automation_rules")
        }
        assert actual["fk_tenant_automation_rules_current_revision"] == expected
    finally:
        engine.dispose()

    command.downgrade(alembic_config(url), DOWN_REVISION)
    upgrade_0035(url)
    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        actual = {
            fk.get("name"): (
                tuple(fk.get("constrained_columns") or ()),
                fk.get("referred_table"),
                tuple(fk.get("referred_columns") or ()),
            )
            for fk in inspector.get_foreign_keys("tenant_automation_rules")
        }
        assert actual["fk_tenant_automation_rules_current_revision"] == expected
    finally:
        engine.dispose()


def test_sqlite_current_revision_fk_rejects_cross_tenant_pointer(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "automation-workflows-current-revision-scope.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT OR IGNORE INTO tenants "
                    "(id,name,plan,status,quota_documents,quota_chunks,doc_count,chunk_count,created_at) "
                    "VALUES (:id,:name,'enterprise','active',1000,100000,0,0,CURRENT_TIMESTAMP)"
                ),
                [{"id": "tenant-a", "name": "Tenant A"}, {"id": "tenant-b", "name": "Tenant B"}],
            )
            connection.execute(
                text(
                    "INSERT INTO tenant_automation_rules "
                    "(id,tenant_id,name,normalized_name,status,active_rule_key,revision,current_revision_id,"
                    "workspace_id,dataset_id,priority,created_at,created_by,updated_at,updated_by,archived_at,archived_by) "
                    "VALUES (:id,:tenant,:name,:normalized,'draft',NULL,1,NULL,NULL,NULL,100,"
                    "CURRENT_TIMESTAMP,'owner',CURRENT_TIMESTAMP,'owner',NULL,NULL)"
                ),
                [
                    {
                        "id": "rule-a",
                        "tenant": "tenant-a",
                        "name": "Rule A",
                        "normalized": "rule a",
                    },
                    {
                        "id": "rule-b",
                        "tenant": "tenant-b",
                        "name": "Rule B",
                        "normalized": "rule b",
                    },
                ],
            )
            connection.execute(
                text(
                    "INSERT INTO tenant_automation_rule_revisions "
                    "(id,tenant_id,rule_id,revision,trigger_code,condition_code,condition_params_json,"
                    "action_plan_json,definition_digest,created_at,created_by) "
                    "VALUES (:id,:tenant,:rule,1,'task_failed','always','{}','[]',:digest,"
                    "CURRENT_TIMESTAMP,'owner')"
                ),
                [
                    {
                        "id": "revision-a",
                        "tenant": "tenant-a",
                        "rule": "rule-a",
                        "digest": "a" * 64,
                    },
                    {
                        "id": "revision-b",
                        "tenant": "tenant-b",
                        "rule": "rule-b",
                        "digest": "b" * 64,
                    },
                ],
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE tenant_automation_rules SET current_revision_id='revision-b' "
                        "WHERE tenant_id='tenant-a' AND id='rule-a'"
                    )
                )
    finally:
        engine.dispose()


def test_sqlite_composite_fk_supports_both_cyclic_ddl_orders_and_cleanup() -> None:
    table_sql = {
        "rules": (
            "CREATE TABLE tenant_automation_rules ("
            "id TEXT NOT NULL PRIMARY KEY, "
            "tenant_id TEXT NOT NULL, "
            "current_revision_id TEXT NULL, "
            "UNIQUE (tenant_id, id), "
            "FOREIGN KEY (tenant_id, current_revision_id) "
            "REFERENCES tenant_automation_rule_revisions (tenant_id, id)"
            ")"
        ),
        "revisions": (
            "CREATE TABLE tenant_automation_rule_revisions ("
            "id TEXT NOT NULL PRIMARY KEY, "
            "tenant_id TEXT NOT NULL, "
            "rule_id TEXT NOT NULL, "
            "UNIQUE (tenant_id, id), "
            "FOREIGN KEY (tenant_id, rule_id) "
            "REFERENCES tenant_automation_rules (tenant_id, id)"
            ")"
        ),
    }

    for first, second in (("rules", "revisions"), ("revisions", "rules")):
        engine = engine_for("sqlite:///:memory:")
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                connection.commit()
                connection.exec_driver_sql(table_sql[first])
                connection.exec_driver_sql(table_sql[second])
                connection.commit()
                assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
                connection.exec_driver_sql(
                    "INSERT INTO tenant_automation_rules "
                    "(id,tenant_id,current_revision_id) VALUES ('rule-a','tenant-a',NULL)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO tenant_automation_rules "
                    "(id,tenant_id,current_revision_id) VALUES ('rule-b','tenant-b',NULL)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO tenant_automation_rule_revisions "
                    "(id,tenant_id,rule_id) VALUES ('revision-a','tenant-a','rule-a')"
                )
                connection.exec_driver_sql(
                    "INSERT INTO tenant_automation_rule_revisions "
                    "(id,tenant_id,rule_id) VALUES ('revision-b','tenant-b','rule-b')"
                )
                connection.exec_driver_sql(
                    "UPDATE tenant_automation_rules SET current_revision_id='revision-a' "
                    "WHERE tenant_id='tenant-a' AND id='rule-a'"
                )
                connection.commit()
                with pytest.raises(IntegrityError):
                    connection.exec_driver_sql(
                        "UPDATE tenant_automation_rules SET current_revision_id='revision-b' "
                        "WHERE tenant_id='tenant-a' AND id='rule-a'"
                    )
                connection.rollback()
                with pytest.raises(IntegrityError):
                    connection.exec_driver_sql(
                        "UPDATE tenant_automation_rules SET current_revision_id='missing' "
                        "WHERE tenant_id='tenant-a' AND id='rule-a'"
                    )
                connection.rollback()
                connection.exec_driver_sql(
                    "UPDATE tenant_automation_rules SET current_revision_id=NULL"
                )
                connection.exec_driver_sql("DELETE FROM tenant_automation_rule_revisions")
                connection.exec_driver_sql("DELETE FROM tenant_automation_rules")
                connection.commit()
                connection.exec_driver_sql("DROP TABLE tenant_automation_rule_revisions")
                connection.exec_driver_sql("DROP TABLE tenant_automation_rules")
                connection.commit()
        finally:
            engine.dispose()


def test_automation_checks_are_allowlisted_and_body_free(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "automation-workflows-checks.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        inspector = inspect(engine)
        forbidden = {
            "sql",
            "query",
            "url",
            "webhook",
            "code",
            "script",
            "token",
            "credential",
            "ticket",
            "raw_payload",
            "body",
            "prompt",
        }
        for table in TABLES:
            assert forbidden.isdisjoint(
                {str(column["name"]) for column in inspector.get_columns(table)}
            )
        revisions = _checks(inspector, "tenant_automation_rule_revisions")
        assert "task_failed" in revisions["ck_tenant_automation_rule_revisions_trigger"]
        assert "severity_at_least" in revisions["ck_tenant_automation_rule_revisions_condition"]
        actions = _checks(inspector, "tenant_automation_action_requests")
        assert "notify_operator" in actions["ck_tenant_automation_action_requests_action"]
        events = _checks(inspector, "tenant_automation_events")
        assert "run_completed" in events["ck_tenant_automation_events_type"]
    finally:
        engine.dispose()


def test_rule_revision_and_event_guards_are_immutable_and_chain_checked(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "automation-workflows-guards.db")
    _upgrade(url)
    engine = engine_for(url)
    try:
        definitions = {
            str(name): str(sql or "").casefold()
            for name, sql in engine.connect().execute(
                text(
                    "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_tenant_automation_%'"
                )
            )
        }
        for name in {
            "trg_tenant_automation_rule_revisions_no_update",
            "trg_tenant_automation_rule_revisions_no_delete",
            "trg_tenant_automation_events_no_update",
            "trg_tenant_automation_events_no_delete",
            "trg_tenant_automation_events_validate_insert",
        }:
            assert name in definitions
        assert (
            "previous_event_digest" in definitions["trg_tenant_automation_events_validate_insert"]
        )
        assert "stream_key" in definitions["trg_tenant_automation_events_validate_insert"]
    finally:
        engine.dispose()


def test_offline_ddl_supports_mysql_postgresql_and_sqlite_fails_closed() -> None:
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
        assert marker in sql
        assert "TENANT_AUTOMATION_EVENTS" in sql
    with pytest.raises(Exception, match="online|SQLite|sqlite"):
        command.upgrade(
            alembic_config("sqlite:///automation-workflows-offline.db", output_buffer=StringIO()),
            f"{DOWN_REVISION}:{REVISION}",
            sql=True,
        )


def test_migration_fails_closed_for_unknown_dialect(monkeypatch: pytest.MonkeyPatch) -> None:
    migration = migration_module()
    monkeypatch.setattr(migration, "_dialect_name", lambda: "oracle")
    monkeypatch.setattr(migration.context, "is_offline_mode", lambda: False)
    monkeypatch.setattr(migration.op, "create_table", lambda *args, **kwargs: None)
    monkeypatch.setattr(migration.op, "create_index", lambda *args, **kwargs: None)
    monkeypatch.setattr(migration.op, "execute", lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError, match="unsupported|dialect"):
        migration.upgrade()


def test_clean_downgrade_restores_0034_and_nonempty_authority_blocks(tmp_path: Path) -> None:
    clean_url = sqlite_url(tmp_path / "automation-workflows-clean-down.db")
    _upgrade(clean_url)
    command.downgrade(alembic_config(clean_url), DOWN_REVISION)
    clean_engine = engine_for(clean_url)
    try:
        assert TABLES.isdisjoint(inspect(clean_engine).get_table_names())
    finally:
        clean_engine.dispose()

    blocked_url = sqlite_url(tmp_path / "automation-workflows-blocked-down.db")
    _upgrade(blocked_url)
    blocked_engine = engine_for(blocked_url)
    with blocked_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenant_automation_rules "
                "(id,tenant_id,name,normalized_name,status,active_rule_key,revision,priority,created_at,created_by,updated_at,updated_by) "
                "VALUES ('rule-a','tenant-a','Rule A','rule a','draft',NULL,1,100,CURRENT_TIMESTAMP,'owner-a',CURRENT_TIMESTAMP,'owner-a')"
            )
        )
    blocked_engine.dispose()
    with pytest.raises(Exception, match="0035 downgrade blocked|Automation"):
        command.downgrade(alembic_config(blocked_url), DOWN_REVISION)
