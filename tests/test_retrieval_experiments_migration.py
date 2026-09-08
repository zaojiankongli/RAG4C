from __future__ import annotations

from io import StringIO
import os
from pathlib import Path
import uuid

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from core import catalog_schema
from core.catalog_schema import (
    CatalogSchemaError, _alembic_config, _trigger_contract_issues,
)
from models.orm import Base, Dataset, RetrievalExperiment, Tenant


TABLES = {"retrieval_experiments", "retrieval_judgments"}


def _assert_live_immutable_triggers(engine) -> None:
    with Session(engine) as session:
        session.add(Tenant(id="trigger-tenant", name="Trigger Tenant"))
        session.add(Dataset(id="trigger-dataset", tenant_id="trigger-tenant", name="Trigger KB"))
        session.flush()
        session.add(RetrievalExperiment(
            id="trigger-experiment", tenant_id="trigger-tenant", dataset_id="trigger-dataset",
            query="trigger", query_hash="a" * 64, strategy_snapshot={}, result_snapshot={},
            evidence_lineage={}, latency_ms=1, status="completed", created_by="tester",
        ))
        session.commit()
    for statement in (
        "UPDATE retrieval_experiments SET status='failed' WHERE id='trigger-experiment'",
        "DELETE FROM retrieval_experiments WHERE id='trigger-experiment'",
    ):
        with pytest.raises(DatabaseError, match="immutable"):
            with engine.begin() as connection:
                connection.execute(text(statement))


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _unique_columns(inspector, table: str) -> set[tuple[str, ...]]:
    return {tuple(item.get("column_names") or ()) for item in inspector.get_unique_constraints(table)}


def _foreign_keys(inspector, table: str) -> set[tuple[tuple[str, ...], tuple[str, ...]]]:
    return {
        (tuple(item.get("constrained_columns") or ()), tuple(item.get("referred_columns") or ()))
        for item in inspector.get_foreign_keys(table)
    }


def _indexes(inspector, table: str) -> dict[str, tuple[str, ...]]:
    return {
        str(item.get("name")): tuple(item.get("column_names") or ())
        for item in inspector.get_indexes(table)
    }


def test_0012_sqlite_upgrade_downgrade_reupgrade_and_head(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path / "retrieval-migration.db")
    config = _alembic_config(url)
    command.upgrade(config, "0011_durable_delete")
    engine = create_engine(url)
    assert not (TABLES & set(inspect(engine).get_table_names()))
    engine.dispose()

    command.upgrade(config, "0012_retrieval_experiments")
    engine = create_engine(url)
    inspector = inspect(engine)
    assert TABLES <= set(inspector.get_table_names())
    assert ("tenant_id", "dataset_id", "id") in _unique_columns(inspector, "retrieval_experiments")
    assert ("tenant_id", "dataset_id", "experiment_id", "result_rank", "created_by") in _unique_columns(
        inspector, "retrieval_judgments"
    )
    judgment_fks = _foreign_keys(inspector, "retrieval_judgments")
    assert (("tenant_id", "dataset_id", "experiment_id"), ("tenant_id", "dataset_id", "id")) in judgment_fks
    assert (("tenant_id", "dataset_id", "document_id"), ("tenant_id", "dataset_id", "id")) in judgment_fks
    assert (("tenant_id", "dataset_id", "chunk_id"), ("tenant_id", "dataset_id", "id")) in judgment_fks
    assert _indexes(inspector, "retrieval_experiments")[
        "ix_retrieval_experiments_scope_run"
    ] == ("tenant_id", "dataset_id", "run_id", "sequence")
    with engine.connect() as connection:
        triggers = {row[0] for row in connection.execute(text(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND tbl_name='retrieval_experiments'"
        ))}
        assert triggers == {
            "trg_retrieval_experiments_no_update",
            "trg_retrieval_experiments_no_delete",
        }
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0012_retrieval_experiments"
    engine.dispose()

    command.downgrade(config, "0011_durable_delete")
    engine = create_engine(url)
    assert not (TABLES & set(inspect(engine).get_table_names()))
    engine.dispose()
    command.upgrade(config, "0012_retrieval_experiments")

    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_revision("0012_retrieval_experiments").down_revision == (
        "0011_durable_delete"
    )


def test_0012_mysql_offline_ddl_is_index_safe_and_orders_foreign_keys() -> None:
    config = _alembic_config("mysql+pymysql://user:pass@localhost/rag4c")
    output = StringIO()
    config.output_buffer = output
    command.upgrade(config, "0011_durable_delete:0012_retrieval_experiments", sql=True)
    ddl = output.getvalue().upper()
    assert "CREATE TABLE RETRIEVAL_EXPERIMENTS" in ddl
    assert "CREATE TABLE RETRIEVAL_JUDGMENTS" in ddl
    assert ddl.index("UQ_CHUNK_HEADS_SCOPE_ID") < ddl.index("FK_RETRIEVAL_JUDGMENTS_SCOPE_CHUNK")
    assert "CHECK (LATENCY_MS >= 0)" in ddl
    assert "CHECK (REVISION > 0)" in ddl
    assert "STRATEGY_SNAPSHOT JSON" in ddl
    assert "RESULT_SNAPSHOT JSON" in ddl
    assert "EVIDENCE_LINEAGE JSON" in ddl
    assert "CREATE TRIGGER TRG_RETRIEVAL_EXPERIMENTS_NO_UPDATE" in ddl
    assert "CREATE TRIGGER TRG_RETRIEVAL_EXPERIMENTS_NO_DELETE" in ddl
    assert "IX_RETRIEVAL_EXPERIMENTS_SCOPE_RUN" in ddl
    index_lines = [line for line in ddl.splitlines() if "CREATE INDEX" in line or "CREATE UNIQUE INDEX" in line]
    assert all("STRATEGY_SNAPSHOT" not in line and "RESULT_SNAPSHOT" not in line and "EVIDENCE_LINEAGE" not in line for line in index_lines)

    downgrade_config = _alembic_config("mysql+pymysql://user:pass@localhost/rag4c")
    downgrade_output = StringIO()
    downgrade_config.output_buffer = downgrade_output
    command.downgrade(
        downgrade_config, "0012_retrieval_experiments:0011_durable_delete", sql=True
    )
    down = downgrade_output.getvalue().upper()
    assert down.index("DROP TRIGGER IF EXISTS TRG_RETRIEVAL_EXPERIMENTS_NO_UPDATE") < down.index(
        "DROP TABLE RETRIEVAL_EXPERIMENTS"
    )
    assert down.index("DROP TRIGGER IF EXISTS TRG_RETRIEVAL_EXPERIMENTS_NO_DELETE") < down.index(
        "DROP TABLE RETRIEVAL_EXPERIMENTS"
    )
    assert down.index("DROP TABLE RETRIEVAL_JUDGMENTS") < down.index("DROP TABLE RETRIEVAL_EXPERIMENTS")
    assert down.index("DROP TABLE RETRIEVAL_EXPERIMENTS") < down.index("UQ_CHUNK_HEADS_SCOPE_ID")


def test_0012_postgresql_offline_ddl_creates_and_drops_immutable_function_first() -> None:
    config = _alembic_config("postgresql+psycopg://user:pass@localhost/rag4c")
    output = StringIO()
    config.output_buffer = output
    command.upgrade(config, "0011_durable_delete:0012_retrieval_experiments", sql=True)
    ddl = output.getvalue().upper()
    assert "CREATE FUNCTION RAG4C_RETRIEVAL_EXPERIMENTS_IMMUTABLE" in ddl
    assert "CREATE TRIGGER TRG_RETRIEVAL_EXPERIMENTS_NO_UPDATE" in ddl
    assert "CREATE TRIGGER TRG_RETRIEVAL_EXPERIMENTS_NO_DELETE" in ddl

    down_config = _alembic_config("postgresql+psycopg://user:pass@localhost/rag4c")
    down_output = StringIO()
    down_config.output_buffer = down_output
    command.downgrade(
        down_config, "0012_retrieval_experiments:0011_durable_delete", sql=True
    )
    down = down_output.getvalue().upper()
    drop_update = down.index("DROP TRIGGER IF EXISTS TRG_RETRIEVAL_EXPERIMENTS_NO_UPDATE")
    drop_delete = down.index("DROP TRIGGER IF EXISTS TRG_RETRIEVAL_EXPERIMENTS_NO_DELETE")
    drop_function = down.index("DROP FUNCTION IF EXISTS RAG4C_RETRIEVAL_EXPERIMENTS_IMMUTABLE")
    drop_table = down.index("DROP TABLE RETRIEVAL_EXPERIMENTS")
    assert max(drop_update, drop_delete) < drop_function < drop_table


def test_trigger_contract_rejects_adversarial_mysql_and_postgresql_metadata() -> None:
    mysql_expected = (
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = "
        "'retrieval_experiments are immutable'"
    )
    valid_mysql = [
        ("trg_retrieval_experiments_no_update", "BEFORE", "UPDATE", mysql_expected, None, "ENABLED"),
        ("trg_retrieval_experiments_no_delete", "BEFORE", "DELETE", mysql_expected, None, "ENABLED"),
    ]
    assert _trigger_contract_issues("mysql", valid_mysql) == ()
    for bad_rows in (
        [(valid_mysql[0][0], "BEFORE", "UPDATE",
          "IF 0 THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='retrieval_experiments are immutable'; END IF",
          None, "ENABLED"), valid_mysql[1]],
        [(valid_mysql[0][0], "AFTER", "UPDATE", mysql_expected, None, "ENABLED"), valid_mysql[1]],
        [(valid_mysql[0][0], "BEFORE", "UPDATE", mysql_expected, "0", "ENABLED"), valid_mysql[1]],
    ):
        assert _trigger_contract_issues("mysql", bad_rows)

    action = "EXECUTE FUNCTION rag4c_retrieval_experiments_immutable()"
    function = (
        "CREATE FUNCTION rag4c_retrieval_experiments_immutable() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
        "'retrieval_experiments are immutable'; END; $$"
    )
    valid_postgres = [
        ("trg_retrieval_experiments_no_update", "BEFORE", "UPDATE", action, None, "O"),
        ("trg_retrieval_experiments_no_delete", "BEFORE", "DELETE", action, None, "O"),
    ]
    assert _trigger_contract_issues("postgresql", valid_postgres, function) == ()
    for bad_rows in (
        [(valid_postgres[0][0], "BEFORE", "UPDATE", action, "false", "O"), valid_postgres[1]],
        [(valid_postgres[0][0], "BEFORE", "UPDATE", action, None, "D"), valid_postgres[1]],
        [(valid_postgres[0][0], "AFTER", "UPDATE", action, None, "O"), valid_postgres[1]],
    ):
        assert _trigger_contract_issues("postgresql", bad_rows, function)
    assert _trigger_contract_issues("postgresql", valid_postgres, None)


def test_manifest_and_stamp_existing_reject_missing_immutable_triggers(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path / "missing-trigger.db")
    catalog_schema.upgrade_catalog(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("DROP TRIGGER trg_retrieval_experiments_no_update"))
    state = catalog_schema.inspect_catalog_schema(engine)
    assert state.status == "incomplete"
    assert any("trg_retrieval_experiments_no_update" in issue for issue in state.schema_issues)
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TRIGGER trg_retrieval_experiments_no_update "
            "BEFORE UPDATE ON retrieval_experiments BEGIN SELECT 1; END"
        ))
    no_op = catalog_schema.inspect_catalog_schema(engine)
    assert no_op.status == "incomplete"
    assert any("invalid immutable trigger" in issue for issue in no_op.schema_issues)
    with engine.begin() as connection:
        connection.execute(text("DROP TRIGGER trg_retrieval_experiments_no_update"))
        connection.execute(text(
            "CREATE TRIGGER trg_retrieval_experiments_no_update BEFORE UPDATE "
            "ON retrieval_experiments WHEN 0 BEGIN "
            "SELECT RAISE(ABORT, 'retrieval_experiments are immutable'); END"
        ))
    when_zero = catalog_schema.inspect_catalog_schema(engine)
    assert any("invalid immutable trigger" in issue for issue in when_zero.schema_issues)
    engine.dispose()

    unstamped_url = _sqlite_url(tmp_path / "unstamped-no-trigger.db")
    unstamped = create_engine(unstamped_url)
    Base.metadata.create_all(unstamped)
    unstamped.dispose()
    with pytest.raises(CatalogSchemaError, match="trigger"):
        catalog_schema.stamp_existing_catalog(unstamped_url)


def test_head_manifest_requires_retrieval_authority(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path / "manifest.db")
    catalog_schema.upgrade_catalog(url)
    engine = create_engine(url)
    state = catalog_schema.verify_catalog_schema(engine)
    assert state.revision == catalog_schema.HEAD_REVISION
    assert TABLES <= catalog_schema.HEAD_CATALOG_TABLES
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE retrieval_judgments"))
    broken = catalog_schema.inspect_catalog_schema(engine)
    assert "retrieval_judgments" in broken.missing_tables
    engine.dispose()


@pytest.mark.skipif(not os.getenv("TEST_MYSQL_URL"), reason="TEST_MYSQL_URL not configured")
def test_0012_disposable_mysql_full_chain_round_trip() -> None:
    source = make_url(os.environ["TEST_MYSQL_URL"])
    assert source.get_backend_name() == "mysql"
    database_name = f"rag4c_retrieval_{uuid.uuid4().hex[:12]}"
    admin_url = source.set(database="mysql")
    test_url = source.set(database=database_name)
    admin_engine = create_engine(admin_url)
    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f"CREATE DATABASE `{database_name}` CHARACTER SET utf8mb4"))
        config = _alembic_config(test_url.render_as_string(hide_password=False))
        command.upgrade(config, "0012_retrieval_experiments")
        engine = create_engine(test_url)
        try:
            assert TABLES <= set(inspect(engine).get_table_names())
            with engine.connect() as connection:
                assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0012_retrieval_experiments"
            _assert_live_immutable_triggers(engine)
        finally:
            engine.dispose()
        command.downgrade(config, "0011_durable_delete")
        command.upgrade(config, "0012_retrieval_experiments")
    finally:
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{database_name}`"))
        admin_engine.dispose()


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="TEST_POSTGRES_URL not configured")
def test_0012_disposable_postgresql_live_triggers() -> None:
    source = make_url(os.environ["TEST_POSTGRES_URL"])
    assert source.get_backend_name() == "postgresql"
    database_name = f"rag4c_retrieval_{uuid.uuid4().hex[:12]}"
    admin_url = source.set(database="postgres")
    test_url = source.set(database=database_name)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        config = _alembic_config(test_url.render_as_string(hide_password=False))
        command.upgrade(config, "0012_retrieval_experiments")
        engine = create_engine(test_url)
        try:
            _assert_live_immutable_triggers(engine)
        finally:
            engine.dispose()
        command.downgrade(config, "0011_durable_delete")
    finally:
        with admin_engine.connect() as connection:
            connection.execute(text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=:name AND pid <> pg_backend_pid()"
            ), {"name": database_name})
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        admin_engine.dispose()
