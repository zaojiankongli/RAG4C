from __future__ import annotations

from io import StringIO
from pathlib import Path

from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import Boolean, DateTime, Integer, JSON, String, create_engine, event, inspect, text
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.catalog_schema import HEAD_REVISION, _alembic_config


DATASET_PROFILE_COLUMNS = {
    "profile_revision",
    "owner_id",
    "visibility",
    "profile_json",
    "parser_policy",
    "chunk_policy",
    "retrieval_policy",
    "retention_policy",
    "metadata_policy",
    "default_language",
    "graph_enabled",
    "qa_enabled",
    "archived_at",
    "archived_by",
    "updated_at",
}


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def test_0010_sqlite_upgrade_downgrade_reupgrade_and_head(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path / "dataset-profile-migration.db")
    config = _alembic_config(url)
    command.upgrade(config, "0009_content")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenants (id, name, plan, status, quota_documents, quota_chunks, "
                "doc_count, chunk_count, created_at) VALUES "
                "('tenant-1', 'Tenant', 'free', 'active', 1000, 100000, 0, 0, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO accounts (id, name, email, created_at) VALUES "
                "('owner-1', 'Owner 1', 'owner1@example.test', CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO tenant_members (account_id, tenant_id, role, created_at) VALUES "
                "('owner-1', 'tenant-1', 'owner', CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO datasets (id, tenant_id, name, description, status, doc_count, chunk_count, created_at) "
                "VALUES ('dataset-1', 'tenant-1', 'Knowledge', '', 'active', 0, 0, CURRENT_TIMESTAMP)"
            )
        )
    assert not DATASET_PROFILE_COLUMNS & {
        item["name"] for item in inspect(engine).get_columns("datasets")
    }
    engine.dispose()

    command.upgrade(config, "0010_dataset_profile")
    engine = create_engine(url)
    inspector = inspect(engine)
    assert DATASET_PROFILE_COLUMNS <= {item["name"] for item in inspector.get_columns("datasets")}
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT profile_revision, visibility, profile_json, parser_policy, "
                "chunk_policy, retrieval_policy, retention_policy, metadata_policy, "
                "default_language, graph_enabled, qa_enabled, updated_at "
                "FROM datasets WHERE id='dataset-1'"
            )
        ).one()
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert tuple(row[:11]) == (1, "private", "{}", "{}", "{}", "{}", "{}", "{}", "zh-CN", 0, 1)
    assert row.updated_at is not None
    assert revision == "0010_dataset_profile"
    assert ScriptDirectory.from_config(config).get_current_head() == HEAD_REVISION
    engine.dispose()

    command.downgrade(config, "0009_content")
    engine = create_engine(url)
    assert not DATASET_PROFILE_COLUMNS & {
        item["name"] for item in inspect(engine).get_columns("datasets")
    }
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(url)
    try:
        assert DATASET_PROFILE_COLUMNS <= {
            item["name"] for item in inspect(engine).get_columns("datasets")
        }
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == HEAD_REVISION
            )
    finally:
        engine.dispose()


def test_0010_constraints_foreign_keys_and_mysql_safe_indexes(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path / "dataset-profile-contract.db")
    command.upgrade(_alembic_config(url), "0010_dataset_profile")
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    inspector = inspect(engine)

    checks = {item.get("name") for item in inspector.get_check_constraints("datasets")}
    assert {
        "ck_datasets_profile_revision_positive",
        "ck_datasets_visibility",
        "ck_datasets_status",
    } <= checks
    foreign_keys = {
        (
            item.get("name"),
            tuple(item.get("constrained_columns") or ()),
            item.get("referred_table"),
            tuple(item.get("referred_columns") or ()),
        )
        for item in inspector.get_foreign_keys("datasets")
    }
    assert (
        "fk_datasets_scope_owner_member",
        ("owner_id", "tenant_id"),
        "tenant_members",
        ("account_id", "tenant_id"),
    ) in foreign_keys
    columns = {item["name"]: item["type"] for item in inspector.get_columns("datasets")}
    for index in inspector.get_indexes("datasets"):
        for column_name in index["column_names"]:
            length = getattr(columns[column_name], "length", None)
            assert length is None or length <= 128

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenants (id, name, plan, status, quota_documents, quota_chunks, "
                "doc_count, chunk_count, created_at) VALUES "
                "('tenant-1', 'Tenant 1', 'free', 'active', 1000, 100000, 0, 0, CURRENT_TIMESTAMP), "
                "('tenant-2', 'Tenant 2', 'free', 'active', 1000, 100000, 0, 0, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO accounts (id, name, email, created_at) VALUES "
                "('owner-2', 'Owner 2', 'owner2@example.com', CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO tenant_members (account_id, tenant_id, role, created_at) VALUES "
                "('owner-2', 'tenant-2', 'owner', CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO datasets ("
                "id, tenant_id, name, description, status, doc_count, chunk_count, created_at, "
                "profile_revision, owner_id, visibility, profile_json, parser_policy, chunk_policy, "
                "retrieval_policy, retention_policy, metadata_policy, default_language, graph_enabled, "
                "qa_enabled, archived_at, archived_by, updated_at"
                ") VALUES ("
                "'dataset-1', 'tenant-1', 'Knowledge', '', 'active', 0, 0, CURRENT_TIMESTAMP, "
                "1, NULL, 'private', '{}', '{}', '{}', '{}', '{}', '{}', 'zh-CN', 0, 1, "
                "NULL, NULL, CURRENT_TIMESTAMP"
                ")"
            )
        )
    with Session(engine) as session:
        for statement in (
            "UPDATE datasets SET profile_revision=0 WHERE id='dataset-1'",
            "UPDATE datasets SET visibility='unknown' WHERE id='dataset-1'",
            "UPDATE datasets SET status='unknown' WHERE id='dataset-1'",
            "UPDATE datasets SET owner_id='owner-2' WHERE id='dataset-1'",
        ):
            try:
                session.execute(text(statement))
                session.commit()
            except IntegrityError:
                session.rollback()
            else:
                raise AssertionError(f"constraint accepted invalid statement: {statement}")
    engine.dispose()


def test_dataset_profile_datetime_columns_compile_as_mysql_datetime6() -> None:
    from models.orm import Dataset

    for name in ("archived_at", "updated_at"):
        assert (
            Dataset.__table__.columns[name].type.compile(dialect=mysql.dialect()) == "DATETIME(6)"
        )


def test_0010_mysql_json_columns_do_not_require_json_defaults() -> None:
    output = StringIO()
    config = _alembic_config("mysql+pymysql://user:pass@localhost/rag4c")
    config.output_buffer = output

    command.upgrade(config, "0010_dataset_profile", sql=True)

    ddl = output.getvalue()
    assert "JSON NOT NULL DEFAULT" not in ddl
    assert "updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP" not in ddl
    assert "ADD COLUMN updated_at DATETIME(6);" in ddl
    assert "MODIFY updated_at DATETIME(6) NOT NULL" in ddl
    for name in (
        "profile_json",
        "parser_policy",
        "chunk_policy",
        "retrieval_policy",
        "retention_policy",
        "metadata_policy",
    ):
        assert f"ALTER TABLE datasets MODIFY {name} JSON NOT NULL" in ddl


def test_0010_dataset_profile_columns_have_authoritative_types_and_no_json_defaults(
    tmp_path: Path,
) -> None:
    url = _sqlite_url(tmp_path / "dataset-profile-column-types.db")
    command.upgrade(_alembic_config(url), "0010_dataset_profile")
    engine = create_engine(url)
    try:
        columns = {item["name"]: item for item in inspect(engine).get_columns("datasets")}
        for name in (
            "profile_json",
            "parser_policy",
            "chunk_policy",
            "retrieval_policy",
            "retention_policy",
            "metadata_policy",
        ):
            assert isinstance(columns[name]["type"], JSON)
            assert columns[name].get("default") is None
        assert isinstance(columns["profile_revision"]["type"], Integer)
        assert not isinstance(columns["profile_revision"]["type"], Boolean)
        assert isinstance(columns["graph_enabled"]["type"], Boolean)
        assert isinstance(columns["qa_enabled"]["type"], Boolean)
        assert isinstance(columns["owner_id"]["type"], String)
        assert columns["owner_id"]["type"].length == 64
        assert columns["visibility"]["type"].length == 16
        assert columns["default_language"]["type"].length == 32
        assert columns["archived_by"]["type"].length == 64
        assert isinstance(columns["archived_at"]["type"], DateTime)
        assert isinstance(columns["updated_at"]["type"], DateTime)
    finally:
        engine.dispose()
