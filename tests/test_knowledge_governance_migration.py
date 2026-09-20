from __future__ import annotations

from tests.head_catalog import align_era_columns
import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.catalog_schema import _alembic_config
from models.orm import (
    Dataset,
    Document,
    DocumentTag,
    KnowledgeFolder,
    KnowledgeTag,
    Tenant,
)


GOVERNANCE_TABLES = {
    "knowledge_folders",
    "knowledge_tags",
    "document_tags",
    "knowledge_audit_events",
}


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _constraint_columns(inspector, table: str) -> set[tuple[str, ...]]:
    return {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints(table)
    }


def _foreign_key_columns(inspector, table: str) -> set[tuple[tuple[str, ...], tuple[str, ...]]]:
    return {
        (tuple(item["constrained_columns"]), tuple(item["referred_columns"]))
        for item in inspector.get_foreign_keys(table)
    }


def _round_trip(url: str) -> None:
    config = _alembic_config(url)
    command.upgrade(config, "0007_chunk_rev")
    engine = create_engine(url)
    assert not (GOVERNANCE_TABLES & set(inspect(engine).get_table_names()))
    engine.dispose()

    command.upgrade(config, "0008_governance")
    engine = create_engine(url)
    inspector = inspect(engine)
    assert GOVERNANCE_TABLES <= set(inspector.get_table_names())
    folder_indexes = inspector.get_indexes("knowledge_folders")
    assert not any("path" in item["column_names"] for item in folder_indexes)
    assert any(
        item["column_names"] == ["tenant_id", "dataset_id", "path_hash"]
        for item in folder_indexes
    )
    assert ("tenant_id", "id") in _constraint_columns(inspector, "datasets")
    assert ("tenant_id", "dataset_id", "id") in _constraint_columns(
        inspector, "documents"
    )
    assert ("tenant_id", "dataset_id", "id") in _constraint_columns(
        inspector, "knowledge_folders"
    )
    assert ("tenant_id", "dataset_id", "id") in _constraint_columns(
        inspector, "knowledge_tags"
    )
    assert (
        ("tenant_id", "dataset_id", "parent_id"),
        ("tenant_id", "dataset_id", "id"),
    ) in _foreign_key_columns(inspector, "knowledge_folders")
    assert (
        ("tenant_id", "dataset_id", "folder_id"),
        ("tenant_id", "dataset_id", "id"),
    ) in _foreign_key_columns(inspector, "documents")
    assert (
        ("tenant_id", "dataset_id", "document_id"),
        ("tenant_id", "dataset_id", "id"),
    ) in _foreign_key_columns(inspector, "document_tags")
    assert (
        ("tenant_id", "dataset_id", "tag_id"),
        ("tenant_id", "dataset_id", "id"),
    ) in _foreign_key_columns(inspector, "document_tags")
    assert "folder_id" in {item["name"] for item in inspector.get_columns("documents")}
    assert {"created_by", "path_hash"} <= {
        item["name"] for item in inspector.get_columns("knowledge_folders")
    }
    assert "created_by" in {
        item["name"] for item in inspector.get_columns("knowledge_tags")
    }
    audit_columns = {item["name"]: item for item in inspector.get_columns(
        "knowledge_audit_events"
    )}
    assert {"sequence", "occurred_at"} <= set(audit_columns)
    engine.dispose()

    command.downgrade(config, "0007_chunk_rev")
    engine = create_engine(url)
    inspector = inspect(engine)
    assert not (GOVERNANCE_TABLES & set(inspector.get_table_names()))
    assert "folder_id" not in {item["name"] for item in inspector.get_columns("documents")}
    assert ("tenant_id", "id") not in _constraint_columns(inspector, "datasets")
    engine.dispose()

    command.upgrade(config, "0008_governance")
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            revision = connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one()
        assert revision == "0008_governance"
    finally:
        engine.dispose()


def test_sqlite_governance_migration_upgrades_downgrades_and_reupgrades(
    tmp_path: Path,
) -> None:
    _round_trip(sqlite_url(tmp_path / "governance-migration.db"))


def test_database_constraints_reject_cross_scope_and_forged_parent_keys(
    tmp_path: Path,
) -> None:
    url = sqlite_url(tmp_path / "governance-constraints.db")
    command.upgrade(_alembic_config(url), "head")
    engine = create_engine(url)
    align_era_columns(engine)
    with Session(engine) as session:
        session.execute(text("PRAGMA foreign_keys=ON"))
        session.add_all(
            [
                Tenant(id="tenant-1", name="Tenant"),
                Dataset(id="dataset-1", tenant_id="tenant-1", name="One"),
                Dataset(id="dataset-2", tenant_id="tenant-1", name="Two"),
                Document(
                    id="doc-2",
                    tenant_id="tenant-1",
                    dataset_id="dataset-2",
                    name="Document",
                ),
            ]
        )
        session.commit()
        root = KnowledgeFolder(
            id="folder-1",
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            parent_id=None,
            parent_key="",
            name="Root",
            normalized_name="root",
            path="Root",
            path_hash="hash-root",
            created_by="user",
        )
        tag = KnowledgeTag(
            id="tag-1",
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            name="Tag",
            normalized_name="tag",
            created_by="user",
        )
        session.add_all([root, tag])
        session.commit()

        session.add(
            KnowledgeFolder(
                id="forged",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                parent_id=None,
                parent_key="arbitrary",
                name="Root",
                normalized_name="root",
                path="Root",
                path_hash="hash-forged",
                created_by="user",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(
            KnowledgeFolder(
                id="cross-child",
                tenant_id="tenant-1",
                dataset_id="dataset-2",
                parent_id=root.id,
                parent_key=root.id,
                name="Child",
                normalized_name="child",
                path="Root/Child",
                path_hash="hash-child",
                created_by="user",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(
            DocumentTag(
                id="cross-link",
                tenant_id="tenant-1",
                dataset_id="dataset-2",
                document_id="doc-2",
                tag_id=tag.id,
                created_by="user",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        document = session.get(Document, "doc-2")
        assert document is not None
        document.folder_id = root.id
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
    engine.dispose()


def test_stamp_existing_head_schema_uses_actual_governance_head(tmp_path: Path) -> None:
    from core import catalog_schema

    url = sqlite_url(tmp_path / "unstamped-head.db")
    catalog_schema.upgrade_catalog(url)
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE alembic_version"))
    engine.dispose()

    catalog_schema.stamp_existing_catalog(url)

    engine = create_engine(url)
    try:
        state = catalog_schema.inspect_catalog_schema(engine)
        assert state.revision == catalog_schema.HEAD_REVISION
        assert state.status == "current"
    finally:
        engine.dispose()


@pytest.mark.skipif(not os.getenv("TEST_MYSQL_URL"), reason="TEST_MYSQL_URL not configured")
def test_mysql_governance_migration_round_trip() -> None:
    source = make_url(os.environ["TEST_MYSQL_URL"])
    assert source.get_backend_name() == "mysql"
    database_name = f"rag4c_governance_{uuid.uuid4().hex[:12]}"
    admin_url = source.set(database="mysql")
    test_url = source.set(database=database_name)
    admin_engine = create_engine(admin_url)
    try:
        with admin_engine.begin() as connection:
            connection.execute(
                text(f"CREATE DATABASE `{database_name}` CHARACTER SET utf8mb4")
            )
        _round_trip(test_url.render_as_string(hide_password=False))
    finally:
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{database_name}`"))
        admin_engine.dispose()
