from __future__ import annotations

from io import StringIO
import os
from datetime import datetime
from pathlib import Path
import uuid

import pytest

from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, Table, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core import catalog_schema
from core.catalog_schema import _alembic_config
from models.orm import DataSourceRecord, Tenant


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _uniques(inspector, table: str) -> dict[str, tuple[str, ...]]:
    return {
        str(item.get("name")): tuple(item.get("column_names") or ())
        for item in inspector.get_unique_constraints(table)
    }


def _foreign_keys(inspector, table: str) -> dict[str, tuple[tuple[str, ...], str, tuple[str, ...]]]:
    return {
        str(item.get("name")): (
            tuple(item.get("constrained_columns") or ()),
            str(item.get("referred_table")),
            tuple(item.get("referred_columns") or ()),
        )
        for item in inspector.get_foreign_keys(table)
    }


def _add_legacy_dataset(session: Session, *, tenant_id: str, dataset_id: str) -> None:
    """Insert the pre-0019 dataset shape while testing historical migrations."""
    now = datetime.utcnow()
    session.execute(
        text(
            "INSERT INTO datasets "
            "(id, tenant_id, name, description, status, profile_revision, owner_id, "
            "visibility, profile_json, parser_policy, chunk_policy, retrieval_policy, "
            "retention_policy, metadata_policy, default_language, graph_enabled, qa_enabled, "
            "archived_at, archived_by, mutation_generation, serving_generation, doc_count, "
            "chunk_count, created_at, updated_at) VALUES "
            "(:dataset_id, :tenant_id, 'Dataset', '', 'active', 1, NULL, 'private', "
            "'{}', '{}', '{}', '{}', '{}', '{}', 'zh-CN', 0, 1, NULL, NULL, 0, 0, 0, 0, "
            ":now, :now)"
        ),
        {"dataset_id": dataset_id, "tenant_id": tenant_id, "now": now},
    )


def _source_run_values_0013(
    run_id: str,
    *,
    source_id: str,
    tenant_id: str,
    dataset_id: str,
    status: str = "running",
    trigger: str = "manual",
    retry_of_run_id: str | None = None,
    idempotency_key: str | None = None,
    request_hash: str | None = None,
    execution_state: str = "pending",
    execution_owner: str = "",
) -> dict[str, object]:
    now = datetime.utcnow()
    return {
        "id": run_id,
        "source_id": source_id,
        "tenant_id": tenant_id,
        "dataset_id": dataset_id,
        "status": status,
        "trigger": trigger,
        "force_full": 0,
        "dry_run": 0,
        "source_generation": 0,
        "dataset_generation": 0,
        "idempotency_key": idempotency_key,
        "request_hash": request_hash,
        "retry_of_run_id": retry_of_run_id,
        "execution_state": execution_state,
        "execution_owner": execution_owner,
        "execution_lease_until": None,
        "execution_heartbeat_at": None,
        "execution_attempts": 0,
        "execution_last_error": "",
        "execution_started_at": None,
        "execution_finished_at": None,
        "execution_next_attempt_at": now if execution_state == "pending" else None,
        "reservation_owner": "",
        "reservation_lease_until": None,
        "reservation_attempts": 0,
        "pending_deletes": 0,
        "cursor_before": {},
        "cursor_after": {},
        "fetched": 0,
        "ingested": 0,
        "skipped": 0,
        "removed": 0,
        "chunks": 0,
        "failed": 0,
        "fetch_error": "",
        "duration_ms": 0,
        "started_at": now,
        "finished_at": now if status != "running" else None,
        "created_at": now,
    }


def test_0013_sqlite_upgrade_downgrade_reupgrade_and_head(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path / "source-control.db")
    config = _alembic_config(url)
    command.upgrade(config, "0012_retrieval_experiments")

    engine = create_engine(url)
    assert not {"idempotency_key", "request_hash", "retry_of_run_id"} <= {
        column["name"] for column in inspect(engine).get_columns("source_sync_runs")
    }
    engine.dispose()

    command.upgrade(config, "0013_source_control")
    engine = create_engine(url)
    inspector = inspect(engine)
    columns = {column["name"]: column for column in inspector.get_columns("source_sync_runs")}
    assert {
        "idempotency_key",
        "request_hash",
        "retry_of_run_id",
        "execution_state",
        "execution_owner",
        "execution_lease_until",
        "execution_heartbeat_at",
        "execution_attempts",
        "execution_last_error",
        "execution_started_at",
        "execution_finished_at",
        "execution_next_attempt_at",
        "reservation_owner",
        "reservation_lease_until",
        "reservation_attempts",
    } <= set(columns)
    assert columns["idempotency_key"]["nullable"] is True
    assert columns["request_hash"]["nullable"] is True
    assert columns["retry_of_run_id"]["nullable"] is True
    uniques = _uniques(inspector, "source_sync_runs")
    assert uniques["uq_source_sync_runs_idempotency"] == (
        "source_id",
        "source_generation",
        "dataset_generation",
        "idempotency_key",
    )
    assert uniques["uq_source_sync_runs_retry_of"] == ("retry_of_run_id",)
    assert _foreign_keys(inspector, "source_sync_runs")["fk_source_sync_runs_retry_of"] == (
        ("retry_of_run_id",),
        "source_sync_runs",
        ("id",),
    )
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0013_source_control"
        )
    engine.dispose()

    command.downgrade(config, "0012_retrieval_experiments")
    engine = create_engine(url)
    assert not {"idempotency_key", "request_hash", "retry_of_run_id"} & {
        column["name"] for column in inspect(engine).get_columns("source_sync_runs")
    }
    engine.dispose()
    command.upgrade(config, "0013_source_control")

    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_current_head() == "0029_enterprise_knowledge_base_releases"
    assert catalog_schema.HEAD_REVISION == "0029_enterprise_knowledge_base_releases"


def test_0013_mysql_offline_ddl_contains_source_run_fences() -> None:
    config = _alembic_config("mysql+pymysql://user:pass@localhost/rag4c")
    output = StringIO()
    config.output_buffer = output
    command.upgrade(config, "0012_retrieval_experiments:0013_source_control", sql=True)
    ddl = output.getvalue().upper()
    assert "ADD COLUMN IDEMPOTENCY_KEY VARCHAR(128)" in ddl
    assert "ADD COLUMN REQUEST_HASH VARCHAR(64)" in ddl
    assert "ADD COLUMN RETRY_OF_RUN_ID VARCHAR(64)" in ddl
    assert "ADD COLUMN EXECUTION_STATE VARCHAR(16)" in ddl
    assert "ADD COLUMN EXECUTION_OWNER VARCHAR(128)" in ddl
    assert "ADD COLUMN EXECUTION_LEASE_UNTIL DATETIME(6)" in ddl
    assert "ADD COLUMN EXECUTION_HEARTBEAT_AT DATETIME(6)" in ddl
    assert "ADD COLUMN EXECUTION_ATTEMPTS INTEGER" in ddl
    assert "ADD COLUMN EXECUTION_LAST_ERROR TEXT" in ddl
    assert "EXECUTION_LAST_ERROR TEXT NOT NULL DEFAULT" not in ddl
    assert "UPDATE SOURCE_SYNC_RUNS SET EXECUTION_LAST_ERROR = '' " in ddl
    assert "MODIFY EXECUTION_LAST_ERROR TEXT NOT NULL" in ddl
    assert (
        ddl.index("ADD COLUMN EXECUTION_LAST_ERROR TEXT")
        < ddl.index("UPDATE SOURCE_SYNC_RUNS SET EXECUTION_LAST_ERROR = '' ")
        < ddl.index("MODIFY EXECUTION_LAST_ERROR TEXT NOT NULL")
    )
    assert "ADD COLUMN EXECUTION_STARTED_AT DATETIME(6)" in ddl
    assert "ADD COLUMN EXECUTION_FINISHED_AT DATETIME(6)" in ddl
    assert "ADD COLUMN EXECUTION_NEXT_ATTEMPT_AT DATETIME(6)" in ddl
    assert "ADD COLUMN RESERVATION_OWNER VARCHAR(128)" in ddl
    assert "ADD COLUMN RESERVATION_LEASE_UNTIL DATETIME(6)" in ddl
    assert "ADD COLUMN RESERVATION_ATTEMPTS INTEGER" in ddl
    assert "CK_SOURCE_SYNC_RUNS_RESERVATION_OWNER" in ddl
    assert "CK_SOURCE_SYNC_RUNS_RESERVATION_ATTEMPTS" in ddl
    assert "SET EXECUTION_STATE='PENDING', EXECUTION_NEXT_ATTEMPT_AT=CURRENT_TIMESTAMP" in ddl
    assert "WHERE SOURCE_SYNC_RUNS.STATUS = 'RUNNING'" in ddl
    assert "SET EXECUTION_STATE='COMPLETED', EXECUTION_NEXT_ATTEMPT_AT=NULL" in ddl
    assert "COLLATE UTF8MB4_BIN" in ddl
    assert "UQ_SOURCE_SYNC_RUNS_IDEMPOTENCY" in ddl
    assert "UQ_SOURCE_SYNC_RUNS_RETRY_OF" in ddl
    assert "FK_SOURCE_SYNC_RUNS_RETRY_OF" in ddl
    assert "CK_SOURCE_SYNC_RUNS_IDEMPOTENCY_PAIR" in ddl
    assert "CK_SOURCE_SYNC_RUNS_RETRY_NOT_SELF" in ddl
    assert "CK_SOURCE_SYNC_RUNS_RETRY_TRIGGER" in ddl
    assert "`TRIGGER` = 'RETRY'" in ddl
    assert "(TRIGGER = 'RETRY'" not in ddl
    assert "CK_SOURCE_SYNC_RUNS_EXECUTION_STATE" in ddl

    downgrade = _alembic_config("mysql+pymysql://user:pass@localhost/rag4c")
    down_output = StringIO()
    downgrade.output_buffer = down_output
    command.downgrade(
        downgrade,
        "0013_source_control:0012_retrieval_experiments",
        sql=True,
    )
    down = down_output.getvalue().upper()
    assert down.index("FK_SOURCE_SYNC_RUNS_RETRY_OF") < down.index("DROP COLUMN RETRY_OF_RUN_ID")


def test_source_sync_run_database_checks_reject_invalid_authority(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path / "source-control-checks.db")
    config = _alembic_config(url)
    command.upgrade(config, "0013_source_control")
    engine = create_engine(url)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-1", name="Tenant"))
        _add_legacy_dataset(session, tenant_id="tenant-1", dataset_id="dataset-1")
        session.add(
            DataSourceRecord(
                id="source-1",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name="Source",
                source_type="local_dir",
            )
        )
        session.commit()

    table = Table("source_sync_runs", MetaData(), autoload_with=engine)
    invalid_runs = [
        _source_run_values_0013(
            "run-unpaired",
            source_id="source-1",
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            idempotency_key="Only-Key",
        ),
        _source_run_values_0013(
            "run-self-retry",
            source_id="source-1",
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            trigger="retry",
            retry_of_run_id="run-self-retry",
        ),
        _source_run_values_0013(
            "run-retry-missing-origin",
            source_id="source-1",
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            trigger="retry",
        ),
        _source_run_values_0013(
            "run-invalid-claim",
            source_id="source-1",
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            execution_state="executing",
            execution_owner="",
        ),
    ]
    for invalid in invalid_runs:
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(table.insert().values(**invalid))
    engine.dispose()


def test_0013_populated_retry_rows_downgrade_and_reupgrade_deterministically(
    tmp_path: Path,
) -> None:
    url = _sqlite_url(tmp_path / "source-control-populated.db")
    config = _alembic_config(url)
    command.upgrade(config, "0013_source_control")
    engine = create_engine(url)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-populated", name="Tenant"))
        _add_legacy_dataset(session, tenant_id="tenant-populated", dataset_id="dataset-populated")
        session.add(
            DataSourceRecord(
                id="source-populated",
                tenant_id="tenant-populated",
                dataset_id="dataset-populated",
                name="Source",
                source_type="local_dir",
            )
        )
        session.commit()
    table = Table("source_sync_runs", MetaData(), autoload_with=engine)
    with engine.begin() as connection:
        connection.execute(
            table.insert(),
            [
                _source_run_values_0013(
                    "run-origin",
                    source_id="source-populated",
                    tenant_id="tenant-populated",
                    dataset_id="dataset-populated",
                    status="failed",
                    execution_state="completed",
                ),
                _source_run_values_0013(
                    "run-retry",
                    source_id="source-populated",
                    tenant_id="tenant-populated",
                    dataset_id="dataset-populated",
                    trigger="retry",
                    retry_of_run_id="run-origin",
                ),
            ],
        )
    engine.dispose()

    command.downgrade(config, "0012_retrieval_experiments")
    engine = create_engine(url)
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT trigger, status, finished_at, fetch_error "
                "FROM source_sync_runs WHERE id = 'run-retry'"
            )
        ).one()
        assert row[0] == "manual"
        assert row[1] == "failed"
        assert row[2] is not None
        assert "interrupted" in row[3]
    assert "retry_of_run_id" not in {
        column["name"] for column in inspect(engine).get_columns("source_sync_runs")
    }
    engine.dispose()

    command.upgrade(config, "0013_source_control")
    engine = create_engine(url)
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT trigger, retry_of_run_id FROM source_sync_runs WHERE id = 'run-retry'")
        ).one()
        assert tuple(row) == ("manual", None)
    engine.dispose()


def test_0013_upgrade_normalizes_legacy_running_rows_to_pending(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path / "source-control-legacy-running.db")
    config = _alembic_config(url)
    command.upgrade(config, "0012_retrieval_experiments")
    engine = create_engine(url)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-legacy", name="Tenant"))
        _add_legacy_dataset(session, tenant_id="tenant-legacy", dataset_id="dataset-legacy")
        session.add(
            DataSourceRecord(
                id="source-legacy",
                tenant_id="tenant-legacy",
                dataset_id="dataset-legacy",
                name="Source",
                source_type="local_dir",
            )
        )
        session.commit()
    table = Table("source_sync_runs", MetaData(), autoload_with=engine)
    now = datetime.utcnow()
    base = {
        "source_id": "source-legacy",
        "tenant_id": "tenant-legacy",
        "dataset_id": "dataset-legacy",
        "trigger": "manual",
        "force_full": 0,
        "dry_run": 0,
        "source_generation": 0,
        "dataset_generation": 0,
        "pending_deletes": 0,
        "cursor_before": {},
        "cursor_after": {},
        "fetched": 0,
        "ingested": 0,
        "skipped": 0,
        "removed": 0,
        "chunks": 0,
        "failed": 0,
        "fetch_error": "",
        "duration_ms": 0,
        "started_at": now,
        "created_at": now,
    }
    with engine.begin() as connection:
        connection.execute(
            table.insert(),
            [
                {**base, "id": "legacy-running", "status": "running", "finished_at": None},
                {**base, "id": "legacy-completed", "status": "completed", "finished_at": now},
            ],
        )
    engine.dispose()

    command.upgrade(config, "0013_source_control")
    engine = create_engine(url)
    with engine.connect() as connection:
        running = connection.execute(
            text(
                "SELECT execution_state, execution_next_attempt_at "
                "FROM source_sync_runs WHERE id='legacy-running'"
            )
        ).one()
        completed = connection.execute(
            text(
                "SELECT execution_state, execution_next_attempt_at "
                "FROM source_sync_runs WHERE id='legacy-completed'"
            )
        ).one()
    assert running[0] == "pending"
    assert running[1] is not None
    assert tuple(completed) == ("completed", None)
    engine.dispose()


@pytest.mark.skipif(not os.getenv("TEST_MYSQL_URL"), reason="TEST_MYSQL_URL not configured")
def test_0013_disposable_mysql_legacy_running_and_populated_roundtrip() -> None:
    source_url = make_url(os.environ["TEST_MYSQL_URL"])
    assert source_url.get_backend_name() == "mysql"
    database = f"rag4c_source_r4_{uuid.uuid4().hex[:12]}"
    admin_engine = create_engine(source_url.set(database="mysql"))
    test_url = source_url.set(database=database)
    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4"))
        config = _alembic_config(test_url.render_as_string(hide_password=False))
        command.upgrade(config, "0012_retrieval_experiments")
        engine = create_engine(test_url)
        with Session(engine) as session:
            session.add(Tenant(id="tenant-mysql", name="Tenant"))
            _add_legacy_dataset(session, tenant_id="tenant-mysql", dataset_id="dataset-mysql")
            session.add(
                DataSourceRecord(
                    id="source-mysql",
                    tenant_id="tenant-mysql",
                    dataset_id="dataset-mysql",
                    name="Source",
                    source_type="local_dir",
                )
            )
            session.commit()
        table = Table("source_sync_runs", MetaData(), autoload_with=engine)
        now = datetime.utcnow()
        with engine.begin() as connection:
            connection.execute(
                table.insert().values(
                    id="legacy-running-mysql",
                    source_id="source-mysql",
                    tenant_id="tenant-mysql",
                    dataset_id="dataset-mysql",
                    status="running",
                    trigger="manual",
                    force_full=0,
                    dry_run=0,
                    source_generation=0,
                    dataset_generation=0,
                    pending_deletes=0,
                    cursor_before={},
                    cursor_after={},
                    fetched=0,
                    ingested=0,
                    skipped=0,
                    removed=0,
                    chunks=0,
                    failed=0,
                    fetch_error="",
                    duration_ms=0,
                    started_at=now,
                    finished_at=None,
                    created_at=now,
                )
            )
        engine.dispose()
        command.upgrade(config, "0013_source_control")
        engine = create_engine(test_url)
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT execution_state, execution_next_attempt_at "
                    "FROM source_sync_runs WHERE id='legacy-running-mysql'"
                )
            ).one()
            assert row[0] == "pending" and row[1] is not None
        engine.dispose()
        command.downgrade(config, "0012_retrieval_experiments")
        command.upgrade(config, "0013_source_control")
    finally:
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{database}`"))
        admin_engine.dispose()


def test_0013_datetime6_type_supports_mariadb_dialect() -> None:
    from sqlalchemy.dialects.mysql.mariadb import MariaDBDialect
    import importlib

    migration = importlib.import_module("catalog_migrations.versions.0013_source_control")
    dialect = MariaDBDialect()
    assert migration._datetime6().dialect_impl(dialect).fsp == 6
    from models.orm import _datetime6 as orm_datetime6

    assert orm_datetime6().dialect_impl(dialect).fsp == 6
