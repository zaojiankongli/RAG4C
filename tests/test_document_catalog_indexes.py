from __future__ import annotations

import importlib
from pathlib import Path

from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateIndex

from core import catalog_schema
from core.catalog_schema import _alembic_config
from models.orm import Document


DOCUMENT_CATALOG_INDEXES: dict[str, tuple[str, ...]] = {
    "ix_documents_catalog_scope_updated": (
        "tenant_id",
        "dataset_id",
        "updated_at",
        "id",
    ),
    "ix_documents_catalog_scope_status_updated": (
        "tenant_id",
        "dataset_id",
        "status",
        "updated_at",
        "id",
    ),
    "ix_documents_catalog_scope_doc_type_updated": (
        "tenant_id",
        "dataset_id",
        "doc_type",
        "updated_at",
        "id",
    ),
    "ix_documents_catalog_scope_folder_updated": (
        "tenant_id",
        "dataset_id",
        "logical_folder_path",
        "updated_at",
        "id",
    ),
}


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _indexes(inspector, table: str) -> dict[str, tuple[str, ...]]:
    return {
        str(item.get("name")): tuple(item.get("column_names") or ())
        for item in inspector.get_indexes(table)
        if item.get("name")
    }


def _migration_module():
    return importlib.import_module(
        "catalog_migrations.versions.0015_document_catalog_indexes"
    )


def test_document_catalog_migration_precedes_the_current_application_head() -> None:
    migration = _migration_module()
    scripts = ScriptDirectory.from_config(_alembic_config("sqlite://"))

    assert migration.revision == "0015_document_catalog_indexes"
    assert migration.down_revision == "0014_source_schedules"
    assert scripts.get_current_head() == catalog_schema.HEAD_REVISION


def test_document_catalog_indexes_are_declared_in_orm_and_manifest() -> None:
    metadata_indexes = {
        str(index.name): tuple(column.name for column in index.columns)
        for index in Document.__table__.indexes
        if index.name in DOCUMENT_CATALOG_INDEXES
    }

    assert metadata_indexes == DOCUMENT_CATALOG_INDEXES
    assert {
        name: tuple(columns)
        for name, columns in catalog_schema._HEAD_REQUIRED_INDEXES["documents"].items()
        if name in DOCUMENT_CATALOG_INDEXES
    } == DOCUMENT_CATALOG_INDEXES


def test_document_catalog_indexes_compile_for_mysql() -> None:
    for name, columns in DOCUMENT_CATALOG_INDEXES.items():
        index = next(index for index in Document.__table__.indexes if index.name == name)
        sql = str(CreateIndex(index).compile(dialect=mysql.dialect()))

        assert "CREATE INDEX" in sql
        assert name in sql
        for column in columns:
            assert column in sql


def test_0015_sqlite_upgrade_downgrade_reupgrade_preserves_catalog_indexes(
    tmp_path: Path,
) -> None:
    url = _sqlite_url(tmp_path / "document-catalog-indexes.db")
    config = _alembic_config(url)

    command.upgrade(config, "0014_source_schedules")
    engine = create_engine(url)
    try:
        assert not (set(_indexes(inspect(engine), "documents")) & set(DOCUMENT_CATALOG_INDEXES))
    finally:
        engine.dispose()

    command.upgrade(config, "0015_document_catalog_indexes")
    engine = create_engine(url)
    try:
        assert {
            name: _indexes(inspect(engine), "documents")[name]
            for name in DOCUMENT_CATALOG_INDEXES
        } == DOCUMENT_CATALOG_INDEXES
        with engine.connect() as connection:
            assert connection.execute(
                __import__("sqlalchemy").text("SELECT version_num FROM alembic_version")
            ).scalar_one() == "0015_document_catalog_indexes"
    finally:
        engine.dispose()

    command.downgrade(config, "0014_source_schedules")
    engine = create_engine(url)
    try:
        assert not (set(_indexes(inspect(engine), "documents")) & set(DOCUMENT_CATALOG_INDEXES))
    finally:
        engine.dispose()

    command.upgrade(config, "0015_document_catalog_indexes")
    engine = create_engine(url)
    try:
        assert {
            name: _indexes(inspect(engine), "documents")[name]
            for name in DOCUMENT_CATALOG_INDEXES
        } == DOCUMENT_CATALOG_INDEXES
    finally:
        engine.dispose()


def test_document_catalog_migration_does_not_claim_full_text_search() -> None:
    migration = _migration_module()
    source = Path(migration.__file__).read_text(encoding="utf-8").casefold()

    assert "full-text" in source
    assert "ordinary b-tree" in source
    assert "future" in source

