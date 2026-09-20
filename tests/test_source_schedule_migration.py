from __future__ import annotations

from datetime import datetime, timedelta
from io import StringIO
import importlib
import os
from pathlib import Path
import uuid

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core import catalog_schema
from core.catalog_schema import _alembic_config
from core.knowledge_governance import AuditContext
from tests.head_catalog import align_era_columns


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


def _indexes(inspector, table: str) -> dict[str, tuple[str, ...]]:
    return {
        str(item.get("name")): tuple(item.get("column_names") or ())
        for item in inspector.get_indexes(table)
    }


def _seed_scope(engine) -> None:
    from models.orm import DataSourceRecord, Tenant

    align_era_columns(engine)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-schedule", name="Tenant", plan="enterprise"))
        session.execute(
            text(
                "INSERT INTO datasets "
                "(id, tenant_id, name, description, status, profile_revision, owner_id, "
                "visibility, profile_json, parser_policy, chunk_policy, retrieval_policy, "
                "retention_policy, metadata_policy, default_language, graph_enabled, qa_enabled, "
                "archived_at, archived_by, mutation_generation, serving_generation, doc_count, "
                "chunk_count, created_at, updated_at) VALUES "
                "('dataset-schedule', 'tenant-schedule', 'Dataset', '', 'active', 1, NULL, "
                "'private', '{}', '{}', '{}', '{}', '{}', '{}', 'zh-CN', 0, 1, NULL, NULL, "
                "0, 0, 0, 0, :now, :now)"
            ),
            {"now": datetime.utcnow()},
        )
        session.add(
            DataSourceRecord(
                id="source-schedule",
                tenant_id="tenant-schedule",
                dataset_id="dataset-schedule",
                name="Source",
                source_type="local_dir",
                status="active",
            )
        )
        session.commit()


def test_0014_sqlite_upgrade_downgrade_reupgrade_and_manifest(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path / "source-schedules.db")
    config = _alembic_config(url)
    command.upgrade(config, "0013_source_control")
    engine = create_engine(url)
    assert "source_schedules" not in inspect(engine).get_table_names()
    engine.dispose()

    command.upgrade(config, "0014_source_schedules")
    engine = create_engine(url)
    inspector = inspect(engine)
    run_columns = {column["name"]: column for column in inspector.get_columns("source_sync_runs")}
    assert {"schedule_id", "schedule_revision", "planned_at"} <= set(run_columns)
    assert run_columns["schedule_id"]["nullable"] is True
    assert run_columns["schedule_revision"]["nullable"] is True
    assert run_columns["planned_at"]["nullable"] is True
    assert _foreign_keys(inspector, "source_sync_runs")["fk_source_sync_runs_schedule"] == (
        ("tenant_id", "dataset_id", "source_id", "schedule_id"),
        "source_schedules",
        ("tenant_id", "dataset_id", "source_id", "id"),
    )
    assert _uniques(inspector, "source_schedules")["uq_source_schedules_scope_id"] == (
        "tenant_id",
        "dataset_id",
        "source_id",
        "id",
    )
    run_checks = {
        item["name"]: str(item["sqltext"]).casefold()
        for item in inspector.get_check_constraints("source_sync_runs")
    }
    assert "schedule_revision > 0" in run_checks["ck_source_sync_runs_schedule_revision"]
    metadata_check = run_checks["ck_source_sync_runs_schedule_metadata"].replace("`", "").replace('"', "")
    assert "trigger = 'scheduled'" in metadata_check
    assert "schedule_id is not null" in metadata_check
    assert "planned_at is not null" in metadata_check

    columns = {column["name"]: column for column in inspector.get_columns("source_schedules")}
    assert set(columns) == {
        "id",
        "tenant_id",
        "dataset_id",
        "source_id",
        "revision",
        "status",
        "interval_seconds",
        "force_full",
        "next_run_at",
        "last_enqueued_at",
        "last_run_id",
        "created_by",
        "updated_by",
        "created_at",
        "updated_at",
    }
    for required in (
        "id",
        "tenant_id",
        "dataset_id",
        "source_id",
        "revision",
        "status",
        "interval_seconds",
        "force_full",
        "next_run_at",
        "created_by",
        "updated_by",
        "created_at",
        "updated_at",
    ):
        assert columns[required]["nullable"] is False
    assert columns["last_enqueued_at"]["nullable"] is True
    assert columns["last_run_id"]["nullable"] is True

    uniques = _uniques(inspector, "source_schedules")
    assert uniques["uq_source_schedules_source"] == ("source_id",)
    assert _uniques(inspector, "data_sources")["uq_data_sources_scope_id"] == (
        "tenant_id",
        "dataset_id",
        "id",
    )
    foreign_keys = _foreign_keys(inspector, "source_schedules")
    assert foreign_keys["fk_source_schedules_scope_dataset"] == (
        ("tenant_id", "dataset_id"),
        "datasets",
        ("tenant_id", "id"),
    )
    assert foreign_keys["fk_source_schedules_scope_source"] == (
        ("tenant_id", "dataset_id", "source_id"),
        "data_sources",
        ("tenant_id", "dataset_id", "id"),
    )
    assert foreign_keys["fk_source_schedules_last_run"] == (
        ("last_run_id",),
        "source_sync_runs",
        ("id",),
    )
    indexes = _indexes(inspector, "source_schedules")
    assert indexes["ix_source_schedules_due"] == ("status", "next_run_at", "id")
    assert indexes["ix_source_schedules_status"] == (
        "tenant_id",
        "dataset_id",
        "status",
        "id",
    )
    assert indexes["ix_source_schedules_scope"] == (
        "tenant_id",
        "dataset_id",
        "source_id",
    )
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0014_source_schedules"
        )
    engine.dispose()

    command.downgrade(config, "0013_source_control")
    engine = create_engine(url)
    inspector = inspect(engine)
    assert "source_schedules" not in inspector.get_table_names()
    assert "uq_data_sources_scope_id" not in _uniques(inspector, "data_sources")
    engine.dispose()

    command.upgrade(config, "0014_source_schedules")
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_current_head() == catalog_schema.HEAD_REVISION
    assert "source_schedules" in catalog_schema.HEAD_CATALOG_TABLES


def test_0014_database_checks_reject_invalid_schedule_authority(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path / "source-schedule-checks.db")
    command.upgrade(_alembic_config(url), "0014_source_schedules")
    engine = create_engine(url)
    _seed_scope(engine)
    base = {
        "id": "schedule-valid",
        "tenant_id": "tenant-schedule",
        "dataset_id": "dataset-schedule",
        "source_id": "source-schedule",
        "revision": 1,
        "status": "active",
        "interval_seconds": 300,
        "force_full": 0,
        "next_run_at": datetime(2026, 8, 25, 0, 5, 0),
        "created_by": "actor",
        "updated_by": "actor",
        "created_at": datetime(2026, 8, 25, 0, 0, 0),
        "updated_at": datetime(2026, 8, 25, 0, 0, 0),
    }
    columns = ", ".join(base)
    values = ", ".join(f":{key}" for key in base)
    with engine.begin() as connection:
        connection.execute(
            text(f"INSERT INTO source_schedules ({columns}) VALUES ({values})"), base
        )
    for suffix, changes in (
        ("revision", {"revision": 0}),
        ("status", {"status": "cron"}),
        ("short", {"interval_seconds": 299}),
        ("long", {"interval_seconds": 604801}),
        ("force", {"force_full": 2}),
    ):
        invalid = {**base, "id": f"schedule-{suffix}", "source_id": f"source-{suffix}", **changes}
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO data_sources (id, tenant_id, dataset_id, name, source_type, effective_config, "
                    "config_fingerprint, status, last_cursor, last_result, last_error, mutation_generation, created_at, updated_at) "
                    "VALUES (:id, 'tenant-schedule', 'dataset-schedule', :name, 'local_dir', '{}', '', "
                    "'active', '{}', '{}', '', 0, :now, :now)"
                ),
                {
                    "id": invalid["source_id"],
                    "name": invalid["source_id"],
                    "now": base["created_at"],
                },
            )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(f"INSERT INTO source_schedules ({columns}) VALUES ({values})"), invalid
                )
    engine.dispose()


def test_0014_mysql_offline_ddl_is_fixed_interval_and_datetime6() -> None:
    config = _alembic_config("mysql+pymysql://user:pass@localhost/rag4c")
    output = StringIO()
    config.output_buffer = output
    command.upgrade(config, "0013_source_control:0014_source_schedules", sql=True)
    ddl = output.getvalue().upper()
    assert "CREATE TABLE SOURCE_SCHEDULES" in ddl
    assert "NEXT_RUN_AT DATETIME(6) NOT NULL" in ddl
    assert "LAST_ENQUEUED_AT DATETIME(6)" in ddl
    assert "CREATED_AT DATETIME(6) NOT NULL" in ddl
    assert "UPDATED_AT DATETIME(6) NOT NULL" in ddl
    assert "INTERVAL_SECONDS >= 300 AND INTERVAL_SECONDS <= 604800" in ddl
    assert "STATUS IN ('ACTIVE', 'PAUSED', 'ARCHIVED')" in ddl
    assert "CRON" not in ddl
    assert "CALENDAR" not in ddl
    assert "ADD COLUMN SCHEDULE_ID VARCHAR(64)" in ddl
    assert "ADD COLUMN SCHEDULE_REVISION INTEGER" in ddl
    assert "ADD COLUMN PLANNED_AT DATETIME(6)" in ddl
    assert "FK_SOURCE_SYNC_RUNS_SCHEDULE" in ddl
    assert "CK_SOURCE_SYNC_RUNS_SCHEDULE_METADATA" in ddl
    assert "`TRIGGER` = 'SCHEDULED'" in ddl
    assert "`TRIGGER` <> 'SCHEDULED'" in ddl
    assert "WITH VARIANT" not in ddl


def test_0014_datetime6_type_supports_mariadb_dialect() -> None:
    from sqlalchemy.dialects.mysql.mariadb import MariaDBDialect

    migration = importlib.import_module("catalog_migrations.versions.0014_source_schedules")
    assert migration._datetime6().dialect_impl(MariaDBDialect()).fsp == 6
    from models.orm import SourceSchedule, SourceSyncRun

    assert SourceSchedule.__table__.c.next_run_at.type.dialect_impl(MariaDBDialect()).fsp == 6
    assert SourceSyncRun.__table__.c.planned_at.type.dialect_impl(MariaDBDialect()).fsp == 6


def test_0014_populated_sqlite_downgrade_reupgrade_preserves_run_history(
    tmp_path: Path,
) -> None:
    from core.db_clock import read_db_utc
    from core.source_schedules import SourceScheduleRepository
    from models.orm import SourceSchedule

    url = _sqlite_url(tmp_path / "source-schedule-populated-roundtrip.db")
    config = _alembic_config(url)
    # The repository uses the current Dataset mapper, so exercise the populated
    # historical downgrade from the complete application schema.
    command.upgrade(config, "head")
    engine = create_engine(url)
    _seed_scope(engine)
    repository = SourceScheduleRepository(engine)
    schedule = repository.put(
        "tenant-schedule",
        "dataset-schedule",
        "source-schedule",
        expected_revision=0,
        interval_seconds=300,
        force_full=False,
        status="active",
        audit=AuditContext.system("operator", "populated-roundtrip"),
    )
    with Session(engine) as session:
        now = read_db_utc(session)
        session.get(SourceSchedule, schedule.id).next_run_at = now - timedelta(seconds=1)
        session.commit()
    run_id = repository.enqueue_due(limit=1)[0].run_id
    engine.dispose()

    command.downgrade(config, "0013_source_control")
    engine = create_engine(url)
    inspector = inspect(engine)
    assert "source_schedules" not in inspector.get_table_names()
    assert not {"schedule_id", "schedule_revision", "planned_at"} & {
        column["name"] for column in inspector.get_columns("source_sync_runs")
    }
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT trigger FROM source_sync_runs WHERE id=:run_id"), {"run_id": run_id}
            ).scalar_one()
            == "scheduled"
        )
    engine.dispose()

    command.upgrade(config, "0014_source_schedules")
    engine = create_engine(url)
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT trigger, schedule_id, schedule_revision, planned_at "
                "FROM source_sync_runs WHERE id=:run_id"
            ),
            {"run_id": run_id},
        ).one()
    assert tuple(row) == ("manual", None, None, None)
    engine.dispose()


@pytest.mark.skipif(not os.getenv("TEST_MYSQL_URL"), reason="TEST_MYSQL_URL not configured")
def test_0014_disposable_mysql_populated_roundtrip() -> None:
    source_url = make_url(os.environ["TEST_MYSQL_URL"])
    assert source_url.get_backend_name() == "mysql"
    database = f"rag4c_schedule_r4_{uuid.uuid4().hex[:12]}"
    admin_engine = create_engine(source_url.set(database="mysql"))
    test_url = source_url.set(database=database)
    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4"))
        config = _alembic_config(test_url.render_as_string(hide_password=False))
        command.upgrade(config, "0014_source_schedules")
        engine = create_engine(test_url)
        _seed_scope(engine)
        from core.source_schedules import SourceScheduleRepository

        schedule = SourceScheduleRepository(engine).put(
            "tenant-schedule",
            "dataset-schedule",
            "source-schedule",
            expected_revision=0,
            interval_seconds=300,
            force_full=True,
            status="active",
            audit=AuditContext.system("operator", "mysql-schedule-roundtrip"),
        )
        assert schedule.revision == 1
        engine.dispose()
        command.downgrade(config, "0013_source_control")
        command.upgrade(config, "0014_source_schedules")
        engine = create_engine(test_url)
        inspector = inspect(engine)
        assert (
            getattr(
                {column["name"]: column for column in inspector.get_columns("source_sync_runs")}[
                    "planned_at"
                ]["type"],
                "fsp",
                None,
            )
            == 6
        )
        engine.dispose()
    finally:
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{database}`"))
        admin_engine.dispose()
